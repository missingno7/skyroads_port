"""Materialize SkyRoads' EXE-derived ``BuildImageBootstrapProvider``.

This is one bootstrap implementation for generated, EXE-detached compositions.
It captures the deterministic post-unpack machine state during development so
the packaged runtime can initialize without opening ``SKYROADS.EXE``:

1. boot the interpreted runtime on the real EXE and run its packer stub to the
   CANONICAL POST-DECOMPRESSION ENTRY -- ``1010:61F3``, the far jump the stub
   makes once it has decompressed the ~30 KB program image and applied its three
   relocations (see ``skyroads/native/exe_image.py``, which reproduces that
   unpack from the file alone and is verified byte-exact at this exact moment);
2. ``write_snapshot`` the whole 1 MB machine there;
3. normalize it through :func:`dos_re.bootimage.poison_snapshot_to_boot_image`,
   scrubbing runtime EXE paths and recording source/format provenance.

Code-byte poisoning is enabled by default as additional destructive evidence:
it proves this image cannot execute those original instructions. It is not the
release or detachment authority. The execution plan, Atlas coverage, dependency
closure, import audit, and exported payload decide whether a build is detached.
``--no-poison`` is useful when the bytes are still needed as data or for
diagnosis; its manifest records that choice explicitly.

Usage:
    python scripts/build_boot_image.py                    # -> artifacts/boot_image/
    python scripts/build_boot_image.py --no-poison        # keep code (debug only)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dos_re"))
sys.path.insert(0, str(ROOT))

from dos_re.bootimage import poison_snapshot_to_boot_image, sha256_file  # noqa: E402
from dos_re.snapshot import write_snapshot  # noqa: E402
from skyroads.runtime import create_game_runtime  # noqa: E402

#: The game's real entry: the packer stub's far jump, taken once the ~30 KB
#: program image is decompressed and relocated (exe_image.py). Everything before
#: it is the stub; everything after is the game. This provider captures that
#: state because the selected generated compositions begin there.
CANONICAL_CS = 0x1010
CANONICAL_IP = 0x61F3
CODE_SEG = 0x1010

#: Instruction bytes the game reads as DATA would be destroyed by the poison, so
#: they are declared here and preserved. None known for SkyRoads: it has no
#: self-checksum, and the unpack's "computed tables" (clip 0x4C..0xE3, shape
#: 0xBA7) live in DGROUP, which the poison never touches. The audit re-derives
#: this independently -- if the clean-room replay ever diverges, a missing range
#: here is the first suspect.
KEEP_CODE_AS_DATA: list[tuple[int, int]] = []


def run_to_canonical_entry(rt, *, max_steps: int = 60_000_000) -> int:
    """Step the interpreted runtime until the stub hands off to the game."""
    cpu = rt.cpu
    steps = 0
    while steps < max_steps:
        if (cpu.s.cs & 0xFFFF, cpu.s.ip & 0xFFFF) == (CANONICAL_CS, CANONICAL_IP):
            return steps
        cpu.step()
        steps += 1
    raise SystemExit(
        f"never reached the canonical entry {CANONICAL_CS:04X}:{CANONICAL_IP:04X} "
        f"in {max_steps} steps -- the stub or the entry moved")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", default=str(ROOT / "assets" / "SKYROADS.EXE"))
    ap.add_argument("--game-root", default=str(ROOT / "assets"))
    ap.add_argument(
        "--ir", default=str(ROOT / "recovery" / "recovery_ir.json"),
        help="retained Recovery IR used to identify optional poison ranges",
    )
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "boot_image"))
    ap.add_argument("--no-poison", action="store_true",
                    help="retain original instruction bytes; detachment remains "
                         "a planner/export property and the manifest records this")
    args = ap.parse_args(argv)

    ir_path = Path(args.ir)
    if not ir_path.exists():
        raise SystemExit(
            f"retained Recovery IR is missing: {ir_path}\n"
            "run: python scripts/build_atlas.py --snapshot artifacts/SNAPSHOT_DIR")

    print(f"[boot] booting the interpreted runtime on {Path(args.exe).name}")
    rt = create_game_runtime(args.exe, game_root=args.game_root)
    steps = run_to_canonical_entry(rt)
    print(f"[boot] reached the canonical entry {CANONICAL_CS:04X}:{CANONICAL_IP:04X} "
          f"after {steps:,} stub steps (decompression + 3 relocations done)")

    out = Path(args.out)
    write_snapshot(rt, out, status="boot_image_canonical_entry", steps=steps)
    print(f"[boot] wrote the snapshot -> {out}")

    manifest = poison_snapshot_to_boot_image(
        out, ir_path,
        source_exe=args.exe,
        code_seg=CODE_SEG,
        canonical_entry={
            "cs": CANONICAL_CS, "ip": CANONICAL_IP,
            "ss": rt.cpu.s.ss, "sp": rt.cpu.s.sp,
            "loader_steps": steps,
            "note": "the packer stub's far jump: image decompressed + relocated, "
                    "game not yet started (skyroads/native/exe_image.py)",
        },
        keep_code_as_data=KEEP_CODE_AS_DATA,
        poison=not args.no_poison,
    )
    p = manifest["poison"]
    print(f"[boot] {'POISONED' if p['enabled'] else 'NOT poisoned (--no-poison)'}: "
          f"{p['poisoned_bytes']:,} bytes zeroed over {p['poisoned_runs']} runs "
          f"({p['censused_functions']} censused functions, {p['instruction_ranges']} "
          f"instruction ranges)")
    print(f"[boot] recovered code bytes still present: "
          f"{p['code_bytes_present_before']:,} -> {p['code_bytes_present_after']:,}")
    print(f"[boot] source EXE sha256={sha256_file(args.exe)[:16]}... recorded as provenance")
    print(f"[boot] boot image ready -> {out}")
    if args.no_poison:
        print(
            "[boot] NOTE: original instruction bytes retained; this image supplies "
            "no destructive no-fallback evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
