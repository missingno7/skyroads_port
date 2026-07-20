"""Native SKYROADS.EXE image — the game's own packer stub, reimplemented.

SKYROADS.EXE is EXE-packed with a CUSTOM stub (no LZ91/PKLITE signature; the
bit-LZ loop resembles LZEXE but the length/distance coding differs). The DOS
loader enters the stub at ``load:0000``; it copies the packed stream high,
decompresses the ~30 KB program image forward to ``load:0000``, applies THREE
relocations, and far-jumps to the real entry `1010:61F3`. The startup
"computed tables" (clip tables `0x4C..0xE3`, shape `0xBA7`) are materialized
by this unpack — static program data after all (corrects the earlier
"computed at startup" note; the frame-8 `1767:06B1` writer is the stub's own
`stosb`).

This module reproduces the unpack from the FILE alone. The decoder is a
REGISTER-EXACT transcription of the stub's decode loop (`1767:063A-06CE`,
disassembled from the live cold-boot capture — see run_status.md):

* getbit (`063A`): 16-bit LSB-first buffer in BP, refilled via ``lodsw``.
* main loop (`06B3`): ``1`` -> literal byte; else read a displacement low
  byte, then: ``1`` -> LONG path (`0646`: BH accumulates high displacement
  bits via ``rcl`` from 0xFF, gamma-style windowing, then a length code);
  ``01`` -> 3 more BH bits then ``dec bh``, 2-byte copy (`06A0`);
  ``00`` -> 2-byte copy with BH=0xFF, UNLESS the low byte is 0xFF = END.
* stream: packed file bytes from offset 0x62 (first ``lodsw`` inits BP).
* output: program offset 0 == physical ``load_seg << 4`` (VM: 0x1010).
* relocations: 3 word sites (`0x0B04`, `0x3ACA`, `0x61F4` for this EXE,
  decoded from the stub's table) get the load segment added.

VERIFIED byte-exact vs the VM's memory at the moment of the stub's far jump
(cold-replay capture at `1010:61F3`) — tests/test_exe_image.py.
"""
from __future__ import annotations

from pathlib import Path

#: file offset where the packed bitstream begins (the initial `lodsw`).
STREAM_START = 0x62
#: program-image word offsets that get the load segment added (the stub's
#: 3-entry table at its cs:0x2A, decoded: es=load, bx=0x0B04, then deltas
#: +0x2FC6 and +0x272A).
RELOC_SITES = (0x0B04, 0x3ACA, 0x61F4)
#: the real entry point after unpack (far jmp target, load-relative).
ENTRY_CS_REL = 0x0000     # 1010 rel 0
ENTRY_IP = 0x61F3
#: DGROUP segment relative to the load segment (VM: 0x1686 - 0x1010).
DGROUP_REL = 0x0676


class _Bits:
    """The stub's getbit (`063A`): shr bp,1 / dec dl / refill lodsw."""

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.bp = 0
        self.dl = 1                      # first bit() triggers a refill after

    def bit(self) -> int:
        b = self.bp & 1
        self.bp >>= 1
        self.dl -= 1
        if self.dl == 0:
            self.bp = self.data[self.pos] | (self.data[self.pos + 1] << 8)
            self.pos += 2
            self.dl = 16
        return b

    def byte(self) -> int:              # lodsb interleaved with the bitstream
        v = self.data[self.pos]
        self.pos += 1
        return v


def decompress(data: bytes, start: int = STREAM_START) -> bytes:
    """The stub's `06B3-06CE` loop, register-exact."""
    bits = _Bits(data, start)
    bits.bp = data[start] | (data[start + 1] << 8)   # initial lodsw
    bits.pos = start + 2
    bits.dl = 16
    out = bytearray()

    def copy(count: int, bh: int, bl: int) -> None:
        # 06AD: mov al,es:[bx+di]; stosb; loop -- bx signed 16-bit backref.
        bx = (bh << 8) | bl
        di = len(out) + 0x10                          # es:di started at 0x10
        src = (bx + di) & 0xFFFF
        for _ in range(count):
            out.append(out[src - 0x10])
            src = (src + 1) & 0xFFFF

    while True:
        if bits.bit():                                # 06B3: literal
            out.append(bits.byte())
            continue
        long_path = bits.bit()                        # 06BB
        bl = bits.byte()                              # 06BE: lodsb
        bh = 0xFF                                     # 06BF
        if long_path:                                 # 06C3 -> 0646
            bh = ((bh << 1) | bits.bit()) & 0xFF      # 0646-0649
            if not bits.bit():                        # 064B (jc 0664 skips)
                dh = 2
                for _ in range(3):                    # 0652: cl=3
                    if bits.bit():                    # 0654 (jc 0662)
                        break
                    bh = ((bh << 1) | bits.bit()) & 0xFF   # 0659-065C
                    dh = (dh << 1) & 0xFF             # 065E
                bh = (bh - dh) & 0xFF                 # 0662: sub bh,dh
            # length code (0664):
            dh = 2
            cl = 4
            count = None
            while True:                               # 0668: inc dh / getbit
                dh += 1
                if bits.bit():                        # 066A (jc 067F)
                    count = dh
                    break
                cl -= 1
                if cl == 0:
                    break                             # 066F loop exhausts
            if count is None:
                if bits.bit():                        # 0671 (jnc 0683)
                    dh += 1                           # 0676
                    if bits.bit():                    # 0678 (jnc 067F)
                        dh += 1                       # 067D
                    count = dh                        # 067F
                elif bits.bit():                      # 0683 (jc 0698)
                    count = bits.byte() + 0x11        # 0698-069B
                else:                                 # 0688
                    dh = 0
                    for _ in range(3):
                        dh = ((dh << 1) | bits.bit()) & 0xFF   # 068C-068F
                    count = dh + 9                    # 0693
            copy(count, bh, bl)
        else:
            if bits.bit():                            # 06C5/06C8 -> 06A0
                for _ in range(3):
                    bh = ((bh << 1) | bits.bit()) & 0xFF   # 06A2-06A5
                bh = (bh - 1) & 0xFF                  # 06A9: dec bh
                copy(2, bh, bl)                       # 06AB: cl=2
            elif bl != 0xFF:                          # 06CA: cmp bh,bl
                copy(2, bh, bl)                       # 06AB
            else:
                break                                 # 06CE: end of stream
    return bytes(out)


def build_program_image(path: "str | Path", load_seg: int = 0x1010) -> bytearray:
    """Unpack SKYROADS.EXE and apply its 3 relocations for ``load_seg`` — the
    program bytes exactly as the stub leaves them at ``load_seg << 4``."""
    data = Path(path).read_bytes()
    image = bytearray(decompress(data))
    for a in RELOC_SITES:
        v = image[a] | (image[a + 1] << 8)
        v = (v + load_seg) & 0xFFFF
        image[a] = v & 0xFF
        image[a + 1] = v >> 8
    return image


def initial_dgroup(path: "str | Path", load_seg: int = 0x1010,
                   size: int = 0x10000) -> bytearray:
    """The initial DGROUP image: the unpacked program's initialized data
    (DGROUP is at ``load + 0x676`` paragraphs), zero-extended over the BSS
    (which the C runtime clears at startup)."""
    image = build_program_image(path, load_seg)
    off = DGROUP_REL << 4
    dg = bytearray(size)
    chunk = image[off:off + size]
    dg[:len(chunk)] = chunk
    return dg
