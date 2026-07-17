"""The end-to-end gate: replay a recorded demo through the LIFTED corpus and
diff it against the pure-ASM oracle, frame by frame.

This is what proves the recovered program, as a whole. Per-entry
``liftverify`` proves routines in isolation and only for the calls it samples;
``install_vmless_graph``'s own docstring says the assembled graph's correctness
"is not proven per-entry but by the END-TO-END oracle" -- this is that oracle.
It covers what nothing else does: the de-SMC transforms under real patch
traffic, the boundary parks and their resume entries, the census's completeness
(a missing entry fails the wall), and the front-end code no hand-written native
path ever reproduced.

METHOD. Both runtimes start from the DEMO'S OWN snapshot, so they begin
byte-identical:

  * reference -- the pure ASM oracle. ``install_replacements=False`` on the
    RUNTIME (the real parameter; setting it on ``args`` is a no-op and leaves
    32 hooks live -- see scripts/play.py). Interpreted, no recovered code.
  * candidate -- the same snapshot with the generated corpus installed as the
    replacement graph and the interpreter POISONED, so it cannot silently fall
    back to interpreting the original.

Each frame both get the SAME recorded input, the SAME 6 timer IRQs through the
game's own INT 08h ISR, and the SAME frame cut; then the VGA plane is diffed.

THE FRAME CUT IS SHARED, and it has to be. A fixed step budget is not a neutral
reference: play.py records with --frame-park ON and treats 48,000 as a CEILING
above peak real work, but --no-replacements turns that park off, so a budgeted
oracle is a machine no demo was recorded on. The fade loop at 1010:434A has a
~40,000-step body (a palette re-blend), so 48,000 cuts the oracle mid-blend and
costs it two frames to notice its tick target -- 2 frames of phase, 285 pixels,
and not one bit of it about the lift. So both sides stop at the 2nd pass over a
declared boundary head. For the corpus that is its park; for the oracle it is
pure observation (an IP check between interpreter steps -- no hook, no
hand-written semantics, nothing that could leak the candidate's answer into the
reference). The interpreter still decides everything the ASM does; the driver
only decides when to stop watching.

Usage:
    python scripts/verify_vmless_demo.py artifacts/demos/demo_skyroads_20260717_122736
    python scripts/verify_vmless_demo.py DEMO --frames 200 --dump-mismatch
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

import scripts.play as sp  # noqa: E402
from dos_re import player  # noqa: E402
from dos_re.cpu import HaltExecution  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.input_demo import InputDemoPlayback  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from dos_re.lift.install import install_vmless_graph  # noqa: E402
from dos_re.player import _use_real_console_input  # noqa: E402
from skyroads.runtime import load_game_snapshot  # noqa: E402

VGA = 0xA0000
VGA_LEN = 64000
DGROUP = 0x1686


class FrameIdle(Exception):
    """The corpus parked in a tick-wait it cannot leave until the next frame."""


#: `enter 0016,00` -- the first instruction of the DECOMPRESSED program at
#: 1010:0000. Before the packer's stub runs, that address holds the stub itself
#: (`cld; push es; push ds; ...`), and every lifted signature disagrees.
_DECOMPRESSED_MARK = bytes.fromhex("c8000000")


def _is_predecompression(pb) -> bool:
    """True if this snapshot was taken before the EXE unpacked itself."""
    img = Path(pb.snapshot_path()) / "memory_1mb.bin"
    if not img.exists():
        return False
    with img.open("rb") as fh:
        fh.seek(0x1010 << 4)
        return fh.read(4) != _DECOMPRESSED_MARK


def read_heads(path: Path) -> set[tuple[int, int]]:
    """The declared boundary heads, as (cs, ip) -- the same file the corpus was
    emitted with, so both sides cut the frame at exactly the same addresses."""
    out: set[tuple[int, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        seg, off = line.split(":")
        out.add((int(seg, 16), int(off, 16)))
    return out


def _mkargs(frontend, demo: Path, pure: bool):
    argv = ["--play-demo", str(demo), "--headless"]
    if pure:
        argv.append("--no-replacements")
    return player.build_arg_parser(frontend).parse_args(argv)


def build_oracle(demo: Path, pb):
    """The reference: this snapshot, interpreted, with NO recovered code."""
    frontend = sp.SkyroadsFrontend(ROOT)
    args = _mkargs(frontend, demo, pure=True)
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    # install_replacements=False is the REAL switch (a create_game_runtime /
    # load_game_snapshot parameter). The frontend does not forward it, so build
    # the runtime directly rather than through frontend.load_snapshot_runtime.
    rt = load_game_snapshot(args.exe, str(pb.snapshot_path()),
                            game_root=args.game_root, install_replacements=False)
    _use_real_console_input(rt)
    rt.dos.mouse_present = pb.mouse_present_hint
    return frontend, args, rt


def build_candidate(demo: Path, pb, lift_dir: Path):
    """The candidate: the same snapshot running the GENERATED corpus, wall armed."""
    frontend = sp.SkyroadsFrontend(ROOT)
    args = _mkargs(frontend, demo, pure=True)
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = load_game_snapshot(args.exe, str(pb.snapshot_path()),
                            game_root=args.game_root, install_replacements=False)
    _use_real_console_input(rt)
    rt.dos.mouse_present = pb.mouse_present_hint
    installed = install_vmless_graph(rt.cpu, lift_dir)
    rt.cpu.interp_forbidden = True          # no silent fallback to the original
    return frontend, args, rt, installed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo")
    ap.add_argument("--lift-dir", default=str(ROOT / "artifacts" / "lifted_full"))
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames")
    ap.add_argument("--irqs", type=int, default=6)
    ap.add_argument("--step-budget", type=int, default=4_000_000)
    ap.add_argument("--heads", default=str(ROOT / "artifacts" / "codemap"
                                           / "boundary_heads.txt"))
    args_cli = ap.parse_args(argv)

    demo = Path(args_cli.demo)
    pb_o = InputDemoPlayback.load(str(demo))
    if getattr(pb_o, "is_cold_start", False):
        # Not a failure and not a pass: there is nothing to resume FROM. A cold
        # demo replays by booting the EXE, which is the one thing an EXE-free
        # corpus cannot do. Its coverage comes through the boot image instead
        # (scripts/build_boot_image.py runs the stub to 1010:61F3 and snapshots
        # the decompressed machine; scripts/play_vmless.py drives that).
        print(f"[verify] SKIP {demo.name}: cold-start demo, no start snapshot. "
              f"The corpus covers the DECOMPRESSED image; boot coverage goes "
              f"through artifacts/boot_image (scripts/build_boot_image.py).")
        return 0
    pb_c = InputDemoPlayback.load(str(demo))
    if _is_predecompression(pb_o):
        print(f"[verify] SKIP {demo.name}: snapshot predates the packer's "
              f"self-decompression (1010:0000 is still the stub, not the game). "
              f"The corpus is lifted for the decompressed image, so there is "
              f"nothing here it could correctly run.")
        return 0

    f_o, a_o, rt_o = build_oracle(demo, pb_o)
    f_c, a_c, rt_c, installed = build_candidate(demo, pb_c, Path(args_cli.lift_dir))
    print(f"[verify] demo={demo.name} frames={pb_o.end_boundary} "
          f"mouse_present={pb_o.mouse_present_hint}")
    print(f"[verify] corpus: {len(installed)} modules installed; wall armed")

    heads = read_heads(Path(args_cli.heads))
    print(f"[verify] frame cut: 2nd pass at any of {len(heads)} boundary heads, "
          f"budget {args_cli.step_budget:,}")

    # PARK ON RE-ARRIVAL, NOT ON ARRIVAL. The emitter calls the hook after the
    # head instruction on EVERY pass; the policy is the host's. Parking on the
    # FIRST pass parks before the loop body has run even once -- and a tick-wait
    # body is not always empty: 1010:434A's is the fade's palette re-blend, so a
    # first-pass park skipped the blend and left its buffer (DGROUP 31AB) zeroed
    # while the oracle had it filled.
    #
    # The tick can only change BETWEEN frames (the driver delivers IRQ0 there),
    # so one iteration is all the loop will ever accomplish this frame: pass 1
    # lets the body run to its steady state, pass 2 proves the wait is still
    # unsatisfied with nothing left to change. That is what pacing.py's verified
    # park_fade_wait encodes -- park only when _fade_loop_cache already holds a
    # blend for the CURRENT tick -- generalized to any head, since "already ran
    # at this tick" == "arrived here before, this frame".
    in_isr = [False]
    seen: set = set()

    def gated(cpu, head_cs, head_ip, resume_ip):
        if in_isr[0]:
            return
        key = (head_cs, head_ip)
        if key not in seen:
            seen.add(key)          # first pass this frame: let the body run
            return
        cpu.s.cs, cpu.s.ip = head_cs & 0xFFFF, resume_ip & 0xFFFF
        raise FrameIdle
    rt_c.cpu.boundary_hook = gated

    def run_oracle(cpu, budget: int) -> None:
        """Run the oracle to the SAME frame cut, by pure observation.

        THE FRAME CUT MUST BE SHARED, or the comparison is not about the lift.
        A fixed step budget is not a neutral reference here: scripts/play.py
        records with --frame-park ON and sizes 48,000 as a CEILING above peak
        real work -- but --no-replacements disables that park, so a budgeted
        oracle is a machine no demo was ever recorded on. It shows: the fade
        loop's body is ~40,000 steps, so 48,000 cuts the oracle MID-BLEND, and
        it needs two extra frames to come back around and notice the tick has
        reached its target. The candidate, parking, sees it at once. Both are
        running the same ASM correctly; only the cut differs -- and that alone
        put them 2 frames out of phase and lit up 285 pixels at frame 48.

        So cut both at the same place: the 2nd pass at a head. This is pure
        OBSERVATION -- an IP comparison between steps, no replacement hook, no
        hand-written semantics, nothing that could feed the candidate's answer
        back to the reference. The interpreter still decides every bit of what
        the ASM does; the driver only decides when to stop watching. The budget
        stays as a ceiling for frames that never reach a head.
        """
        cs_hit: dict = {}
        for _ in range(budget):
            key = (cpu.s.cs, cpu.s.ip)
            if key in heads:
                cs_hit[key] = cs_hit.get(key, 0) + 1
                if cs_hit[key] >= 2:
                    cpu.step()      # execute the head, landing on resume_ip
                    return          # ...exactly where the candidate parks
            cpu.step()

    end = pb_o.end_boundary or 100000
    if args_cli.frames:
        end = min(end, args_cli.frames)

    for frame in range(end):
        if pb_o.finished(frame):
            break
        seen.clear()          # "re-arrival" is per FRAME: a new tick, a new pass
        pb_o.apply_to_runtime(frame, rt_o, deliver=lambda r, sc: f_o.deliver_input(r, sc))
        pb_c.apply_to_runtime(frame, rt_c, deliver=lambda r, sc: f_c.deliver_input(r, sc))

        # oracle: IRQs, then run to the SAME frame cut as the candidate
        for _ in range(args_cli.irqs):
            deliver_interrupt(rt_o, 0x08)
        try:
            run_oracle(rt_o.cpu, args_cli.step_budget)
        except (ConsoleInputWouldBlock, HaltExecution):
            pass

        # candidate: same IRQs, then run until it parks
        in_isr[0] = True
        try:
            for _ in range(args_cli.irqs):
                deliver_interrupt(rt_c, 0x08)
        finally:
            in_isr[0] = False
        try:
            rt_c.cpu.run(args_cli.step_budget)
        except FrameIdle:
            pass
        except (ConsoleInputWouldBlock, HaltExecution):
            pass
        except Exception as exc:                     # noqa: BLE001
            print(f"\n[verify] FRAME {frame}: candidate raised "
                  f"{type(exc).__name__}: {str(exc)[:400]}")
            return 1

        vo = bytes(rt_o.cpu.mem.data[VGA:VGA + VGA_LEN])
        vc = bytes(rt_c.cpu.mem.data[VGA:VGA + VGA_LEN])
        if vo != vc:
            n = sum(1 for a, b in zip(vo, vc) if a != b)
            first = next(i for i, (a, b) in enumerate(zip(vo, vc)) if a != b)
            print(f"\n[verify] VGA DIVERGED at frame {frame}: {n} px differ; "
                  f"first at row {first // 320} col {first % 320} "
                  f"(oracle={vo[first]:02X} corpus={vc[first]:02X})")
            return 1
        if frame % 50 == 0:
            print(f"  frame {frame:4d}: VGA identical")

    print(f"\n[verify] PASS -- {frame + 1} frames, the generated corpus is "
          f"pixel-identical to the pure ASM oracle over {demo.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
