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

COLD-START DEMOS are the strongest form of this, and the only one that tests
the actual deliverable:

  * reference -- the real SKYROADS.EXE, interpreted, from its own boot.
  * candidate -- the data-only BOOT IMAGE. No EXE, and not merely unused: its
    code bytes are ZEROED, so falling back to the binary is not something it
    could do if it tried.

Two kinds count as cold: a demo with NO snapshot (it re-boots the EXE), and a
demo whose snapshot is of the machine BEFORE the packer unpacked it (1010:0000
still holds the stub -- it recorded its starting image rather than re-booting).
Both are settled identically. Treating only the first as cold and SKIPPING the
second cost the only coverage of the Controls and Help screens -- the front-end
this gate exists to prove -- so 9 demos sat unverified behind a distinction
that makes no difference.

The stub is the one asymmetry, and it is not part of the comparison -- it is
what PRODUCES the thing being compared. A cold demo's frame 0 is the EXE entry;
the image's is 1010:61F3, because a data-only image cannot unpack itself: the
unpack already happened, at build time, and its output IS the image. So the
oracle runs the stub as a pre-phase (438,448 steps) and both start frame 0 at
the same instruction. That is sound by construction -- build_boot_image.py makes
the image by running this exact stub on this exact EXE -- and it is also
necessary: at the recorded 48,000 steps/frame the stub spans ~9 frames, and the
candidate has no way to spend them.

Each frame both get the SAME recorded input, the SAME 6 timer IRQs through the
game's own INT 08h ISR, and the SAME frame cut; then the VGA plane AND the DAC
palette are diffed. Both, because they are different state: the plane holds
indices, the DAC holds the colours they index, and vga_palette is device state
rather than mem.data. SkyRoads' fades are pure palette animation -- the index
plane does not change at all during one -- so a plane-only diff would call a
wholly wrong screen identical.

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

THE ONE KNOWN RESIDUAL is intro frame 1115 (demo_intro_20260717_125403): 9 DAC
entries (161-169) differ -- the oracle has the logo gradient faded in, the
candidate still black. It is NOT a lift defect, and it is not even a frame-1115
event. Traced, both machines ENTER 1115 already a fraction of a blend apart: at
the first arrival at 434A the oracle holds bx=B80A, the candidate bx=B822 -- the
fade-buffer pointer. The 2nd-pass cut removed the gross 2-frame phase error, but
a sub-frame remainder (the lifted head fires its park one blend-step off the raw
head's IP hit) accumulates across ~1000 frames of one continuous fade and tips
one blend cycle at 1115. The gate compares OBSERVABLE output -- plane + palette,
what the player sees -- which stays identical the whole way up because the drift
lives in the 31AB blend scratch, invisible until it crosses a palette boundary.
Both machines run the same ASM correctly. Left strict (34/35) rather than
papered over: masking a real divergence to hide a characterized cosmetic one
would cost more than the one frame it buys.

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
from dos_re.crash import save_crash  # noqa: E402
from dos_re.dos import ConsoleInputWouldBlock  # noqa: E402
from dos_re.hooks import assert_pure_oracle  # noqa: E402
from dos_re.independence import boot_vmless_image  # noqa: E402
from dos_re.input_demo import InputDemoPlayback  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from dos_re.lift.install import install_vmless_graph  # noqa: E402
from dos_re.player import _use_real_console_input  # noqa: E402
from skyroads.runtime import create_game_runtime, load_game_snapshot  # noqa: E402

VGA = 0xA0000
VGA_LEN = 64000
DGROUP = 0x1686
#: The far jump the packer stub makes once it has decompressed the program and
#: applied its relocations -- where the boot image begins (build_boot_image.py).
CANONICAL_ENTRY = (0x1010, 0x61F3)


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


def _save_both(args_cli, demo: Path, frame: int, rt_o, rt_c, status: str,
               exc: BaseException | None = None, **detail) -> None:
    """Keep BOTH machines at the moment they disagreed.

    A divergence is a PAIR -- the answer is never in one side alone, it is in
    what the two did differently -- so saving one is saving half the evidence.
    And getting back here is the expensive part: the palette divergence at intro
    frame 1115 took a multi-minute replay of two runtimes for every question
    asked of it, over and over, about a pair of machines that had both been
    sitting right here when they split.

    Both snapshots are ordinary and resumable, so the next question costs a load
    rather than a replay:

        rt = load_snapshot_headless(<dir>/candidate, game_root='assets')
    """
    root = Path(args_cli.crash_root) / f"{demo.name}_f{frame:05d}_{status}"
    for name, rt in (("oracle", rt_o), ("candidate", rt_c)):
        save_crash(rt, root / name, exc=exc if name == "candidate" else None,
                   status=f"{status}:{name}", frame=frame, demo=demo.name,
                   **detail)
    print(f"[verify] both machines saved -> {root}")
    print(f"[verify]   resume either (you land ON the divergence, no replay):\n"
          f"             from dos_re.snapshot_headless import load_snapshot_headless\n"
          f"             rt = load_snapshot_headless(r'{root / 'candidate'}',"
          f" game_root='assets')")


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
    # PROVE it, do not assume it: a replacement on the reference side makes this
    # a diff against a MODIFIED original.  allow= is empty because the synthetic
    # hardware this oracle needs (BIOS INT 09h ISR, dummy IRET stub) is
    # installed outside the registry and is therefore not in scope here.
    assert_pure_oracle(rt.cpu, allow=frozenset())
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


def build_oracle_cold(demo: Path, pb):
    """Reference for a COLD demo: a fresh boot of the real EXE, interpreted.

    Starts where DOS starts it -- on the packer stub, ~438,000 steps short of
    the game. That asymmetry is the whole difficulty of a cold differential;
    see ``run_stub`` for how it is settled.
    """
    frontend = sp.SkyroadsFrontend(ROOT)
    args = _mkargs(frontend, demo, pure=True)
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt = create_game_runtime(args.exe, game_root=args.game_root,
                             command_tail=args.dos_args,
                             install_replacements=False)
    assert_pure_oracle(rt.cpu, allow=frozenset())   # see build_oracle
    _use_real_console_input(rt)
    rt.dos.mouse_present = pb.mouse_present_hint
    return frontend, args, rt


def build_candidate_cold(demo: Path, pb, boot_dir: Path, lift_dir: Path):
    """Candidate for a COLD demo: the data-only BOOT IMAGE. No EXE at all.

    This is the only configuration that tests what the project is actually for:
    the image's code bytes are ZEROED, so nothing here can fall back to running
    the binary even in principle. The image is already at the canonical
    post-decompression entry (1010:61F3) -- it has no stub to run, because
    ``build_boot_image.py`` already ran it once, at build time, and poisoned the
    result.
    """
    frontend = sp.SkyroadsFrontend(ROOT)
    args = _mkargs(frontend, demo, pure=True)
    frontend.apply_demo_metadata(args, pb.manifest.get("metadata", {}))
    rt, manifest = boot_vmless_image(boot_dir, game_root=args.game_root,
                                     lift_dir=lift_dir)
    _use_real_console_input(rt)
    rt.dos.mouse_present = pb.mouse_present_hint
    return frontend, args, rt, manifest


def run_stub(cpu, entry_cs: int, entry_ip: int, limit: int = 5_000_000) -> int:
    """Run the oracle's packer stub up to the canonical entry. Returns steps.

    THE COLD ALIGNMENT. A cold demo's frame 0 is the EXE entry; the candidate's
    is 1010:61F3, because a data-only image cannot unpack itself -- the unpack
    already happened, at build time, and its output IS the image. So the stub is
    not part of the comparison: it is what produces the thing being compared.
    Run it as a PRE-PHASE, outside the frame loop, and both sides start frame 0
    at the same instruction with the same machine.

    This is sound precisely because the stub is what build_boot_image.py itself
    runs to make the image (`--- 438,448 stub steps ---`), from the same EXE:
    the candidate's frame 0 state is BY CONSTRUCTION the oracle's state here.
    It is also why the stub cannot be left inside the frame loop -- at the
    recorded 48,000 steps/frame it spans ~9 frames, and the candidate has no
    way to spend them.
    """
    for n in range(limit):
        if cpu.s.cs == entry_cs and cpu.s.ip == entry_ip:
            return n
        cpu.step()
    raise RuntimeError(f"stub did not reach {entry_cs:04X}:{entry_ip:04X} "
                       f"in {limit:,} steps (at {cpu.s.cs:04X}:{cpu.s.ip:04X})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo")
    # The SHIPPED corpus -- the same directory close_vmless_wall.py emits and
    # play_vmless.py/hooks.py import.  This defaulted to the 1.0-era
    # artifacts/lifted_full, which nothing has written since the dos_re 2.0
    # rename: the differential was proving an orphaned snapshot rather than the
    # code that runs.  It happened to be harmless (the 182 shared modules were
    # byte-identical) right up until a census added three functions to the
    # shipped corpus and not to the snapshot -- a gate that proves a different
    # artifact than the one that ships is not a gate.
    ap.add_argument("--lift-dir",
                    default=str(ROOT / "skyroads" / "lifted" / "functions"))
    ap.add_argument("--frames", type=int, default=0, help="stop after N frames")
    ap.add_argument("--irqs", type=int, default=6)
    # 4_000_000 was sized for an oracle that still carried the replacement
    # hooks (install_replacements=False only guarded the hooks IMPORT, which
    # gates nothing). A genuinely pure oracle interprets the real blitters and
    # decompressors: measured peak 17.1M steps/frame, so this leaves ~3.7x
    # headroom, and run_oracle now fails loud rather than truncating.
    ap.add_argument("--step-budget", type=int, default=64_000_000)
    ap.add_argument("--heads", default=str(ROOT / "artifacts" / "codemap"
                                           / "boundary_heads.txt"))
    ap.add_argument("--boot-dir", default=str(ROOT / "artifacts" / "boot_image"),
                    help="the data-only boot image (cold demos run FROM it)")
    ap.add_argument("--crash-root", default=str(ROOT / "artifacts" / "crashes"),
                    help="where a divergence leaves BOTH resumable machines")
    args_cli = ap.parse_args(argv)

    demo = Path(args_cli.demo)
    pb_o = InputDemoPlayback.load(str(demo))
    pb_c = InputDemoPlayback.load(str(demo))
    # A demo is COLD if it has no snapshot at all, OR if its snapshot is of the
    # machine BEFORE the packer unpacked it (1010:0000 still holds the stub).
    # The second kind is a cold start too -- it just recorded its starting image
    # instead of re-booting the EXE -- and both are settled the same way: run the
    # stub as a pre-phase, then start frame 0 at the canonical entry with the
    # candidate on the boot image. Skipping them cost the ONLY coverage of the
    # Controls and Help screens (demo_cold_20260713_213510 walks intro -> menu ->
    # controls -> help -> select -> play), which is exactly the front-end this
    # differential is supposed to be proving.
    cold = bool(getattr(pb_o, "is_cold_start", False)) or _is_predecompression(pb_o)

    if cold:
        # THE REAL THING: no EXE on the candidate side at all.
        f_o, a_o, rt_o = (build_oracle_cold(demo, pb_o)
                          if getattr(pb_o, "is_cold_start", False)
                          else build_oracle(demo, pb_o))
        f_c, a_c, rt_c, manifest = build_candidate_cold(
            demo, pb_c, Path(args_cli.boot_dir), Path(args_cli.lift_dir))
        # boot_vmless_image installs the graph itself; count the dispatch
        # points it registered (entries + every re-entry), not modules.
        installed = None
        kind = ("no snapshot" if getattr(pb_o, "is_cold_start", False)
                else "pre-decompression snapshot")
        print(f"[verify] demo={demo.name} COLD START ({kind}), "
              f"frames={pb_o.end_boundary} "
              f"mouse_present={pb_o.mouse_present_hint}")
        n = run_stub(rt_o.cpu, *CANONICAL_ENTRY)
        print(f"[verify] oracle: ran the packer stub {n:,} steps to "
              f"{CANONICAL_ENTRY[0]:04X}:{CANONICAL_ENTRY[1]:04X}; the candidate "
              f"starts there by construction (its image IS that unpack, poisoned)")
        poison = manifest["poison"]
        print(f"[verify] candidate: BOOT IMAGE, no EXE -- "
              f"{poison['poisoned_bytes']:,} code bytes zeroed over "
              f"{poison['censused_functions']} functions; "
              f"{poison['code_bytes_present_after']} original code bytes remain")
    else:
        f_o, a_o, rt_o = build_oracle(demo, pb_o)
        f_c, a_c, rt_c, installed = build_candidate(demo, pb_c,
                                                    Path(args_cli.lift_dir))
        print(f"[verify] demo={demo.name} frames={pb_o.end_boundary} "
              f"mouse_present={pb_o.mouse_present_hint}")
    if installed is None:
        print(f"[verify] corpus: {len(rt_c.cpu.replacement_hooks)} dispatch "
              f"points registered (entries + re-entries); wall armed")
    else:
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
        the ASM does; the driver only decides when to stop watching.

        Exhausting the budget FAILS LOUD. It used to return quietly, which left
        the oracle parked mid-frame -- a truncated reference that still looks
        authoritative, so the differential blames the candidate for the
        oracle's own unfinished work. That is not theoretical: once
        install_replacements=False started actually working, the oracle lost the
        accelerator replacements (lzs_decode_loop, intro_anim_unpack, the
        blitters) and began interpreting the real instruction sequence, whose
        peak is ~17.1M steps/frame -- 4x the old 4,000,000 default. Every frame
        silently truncated, and the harness reported a frame-0 divergence that
        was entirely its own.
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
        raise RuntimeError(
            f"oracle step budget exhausted: {budget:,} steps without reaching a "
            f"boundary head twice (cs:ip={cpu.s.cs:04X}:{cpu.s.ip:04X}). The "
            f"oracle is parked mid-frame; comparing from here blames the "
            f"candidate for the oracle's truncation. Raise --step-budget.")

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
            _save_both(args_cli, demo, frame, rt_o, rt_c, "candidate-raised",
                       exc=exc)
            return 1

        vo = bytes(rt_o.cpu.mem.data[VGA:VGA + VGA_LEN])
        vc = bytes(rt_c.cpu.mem.data[VGA:VGA + VGA_LEN])
        if vo != vc:
            n = sum(1 for a, b in zip(vo, vc) if a != b)
            first = next(i for i, (a, b) in enumerate(zip(vo, vc)) if a != b)
            print(f"\n[verify] VGA DIVERGED at frame {frame}: {n} px differ; "
                  f"first at row {first // 320} col {first % 320} "
                  f"(oracle={vo[first]:02X} corpus={vc[first]:02X})")
            _save_both(args_cli, demo, frame, rt_o, rt_c, "vga-diverged",
                       pixels=n, first_row=first // 320, first_col=first % 320)
            return 1
        # THE PALETTE IS STATE TOO, and diffing the plane alone does not see it:
        # the plane holds INDICES, the DAC holds the colours those index. They
        # are different memory -- vga_palette is device state, not mem.data --
        # so a wholly wrong screen can be "pixel-identical". SkyRoads is the
        # worst case for that: its fades are pure palette animation, and during
        # one the index plane does not change AT ALL. A broken fade would have
        # passed this gate silently, all 10,941 frames of it.
        po, pc = rt_o.dos.vga_palette, rt_c.dos.vga_palette
        if po != pc:
            bad = [i for i, (a, b) in enumerate(zip(po, pc)) if a != b]
            print(f"\n[verify] PALETTE DIVERGED at frame {frame}: "
                  f"{len(bad)} of 256 DAC entries differ. The plane matched -- "
                  f"this is colour, not layout.")
            print(f"          indices: {bad}")
            for i in bad[:12]:
                print(f"            [{i:3d}] oracle={po[i]}  corpus={pc[i]}")
            _save_both(args_cli, demo, frame, rt_o, rt_c, "palette-diverged",
                       entries=bad,
                       oracle={i: po[i] for i in bad[:16]},
                       corpus={i: pc[i] for i in bad[:16]})
            return 1
        if frame % 50 == 0:
            print(f"  frame {frame:4d}: VGA + palette identical")

    print(f"\n[verify] PASS -- {frame + 1} frames: VGA plane AND DAC palette "
          f"identical\n          to the pure ASM oracle over {demo.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
