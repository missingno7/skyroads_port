"""Island-driven bodies checked against the generated bodies they claim to reproduce.

An island's ``status`` is a claim about evidence. ``ASM_MATCHED`` means "diffed on
captured cases" -- weaker than the standard the generated corpus already meets
(672 frames byte-exact, VGA plane and DAC palette, from cold start). Promoting an
island on that basis would lower the proof standard, so the ladder needs a rung
between "diffed once" and "drives the program".

Shadow mode is that rung, and it now lives in :mod:`dos_re.lift.shadow` because
Overkill needs the same thing. The generated body drives -- outputs, flags and
cost are its own, so behaviour is provably unchanged -- and the candidate from
:mod:`skyroads.island_bodies` runs beside it on every call the real game makes,
with every observable compared.

WHAT CHANGED, and why it matters more than it looks: this module used to pass a
``checker`` callback, which left it free to compare whatever it liked. It
compared **AX and nothing else** -- not the other six outputs, not flags, not
fmask, not the 25 words the body leaves on the stack -- while tallying cost into
a counter that read like an assertion and asserted nothing. The candidate is now
a drop-in with the generated signature, so the comparison is total by
construction and the thing being proven is the thing that would ship.

Run it:  python scripts/verify_cpuless.py <demo> --shadow-islands
         python scripts/check_all.py            (gated there too)

NOT SHADOWED: ``1010:3A96`` unpack_animation_segment
    It has no drop-in body, only a pure island, and the checker that used to
    stand in for one compared exactly ONE 64 KB segment per call -- the one named
    by the post-state ``es`` -- and neither AX, BX, CX, DS, DX, flags, fmask nor
    cost. Its recorded "full 64 KB segment byte-exact" evidence is therefore
    materially narrower than its VERIFIED status suggests. An inherited survey
    also reports the pre-state snapshot overlooking ~39 KB of overlap accumulated
    by preceding passes; that specific figure is NOT re-measured here and is
    recorded as a caution, not a result. Either way it is further out than 04C0
    and is deliberately left alone.
"""
from __future__ import annotations

from dos_re.lift.shadow import Verdict, install_shadows, records, report, reset, verdict

from skyroads.cpuless_overrides import RECOVERED_PKG
from skyroads.island_bodies import BODIES

__all__ = ["Verdict", "install_all", "records", "report", "reset", "verdict"]

#: No exemptions. Every output, flags, fmask, cost and the ordered byte-write log
#: are compared for every shadowed address. If that ever has to change, the
#: exemption goes here with a written reason -- see :class:`dos_re.lift.shadow.Exemption`.
EXEMPTIONS: dict = {}


def install_all() -> list:
    """Install every shadow. Call BEFORE the corpus is imported."""
    reset()
    return install_shadows(RECOVERED_PKG, BODIES, exemptions=EXEMPTIONS)
