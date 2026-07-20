"""Real-mode CPU adapters for implementations declared by skyroads.execution.

Each active adapter marshals the recovered ABI, calls the cataloged CPUless
semantic body, and reproduces the evidenced continuation. Importing this module
installs nothing.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086, CF, DF

from skyroads.handrecovered.blit import stencil_blit_steps
from skyroads.handrecovered.present import sprite_blit_detail
from skyroads.handrecovered.renderer import (
    perspective_row_offset,
    road_object_visible_detail,
    road_segment_clip_detail,
)
from skyroads.handrecovered.rle_sprite import (
    rle_sprite_backward,
    rle_sprite_forward,
)
from skyroads.handrecovered.tile_raster import (
    tile_mask_build,
    tile_rasterize,
    tile_shade,
)

CODE_SEG = 0x1010  # SKYROADS.EXE's single code segment (from program.entry_cs)

# CS:IP 1010:3A22 - masked sprite/overlay blit. Bare register-clobbering
# routine (no push/pop anywhere in it -- its caller, 1010:39D4, reloads
# SI/BX/DX fresh before each of its up-to-4 calls into this one routine and
# never expects anything preserved). Inputs: DS:SI = source pixel data (a
# 320-byte/row linear layout -- see below), SS:BX = a TIGHTLY PACKED
# (29-byte rows, no padding) transparency-mask table parallel to the source,
# ES = dest segment (DI is reset to the CURRENT SI at the top of every row,
# so dest and source share the exact same row*320+col offset, just different
# segments -- a masked flip from an off-screen buffer onto the visible one),
# DX = row count (24 or 9 in the two observed calls from 1010:39D4). Width is
# a FIXED 0x1D (29) columns, baked into the routine itself (`mov cx,1Dh`),
# not passed in. Per pixel: copy DS:[si]->ES:[di] only where SS:[bx]==2
# (opaque); the 29-column inner loop always runs to completion (no early
# exit -- it's a do-while, like palette_upload's color loop: 3A22 is entered
# directly with no upfront DX check), and each row afterward does `add
# si,0123h` (0x1D + 0x123 == 0x140 == 320, confirming the 320-byte/row
# source+dest stride) before `dec dx` decides whether to loop for another
# row -- the LAST row's `add si,0123h` (sets CF/OF/SF/ZF/AF/PF) followed by
# `dec dx` (sets SF/ZF/AF/PF, leaves CF alone -- same DEC-preserves-CF
# semantics as CPU8086's own 0x48-0x4F opcode handler) are the routine's
# final flags-setting instructions; nothing after them (the plain `ret`)
# touches flags.
#
# This is the SOLE entry point (3A22) AND the loop's own re-entry target
# (3A3C's `jnz 3A22`) -- hooking it here means the interpreter never actually
# re-executes 3A3C's jump (this hook always runs every row to completion
# before returning), so like lzs_decode_loop this address is only ever
# reached fresh, from a real CALL, never mid-function.
_SPRITE_BLIT_WIDTH = 0x1D
_SPRITE_BLIT_ROW_SKIP = 0x0123


def _sprite_blit_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ds, es, ss = s.ds, s.es, s.ss

    dx = s.dx & 0xFFFF
    rows = dx or 0x10000  # the original is a do-while and wraps DX=0
    result = sprite_blit_detail(
        mem.rb,
        mem.wb,
        es,
        ds,
        ss,
        s.si,
        s.bx,
        rows,
    )

    s.si = result.si
    s.di = result.di
    s.bx = result.bx
    s.dx = 0
    s.cx = 0
    if result.last_value is not None:
        s.ax = (s.ax & 0xFF00) | result.last_value

    si_before_add = (result.si - _SPRITE_BLIT_ROW_SKIP) & 0xFFFF
    cpu.set_add_flags(si_before_add, _SPRITE_BLIT_ROW_SKIP, si_before_add + _SPRITE_BLIT_ROW_SKIP, 16)
    dec_cf = cpu.get_flag(CF)
    cpu.set_sub_flags(1, 1, 0, 16)
    cpu.set_flag(CF, dec_cf)

    s.ip = mem.rw(ss, s.sp)
    s.sp = (s.sp + 2) & 0xFFFF


def sprite_blit_hook(cpu: CPU8086) -> None:
    _sprite_blit_hook(cpu)


# CS:IP 1010:32C1 -- the tile-renderer visible-column MASK builder. Bare near
# proc (`push es` ... `pop es; ret` -- ES restored, ds/bp/sp preserved, only
# AX/BX/CX/DX/SI/DI + flags clobbered). Builds a 33-row x 29-column (0x1D)
# coverage mask at ss:[0E86] (es is set to ss): first `rep stosw`-zeroes the
# 0x1DE-word buffer, then for each of 33 rows computes the object's visible
# column span and writes 0x01 bytes (`rep stosb`) marking covered columns.
# Per-row inputs: bx = the row's screen X (starts ds:[0E2C], decremented each
# row, with a horizon jump at row 10 by ds:[0E34]-8); si indexes a per-row
# half-extent table at ds:[si+0x47A]; ds:[0E28] is the object's centre X. Rows
# whose X leaves [0x14,0x9D] are skipped (left zero). The span is the extent
# clipped to the 29-column row. Faithful replication -> exact memory + exit
# registers by construction (a leaf, no calls).
_TILE_MASK_ROWS = 0x21          # 33 rows
_TILE_MASK_ROW_STRIDE = 0x1D    # 29 columns/row
_TILE_MASK_BUF = 0x0E86


def _sgn16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


# ABI-only exit-state trace for the pure tile_mask_build implementation.
# It repeats the target's register arithmetic but deliberately performs no
# semantic writes; the CPUless body above is the sole memory authority.
def _tile_mask_exit_state(cpu: CPU8086, ds: int, ax: int, cx: int):
    mem = cpu.mem
    rw = mem.rw

    di = _TILE_MASK_BUF                         # 32D0
    bx = rw(ds, 0x0E2C)                          # 32D3
    si = ((0x009D - rw(ds, 0x0E2C)) << 1) & 0xFFFF  # 32D7-32DE
    dx = _TILE_MASK_ROWS                         # 32E0

    while True:                                  # row loop @32E3
        if not (bx > 0x009D or bx < 0x0014):     # 32E3 ja / 32E9 jb (unsigned) -> skip
            saved_bx, saved_di = bx, di          # 32EE push bx / 32EF push di
            bx = 0x006E                          # 32F0
            cx = 0x01AE                          # 32F3
            ext = rw(ds, (si + 0x047A) & 0xFFFF)  # 32F6
            if ext != 0:                         # 32FC jz 331C
                ax = (0x010E - ext) & 0xFFFF     # 32FE-3301
                center = rw(ds, 0x0E28)
                if not (_sgn16(center) < _sgn16(ax)):   # 330A jl -> 3313 (skip 330C)
                    bx = (0x010E + ext) & 0xFFFF # 330C-330F
                if _sgn16(center) < _sgn16(ax):  # 3318 jge -> 331C (skip 331A)
                    cx = ax                      # 331A
            ax = cx                              # 331C
            cx = _TILE_MASK_ROW_STRIDE           # 331E
            bx = (bx - rw(ds, 0x0E28)) & 0xFFFF  # 3321
            if _sgn16(bx) < 0:                   # 3326 jge (skip xor)
                bx = 0                           # 3328
            if bx < 0x001D:                      # 332A cmp / 332D jnb (unsigned) -> 3349
                di = (di + bx) & 0xFFFF          # 332F
                cx = (cx - bx) & 0xFFFF          # 3331
                bx = (rw(ds, 0x0E28) + 0x1D - ax) & 0xFFFF  # 3333-333B
                if _sgn16(bx) < 0:               # 333D jge (skip xor)
                    bx = 0                        # 333F
                old_cx = cx
                cx = (cx - bx) & 0xFFFF          # 3341
                if not (old_cx <= bx):           # 3343 jbe (unsigned) -> 3349
                    # 3345-3348: mov al,1; rep stosb drains CX and advances DI.
                    di = (di + cx) & 0xFFFF
                    ax = (ax & 0xFF00) | 0x01    # al = 1
                    cx = 0                        # rep drains cx
            bx, di = saved_bx, saved_di          # 3349 pop di / 334A pop bx

        di = (di + _TILE_MASK_ROW_STRIDE) & 0xFFFF   # 334B
        bx = (bx - 1) & 0xFFFF                        # 334E
        si = (si + 2) & 0xFFFF                        # 334F
        if dx == 0x0A:                               # 3352 / 3355
            ax = (rw(ds, 0x0E34) - 0x0008) & 0xFFFF  # 3357-335A
            bx = (bx - ax) & 0xFFFF                   # 335D
            ax = (ax << 1) & 0xFFFF                   # 335F
            si = (si + ax) & 0xFFFF                   # 3361
        dx_before = dx
        dx = (dx - 1) & 0xFFFF                        # 3363
        if dx == 0:                                  # 3364 jz 3369
            break
    return ax, bx, cx, dx, si, di, dx_before


def _tile_clip_mask_hook(cpu: CPU8086) -> None:
    s = cpu.s
    ret_ip = cpu.mem.rw(s.ss, s.sp)
    mem = cpu.mem
    tile_mask_build(
        lambda off: mem.rw(s.ds, off),
        lambda off, value: mem.ww(s.ss, off, value),
        lambda off, value: mem.wb(s.ss, off, value),
    )
    ax, bx, cx, dx, si, di, dx_before = _tile_mask_exit_state(
        cpu, s.ds, s.ax, s.cx,
    )

    s.ax, s.bx, s.cx, s.dx, s.si, s.di = (ax & 0xFFFF, bx & 0xFFFF, cx & 0xFFFF,
                                          dx & 0xFFFF, si & 0xFFFF, di & 0xFFFF)
    # exit flags: the loop-terminating `dec dx` (1 -> 0) sets SF/ZF/PF/AF/OF but
    # PRESERVES CF, which was last set by that iteration's `cmp dx,0x0A` (3352):
    # CF = borrow = (dx_before < 0x0A) unsigned.
    cpu.set_sub_flags(dx_before, 1, dx_before - 1, 16)
    cpu.set_flag(CF, (dx_before & 0xFFFF) < 0x000A)

    s.sp = (s.sp + 2) & 0xFFFF
    s.ip = ret_ip


def tile_clip_mask_hook(cpu: CPU8086) -> None:
    _tile_clip_mask_hook(cpu)


# CS:IP 1010:33FD -- the road-tile SHADER (linear/mode-13h, the exercised twin;
# the EGA-planar variant at 336B is used by 31DB, not on this replay's path).
# Bare near proc (`mov ds,ss` up front -> DS=SS on exit; no register saves, so
# AX/BX/CX/DX/SI/DI + flags clobbered; es/bp/sp preserved). Selects a 9x29 tile
# pattern at ds:[0x68E + (ds:[0E34]/5)*0x105] (skips, returning early, if that
# index >= 5), computes a screen offset di into es from the tile's road
# position (ds:[0E2C]/[0E28]/[0E34]), stores it at ds:[0E70], then walks the
# tile column-major (9 rows down x 29 cols): where BOTH the pattern byte and the
# coverage byte ds:[bx+0x113E] are non-zero, it marks the coverage byte 2 and
# recolours the screen pixel es:[di] -- 0x3D -> 0x40, and any index in 1..0x0F
# gets +0x2D (a shade ramp); 0 and >=0x10 pass through. di steps +0x140 per row
# (down), the pattern/mask index +0x1D; per column di rewinds to +1 and the
# index to +1. Faithful replication -> exact memory + exit registers.
_TILE_SHADE_PATTERN_BASE = 0x068E
_TILE_SHADE_MASK_BASE = 0x113E


# The shade loop, shared by the standalone tile_shade hook AND the 325B
# rasterizer (which calls 33FD as its last step). Reads/writes es:[di] (the dest
# buffer) and ds=ss globals. Returns (early, ax,bx,cx,dx,si,di,dx_before,rem) so
# the standalone hook can reproduce 33FD's exact exit registers; 325B ignores
# them (it only needs the pixel writes). `early` marks the tile-index>=5 no-op.
def _tile_shade_exit_state(cpu: CPU8086, ss: int, es: int):
    mem = cpu.mem
    rb, wb, rw = mem.rb, mem.wb, mem.rw
    ds = ss                                         # 33FD mov ds,ss

    v34 = rw(ss, 0x0E34)
    ax = v34 // 5                                    # 3403-3409 div 5
    rem = v34 % 5
    if ax >= 5:                                       # 340B cmp ax,5; 340E jnb 347D
        return True, ax, 0, 5, rem, 0, 0, 0, rem

    si = (_TILE_SHADE_PATTERN_BASE + ax * 0x0105) & 0xFFFF   # 3410-3418
    row = (0x009D - rw(ss, 0x0E2C) + 0x0010 + v34) & 0xFFFF  # 341A-3424
    prod = _sgn16(row) * 0x0140                              # 3428-342B imul cx
    ax = prod & 0xFFFF                                        # ax = low word (survives; AH kept)
    di = ax
    di = (di + rw(ss, 0x0E28) - 0x006E) & 0xFFFF            # 342F-3433
    bx = 0                                            # 343A
    dx = 0x001D                                       # 343C (outer: 29 columns)
    while True:
        cx = 9                                         # 343F (inner: 9 rows)
        while True:
            if rb(ds, (bx + si) & 0xFFFF) != 0 and \
                    rb(ds, (bx + _TILE_SHADE_MASK_BASE) & 0xFFFF) != 0:
                al = rb(es, di)                        # 3453
                if al == 0x3D:                          # 3456-345A
                    al = 0x40
                if al != 0 and al < 0x10:               # 345C jz / 3460 jnb -> else +0x2D
                    al = (al + 0x2D) & 0xFF             # 3464
                ax = (ax & 0xFF00) | al                 # AL updated; AH from the imul
            di = (di + 0x0140) & 0xFFFF                # 3469
            bx = (bx + 0x001D) & 0xFFFF                # 346D
            cx -= 1
            if cx == 0:                                 # loop 3442
                break
        di = (di - 0x0B3F) & 0xFFFF                    # 3472
        bx = (bx - 0x0104) & 0xFFFF                    # 3476
        dx_before = dx
        dx = (dx - 1) & 0xFFFF                          # 347A
        if dx == 0:                                     # 347B jnz 343F
            break
    return False, ax, bx, 0, 0, si, di, dx_before, rem


def _tile_shade_hook(cpu: CPU8086) -> None:
    s = cpu.s
    ss = s.ss
    ret_ip = cpu.mem.rw(ss, s.sp)
    early, ax, bx, cx, dx, si, di, dx_before, rem = _tile_shade_exit_state(
        cpu, ss, s.es,
    )
    mem = cpu.mem
    tile_shade(
        mem.rb,
        mem.wb,
        lambda off: mem.rw(ss, off),
        lambda off, value: mem.ww(ss, off, value),
        ss,
        s.es,
    )

    if early:
        s.ax, s.dx, s.cx, s.ds = ax & 0xFFFF, rem & 0xFFFF, 5, ss
        cpu.set_sub_flags(ax, 5, ax - 5, 16)          # cmp ax,5
    else:
        s.ax, s.bx, s.cx, s.dx, s.si, s.di, s.ds = (
            ax & 0xFFFF, bx & 0xFFFF, 0, 0, si & 0xFFFF, di & 0xFFFF, ss)
        # exit flags: `dec dx` (1 -> 0) sets SF/ZF/PF/AF/OF, preserves CF from
        # the preceding `sub bx,0x0104` (3476). bx here is the post-sub value.
        cpu.set_sub_flags(dx_before, 1, dx_before - 1, 16)
        cpu.set_flag(CF, ((bx + 0x0104) & 0xFFFF) < 0x0104)

    s.sp = (s.sp + 2) & 0xFFFF
    s.ip = ret_ip


def tile_shade_hook(cpu: CPU8086) -> None:
    _tile_shade_hook(cpu)


# CS:IP 1010:325B -- the whole road-tile RASTERIZER, and the first hook that
# COLLAPSES two child hooks: it calls 32C1 (build the coverage mask) and 33FD
# (shade) directly, and this hook reproduces BOTH by calling the shared
# _tile_mask_build / _tile_shade_build helpers -- so the 32C1 and 33FD hooks no
# longer fire on this path; their logic lives once and is reused. Bare near proc
# saving bx/di/ds/es (all restored), so only AX/CX/DX/SI + flags are caller-
# visible scratch. Steps: ES <- DS:[0E36]; build mask at SS:[0E86]; compute a
# screen offset di (stored at ds:[0E6C]); `lds si,[0E2E]` loads the tile bitmap
# far pointer (DS -> the bitmap segment); then a 29-row x 24-col masked blit
# copies each non-zero bitmap pixel to es:[di] wherever the coverage mask is
# set (marking that mask byte 2); finally 33FD shades. Exit AX/CX/DX/SI + flags
# come from the trailing 33FD call (its results are 325B's last writes before
# the register-restoring pops); bx/di/ds/es are popped back to the caller's.
def _tile_rasterizer_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss = s.ss
    ret_ip = mem.rw(ss, s.sp)
    rw = mem.rw
    es = rw(ss, 0x0E36)
    v34 = rw(ss, 0x0E34)
    shade_started = False
    last_shade_pixel = None

    def semantic_ww(off, value):
        nonlocal shade_started
        mem.ww(ss, off, value)
        if off == 0x0E70:
            shade_started = True

    def semantic_wb(seg, off, value):
        nonlocal last_shade_pixel
        mem.wb(seg, off, value)
        if shade_started and seg == es:
            last_shade_pixel = value & 0xFF

    tile_rasterize(
        mem.rb,
        semantic_wb,
        lambda off: mem.rw(ss, off),
        semantic_ww,
        ss,
    )

    blit_si = (rw(ss, 0x0E2E) + 0x1D * 0x18) & 0xFFFF
    shade_index, rem = divmod(v34, 5)
    early = shade_index >= 5
    if early:
        s.ax, s.dx, s.cx, s.si = (
            shade_index & 0xFFFF,
            rem & 0xFFFF,
            5,
            blit_si,
        )
        cpu.set_sub_flags(shade_index, 5, shade_index - 5, 16)
    else:
        row = (0x009D - rw(ss, 0x0E2C) + 0x0010 + v34) & 0xFFFF
        ax = (_sgn16(row) * 0x0140) & 0xFFFF
        if last_shade_pixel is not None:
            ax = (ax & 0xFF00) | last_shade_pixel
        s.ax, s.cx, s.dx, s.si = (
            ax,
            0,
            0,
            (_TILE_SHADE_PATTERN_BASE + shade_index * 0x0105) & 0xFFFF,
        )
        cpu.set_sub_flags(1, 1, 0, 16)
        cpu.set_flag(CF, False)

    s.sp = (s.sp + 2) & 0xFFFF
    s.ip = ret_ip


def tile_rasterizer_hook(cpu: CPU8086) -> None:
    _tile_rasterizer_hook(cpu)


# CS:IP 1010:3153 -- the FORWARD run-length sprite rasterizer (one of a mirror
# pair; the backward twin is at 1010:3190, hooked below). The dominant render
# cost in the in-game replay: 5,884 calls driving 41,162 inner-loop iterations
# (~13% of all interpreted steps together with its twin). Near proc; saves DI
# and BP only (push at 3153/3154, pop at 318D/318E) -- everything else is
# scratch.
#
# Inputs at entry (all live registers / segments):
#   DS:SI -> the RLE control stream; ES = destination segment; SS = the
#   fill-colour table segment. Setup (3153-3169): read a sprite/row index byte
#   (`lodsb`), look its fill colour up in ss:[index*4 + 0x352] into DL, then
#   read a 16-bit destination offset (`lodsw`) into DI.
# Per control byte (loop 316B-318B):
#   al = *si++; if al == 0xFF -> done. Else BP=DI (row anchor), DI -= al (this
#   row's left skip), read run length (`lodsb`) then SKIP one stream byte
#   (`inc si`), and fill `runlen` bytes of colour DL forward from ES:DI as an
#   optional leading `stosb` (when runlen is odd) followed by `rep stosw`
#   (runlen>>1 words of DL:DL). Then DI = BP + 0x140 (down one 320-wide row)
#   and loop. So each control byte paints one horizontal run on a successive
#   scanline -- a vertical strip of spans.
#
# Exit state at the `ret` (mirrors the ASM exactly):
#   AX=0x00FF (ah zeroed at 3169/3183, al=the 0xFF terminator); BX=index*4
#   (set once at 315C, never touched in the loop); DX=(entry DH):(fill colour);
#   CX=0 after any run (the last `rep stosw` drains it) else entry CX with its
#   high byte zeroed (the `xor ch,ch` at 3155); SI just past the terminator;
#   DI and BP restored to their entry values by the pops; ES/DS/SS untouched.
#   FLAGS come from the `cmp al,0xFF` (8-bit, al==0xFF -> result 0): ZF=PF=1,
#   CF=SF=OF=AF=0. Forward `stos` assumes DF=0 (the routine never sets it),
#   matching the default direction.
_RLE_FILL_TABLE = 0x0352   # ss-relative fill-colour table (indexed by first byte * 4)
_RLE_ROW_STRIDE = 0x0140   # 320 bytes -> one scanline down
_RLE_TERMINATOR = 0xFF


def _rle_sprite_forward_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, ds, es = s.ss, s.ds, s.es
    sp = s.sp
    ret_ip = mem.rw(ss, sp)

    entry_si = s.si
    index = mem.rb(ds, entry_si)
    bx = (index << 2) & 0xFFFF
    fill = mem.rb(ss, (bx + _RLE_FILL_TABLE) & 0xFFFF)
    first_ctrl = mem.rb(ds, (entry_si + 3) & 0xFFFF)
    si = rle_sprite_forward(mem.rb, mem.wb, ss, ds, es, entry_si)

    s.ax = 0x00FF
    s.bx = bx
    s.cx = 0 if first_ctrl != _RLE_TERMINATOR else s.cx & 0x00FF
    s.dx = (s.dx & 0xFF00) | fill
    s.si = si
    # DI and BP are push/pop-restored -> unchanged; ES/DS/SS untouched.
    cpu.set_sub_flags(_RLE_TERMINATOR, _RLE_TERMINATOR, 0, 8)  # cmp al,0xFF -> 0
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


def rle_sprite_forward_hook(cpu: CPU8086) -> None:
    _rle_sprite_forward_hook(cpu)


# CS:IP 1010:3190 -- the BACKWARD (mirror) twin of the RLE sprite rasterizer
# above; called once per call alongside the forward one (5,884 calls / ~41K
# iterations). Same structure with three differences: the fill-colour table is
# ss:[index*4 + 0x353] (the odd-parity companion of 0x352); DI starts one lower
# (`dec di` at 31A6); each row's anchor is offset by `add di,ax` (RIGHT, vs the
# forward variant's `sub`); and the run is filled DOWNWARD with `std` (a
# leading conditional `stosb`, an unconditional `dec di`, then a reverse
# `rep stosw`), the routine restoring `cld` each iteration.
#
# The reverse fill writes `runlen` bytes contiguously downward from the
# post-`add` DI -- i.e. it paints exactly the span [DI-runlen+1 .. DI], all
# colour DL. Since every byte is the same colour, the resulting memory is
# identical regardless of write order, so this hook fills that span directly
# (matching the ASM's exact addresses, with 16-bit wrap). DI is discarded each
# row (reset to BP+0x140), so the fill's own final DI never reaches the caller.
#
# Exit state matches the forward twin (AX=0x00FF, BX=index*4, CX=0 after any
# run, DX=(entry DH):fill, SI past the terminator, DI/BP restored, flags from
# `cmp al,0xFF`) with ONE addition: the per-iteration `cld` (31C4) leaves DF=0
# after any run, so this hook clears DF when at least one run was drawn (a zero-
# run sprite leaves DF at its entry value, exactly as the untouched ASM would).
_RLE_FILL_TABLE_BACK = 0x0353


def _rle_sprite_backward_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, ds, es = s.ss, s.ds, s.es
    sp = s.sp
    ret_ip = mem.rw(ss, sp)

    entry_si = s.si
    index = mem.rb(ds, entry_si)
    bx = (index << 2) & 0xFFFF
    fill = mem.rb(ss, (bx + _RLE_FILL_TABLE_BACK) & 0xFFFF)
    first_ctrl = mem.rb(ds, (entry_si + 3) & 0xFFFF)
    ran = first_ctrl != _RLE_TERMINATOR
    si = rle_sprite_backward(mem.rb, mem.wb, ss, ds, es, entry_si)

    s.ax = 0x00FF
    s.bx = bx
    s.cx = 0 if ran else s.cx & 0x00FF
    s.dx = (s.dx & 0xFF00) | fill
    s.si = si
    cpu.set_sub_flags(_RLE_TERMINATOR, _RLE_TERMINATOR, 0, 8)
    if ran:
        cpu.set_flag(DF, False)  # the per-iteration `cld` at 31C4
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


def rle_sprite_backward_hook(cpu: CPU8086) -> None:
    _rle_sprite_backward_hook(cpu)


# CS:IP 1010:04C0 -- the fixed-point perspective transform, the KEYSTONE of the
# renderer subsystem: every road/object render path funnels through it, and it now
# sits entirely on already-recovered primitives (its three 32-bit divides are
# the ulong_div helper). This is the first renderer layer wired to a clean
# skyroads/handrecovered/renderer.py function; the hook only adapts registers and
# reproduces the ASM's exact exit state. cdecl-style near proc (`enter 0,0` /
# `leave` / bare `ret`, caller pops the 3 word args), args at [sp+2]=x_lo,
# [sp+4]=x_hi, [sp+6]=depth; returns the perspective-table word in AX. SI/DI/BP
# are saved+restored, so only AX/BX/CX/DX and flags are caller-visible scratch.
#
# Two exit paths (see perspective_row_offset):
#  - out of range (row idx>=322): the ASM's `cmp si,0x142; jmp 0529; mov ax,0`
#    leaves AX=0, BX=entry BX (never written on this path), CX=128 (the divisor
#    still in CX from 04D0), DX=depth%128, and FLAGS from `cmp si,0x142`.
#  - in range: AX=ds:[offset] (the looked-up word), BX=CX=offset, DX=idx%46,
#    and FLAGS from the final `add cx,ax` (04C0's 051D) whose operands are the
#    table base+quotient and 2*(idx/46).
_PERSPECTIVE_EXIT_RET_BYTES = 2  # bare `ret`; the caller drops the 3 word args


def _perspective_transform_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp
    ret_ip = mem.rw(ss, sp)

    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    depth = mem.rw(ss, (sp + 6) & 0xFFFF)
    r = perspective_row_offset(x_lo, x_hi, depth)

    if not r.in_range:
        s.ax = 0
        # BX untouched on this path; CX still holds the 04C9 divisor (128); DX
        # holds depth%128 from the 04D0 divide.
        s.cx = 0x80
        s.dx = r.rem128
        cpu.set_sub_flags(r.idx, 0x142, r.idx - 0x142, 16)  # `cmp si,0x142`
    else:
        s.ax = mem.rw(s.ds, r.offset)
        s.bx = r.offset
        s.cx = r.offset
        s.dx = r.rem46
        cpu.set_add_flags(r.add_lhs, r.add_rhs,
                          r.add_lhs + r.add_rhs, 16)  # `add cx,ax` at 051D

    s.sp = (sp + _PERSPECTIVE_EXIT_RET_BYTES) & 0xFFFF
    s.ip = ret_ip


def perspective_transform_hook(cpu: CPU8086) -> None:
    _perspective_transform_hook(cpu)


# CS:IP 1010:1732 -- the layer-2 per-segment cull, the renderer root that
# ties 04C0 (perspective) and 1631 (clip) together. Hooking it collapses its
# FOUR nested 04C0 calls plus the cull glue (~28% of real render work) into one
# Python call. cdecl near proc (`enter 0xA` / `leave` / bare `ret`, caller pops
# the 4 word args); args [sp+2]=x_lo, [sp+4]=x_hi, [sp+6]=depth(si), [sp+8]=
# screen_y(di). Returns 0/1 in AX; SI/DI/BP are saved+restored (net-preserved),
# so only AX/BX/CX/DX + flags are caller-visible scratch. The pure decision is
# renderer.py::road_object_visible (ASM_MATCHED over 12,152 in-game calls); this
# hook re-walks the same control flow only to reproduce the exact exit BX/CX/DX
# (inherited from whichever nested 04C0/1631 call was last on the taken path)
# and the flags from that path's final compare.
def _persp_exit(cpu, ds, x_lo, x_hi, depth, bx):
    """One 04C0 call: returns (table_word, bx, cx, dx) with 04C0's exit regs."""
    r = perspective_row_offset(x_lo, x_hi, depth)
    if r.in_range:
        return cpu.mem.rw(ds, r.offset), r.offset, r.offset, r.rem46
    return 0, bx, 0x80, r.rem128  # out-of-range leaves BX untouched, CX=128


def _clip_exit(cpu, ds, dir_sel, seg, coord, bx, cx, dx):
    """One 1631 call: returns (ret, bx, cx, dx) with 1631's exit regs. seg>37
    returns 0 without touching bx/cx/dx; otherwise cx=128, dx=(coord-0x2200)%128,
    and bx=seg*2 only on the table cases (sel 0x100/0x300/0x500)."""
    seg &= 0xFFFF
    result = road_segment_clip_detail(
        dir_sel,
        seg,
        coord,
        lambda: cpu.mem.rw(ds, (0x4C + 2 * seg) & 0xFFFF),
        lambda: cpu.mem.rw(ds, (0x98 + 2 * seg) & 0xFFFF),
    )
    if seg > 0x25:
        return result.result, bx, cx, dx
    return (
        result.result,
        bx if result.bx is None else result.bx,
        0x80,
        result.rem128,
    )


def _road_object_visible_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, ds, sp = s.ss, s.ds, s.sp
    ret_ip = mem.rw(ss, sp)
    x_lo = mem.rw(ss, (sp + 2) & 0xFFFF)
    x_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    si = mem.rw(ss, (sp + 6) & 0xFFFF)   # depth base
    di = mem.rw(ss, (sp + 8) & 0xFFFF)   # screen_y
    bx, cx, dx = s.bx, s.cx, s.dx

    def persp(depth_arg):
        nonlocal bx, cx, dx
        value, bx, cx, dx = _persp_exit(
            cpu, ds, x_lo, x_hi, depth_arg, bx,
        )
        return value

    def clip(dir_sel, seg, coord):
        nonlocal bx, cx, dx
        value, bx, cx, dx = _clip_exit(
            cpu, ds, dir_sel, seg, coord, bx, cx, dx,
        )
        return value

    result = road_object_visible_detail(
        persp,
        clip,
        x_lo,
        x_hi,
        si,
        di,
    )
    s.ax = result.result & 0xFFFF
    s.bx, s.cx, s.dx = bx & 0xFFFF, cx & 0xFFFF, dx & 0xFFFF
    cpu.set_sub_flags(
        result.cmp_lhs & 0xFFFF,
        result.cmp_rhs & 0xFFFF,
        (result.cmp_lhs & 0xFFFF) - (result.cmp_rhs & 0xFFFF),
        16,
    )
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


def road_object_visible_hook(cpu: CPU8086) -> None:
    _road_object_visible_hook(cpu)


# --- stencil blit (1010:0F62) --------------------------------------------------
#
# The menu text/glyph rendering primitive: `push si,di; es:=[AF2A]; es:di=0;
# ds:si=far arg (bp+4); cx=count (bp+8); loop { al=lodsb; al = al==0 ? al :
# (al==1 ? word[bp+10] : word[bp+12]); stosb }; pop ds,di,si`. Small per-call
# count (1-20+ observed live) but called MANY times per menu frame -- profiling
# found it among the hottest un-hooked interpreted work during menu/transition
# screens (run_status.md, 2026-07-11 perf diagnosis). No port I/O, so (unlike
# the music-engine hook attempt logged the same day) full register-exact
# parity is tractable -- got it wrong twice on the first attempt (both caught
# by the strict differential verifier, not by reasoning ahead of time):
#
#   SI/DI: the function opens with `push si; push di` and closes with
#       `pop di; pop si` -- they are the CALLER's original values, UNCHANGED,
#       not "final cursor position" (an initially-plausible guess that's wrong).
#   AX: AL = the last byte's write value (see stencil_blit); AH is untouched by
#       a zero byte (0F76-0F78 `or al,al` only reads AL) and RUNNING through
#       the whole loop -- it is whatever the most recent `mov ax,[bp+10 or
#       +12]` full-word load set it to, which can be several bytes before the
#       end if the source has trailing zeros (checking only the LAST byte, as
#       an earlier version of this hook did, is wrong whenever the source ends
#       in zeros after a substitution -- the very first live call hit exactly
#       this case).
#   CX: 0 (the `loop` instruction's own postcondition for a normal exit).
#   ES: ds:[AF2A] (loaded once, unconditionally, at entry).
#   DS: net unchanged -- `push ds; lds si,[bp+4]` then `pop ds` restores it.
#   BX/DX: untouched.
#   FLAGS: from the LAST byte's own comparison -- `or al,al` (b==0) or
#       `cmp al,1` (b==1 or b>1; identical instruction either way).
_STENCIL_ES_PTR = 0xAF2A


def stencil_blit_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp
    ret_ip = mem.rw(ss, sp)
    src_off = mem.rw(ss, (sp + 2) & 0xFFFF)
    src_seg = mem.rw(ss, (sp + 4) & 0xFFFF)
    count = mem.rw(ss, (sp + 6) & 0xFFFF)
    template_color = mem.rw(ss, (sp + 8) & 0xFFFF)
    other_color = mem.rw(ss, (sp + 10) & 0xFFFF)
    es = mem.rw(s.ds, _STENCIL_ES_PTR)

    source = (
        mem.rb(src_seg, (src_off + i) & 0xFFFF)
        for i in range(count)
    )
    final_ax = s.ax
    for i, step in enumerate(
        stencil_blit_steps(source, template_color, other_color, s.ax)
    ):
        # Consume and write one byte at a time. This preserves the original's
        # semantics when source and destination overlap.
        mem.wb(es, i & 0xFFFF, step.value)
        final_ax = step.ax
        if step.compared:
            cpu.set_sub_flags(
                step.byte, 1, (step.byte - 1) & 0xFF, 8,
            )
        else:
            cpu.set_logic_flags(0, 8)
    s.ax = final_ax & 0xFFFF
    s.cx = 0
    s.es = es
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


# --- Generated literal implementation adapters --------------------------------
# skyroads/lifted/ holds generated instruction-level implementations proven
# byte-exact against the interpreter oracle. They remain valid baseline
# representations whether or not an authored semantic implementation is useful.

# 1010:34AE -- the [0E38]-dispatched tile renderer (reached via the 34A7 wrapper),
# the dominant un-hooked cost in the full start->finish level replay
# (replay_skyroads_20260710_145303): ~29% of interpreted work and the source of
# the in-level performance drops. Lifted + verified ORACLE_PASSING (liftverify:
# 401 calls, 26/28 blocks; plus 400 full-level-replay calls under the strict
# differential verifier), byte-exact, zero divergence.
from skyroads.lifted.functions.lifted_1010_34ae import lifted_1010_34ae as _lifted_34ae  # noqa: E402

# 1010:186B -- the road-segment movement stepper: a ~274-instruction, 5-phase
# swept movement+collision resolver that steps the ship's lateral/depth
# accumulators (ds:[9618:961A], ds:[AF1C], ds:[AF2C]) from their current values
# toward the requested target in sub-steps, using 1732 (road_object_visible) as
# the collision predicate and refining each axis to the exact contact boundary
# (calls 1732/5D4C/5E5A/5D8C, all already recovered). Uses an `enter`-prologue,
# so this also exercises dos_re's entry-fallback recursion fix (11917f2). The
# largest single remaining render/movement-path recovery, and it collapses the
# road-segment path (subsumes repeated 1732+04C0 calls). Lifted + verified
# ORACLE_PASSING (liftverify: 40 calls, 58/80 blocks; plus the full-level replay
# under the strict differential verifier), byte-exact.
from skyroads.lifted.functions.lifted_1010_186b import lifted_1010_186b as _lifted_186b  # noqa: E402

# 1010:39D4 -- the fixed-position HUD/dashboard sprite blitter that every 34AE
# render pass finalizes into (called every frame, 2 sprites always + 2 more
# gated on the VGA pass). Calls 1010:3A22 4x per invocation -- ALREADY
# hand-recovered above as sprite_blit_hook (verified 2026-07-09, 1,806 calls,
# zero divergence), so only 39D4 itself is new here. Surfaced while decoding
# 34AE's own algorithm from its proven lift (2026-07-12); lifted + verified
# ORACLE_PASSING (liftverify: 100 calls, 3/3 blocks, full coverage),
# byte-exact.
from skyroads.lifted.functions.lifted_1010_39d4 import lifted_1010_39d4 as _lifted_39d4  # noqa: E402

# 1010:2D1F -- the top-level per-frame ROAD RENDER DRIVER: takes 8 params
# (bp+4..+18 -> [0E28..0E36]), sets up record_base from the 0x168E road
# perspective table, runs the classify/dispatch loop (the same triple loop as
# recovered render_classify) calling per-column road draws via ss:[bx+2991],
# calls 34AE to finalize, and copies the occlusion mask (0E86->1243). This is
# the last unrecovered node in the per-frame render call tree (34AE/39D4 already
# lifted; road_column/sprite_blit/masked_blit/present_rect/stencil_blit pure).
# Lifted 2026-07-12 via live replay disassembly -> liftgen -> liftverify from a
# gameplay-frame-640 snapshot (the cold snapshot has code-overlay garbage here).
# liftverify: ORACLE_PASSING, 7/7 byte-exact vs ASM (full machine state incl.
# VGA), 16/17 blocks (the [003C]==0 non-gameplay branch not exercised in the
# gameplay window). Additionally pixel-validated in situ: 190/190 gameplay
# frames (571-760) produce byte-IDENTICAL VGA with vs without this lift.
from skyroads.lifted.functions.lifted_1010_2d1f import lifted_1010_2d1f as _lifted_2d1f  # noqa: E402

# --- 2026-07-12 leaf-function lifts (movement/projection math helpers) ---------
# Surfaced by censusing unhooked call targets on the level-start path (72 of 83
# distinct targets were unhooked; the top 15 by call count are all liftable).
# These three are small, call-free math LEAVES verified ORACLE_PASSING by
# liftverify. Partial block coverage is noted per hook -- but the strict
# auto-continuation verifier re-checks EVERY call against the ASM oracle at
# runtime (it's exactly what caught 1010:59CF diverging in the same batch, which
# was therefore NOT installed), so an unproven branch cannot silently diverge
# with the verifier active. CPU adapters, not semantic implementations.

# 1010:5D80 -- DX:AX <<= CL, a 32-bit shift-left-by-count helper (xor ch,ch;
# jcxz; loop: shl ax,1/rcl dx,1). Verified 3/3 blocks (FULL coverage), 3 calls.
from skyroads.lifted.functions.lifted_1010_5d80 import lifted_1010_5d80 as _lifted_5d80  # noqa: E402

# 1010:0BE9 -- a projection helper: si = ((ss:[bp+4] / 128) - 0x5F) / 46, then
# branches on its sign (perspective-row math, same family as 04C0). Verified
# ORACLE_PASSING, 6/8 blocks, 2 calls.
from skyroads.lifted.functions.lifted_1010_0be9 import lifted_1010_0be9 as _lifted_0be9  # noqa: E402

# 1010:0BAF -- a bounds/clamp predicate on two 16-bit params (cmp ss:[bp+4] vs
# 0xFE9D, ss:[bp+6] vs 0x2800). Verified ORACLE_PASSING, 7/10 blocks, 1 call.
from skyroads.lifted.functions.lifted_1010_0baf import lifted_1010_0baf as _lifted_0baf  # noqa: E402

# Authored declarations pair one CPUless semantic implementation with its
# real-mode CPU adapter. skyroads.execution is the sole catalog and activation
# authority. Only complete semantic-plus-adapter pairs remain in this module;
# there is no dormant override inventory outside the catalog declaration below.
FAITHFUL_OVERRIDE_ADAPTERS = {
    0x3A22: ("sprite_blit", sprite_blit_detail, sprite_blit_hook),
    0x32C1: ("tile_clip_mask", tile_mask_build, tile_clip_mask_hook),
    0x33FD: ("tile_shade", tile_shade, tile_shade_hook),
    0x325B: ("tile_rasterizer", tile_rasterize, tile_rasterizer_hook),
    0x3153: ("rle_sprite_forward", rle_sprite_forward, rle_sprite_forward_hook),
    0x3190: ("rle_sprite_backward", rle_sprite_backward, rle_sprite_backward_hook),
    0x04C0: (
        "perspective_transform",
        perspective_row_offset,
        perspective_transform_hook,
    ),
    0x1732: (
        "road_object_visible",
        road_object_visible_detail,
        road_object_visible_hook,
    ),
    0x0F62: ("stencil_blit", stencil_blit_steps, stencil_blit_hook),
}

GENERATED_FUNCTION_ADAPTERS = {
    0x34AE: ("lifted_tile_render_34AE", _lifted_34ae),
    0x186B: ("lifted_road_stepper_186B", _lifted_186b),
    0x39D4: ("lifted_hud_blit_finalize_39D4", _lifted_39d4),
    0x2D1F: ("lifted_road_render_driver_2D1F", _lifted_2d1f),
    0x5D80: ("lifted_shl32_5D80", _lifted_5d80),
    0x0BE9: ("lifted_project_row_0BE9", _lifted_0be9),
    0x0BAF: ("lifted_bounds_check_0BAF", _lifted_0baf),
}
