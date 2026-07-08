"""Throwaway probe: log every DOS file open/read/alloc during boot.

Usage: python skyroads/probes/trace_file_loads.py [frames] [timer_irqs_per_frame]

Wraps DOSMachine.int21 to print AH=3Dh (open, with resolved filename), 3Fh
(read, with handle/requested length), 3Eh (close), and 48h (allocate, with
paragraph count) before delegating to the real handler. This is throwaway
observation, not a permanent hook — see skyroads/codecs for where the
recovered decompression logic will actually live once found.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dos_re.cpu import CF, HaltExecution  # noqa: E402
from dos_re.interrupts import deliver_interrupt  # noqa: E402
from skyroads.runtime import create_game_runtime  # noqa: E402


def main() -> None:
    n_frames = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    irqs_per_frame = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    rt = create_game_runtime(ROOT / "assets" / "SKYROADS.EXE", install_replacements=False)
    dos = rt.dos
    real_int21 = dos.int21

    def traced_int21(cpu):
        ah = (cpu.s.ax >> 8) & 0xFF
        if ah == 0x3D:
            name = dos.read_asciiz(cpu, cpu.s.ds, cpu.s.dx)
            print(f"[open]  AL={cpu.s.ax & 0xFF:02X} name={name!r}")
        elif ah == 0x3F:
            h = cpu.s.bx
            length = cpu.s.cx
            print(f"[read]  handle={h} requested={length} -> buf {cpu.s.ds:04X}:{cpu.s.dx:04X}")
        elif ah == 0x3E:
            print(f"[close] handle={cpu.s.bx}")
        elif ah == 0x48:
            print(f"[alloc] paragraphs={cpu.s.bx:04X} ({cpu.s.bx * 16} bytes)")
        elif ah == 0x42:
            print(f"[lseek] handle={cpu.s.bx} origin={cpu.s.ax & 0xFF} "
                  f"offset={(cpu.s.cx << 16) | cpu.s.dx:08X}")
        real_int21(cpu)
        if ah == 0x3D:
            print(f"        -> handle={cpu.s.ax if not cpu.get_flag(CF) else 'FAIL'}")
        elif ah == 0x3F:
            print(f"        -> got={cpu.s.ax}")

    dos.int21 = traced_int21
    rt.cpu.interrupt_handler = dos.interrupt

    for i in range(n_frames):
        for _ in range(irqs_per_frame):
            deliver_interrupt(rt, 0x08)
        try:
            rt.cpu.run(200_000)
        except HaltExecution:
            print("halted")
            break
    print(f"done: addr={rt.cpu.addr()} count={rt.cpu.instruction_count}")


if __name__ == "__main__":
    main()
