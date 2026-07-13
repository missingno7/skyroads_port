"""Native cold-boot DGROUP builder — the snapshot-free baseline (milestone 2).

Reproduces, from the game files alone, the DGROUP state the real game reaches
by menu time, per the VM-traced boot manifest (run_status.md 2026-07-13):

1. the unpacked EXE's initialized data + zero BSS (`exe_image.initial_dgroup`),
2. `skyroads.cfg` -> `[4516]` (66 B),
3. the gauge cell tables: `oxy_disp.dat` -> `[95F8]` (20 B),
   `ful_disp.dat` -> `[5480]` (20 B), `speed.dat` -> `[4572]` (68 B),
4. `demo.rec` -> `[961E]` (6,398 B, the attract-mode input recording).

The remaining VM-vs-native DGROUP differences at menu time are runtime state
(counters, live cursors, the `31A8` LZS staging scratch) and the allocator's
segment-pointer variables — catalogued in tests/test_boot.py as they get
pinned down.
"""
from __future__ import annotations

import struct
from pathlib import Path

from skyroads.native.exe_image import initial_dgroup
from skyroads.native.level_load import read_game_file

CFG_OFF = 0x4516
CFG_LEN = 66
OXY_CELLS_OFF = 0x95F8
FUL_CELLS_OFF = 0x5480
SPEED_CELLS_OFF = 0x4572
DEMO_REC_OFF = 0x961E


#: The game's boot-time allocator layout — deterministic (same allocation
#: sequence every boot; verified against the menu-time cold capture). Values
#: are SEGMENTS. The DGROUP pointer variables that hold them are set below.
SEG_DISPLAY_LISTS = (0x2B12, 0x311B, 0x3766, 0x3DD4, 0x4459,
                     0x4B02, 0x518C, 0x57FE)     # [0E76..] rotation buffers
SEG_SFX_BANK = 0x233B         # [4560]; also intro.snd's buffer
#: FIXED 2026-07-13 (were swapped): verified against the menu-time cold
#: capture by content match, not just by name -- 0x221A holds OXY_DISP.DAT's
#: bytes byte-exact (375/375), 0x2232 holds FUL_DISP.DAT's (387/387).
SEG_OXY_BANK = 0x221A         # OXY_DISP.DAT stencils; [5476]
SEG_FUL_BANK = 0x2232         # FUL_DISP.DAT stencils; [9610]
SEG_SPEED_BANK = 0x224B       # SPEED.DAT stencils; [54A6]
SEG_CARS_BANK = 0x5E61        # cars.lzs 55,440 B; [AF36]
SEG_DASHBRD = 0x6BEA          # dashbrd.lzs 22,720 B
SEG_SCREEN_BANK = 0x7176      # gomenu screen / world background; [4512]
SEG_AUX_8116 = 0x8116         # gomenu's 30-B record / offscreen compose
LOAD_SEG = 0x1010
DGROUP_SEG = 0x1686

#: DGROUP pointer variables the boot code sets at runtime (offset -> value).
BOOT_POINTERS = {
    0x0CB6: 0x0220,           # Sound Blaster base (detection result)
    0x4512: SEG_SCREEN_BANK,  # present source A
    0x4514: 0x8118,           # present source B
    0x4560: SEG_SFX_BANK,
    0x54A6: SEG_SPEED_BANK,   # speed-dial widget bank
    0x5476: SEG_OXY_BANK,     # oxygen-bar widget bank
    0x9610: SEG_FUL_BANK,     # fuel-bar widget bank
    0x961C: 0xA000,           # present destination
    0xAF36: SEG_CARS_BANK,    # sprite/tile bitmap bank (hi)
}

#: Level-start segment init (values observed at gameplay time, snap92):
#: what the level-start setup assigns before the first gameplay frame.
GAMEPLAY_POINTERS = {
    0x5478: SEG_AUX_8116,     # off-screen compose buffer ([0E36] source)
    0x5170: SEG_SCREEN_BANK,  # background bank (world block C target)
    0xAF2A: 0x19A1,           # HUD compose window (aliases DGROUP top)
    0x4514: 0x19A1,           # present source B during gameplay
    0x4512: 0x0000,
    0x54A4: 0x0000,
    0xAF34: 0x0000,
}


def apply_gameplay_segment_init(dg: bytearray) -> None:
    """The level-start pointer assignments (see GAMEPLAY_POINTERS)."""
    for off, val in GAMEPLAY_POINTERS.items():
        struct.pack_into("<H", dg, off, val)


def native_boot_dgroup(game_root: "str | Path") -> bytearray:
    """Build the menu-time DGROUP image from files alone (steps 1-4)."""
    root = Path(game_root)
    dg = initial_dgroup(root / "SKYROADS.EXE")

    cfg = read_game_file(root, "skyroads.cfg")
    dg[CFG_OFF:CFG_OFF + CFG_LEN] = cfg[:CFG_LEN].ljust(CFG_LEN, b"\0")

    # The DAT loads: first read -> the DGROUP cell table (observed lengths:
    # oxy/ful 20 B, speed 68 B), second read -> the stencil bank segment.
    oxy = read_game_file(root, "OXY_DISP.DAT")
    ful = read_game_file(root, "FUL_DISP.DAT")
    spd = read_game_file(root, "SPEED.DAT")
    dg[OXY_CELLS_OFF:OXY_CELLS_OFF + 20] = oxy[:20]
    dg[FUL_CELLS_OFF:FUL_CELLS_OFF + 20] = ful[:20]
    dg[SPEED_CELLS_OFF:SPEED_CELLS_OFF + 68] = spd[:68]

    rec = read_game_file(root, "DEMO.REC")
    dg[DEMO_REC_OFF:DEMO_REC_OFF + len(rec)] = rec

    for off, seg in zip(range(0x0E76, 0x0E86, 2), SEG_DISPLAY_LISTS):
        struct.pack_into("<H", dg, off, seg)
    for off, val in BOOT_POINTERS.items():
        struct.pack_into("<H", dg, off, val)
    return dg


#: DASHBRD.LZS's PICT `dest` field: an ABSOLUTE VGA byte offset (0xA140 =
#: row 129 of the 320x200 plane) -- not a segment-relative offset. The
#: dashboard is 71 rows x 320 = 22,720 bytes, reaching exactly the end of
#: the VGA plane (129 + 71 == 200).
DASHBOARD_VGA_OFFSET = 0xA140
DASHBOARD_LEN = 22720


def paint_dashboard(img_data: bytearray, dashboard_seg: int) -> None:
    """Overlay the cockpit dashboard onto a live 1 MB image's VGA plane
    (`img_data`, e.g. `NativeGameImage.data`), NONZERO PIXELS ONLY -- zero
    is transparent, same convention as every other biased asset bank in
    this module. Call this AFTER the per-frame road/background render: the
    gameplay renderer's 138-row output (rows 0..137) overlaps the
    dashboard's own top ~9 rows (129..137, its bezel), and only a masked
    overlay reproduces the real windshield cutout -- painting dashboard
    first would have it immediately overwritten; painting unmasked would
    blank the visible road out from under the bezel."""
    src_base = dashboard_seg << 4
    dst_base = 0xA0000 + DASHBOARD_VGA_OFFSET
    for i in range(DASHBOARD_LEN):
        p = img_data[src_base + i]
        if p:
            img_data[dst_base + i] = p


#: The gameplay DAC layout — each asset container's CMAP slots in
#: sequentially, and its PICT pixels are stored palette-relative, biased by
#: the window base when banked (nonzero pixels only; 0 = transparent).
#: VERIFIED byte-exact vs the menu-time cold capture: cars 55,440/55,440
#: with +72, dashbrd 22,720/22,720 with +92; world's +142 was verified
#: earlier (its background has no zero pixels).
DAC_ROADS_BASE = 0        # 72 colours from ROADS.LZS[level]
DAC_CARS_BASE = 72        # 20 colours from CARS.LZS
DAC_DASHBRD_BASE = 92     # 50 colours from DASHBRD.LZS
DAC_WORLD_BASE = 142      # 114 colours from WORLD<n>.LZS


def bias_pixels(pix: bytes, base: int) -> bytes:
    """Shift nonzero palette-relative pixels into their DAC window."""
    return bytes((p + base) & 0xFF if p else 0 for p in pix)


def native_boot_dac(game_root: "str | Path") -> list:
    """The level-independent part of the gameplay palette: CARS' 20 colours
    at 72.. and DASHBRD's 50 at 92.. (6-bit VGA, expanded). ROADS' 72 and
    the WORLD 114 are per-level (see world_load / level_load)."""
    from skyroads.native.world_load import expand6
    root = Path(game_root)
    dac = [(0, 0, 0)] * 256
    for name, base in (("CARS.LZS", DAC_CARS_BASE),
                       ("DASHBRD.LZS", DAC_DASHBRD_BASE)):
        cmap, _, _, _, _, _ = parse_lzs_container(read_game_file(root, name))
        for i in range(len(cmap) // 3):
            dac[base + i] = tuple(expand6(cmap[3 * i + k]) for k in range(3))
    return dac


def parse_lzs_container(data: bytes):
    """The graphics container layout (decoded 2026-07-13, generalizing the
    WORLD finding): ``"CMAP" + u8 colour-count + colours``, an aux table
    (loaded to `[AF3C]`), then ``"PICT" + u16 dest_off + u16 h + u16 w +
    3 LZS width bytes + stream``. Returns (cmap, aux, pict_at, dest, h, w).
    Verified: WORLD4 background (2,11,13)@138x320; CARS 2310x24=55,440;
    DASHBRD dest=0xA140 71x320=22,720; GOMENU 200x320=64,000."""
    if data[:4] != b"CMAP":
        raise ValueError("container does not start with CMAP")
    n = data[4]
    cmap = data[5:5 + 3 * n]
    at = data.find(b"PICT", 5 + 3 * n)
    if at < 0:
        raise ValueError("no PICT record")
    aux = data[5 + 3 * n:at]
    dest, h, w = struct.unpack_from("<3H", data, at + 4)
    return cmap, aux, at, dest, h, w


def load_pict(data: bytes, pict_at: int):
    """Decompress the PICT record at ``pict_at``: (dest_off, pixels)."""
    from skyroads.codecs.lzs import LzsWidths, decompress_block
    dest, h, w = struct.unpack_from("<3H", data, pict_at + 4)
    p = pict_at + 10
    widths = LzsWidths(data[p], data[p + 1], data[p + 2])
    return dest, decompress_block(data[p + 3:], widths, h * w)


def _load_graphic_bank(dg: bytearray, data: bytes, cmap_dest: int,
                       aux_dest: int = 0xAF3C) -> "tuple[int, bytes]":
    """Replay the generic graphic loader for one container: CMAP colours ->
    DGROUP ``cmap_dest``, aux table -> ``aux_dest``, first PICT decompressed.
    Returns (pict_dest_off, pixels)."""
    cmap, aux, at, dest, h, w = parse_lzs_container(data)
    if cmap_dest is not None:
        dg[cmap_dest:cmap_dest + len(cmap)] = cmap
    if aux_dest is not None and aux:
        dg[aux_dest:aux_dest + len(aux)] = aux
    return load_pict(data, at)


def native_boot_image(game_root: "str | Path") -> bytearray:
    """The full snapshot-free 1 MB boot image: program at ``LOAD_SEG``,
    DGROUP, and every asset bank at its (deterministic) segment. Enough for
    gameplay; the menu/intro screens have their own loads on top."""
    from skyroads.native.exe_image import build_program_image
    root = Path(game_root)
    img = bytearray(0x100000)
    prog = build_program_image(root / "SKYROADS.EXE", LOAD_SEG)
    img[LOAD_SEG << 4:(LOAD_SEG << 4) + len(prog)] = prog
    dg = native_boot_dgroup(root)

    # cars.lzs -> the sprite/tile bitmap bank (2310x24; CMAP 20 colours ->
    # [429A], aux -> [AF3C]); pixels biased into the CARS DAC window. Mutates
    # `dg` (the CMAP/aux writes) -- must run BEFORE `dg` goes into `img`.
    _, cars_pix = _load_graphic_bank(dg, read_game_file(root, "CARS.LZS"), 0x429A)
    # dashbrd.lzs -> the cockpit art (71x320, dest 0xA140 = screen row 129;
    # CMAP 50 colours -> [42D6]); biased into the DASHBRD DAC window.
    _, dashbrd_pix = _load_graphic_bank(dg, read_game_file(root, "DASHBRD.LZS"), 0x42D6)

    # DGROUP goes into the image FIRST (now that `dg` is fully built): several
    # banks allocated just above it (OXY/FUL/SPEED/SFX -- all within ~0x1000
    # paragraphs of DGROUP_SEG) physically overlap its 64 KB window, exactly
    # like the real allocator's layout. Writing DGROUP before those `place()`
    # calls lets their content correctly overlay on top (matching real
    # memory); the old order wrote DGROUP last and silently zeroed all four
    # banks -- latent until the HUD widget system started actually reading
    # them (see run_status.md).
    img[DGROUP_SEG << 4:(DGROUP_SEG << 4) + 0x10000] = dg

    def place(seg: int, data: bytes) -> None:
        img[seg << 4:(seg << 4) + len(data)] = data

    # DAT stencil banks: the bytes after the DGROUP cell table.
    place(SEG_OXY_BANK, read_game_file(root, "OXY_DISP.DAT")[20:])
    place(SEG_FUL_BANK, read_game_file(root, "FUL_DISP.DAT")[20:])
    place(SEG_SPEED_BANK, read_game_file(root, "SPEED.DAT")[68:])
    place(SEG_SFX_BANK, read_game_file(root, "SFX.SND"))
    place(SEG_CARS_BANK, bias_pixels(cars_pix, DAC_CARS_BASE))
    place(SEG_DASHBRD, bias_pixels(dashbrd_pix, DAC_DASHBRD_BASE))

    # TREKDAT.LZS -> the 8 display-list strip buffers, then the recovered
    # `3A96` token expansion IN PLACE (the "intro anim unpacker" turned out
    # to be the strip expander: its 8 segments at [0E76] ARE these buffers).
    # Record framing (traced live at 1010:00E6-017D, boot frame 9, VM-
    # verified byte-exact against a pre-3A96 memory capture): each record's
    # 4-byte header is TWO RAW WORDS (A, B) read byte-aligned (the same
    # 6576 scalar reader ROADS/MUZAX use, not the LZS bitstream) --
    # size = B, dest_off = A - B (the position within the freshly allocated
    # segment where this record's data lands; NOT offset 0 -- 3A96's "self-
    # referential offset" header word is this same dest_off, baked at
    # compile time so it lines up with where the loader places the data).
    # Then 3 LZS width bytes + the compressed stream, consumed length
    # tracked via the bit reader's own position to locate the next record.
    from skyroads.codecs.lzs import LzsWidths, _BitReader
    from skyroads.recovered.intro_anim import unpack_animation_segment

    def _decompress_tracked(payload: bytes, widths: LzsWidths, out_size: int):
        r = _BitReader(payload)
        out = bytearray()
        while len(out) < out_size:
            if r.get_bit() == 0:
                d = r.get_bits(widths.width_dist_long) + 2
            else:
                if r.get_bit() == 1:
                    out.append(r.get_bits(8))
                    continue
                d = r.get_bits(widths.width_dist_short) + (1 << widths.width_dist_long) + 2
            length = r.get_bits(widths.width_len) + 2
            src = len(out) - d
            for _ in range(length):
                out.append(out[src] if 0 <= src < len(out) else 0)
                src += 1
        consumed = r._pos - (1 if r._bits_left == 8 else 0)
        return bytes(out), consumed

    trek = read_game_file(root, "TREKDAT.LZS")
    pos = 0
    for seg in SEG_DISPLAY_LISTS:
        a, b = struct.unpack_from("<2H", trek, pos)
        size = b
        dest_off = (a - b) & 0xFFFF
        widths = LzsWidths(trek[pos + 4], trek[pos + 5], trek[pos + 6])
        out, consumed = _decompress_tracked(trek[pos + 7:], widths, size)
        base = seg << 4
        # [asm 1010:015E] the loader also stamps dest_off itself as a word
        # bookmark at the segment's offset 0 -- 3A96 reads it back as the
        # "self-referential offset" of its own header-relocation step.
        img[base] = dest_off & 0xFF
        img[base + 1] = (dest_off >> 8) & 0xFF
        img[base + dest_off:base + dest_off + size] = out
        pos += 7 + consumed
        unpack_animation_segment(
            lambda off, b_=base: img[b_ + off],
            lambda off, v, b_=base: img.__setitem__(b_ + off, v))

    return img
