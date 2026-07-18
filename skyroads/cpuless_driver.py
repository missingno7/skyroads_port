"""The CPUless FRAME DRIVER -- the one per-frame model, shared by every no-CPU
consumer of ``skyroads/recovered/``.

There is no interpreter here and no CPU to step: the recovered program runs as
plain Python and calls back OUT to the platform.  So the frame loop cannot be a
loop around a stepper -- it lives INSIDE two synchronous seams the recovered code
reaches on its own:

``boundary``
    SkyRoads paces off ``ds:[1600]``, a tick its INT 08h ISR bumps.  Its wait
    loops call ``plat.boundary`` at a boundary head.  PARK ON RE-ARRIVAL: the 1st
    pass at a head this frame lets the wait body run to steady state; the 2nd
    pass proves the wait unsatisfied with nothing left to do -- that IS the frame
    boundary.  (Parking on pass 1 silently cuts the frame early; see the
    boundary-park note in the VMless differential.)

``blocking read``
    A press-any-key ``INT 21h AH=07`` on an empty type-ahead buffer must WAIT.
    A flat CPU rewinds IP and re-runs the read next frame; we cannot rewind a
    Python call stack, so ``CPUlessPlatformRuntime.blocking_read_cb`` advances a
    frame IN PLACE (frozen screen + IRQ-driven palette fade) until the awaited
    key arrives, then the read retries.

Both seams advance the SAME counter through the SAME :meth:`advance`, so a
consumer sees one consistent frame numbering however the game is waiting.  Order
within a frame boundary matters and is fixed here: the finished frame is handed
over FIRST, then the NEXT frame's input is applied BEFORE it renders (input N
affects frame N), then its timer IRQs are delivered through the game's own
recovered INT 08h ISR.  Getting that order wrong is silent -- it shows up only as
a one-frame lag in a differential.

CPU-FREE by construction: this module imports nothing but the recovered ISR the
caller hands it.  ``tools/lint_cpuless.py`` proves the whole reachable graph.
"""
from __future__ import annotations

#: Register bundle the recovered INT 08h timer ISR (1010:3B17) takes.  It is
#: flags-live (``_flags_in``); the INT 09h keyboard ISR is NOT, so a caller that
#: also drives 3BCC must not pass flags to it.
TIMER_INPUTS = ("ax", "bp", "bx", "cx", "di", "ds", "dx", "es", "si", "sp", "ss")

#: SkyRoads' pacing: 6 IRQ0 ticks per displayed frame (30 Hz frames = 180 Hz
#: IRQ0), the same ratio every recorded demo carries as ``timer_irqs_per_frame``.
TIMER_IRQS_PER_FRAME = 6


class CPUlessFrameDriver:
    """Drives frames for a recovered corpus running under a CPUless runtime.

    ``present(frame)`` is called with each finished frame's number -- draw it,
    capture it, whatever the consumer needs.  It may raise to stop the run (the
    exception propagates out through the recovered call stack to ``rt.call``).

    ``supply_input(frame, regs)`` is called for the UPCOMING frame, before it
    renders, so the consumer can deliver that frame's keys.
    """

    def __init__(self, mem, rt, timer_isr, *, present, supply_input=None,
                 irqs: int = TIMER_IRQS_PER_FRAME):
        self.mem = mem
        self.rt = rt
        self.timer_isr = timer_isr
        self.irqs = irqs
        self._present = present
        self._supply_input = supply_input
        self.frame = 0
        #: the boundary head that last cut a frame, ``(cs, ip)`` -- the witness a
        #: consumer reports ("reached the frame loop at 1010:434A").  None while
        #: only blocking-read waits have advanced frames.
        self.head: tuple[int, int] | None = None
        self._seen: set[tuple[int, int]] = set()

    # -- the two seams -----------------------------------------------------

    def boundary(self, head_cs, head_ip, resume_ip, regs, cost):
        """``plat.boundary`` callback: park on RE-arrival (see module docstring)."""
        key = (head_cs, head_ip)
        if key not in self._seen:
            self._seen.add(key)             # 1st pass: let the wait body run
            return regs, regs.get("_flags_in", 2), 0
        self.head = key
        self.advance(regs)                  # 2nd pass: the frame is done
        return regs, regs.get("_flags_in", 2), 0

    def advance(self, regs):
        """Hand over the finished frame, then prepare the next one.

        Also serves as ``rt.blocking_read_cb`` so a waiting console read keeps
        frames (and the timer-driven palette fade) running while it blocks.
        """
        self._present(self.frame)
        self.frame += 1
        self._seen.clear()                  # a new frame starts a fresh pass count
        if self._supply_input is not None:
            self._supply_input(self.frame, regs)
        for _ in range(self.irqs):
            kw = {k: regs[k] for k in TIMER_INPUTS if k in regs}
            kw["_flags_in"] = regs.get("_flags_in", 2)
            self.timer_isr(self.mem, self.rt, **kw)

    def install(self, rt) -> "CPUlessFrameDriver":
        """Wire both seams on a :class:`CPUlessPlatformRuntime` and return self."""
        rt.boundary_cb = self.boundary
        rt.blocking_read_cb = self.advance
        return self
