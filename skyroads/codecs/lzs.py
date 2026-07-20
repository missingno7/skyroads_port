"""SKYROADS .LZS/.DAT asset compression codec.

The core LZ decode loop was recovered by live-tracing the oracle (dos_re VM
running assets/SKYROADS.EXE) while it unpacked TREKDAT.LZS. A live
self-modifying-code-aware disassembly plus a register-level trace confirmed the
algorithm below. See docs/history/skyroads/run_status.md for the historical
evidence trail.

CONFIRMED (byte-for-byte from the oracle trace, ASM addresses noted for every
claim):

  Bit reader (1010:64AB "get_bit"): MSB-first bit reader over a byte at
  ds:[41B0], refilling from the input stream every 8 bits (counter ds:[41AE],
  cursor ds:[41B6], end ds:[41B4]); refilling past the loaded 4KB staging
  buffer triggers a file read (1010:6350 -> ... -> INT 21h AH=3Fh). The
  retained historical trace confirms that TREKDAT.LZS is read in 4096-byte
  chunks into a fixed buffer at 1686:31A8, matching
  ds:[41B2]/[41B4]/[41B6].

  get_bits(n) (1010:64FF/6508): n calls to get_bit, accumulated MSB-first.

  Header-derived widths (four raw bytes read byte-aligned via 1010:6490
  *before* the bitstream proper, at 1010:66F2-670E, poked into these opcodes'
  immediate operands as self-modifying code):
    WIDTH_LEN        <- ds:[6729] (patches "push imm16" at 1010:6728)
    WIDTH_DIST_LONG  <- ds:[671F] (patches "push imm16" at 1010:671E)
    WIDTH_DIST_SHORT <- ds:[674C] (patches "push imm16" at 1010:674B)
    (a fourth header byte's purpose is still unconfirmed)

  Main loop (1010:6712-675E), per output byte until di reaches the output end:
    b1 = get_bit()
    if b1 == 0:
        distance = get_bits(WIDTH_DIST_LONG) + 2
    else:
        b2 = get_bit()
        if b2 == 1:
            output_byte(get_bits(8))          # literal
            continue
        distance = get_bits(WIDTH_DIST_SHORT) + (1 << WIDTH_DIST_LONG) + 2
    length = get_bits(WIDTH_LEN) + 2
    copy `length` bytes from (output_pos - distance) to output_pos, one byte
    at a time, advancing both pointers together (so overlapping/run copies
    work, standard LZ77 behaviour)

  The short-distance base term (1010:6750 "ADD AX,imm16", operand patched at
  1010:6751/6752) is `1 << WIDTH_DIST_LONG`, NOT the fixed 0x400 first
  assumed: TREKDAT.LZS and MUZAX.LZS both happen to use WIDTH_DIST_LONG=10
  (giving 0x400 either way, which is why testing only those two files never
  caught this), but INTRO.LZS uses WIDTH_DIST_LONG=9 and its patched operand
  reads 0x0200 = 1<<9 live from oracle memory — direct proof this is
  computed per-file, not a compiled-in constant. (An earlier write-watch on
  1010:6751/6752 across TREKDAT.LZS's own header-parse window found zero
  writes and was wrongly read as "never patched, for any file" — it only
  showed that TREKDAT's own patch, if any, happens outside that specific
  window, or that TREKDAT's default matches its own target value.) The
  short branch (1010:674B, 13-bit width) jumps into the long branch's shared
  tail at 1010:6723 after adding this base, so both branches share one
  "distance -> source pointer" computation; the common "+2" is applied
  there, not per-branch — hence long-distance is `+2` and short-distance is
  `+(1<<WIDTH_DIST_LONG)+2`, not the previously-assumed `+3` or fixed
  `+0x400`.

NOT YET CONFIRMED (open — do not trust until traced/verified):
  - The exact on-disk header layout before the four width bytes (the "CMAP"/
    magic + length fields seen in raw file inspection of CARS.LZS/WORLD0.LZS/
    MAINMENU.LZS have not been correlated byte-for-byte against where the
    oracle actually starts reading the four width bytes).
  - The purpose of the fourth header byte (confirmed NOT to encode the
    short-distance base directly, since that's computed as 1<<WIDTH_DIST_LONG
    — still unidentified).
  - Multi-record chaining for files with several compressed blocks
    (TREKDAT.LZS's repeated alloc() calls during loading, ~8-10 records).
  - The ds:[41AA]/ds:[41BB] flag checks seen in nearby helper functions
    (1010:6595, 1010:6350) — plausibly "stored/uncompressed block" and
    "last chunk" flags, not yet traced to a concrete effect.

Status: VERIFIED across multiple files and records — full-record oracle-
memory dumps (anchored to the real INT21h AH=3Dh file-open event, not a
step-count guess) diffed byte-for-byte against this module's output:
TREKDAT.LZS records 0/1 and all 9 of its records via the differential hook
verifier (skyroads/hooks.py's lzs_decode_loop_hook), MUZAX.LZS, and
INTRO.LZS — 100% exact match once the short-distance formula above was
fixed. Three real bugs were found and fixed to get here: (1) match length
was get_bits(WIDTH_LEN)+1; the ASM copy loop actually does +2 (one match's
worth of movsb inside LOOP plus one more unconditional at 6740h);
(2) short-distance was assumed get_bits(WIDTH_DIST_SHORT)+3 by analogy with
the long branch; (3) the base was then assumed a fixed 0x400 (matched
TREKDAT/MUZAX by coincidence, both WIDTH_DIST_LONG=10) before INTRO.LZS's
WIDTH_DIST_LONG=9 proved it's actually 1<<WIDTH_DIST_LONG.

Cross-reference (corroboration, not a source): the independent RE project
github.com/ammaarreshi/SkyRoads-Codex published a structurally matching
description ("3 bytes: SkyRoads compression widths" per compressed block,
concrete widths (4, 10, 13) for TREKDAT.LZS / (6, 10, 12) for MUZAX.LZS) from
its own DOSBox-X + static-analysis work. This lines up with WIDTH_LEN/
WIDTH_DIST_LONG/WIDTH_DIST_SHORT above, but per
docs/history/pitfalls.md #21 it is a
lead to verify against OUR oracle, not a fact to import — see
docs/history/skyroads/run_status.md (2026-07-08 entry) for the full note.
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
            distance = reader.get_bits(widths.width_dist_short) + (1 << widths.width_dist_long) + 2
        length = reader.get_bits(widths.width_len) + 2
        src = len(out) - distance
        for _ in range(length):
            out.append(out[src] if 0 <= src < len(out) else 0)
            src += 1
    return bytes(out[:out_size])
