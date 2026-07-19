"""Profile a recorded SkyRoads input demo through real gameplay.

Unlike tools/profile_hotspots.py (raw interpreter, no game hooks, no input),
this replays the actual recorded keypresses through skyroads.runtime's game
adapter -- so it profiles with the LZS decode-loop and palette-fade hooks
installed (showing what's STILL hot after those), driving the real 3D
road-rendering / gameplay code the demo exercises, not just idle/menu loops.

Usage:
    python tools/profile_demo.py artifacts/demos/<name> [--top N] [--frames N]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dos_re"))

from skyroads.replay import SkyroadsReplayPlayback
from dos_re.interrupts import deliver_interrupt, deliver_scancode
from dos_re.dos import HaltExecution, ConsoleInputWouldBlock
from dos_re.cpu import UnsupportedInstruction

from dos_re import player
from scripts.play import SkyroadsFrontend


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("demo_dir", help="path to a recorded demo directory")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--frames", type=int, default=0, help="stop after N frames (0 = full demo)")
    args = p.parse_args(argv)

    playback = SkyroadsReplayPlayback.load(args.demo_dir)
    meta = playback.manifest.get("metadata", {})
    steps_per_frame = meta.get("steps_per_frame", 30_000)
    timer_irqs_per_frame = meta.get("timer_irqs_per_frame", 6)
    frontend = SkyroadsFrontend(ROOT)
    run_args = player.build_arg_parser(frontend).parse_args(
        ["--play-demo", str(args.demo_dir), "--headless"])
    frontend.apply_demo_metadata(run_args, meta)
    rt = frontend.load_demo_runtime(run_args, playback)
    frontend.apply_hook_mode(rt, run_args)
    rt.dos.console_input_fallback = None  # real DOS reads should block, not synthesize Esc
    cpu = rt.cpu
    cpu.trace_enabled = False

    # Wrap every registered hook (LZS decode loop, palette fade) with a
    # timing/counting shim, same technique as tools/profile_hotspots.py.
    hook_calls: Counter = Counter()
    hook_time: dict = {}
    perf = time.perf_counter

    def wrap(addr, fn):
        def shim(c):
            t0 = perf()
            fn(c)
            hook_time[addr] = hook_time.get(addr, 0.0) + (perf() - t0)
            hook_calls[addr] += 1
        return shim

    for addr, fn in list(cpu.replacement_hooks.items()):
        cpu.replacement_hooks[addr] = wrap(addr, fn)
    hook_set = set(cpu.replacement_hooks)

    addr_counts: Counter = Counter()
    backward_edges: Counter = Counter()
    cur = cpu.addr
    step = cpu.step
    previous_addr = None

    end_boundary = playback.end_boundary or 10**9
    if args.frames:
        end_boundary = min(end_boundary, args.frames)

    t_start = perf()
    executed = 0
    frame = 0
    status = "complete"
    blocked_frames = 0
    try:
        while not playback.finished(frame) and frame < end_boundary:
            playback.apply_to_runtime(frame, rt, deliver=deliver_scancode)
            for _ in range(timer_irqs_per_frame):
                deliver_interrupt(rt, 0x08)
            target = cpu.instruction_count + steps_per_frame
            try:
                while cpu.instruction_count < target:
                    a = cur()
                    addr_counts[a] += 1
                    step()
                    b = cur()
                    if b[0] == a[0] and b[1] <= a[1]:
                        backward_edges[(a, b)] += 1
                    previous_addr = a
                    executed += 1
            except ConsoleInputWouldBlock:
                # Matches dos_re.player._step_frame: a frame that finds no
                # queued key at a blocking read is not fatal, just idle --
                # the recorded demo's own timing supplies the next keypress
                # at its recorded boundary. Continue to the next frame.
                blocked_frames += 1
            frame += 1
    except (HaltExecution, UnsupportedInstruction) as e:
        status = f"{type(e).__name__}: {e} at frame {frame}"
    except Exception as exc:  # keep partial results useful
        cs, ip = cpu.addr()
        status = f"exception @ {cs:04X}:{ip:04X} {type(exc).__name__}: {exc}"

    wall = perf() - t_start
    if wall <= 0:
        wall = 1e-9

    total_hook_time = sum(hook_time.values())
    interp_time = max(0.0, wall - total_hook_time)

    print("=" * 64)
    print(f"demo profile  dir={args.demo_dir}  frames={frame}/{playback.end_boundary}  "
          f"blocked_frames={blocked_frames}  status={status}")
    print(f"steps={executed:,}  wall={wall:.2f}s   {executed / wall:,.0f} interpreted-steps/sec")
    cs, ip = cpu.addr()
    print(f"final CS:IP = {cs:04X}:{ip:04X}")
    print("-" * 64)
    print("Time breakdown (wall-clock):")
    print(f"  interpreter (interpreted ASM) : {interp_time:7.2f}s  {100*interp_time/wall:5.1f}%")
    print(f"  replacement hooks (all)       : {total_hook_time:7.2f}s  {100*total_hook_time/wall:5.1f}%")
    print("-" * 64)

    print(f"Top {args.top} executed CS:IP (interpreted-instruction frequency):")
    for (hcs, hip), count in addr_counts.most_common(args.top):
        name = cpu.hook_names.get((hcs, hip), "")
        tag = f"  <hook {name}>" if (hcs, hip) in hook_set else ""
        print(f"  {hcs:04X}:{hip:04X}  {count:9,}{tag}")
    print("-" * 64)

    print("Replacement hooks by cumulative time:")
    ranked = sorted(hook_time.items(), key=lambda kv: kv[1], reverse=True)
    for addr, t in ranked[: args.top]:
        name = cpu.hook_names.get(addr, "")
        calls = hook_calls[addr]
        per = (t / calls * 1e6) if calls else 0.0
        print(f"  {addr[0]:04X}:{addr[1]:04X}  {t:7.3f}s  calls={calls:>8,}  {per:8.1f}us/call  {name}")
    print("-" * 64)

    print("Top interpreted backward edges / tight loops:")
    for (src, dst), count in backward_edges.most_common(args.top):
        print(f"  {src[0]:04X}:{src[1]:04X} -> {dst[0]:04X}:{dst[1]:04X}  {count:9,}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
