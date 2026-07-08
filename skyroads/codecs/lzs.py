"""SKYROADS .LZS/.DAT asset compression codec.

The core LZ decode loop was recovered by live-tracing the oracle (dos_re VM
running assets/SKYROADS.EXE) while it unpacked TREKDAT.LZS: profiling found the
hottest interpreted addresses at CS:IP 1010:64A0-1010:675E (see
tools/profile_hotspots.py output, ~547K-618K hits in a 6M-instruction window),
then a forced linear disassembly of that live (self-modifying-code-populated)
region plus a register-level single-step trace confirmed the algorithm below.
See docs/skyroads/lzs_format.md for the full evidence trail once written.

CONFIRMED (byte-for-byte from the oracle trace, ASM addresses noted for every
claim):

  Bit reader (1010:64AB "get_bit"): MSB-first bit reader over a byte at
  ds:[41B0], refilling from the input stream every 8 bits (counter ds:[41AE],
  cursor ds:[41B6], end ds:[41B4]); refilling past the loaded 4KB staging
  buffer triggers a file read (1010:6350 -> ... -> INT 21h AH=3Fh, confirmed by
  the file-load trace in skyroads/probes/trace_file_loads.py: TREKDAT.LZS is
  read in 4096-byte chunks into a fixed buffer at 1686:31A8, matching
  ds:[41B2]/[41B4]/[41B6]).

  get_bits(n) (1010:64FF/6508): n calls to get_bit, accumulated MSB-first.

  Header-derived widths (four raw bytes read byte-aligned via 1010:6490
  *before* the bitstream proper, at 1010:66F2-670E, poked into these opcodes'
  immediate operands as self-modifying code):
    WIDTH_LEN        <- ds:[6729] (patches "push imm16" at 1010:6728)
    WIDTH_DIST_LONG  <- ds:[671F] (patches "push imm16" at 1010:671E)
    WIDTH_DIST_SHORT <- ds:[674C] (patches "push imm16" at 1010:674B)
    (a fourth byte computes 1 << byte, stored at 1010:6751 — not observed
    consumed by the main loop in this trace window; purpose not yet confirmed)

  Main loop (1010:6712-675E), per output byte until di reaches the output end:
    b1 = get_bit()
    if b1 == 0:
        distance = get_bits(WIDTH_DIST_LONG) + 2
    else:
        b2 = get_bit()
        if b2 == 1:
            output_byte(get_bits(8))          # literal
            continue
        distance = get_bits(WIDTH_DIST_SHORT) + 3
    length = get_bits(WIDTH_LEN) + 2
    copy `length` bytes from (output_pos - distance) to output_pos, one byte
    at a time, advancing both pointers together (so overlapping/run copies
    work, standard LZ77 behaviour)

NOT YET CONFIRMED (open — do not trust until traced/verified):
  - The exact on-disk header layout before the four width bytes (the "CMAP"/
    magic + length fields seen in raw file inspection of CARS.LZS/WORLD0.LZS/
    MAINMENU.LZS have not been correlated byte-for-byte against where the
    oracle actually starts reading the four width bytes).
  - The purpose of the `1 << byte` value stored at 1010:6751.
  - Multi-record chaining for files with several compressed blocks
    (TREKDAT.LZS's repeated alloc() calls during loading, ~8-10 records).
  - The ds:[41AA]/ds:[41BB] flag checks seen in nearby helper functions
    (1010:6595, 1010:6350) — plausibly "stored/uncompressed block" and
    "last chunk" flags, not yet traced to a concrete effect.

Status: OBSERVED, partially byte-verified — a direct oracle-memory diff
(TREKDAT.LZS record 0) found and fixed a real bug (length was
get_bits(WIDTH_LEN)+1; the ASM copy loop actually does +2 — one match's worth
of movsb inside LOOP plus one more unconditional at 6740h). That fix took the
exact-byte match from 933/18072 to 8964/18072 (~50%). A residual, precisely
localized divergence remains (a short-distance match at output-relative
position 2938 reads a different raw bit value than the oracle) — logged as a
blocker in docs/skyroads/blockers.md with the full symbol-trace evidence, not
yet resolved. Do NOT treat this module's output as ground truth yet.

Cross-reference (corroboration, not a source): the independent RE project
github.com/ammaarreshi/SkyRoads-Codex published a structurally matching
description ("3 bytes: SkyRoads compression widths" per compressed block,
concrete widths (4, 10, 13) for TREKDAT.LZS / (6, 10, 12) for MUZAX.LZS) from
its own DOSBox-X + static-analysis work. This lines up with WIDTH_LEN/
WIDTH_DIST_LONG/WIDTH_DIST_SHORT above, but per docs/pitfalls.md #21 it is a
lead to verify against OUR oracle, not a fact to import — see
docs/skyroads/run_status.md (2026-07-08 entry) for the full note.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LzsWidths:
    """The four header-derived bit-widths patched into the decoder at runtime."""
    width_len: int
    width_dist_long: int
    width_dist_short: int


class _BitReader:
    """MSB-first bit reader mirroring 1010:64AB/64FF exactly (no refill-from-file
    here — the caller supplies the whole compressed payload up front)."""

    def __init__(self, data: bytes, start: int = 0) -> None:
        self._data = data
        self._pos = start
        self._byte = data[start] if start < len(data) else 0
        self._bits_left = 8
        self._pos += 1

    def get_bit(self) -> int:
        bit = (self._byte >> 7) & 1
        self._byte = (self._byte << 1) & 0xFF
        self._bits_left -= 1
        if self._bits_left == 0:
            self._bits_left = 8
            self._byte = self._data[self._pos] if self._pos < len(self._data) else 0
            self._pos += 1
        return bit

    def get_bits(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self.get_bit()
        return value


def decompress_block(payload: bytes, widths: LzsWidths, out_size: int) -> bytes:
    """Decompress one LZ block per the recovered 1010:6712-675E main loop.

    ``payload`` is the compressed bitstream (the byte immediately after the
    header-derived width bytes); ``out_size`` is the known decompressed length
    for this block (the ASM loop bounds itself the same way, via a caller-
    supplied end pointer at ss:[bp+8] — see 1010:6712 "cmp di,ss:[bp+8]").
    """
    reader = _BitReader(payload)
    out = bytearray()
    while len(out) < out_size:
        if reader.get_bit() == 0:
            distance = reader.get_bits(widths.width_dist_long) + 2
        else:
            if reader.get_bit() == 1:
                out.append(reader.get_bits(8))
                continue
            distance = reader.get_bits(widths.width_dist_short) + 3
        length = reader.get_bits(widths.width_len) + 2
        src = len(out) - distance
        for _ in range(length):
            out.append(out[src] if 0 <= src < len(out) else 0)
            src += 1
    return bytes(out[:out_size])
