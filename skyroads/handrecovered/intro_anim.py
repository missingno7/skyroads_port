"""SkyRoads intro ship/logo animation frame unpacker — `1010:3A96-3AC8`.

Recovered via lift-then-refactor (`dos_re.tools.liftverify` proved a literal
transcription byte-exact against the ASM oracle first — see
`docs/skyroads/run_status.md`). Called once per intro frame; unpacks 8
animation-data segments (a fixed table at `ss:[bx+0xE76]`, `bx = 0, 2, ..,
14`), each holding **1040 fixed rows** of a small RLE-ish token stream,
expanded in place (`ds == es == that segment` throughout each segment's
unpack).

Consecutive table segments are less than 64K apart in real (paragraph)
memory, so they **overlap in physical memory** — this module therefore
operates on live memory through `rb`/`wb` callbacks (like
`skyroads/handrecovered/relocate.py`), never an isolated per-segment copy, so
that pass N's writes are visible to pass N+1 exactly as they are on real
hardware (whether or not the game *relies* on that isn't settled — it's
faithfully reproduced regardless).

## Per-segment layout and unpack

1. The segment's own byte 0 (a word) is a **self-referential offset**: the
   segment relocates its own first 624 bytes (312 words) from that offset to
   its own start (`1010:3AAC rep movsw`) — a one-time header shift.
2. Then, 1040 times: copy a fixed 3-byte row prefix verbatim — one `movsb`
   then one `movsw`, as two *separate* instructions, not atomic with each
   other (`1010:3AAE-3AAF`) — then expand **token pairs**: read 2 source
   bytes, write `[b1, b2, 0x00]`, until a token's first byte is `0xFF`
   (written through as-is, ending that row's stream; `1010:3AB0-3ABA`).

The output is a **wider** byte stream than the input (each 2-byte token
becomes 3 output bytes), consistent with unpacking a compact stored format
into whatever wider per-pixel/per-cell layout the renderer expects.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from skyroads.islands import oracle_link

#: Number of rows per segment (1010:3AA2 `mov dx,0x0410`).
ROWS_PER_SEGMENT = 0x0410
#: Bytes of the self-referential header block relocated at segment start
#: (1010:3AA9 `mov cx,0x138` words = 624 bytes).
HEADER_BYTES = 0x138 * 2
#: A token's first byte at this value ends the row (copied through as-is,
#: not expanded; 1010:3AB2 `cmp al,0xFF`).
ROW_TERMINATOR = 0xFF


class UnpackResult(NamedTuple):
    cursor_si: int   # final read offset within the segment (register SI)
    cursor_di: int   # final write offset within the segment (register DI)


@oracle_link(
    boundary="1010:3A96",
    contract="unpack_animation_segment(rb, wb) -> UnpackResult: unpacks one "
             "animation-data segment in place through the given byte "
             "read/write callbacks (offsets 0-65535 within the segment). "
             "Relocate the first 624 bytes from the self-referential offset "
             "at [0:2] to [0:624]; then for ROWS_PER_SEGMENT rows, copy a "
             "3-byte prefix (movsb then movsw, NOT atomic with each other), "
             "then expand 2-byte tokens into [b1,b2,0x00] triplets until a "
             "token's b1==0xFF (copied through, ending the row). Returns the "
             "final (si, di) cursor offsets.",
    status="VERIFIED",  # the mechanical lift of 1010:3A96 (its call site, one
    # full 8-segment invocation) matched the ASM oracle byte-exact -- every
    # register, flag and the whole memory image -- against real E2E-replay
    # gameplay (dos_re.tools.liftverify + a hand-driven strict-verifier run;
    # see run_status.md). This refactor was derived from that proven
    # transcription, not from reading the disassembly directly. Row-by-row
    # (si,di) cross-checked against the real ASM for all 1040 rows of a real
    # segment, byte-exact.
    merge_target="skyroads.native.intro_anim (future)",
)
def unpack_animation_segment(
    rb: Callable[[int], int], wb: Callable[[int, int], None],
) -> UnpackResult:
    """Unpack one animation-data segment in place (1010:3A96 inner body, one
    pass of the outer bx-indexed loop)."""
    si = rb(0) | (rb(1) << 8)

    # 1010:3AAC `rep movsw`: forward word-by-word copy, source (si) ahead of
    # dest (0) throughout -- safe without reversing, same as real hardware.
    for i in range(0, HEADER_BYTES, 2):
        lo, hi = rb((si + i) & 0xFFFF), rb((si + i + 1) & 0xFFFF)
        wb(i, lo)
        wb(i + 1, hi)
    si = (si + HEADER_BYTES) & 0xFFFF
    di = HEADER_BYTES

    for _ in range(ROWS_PER_SEGMENT):
        # 1010:3AAE `movsb` then 3AAF `movsw` -- two separate instructions:
        # movsb's write must land BEFORE movsw's (word) read, since they can
        # overlap once di catches up to si (di grows faster than si whenever a
        # row has tokens). Each instruction's own read is atomic across its
        # width, but the two instructions are NOT atomic with each other.
        wb(di, rb(si))                          # movsb (write before movsw reads)
        si = (si + 1) & 0xFFFF
        di = (di + 1) & 0xFFFF
        lo, hi = rb(si), rb((si + 1) & 0xFFFF)  # movsw: atomic 2-byte read
        si = (si + 2) & 0xFFFF
        wb(di, lo)                               # movsw: atomic 2-byte write
        wb((di + 1) & 0xFFFF, hi)
        di = (di + 2) & 0xFFFF
        while True:
            b1 = rb(si)
            si = (si + 1) & 0xFFFF
            wb(di, b1)
            di = (di + 1) & 0xFFFF
            if b1 == ROW_TERMINATOR:
                break
            b2 = rb(si)
            si = (si + 1) & 0xFFFF
            wb(di, b2)
            di = (di + 1) & 0xFFFF
            wb(di, 0)
            di = (di + 1) & 0xFFFF

    return UnpackResult(si, di)
