"""Derive the tick-wait BOUNDARY HEADS from the recovery IR.

SkyRoads paces itself off ``ds:[1600]``, the counter its INT 08h ISR bumps. A
frame's timer IRQs are delivered only at frame start, so ``[1600]`` is
architecturally constant for the whole frame: any loop that waits for it to
change cannot exit, and the frame is over the moment the game reaches one.
``irgen --boundary-heads`` turns each head into an emitted observer +
``RESUME_ENTRIES`` so the lifted body parks and resumes instead of spinning.

Why derive instead of hand-list: ``skyroads/pacing.py`` names three heads
(22F8 / 434A / 47CD) and they are correct -- but that list was written for the
INTERPRETED runtime, where an *unlisted* wait merely burned the step budget and
the frame ended anyway. Lifted, an unlisted wait is an infinite Python loop:
the corpus has no way to advance time inside a lifted function. So the lifted
runner needs the COMPLETE set, and completeness is not something to eyeball --
`1010:4468` (a `delay(ticks)` helper: zero [1600], spin until it reaches the
argument) was missed by hand and only surfaced as a MAX_ITERATIONS crash five
screens into the boot.

The rule: a loop that READS ``ds:[1600]`` and never writes it. Since the tick
is architecturally constant for the whole frame, such a loop computes the same
thing every iteration and cannot reach a tick-dependent exit -- by
construction, not by observation.

The rule used to say COMPARES, and that was too narrow in the expensive
direction: it models a spin (``cmp ds:[1600],2; jnb``) and misses the FADE
shape, which derives a value from the tick instead of comparing it --

    1010:4860  mov word [1600],0     ; reset, OUTSIDE the loop
    1010:4866  mov ax,013Fh          ; <- the head: the back-edge lands here
    1010:4869  imul word [1600]      ; 319 * tick        <- a read, not a cmp
    1010:4872  div cx                ;   / 18
    1010:4877  sub cx,ax             ; x = 319 - that
    1010:487C  cmp word [bp-2],0
    1010:4880  jge ...               ; loop while x >= 0 -- i.e. until tick>=18

-- a 319-pixel wipe paced over 18 ticks. Same shape as 1010:434A, the fade,
which is why 434A had to be hand-listed in KNOWN_HEADS to begin with: the note
there ("a couple read the tick in shapes the generic rule below does not
model") was this gap, admitted but not closed. It cost a 100,000,000-iteration
hang at frame 280 of the cold boot -- and note the stuck detector got it right,
reporting "registers WERE still changing", which is exactly what separates this
from a spin.

Writes to [1600] are the loop's own reset and are not reads; a loop that writes
the tick is not waiting on the ISR and is excluded.

Usage:
    python scripts/find_boundary_heads.py            # -> artifacts/codemap/boundary_heads.txt
    python scripts/find_boundary_heads.py --print
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ds:[1600] — the tick counter (skyroads/pacing.py's TICK_ADDR).
TICK_ADDR = 0x1600
#: Heads named by pacing.py as verified recovery facts. Kept as a floor: they
#: were each proven byte-equivalent to burning the full budget, and a couple
#: read the tick in shapes the generic rule below does not model.
KNOWN_HEADS = (0x22F8, 0x434A, 0x47CD)


#: `mov word [1600],imm` (C7 06) and `mov [1600],ax` (A3) -- the loop RESETS.
#: A write is not a wait: whoever writes the tick is not waiting on the ISR.
_TICK_WRITE_PREFIXES = ("c706", "a3")


def _touches_tick(inst: dict) -> bool:
    return TICK_ADDR.to_bytes(2, "little") in bytes.fromhex(inst["bytes"])


def _writes_tick(inst: dict) -> bool:
    b = inst["bytes"]
    if not _touches_tick(inst):
        return False
    return any(b.startswith(p) for p in _TICK_WRITE_PREFIXES)


def _observable(inst: dict | None) -> bool:
    """The emitter can only place an observer at a non-transfer instruction: a
    park at a branch would have nowhere to resume (emit.py enforces this)."""
    return inst is not None and inst["kind"] in ("seq", "call", "call_far",
                                                 "call_ind", "int")


def compare_heads(ir: dict) -> dict[int, str]:
    """The ORIGINAL rule: a tick-COMPARE inside a backward loop.

    Kept, and kept first, because it is the proven one -- this exact set drives
    26 demos and 10,941 frames of pixel-identical replay. The read-loop rule
    below is strictly ADDITIVE to it. They disagree about which instruction to
    name (this one names the compare, that one the loop header), and both are
    valid observation points, so union them rather than pick: a head that is
    never reached twice in a frame is inert, but a head that is MISSING is an
    infinite loop.
    """
    out: dict[int, str] = {}
    for key, fn in ir["functions"].items():
        insts = {int(i["ip"], 16): i
                 for b in fn["blocks"] for i in b["instructions"]}
        for ip, inst in sorted(insts.items()):
            if not inst["mnemonic"].startswith("cmp") or not _touches_tick(inst):
                continue
            loops_back = any(
                j.get("target") and j["kind"] in ("jcc", "jmp")
                and int(j["ip"], 16) > ip and int(j["target"], 16) <= ip
                for j in insts.values())
            if loops_back and _observable(inst):
                out[ip] = f"cmp ds:[{TICK_ADDR:04X}] in a backward loop (fn {key})"
    return out


def read_loop_heads(ir: dict) -> dict[int, str]:
    """``head_ip -> evidence``: the header of every loop that READS the tick.

    A back-edge (a jump to at or before itself) defines the loop; its body is
    [target, branch]. If anything in that body reads ds:[1600] and nothing in
    it writes the tick, the loop is waiting on the ISR -- and no ISR can run
    inside a lifted function, so the header is a boundary head.

    This is what catches the FADE shape, which derives a value from the tick
    rather than comparing it (see the module docstring): 1010:434A had to be
    hand-listed for exactly this reason, and 1010:4866 -- the same shape, in
    the intro -- was simply missing until a cold-boot differential hung on it.

    The head is the LOOP HEADER, not the reading instruction: every iteration
    passes through it, so it is where a park observes the boundary once a pass.
    """
    out: dict[int, str] = {}
    for key, fn in ir["functions"].items():
        insts = {int(i["ip"], 16): i
                 for b in fn["blocks"] for i in b["instructions"]}
        for j in insts.values():
            if not (j.get("target") and j["kind"] in ("jcc", "jmp")):
                continue
            back, head = int(j["ip"], 16), int(j["target"], 16)
            if head > back:
                continue                        # forward jump: not a loop
            body = [i for ip, i in insts.items() if head <= ip <= back]
            if any(_writes_tick(i) for i in body):
                continue                        # writes its own tick: not a wait
            reads = [i for i in body if _touches_tick(i)]
            if not reads or not _observable(insts.get(head)):
                continue
            why = ", ".join(sorted({i["mnemonic"].split()[0] for i in reads}))
            out[head] = (f"loop {head:04X}..{back:04X} reads ds:[{TICK_ADDR:04X}]"
                         f" ({why}) and never writes it (fn {key})")
    return out


def spin_heads(ir: dict) -> dict[int, str]:
    """Every derived head: the proven compare rule, plus the read-loop rule."""
    out = dict(compare_heads(ir))
    for ip, why in read_loop_heads(ir).items():
        out.setdefault(ip, why)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ir", default=str(ROOT / "artifacts" / "codemap" / "recovery_ir.json"))
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "codemap" / "boundary_heads.txt"))
    ap.add_argument("--seg", default="1010")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    ir = json.loads(Path(args.ir).read_text())
    found = spin_heads(ir)
    seg = int(args.seg, 16)
    heads = dict.fromkeys(sorted(set(found) | set(KNOWN_HEADS)))
    lines = [
        "# Tick-wait boundary heads -- DERIVED by scripts/find_boundary_heads.py",
        "# from the recovery IR (a cmp of ds:[1600] inside a backward loop),",
        "# unioned with skyroads/pacing.py's verified KNOWN_HEADS.",
        "# Regenerate after any census change: an unlisted wait is an infinite",
        "# loop in the lifted corpus, not merely a wasted step budget.",
    ]
    for ip in heads:
        why = found.get(ip, "skyroads/pacing.py verified head")
        lines.append(f"# {seg:04X}:{ip:04X}  {why}")
    lines += [f"{seg:04X}:{ip:04X}" for ip in heads]
    text = "\n".join(lines) + "\n"
    if args.show:
        print(text)
    Path(args.out).write_text(text)
    print(f"[heads] {len(heads)} boundary heads -> {args.out} "
          f"({len(found)} derived, {len(set(KNOWN_HEADS) - set(found))} from pacing.py only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
