"""Replacement hooks: thin VM adapters over pure recovered rules.

A hook only: (1) reads relevant state from original memory/registers, (2)
calls a clean recovered function that knows nothing about the CPU, (3) writes
the result back, (4) reproduces the exact return mechanics. No logic
accumulates here — see docs/hooks_and_verification.md and pitfall #3.

Installed hooks:

- palette_fade_inner (1010:43A9): verified 2026-07-08 — 34,439 hook calls
  (~45 full 768-byte passes, including many pass-boundary transitions) with
  zero divergence under dos_re.verification's strict differential verifier
  (full-memory diff, auto-continuation). See docs/skyroads/symbol_ledger.md.

- lzs_decode_loop (1010:6712): verified 2026-07-09 — decodes an entire LZS
  block in one Python call instead of one interpreted iteration per symbol.
  15 hook calls verified with zero divergence (full-memory diff, strict
  auto-continuation), covering all 9 records of TREKDAT.LZS plus MUZAX.LZS
  and INTRO.LZS (a file with a different WIDTH_DIST_LONG, which is what
  caught the short-distance formula bug below). Getting here required
  reconstructing the staging-buffer refill mechanism in full (file position,
  buffer end/cursor, EOF short-read chunk-tail bleed-through, the [41B8]
  request-size field) and per-symbol scratch-register reconstruction
  (AX/BX/CX/DX/SI/FLAGS) precise enough to satisfy a full-memory diff, not
  just matching decoded output. See skyroads/codecs/lzs.py for the recovered
  algorithm and evidence trail.

- palette_upload (1010:6168): verified 2026-07-09 — 6,305 hook calls with
  zero divergence (full-memory diff, strict auto-continuation) replaying the
  ENTIRE recorded gameplay demo (artifacts/demos/demo_skyroads_20260709_184949,
  988 frames, level-select through driving), not just a slice. The single
  hottest un-hooked routine in that demo's profile (tools/profile_demo.py):
  20.9% of all interpreted instructions. Getting a clean full-memory diff
  required discovering the routine's own register/flags footprint is NOT the
  no-op its pusha/popa wrapper first suggested — its prologue's `mov bx,sp`
  runs BEFORE that pusha, so popa restores the entry stack-pointer value into
  BX, not the caller's real BX; and the final popf restores whatever the
  LAST pushf captured (paired with the last vsync-wait's own `TEST AL,8`),
  not the caller's original flags. Both found via a real ASM oracle
  divergence, not guessed in advance.

- sprite_blit (1010:3A22): verified 2026-07-09 — 1,806 hook calls with zero
  divergence (full-memory diff, strict auto-continuation) replaying the
  ENTIRE recorded gameplay demo (artifacts/demos/demo_skyroads_20260709_184949,
  988 frames). The second-hottest un-hooked routine in that demo's profile:
  12.8% of all interpreted instructions. First attempt matched the oracle
  immediately (no divergence) — the register/flags derivation (DEC preserves
  CF, matching CPU8086's own INC/DEC opcode handlers; the do-while loop
  shape; AL only updated on an actual mask==2 copy) was worked out from
  static disassembly up front, informed by the register/flags mistakes the
  palette_upload verifier had just caught the same session.

- road_object_visible (1010:1732): verified 2026-07-10 — the renderer-island
  LAYER-2 ROOT: the per-segment cull that ties 04C0 (perspective) and 1631
  (clip) together. All 12,152 in-game calls verified byte-exact, zero
  divergence. Hooking it collapses its FOUR nested 04C0 calls plus the cull
  glue (~28% of real render work) into one Python call (renderer.py::
  road_object_visible for the decision; the hook re-walks the flow only to
  reproduce exit BX/CX/DX — inherited from whichever nested 04C0/1631 call was
  last on the taken path — and each path's final-compare flags). Verified
  first try, including the deep two-sided-clip paths, because the exit-state
  model reused the already-known 04C0 exit regs and a reconstruction of 1631's
  (bx=seg*2 on table cases else caller bx; cx=128; dx=(coord-0x2200)%128).

- perspective_transform (1010:04C0): verified 2026-07-09 — the KEYSTONE of the
  renderer island and the first layer wired to a clean recovered-code module
  (skyroads/recovered/renderer.py::perspective_row_offset). Every road/object
  render path funnels through it. All 34,786 calls verified across the in-game
  demo, zero divergence. The recovery caught a real decode error: the third
  arithmetic stage calls ulong_MUL (5D4C, ×14), not ulong_div — I initially
  read the call target as 5D8C; the differential verifier flagged the wrong
  table offset on call #1, and capturing the ASM's intermediate divide results
  pinned it to a multiply (d2=3 -> 42 = 3*14, not 3/14).

- road_column_strip (1010:38BF): verified 2026-07-10 — the road-column strip
  compositor, the single most-called rasterizer in gameplay (34 callsites,
  ~13% of real render work). All 14,896 calls verified byte-exact across the
  in-game demo, zero divergence (full-memory diff, strict auto-continuation).
  Scans two stride-3 display lists for the column marker, then rep-movsw's
  word-aligned horizontal pixel runs from a source bitmap onto the screen,
  one scanline per record, with cld/std (up/down) direction variants. The
  verifier drove out three decode errors: (1) AX at exit = the source segment
  [0E66] (never assigned in the first cut); (2) the bit15-set path still
  composites — it only skips the pre-skip loop; (3) the 38EA `mov al,0xFF`
  marker load leaves AX=(mul_hi<<8)|0xFF on the early-exit path, and DX there
  is bx-after-the-first-scan (mov dx,bx); (4) the down variant's rep movsw
  DECREMENTS si/di (std) — an increment-only loop matched 1-word copies by
  luck but would corrupt multi-word down runs.

- rle_sprite_forward (1010:3153) + rle_sprite_backward (1010:3190): verified
  2026-07-09 — the mirror pair of run-length sprite rasterizers, the dominant
  render cost in the in-game demo (~9K calls each, ~13% of all interpreted
  steps together). Each decodes an RLE control stream into horizontal runs of
  one fill colour, one run per successive scanline (the forward twin painting
  rightward from sub-offset anchors, the backward twin painting leftward with
  `std`). Both matched the oracle on the first attempt (full-demo, zero
  divergence). The one subtlety was the backward twin's reverse `rep stosw`
  fill and its `cld`-restored DF; see the hook comments.

- ulong_mul (1010:5D4C): verified 2026-07-09 — the C-runtime 32-bit unsigned
  long-MULTIPLY helper, companion to ulong_div and equally hot in real
  gameplay (~37K calls in the in-game driving demo, the fixed-point 3D
  transform math). Same hook-the-common-case pattern: the 16x16 simple path
  (both operand high words == 0, 99.7% of calls) reproduced exactly
  (DX:AX=product, BX=B_low, flags from the `or bx,ax`+`mul bx` pair), the
  true 32x32 path (0.3%) delegated to the original ASM. Verified with a
  3,024-call in-game differential sample (zero divergence) plus a unit test
  in tests/test_ulong_div_hook.py.

- ulong_div (1010:5D8C): verified 2026-07-09 — the C-runtime 32-bit unsigned
  long-division helper (~68K calls in the demo, almost certainly the 3D road
  renderer's perspective divide). Hooks the hot common case only (divisor's
  high word == 0, a 32/16 divide: 99.8% of calls) exactly — quotient in DX:AX,
  CX=divisor_lo, flags from the routine's own `xor dx,dx` — and DELEGATES the
  rare true 32/32 path (0.2%) and the never-observed divide-by-zero to the
  original ASM via interpret_current_instruction_without_hook, correct by
  construction. Because it's called so often, verification used a proportionate
  3,076-call sample (both paths, zero divergence) rather than all ~135K calls
  (a full strict verify would clone/diff the 1MB image 135K times — hours), plus
  a self-contained unit test (tests/test_ulong_div_hook.py) pinning the
  simple-path arithmetic/flags/stack contract.

- occluded_column_blit (1010:3283): verified 2026-07-09 — 358 hook calls
  with zero divergence (full-memory diff, strict auto-continuation) across
  the ENTIRE recorded gameplay demo. A column-major stencil-limited
  compositor (29x24), the largest un-hooked interpreted loop remaining after
  the three hooks above. One bug caught by the verifier on the first run: I
  read the stencil test backwards — the ASM `cmp mask,0; jz skip` draws only
  where the stencil is NON-zero (a permit-mask that later passes stamp to 2),
  not where it's zero; the full-memory diff flagged it instantly (stencil/
  framebuffer bytes 02-vs-01 in the wrong slots). Fixed and re-verified clean.

- fade_loop_tick_gate (1010:4344 + 1010:434A): added 2026-07-09 — NOT a thin
  representational hook like the ones above; see its own docstring for why
  this one is a genuine BEHAVIORAL change (skips provably-redundant work)
  verified by a different methodology (full-demo bit-exact final-state
  comparison, not per-call oracle diff — the whole point is to diverge from
  what the oracle would do). Cuts the fade-transition busy-wait's redundant
  iterations, which palette_upload's own speedup had made worse (see
  docs/skyroads/symbol_ledger.md's palette_upload caveat) by freeing enough
  step budget for it to spin far more before its wait condition changes.
"""
from __future__ import annotations

from dos_re.cpu import CPU8086, CF, OF, DF
from dos_re.hooks import interpret_current_instruction_without_hook, registry

from skyroads.codecs.lzs import LzsWidths
from skyroads.recovered.palette_fade import blend_byte
from skyroads.recovered.renderer import perspective_row_offset, road_segment_clip

CODE_SEG = 0x1010  # SKYROADS.EXE's single code segment (from program.entry_cs)

# ds-relative offsets of the LZS bit-reader's persistent state (1010:64AB-6350,
# see skyroads/codecs/lzs.py for the full trace evidence).
_LZS_CUR_BYTE = 0x41B0      # byte  — the working byte currently being shifted out
_LZS_BITS_LEFT = 0x41AE     # word  — bits remaining in _LZS_CUR_BYTE (0-8)
_LZS_BUF_START = 0x41B2     # word  — staging buffer base offset (constant, 0x31A8)
_LZS_BUF_END = 0x41B4       # word  — staging buffer end offset (start + last refill's byte count)
_LZS_BUF_CURSOR = 0x41B6    # word  — next byte to fetch from the staging buffer
_LZS_FILE_HANDLE = 0x41AC   # word  — DOS handle the refill routine (1010:6350) reads from
_LZS_LAST_REFILL_LEN = 0x41B8  # word — bytes actually read by the most recent refill (1010:6361 "mov [41B8],ax")
# cs-relative addresses of the header-derived width immediates (self-modifying
# "push imm16" operands, patched once per block at 1010:66F2-670E).
_LZS_WIDTH_LEN_ADDR = 0x6729
_LZS_WIDTH_DIST_LONG_ADDR = 0x671F
_LZS_WIDTH_DIST_SHORT_ADDR = 0x674C
_LZS_LOOP_EXIT_IP = 0x6760  # 1010:6715 "jnb" target when di >= ss:[bp+8]


def _divmod_trunc(prod: int) -> tuple[int, int]:
    """x86 IDIV semantics: quotient truncates toward zero, remainder takes
    the dividend's sign (unlike Python's floor-based // and %)."""
    if prod < 0:
        q = -((-prod) // 100)
    else:
        q = prod // 100
    r = prod - q * 100
    return q & 0xFFFF, r & 0xFFFF


# CS:IP 1010:43A9 — the hot inner loop of the palette-fade interpolation
# (see docs/skyroads/symbol_ledger.md "Palette-fade interpolation"). One hook
# call = one iteration (one output byte), matching the ASM's own per-byte
# loop body exactly so it can be verified with dos_re.verification's
# differential hook verifier at the SAME granularity the ASM naturally
# re-enters this address. The OUTER function at 4331 re-runs this inner pass
# once per real elapsed tick to animate the fade over time; hooking only the
# inner loop (not 4331 wholesale) preserves that pacing exactly while
# removing its ~20-interpreted-instruction-per-byte cost.
#
# Stack frame (bp set up by the enclosing 4331 function's `enter 0x16,0`):
# bp-2=loop index i, bp-4=percent (0-100, computed once per outer pass by
# 4331, unchanged across inner-loop calls), bp-6/bp-8=srcB (segment/offset),
# bp-10/bp-12=srcA (segment/offset), bp-14=dest offset (fixed scratch buffer,
# segment=DS), bp+4=ptr to a small struct whose word+4 is the palette entry
# count (x3 = byte bound). bp-16/-18/-20/-22 are pure scratch the ASM reuses
# within one iteration and never reads across iterations — replicated here
# only so a full-memory verify diff matches, not because they carry state.
def _palette_fade_inner_hook(cpu: CPU8086) -> None:
    s = cpu.s
    ss, bp, ds = s.ss, s.bp, s.ds
    mem = cpu.mem

    i = mem.rw(ss, (bp - 2) & 0xFFFF)
    new_i = (i + 1) & 0xFFFF
    mem.ww(ss, (bp - 2) & 0xFFFF, new_i)
    mem.ww(ss, (bp - 16) & 0xFFFF, i)

    dest_off = mem.rw(ss, (bp - 14) & 0xFFFF)
    new_dest_off = (dest_off + 1) & 0xFFFF
    mem.ww(ss, (bp - 14) & 0xFFFF, new_dest_off)
    mem.ww(ss, (bp - 18) & 0xFFFF, dest_off)

    src_b_off = mem.rw(ss, (bp - 8) & 0xFFFF)
    src_b_seg = mem.rw(ss, (bp - 6) & 0xFFFF)
    new_src_b_off = (src_b_off + 1) & 0xFFFF
    mem.ww(ss, (bp - 8) & 0xFFFF, new_src_b_off)
    mem.ww(ss, (bp - 22) & 0xFFFF, src_b_off)
    mem.ww(ss, (bp - 20) & 0xFFFF, src_b_seg)

    src_a_off = mem.rw(ss, (bp - 12) & 0xFFFF)
    src_a_seg = mem.rw(ss, (bp - 10) & 0xFFFF)
    new_src_a_off = (src_a_off + 1) & 0xFFFF
    mem.ww(ss, (bp - 12) & 0xFFFF, new_src_a_off)

    mem.ww(ss, (bp - 16) & 0xFFFF, new_i)

    struct_ptr = mem.rw(ss, (bp + 4) & 0xFFFF)
    count = mem.rw(ds, (struct_ptr + 4) & 0xFFFF)
    bound = (3 * count) & 0xFFFF

    if new_i >= bound:  # unsigned JB not taken -> falls straight through to 43F1: jmp 4430
        s.ax, s.cx, s.bx = bound, new_i, struct_ptr  # matches 43E1/43EA/43E4 — this exit
        s.es = src_a_seg                             # never reaches 43F4/43FD/4417 (those
        s.ip = 0x4430                                # come AFTER the bound check); es stays
        return                                        # at 43D2's `mov es,[bp-10]` = src_a_seg

    byte_b = mem.rb(src_b_seg, new_src_b_off)
    byte_a = mem.rb(src_a_seg, new_src_a_off)
    mem.ww(ss, (bp - 16) & 0xFFFF, byte_b)
    # LES loads BOTH bx and es from the far pointer — not just bx. 43F4, 43FD
    # and 4417 are all `les bx,[...]`; the LAST one (4417, re-fetching srcB)
    # is what determines the final es reaching the 43A9 continuation, not
    # 43D2's plain `mov es,[bp-10]` (a first attempt at this missed that LES
    # touches es at all three sites, not just the mov at 43C0/43D2).
    s.es = src_b_seg

    percent = mem.rw(ss, (bp - 4) & 0xFFFF)
    divided, remainder = _divmod_trunc((byte_a - byte_b) * percent)
    mem.ww(ss, (bp - 18) & 0xFFFF, divided)

    result = blend_byte(byte_a, byte_b, percent)
    mem.wb(ds, new_dest_off, result)

    # Registers left live at the continuation (43A9), matching the ASM's own
    # final touches: 4428 bx=dest_off, 4423 cx=divided, 4426 ax=byte_b+divided
    # (16-bit, only its low byte was stored); dx last set by 4415's `idiv cx`,
    # which leaves the REMAINDER in dx (not touched again before 442D). FLAGS:
    # idiv doesn't define them (confirmed from dos_re/cpu.py's IDIV, no
    # set_flag calls); the true last flag-setting instruction is 4426's
    # `add ax,cx` (byte_b+divided) — NOT 440C's earlier `sub ax,cx` (a first
    # attempt at this wrongly assumed sub was last; verified wrong via a
    # register-value trace showing 4426 changes flags after 440C already ran).
    ax_final = (byte_b + divided) & 0xFFFF
    cpu.set_add_flags(byte_b, divided, byte_b + divided, 16)
    s.ax = ax_final
    s.bx = new_dest_off
    s.cx = divided
    s.dx = remainder
    s.ip = 0x43A9


@registry.replace(CODE_SEG, 0x43A9, "palette_fade_inner")
def palette_fade_inner_hook(cpu: CPU8086) -> None:
    _palette_fade_inner_hook(cpu)


# CS:IP 1010:6712 — top of the LZS main decode loop (per-symbol dispatch +
# the "cmp di,ss:[bp+8] / jnb <exit>" bound check). Decodes the ENTIRE
# remainder of the current block in one call instead of re-entering this
# address once per symbol (thousands of times per block); this is safe
# because 6712 is only ever re-visited fresh, with a new bp frame, once the
# CURRENT block is fully consumed — a mid-block re-entry from this hook's own
# jump to the exit target (6760) never happens.
#
# The bit-reader's persistent state (_LZS_* ds-relative fields) is read to
# resume mid-stream exactly where the ASM left off, then written back so any
# LATER block's own header-parse/refill continues correctly. Rather than
# replaying the real 4KB staging-buffer refill protocol, this reconstructs
# the ABSOLUTE file offset of the byte currently loaded (buffer start/end are
# staging-buffer-relative; DOSMachine.FileHandle already holds the file's
# full bytes, so once the absolute offset is known there is nothing left to
# "refill" — the recovered decoder just keeps reading from that same buffer).
# See skyroads/codecs/lzs.py's module docstring for the full evidence trail
# (in particular the 1010:6350 disassembly that identified _LZS_FILE_HANDLE
# and the buffer start/end/cursor roles).
def _lzs_decode_loop_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ds = s.ds

    end_di = mem.rw(s.ss, (s.bp + 8) & 0xFFFF)
    cur_di = s.di
    remaining = (end_di - cur_di) & 0xFFFF
    # The real loop-top check (1010:6712 "cmp di,ss:[bp+8]") is an UNSIGNED
    # compare, so the only well-formed "already done" case is exact equality
    # — an earlier version also bailed out on remaining >= 0x8000 as a
    # defensive guard against "negative delta wrapped to a huge unsigned
    # value", but that's indistinguishable from a genuinely large block
    # (found via a real block needing ~64000 output bytes, comfortably over
    # that threshold, silently skipped entirely instead of decoded).
    if remaining == 0:
        s.ip = _LZS_LOOP_EXIT_IP
        return

    width_len = mem.rb(CODE_SEG, _LZS_WIDTH_LEN_ADDR)
    width_dist_long = mem.rb(CODE_SEG, _LZS_WIDTH_DIST_LONG_ADDR)
    width_dist_short = mem.rb(CODE_SEG, _LZS_WIDTH_DIST_SHORT_ADDR)
    widths = LzsWidths(width_len, width_dist_long, width_dist_short)

    handle = mem.rw(ds, _LZS_FILE_HANDLE)
    dos = cpu.interrupt_handler.__self__  # cpu.interrupt_handler is dos.interrupt (bound)
    fh = dos.files[handle]

    buf_start = mem.rw(ds, _LZS_BUF_START)
    buf_end = mem.rw(ds, _LZS_BUF_END)
    cursor = mem.rw(ds, _LZS_BUF_CURSOR)
    loaded_this_refill = buf_end - buf_start
    buf_file_start = fh.pos - loaded_this_refill
    # cursor always points one PAST the byte currently loaded into
    # _LZS_CUR_BYTE (the refill routine fetches that byte immediately after
    # resetting the cursor, see 1010:638F-6399) — confirmed by reconstructing
    # this exact offset for two consecutive TREKDAT.LZS records and finding
    # the byte there matches _LZS_CUR_BYTE precisely both times.
    abs_pos = buf_file_start + (cursor - buf_start) - 1

    cur_byte = mem.rb(ds, _LZS_CUR_BYTE)
    bits_left = mem.rw(ds, _LZS_BITS_LEFT)
    data = fh.data
    file_pos = abs_pos + 1  # next unread byte

    # BX tracking (see the writeback comment near the end for the full
    # rationale): get_bits(n) itself does "mov bx,sp" at its own entry
    # (1010:64FF), unconditionally clobbering BX with a STACK address before
    # it does anything else — so BX's final value depends only on the LAST
    # width-consuming get_bits(n) call of the LAST symbol: either that call's
    # own internal bit-loop triggered at least one byte-boundary refetch
    # (in which case BX ends up cursor/byte-blended, see below), or it didn't
    # and BX is simply left at that call's own entry SP value. Standalone
    # get_bit() (used only for the b1/b2 flag bits) does NOT touch SP — any
    # refetch it triggers is superseded by the NEXT get_bits(n) call's own
    # unconditional "mov bx,sp" regardless.
    entry_sp = s.sp
    call_refetch_pos = None  # reset before each "final" get_bits(n) call, see below

    def get_bit() -> int:
        nonlocal cur_byte, bits_left, file_pos, call_refetch_pos
        bit = (cur_byte >> 7) & 1
        cur_byte = (cur_byte << 1) & 0xFF
        bits_left -= 1
        if bits_left == 0:
            bits_left = 8
            # 1010:64BF/651C "mov bx,[41B6]" reads the cursor BEFORE it
            # advances, then (no-refill-needed case) "mov bl,[bx]" fetches
            # the byte into BX's LOW half only — see the writeback comment.
            call_refetch_pos = file_pos
            cur_byte = data[file_pos] if file_pos < len(data) else 0
            file_pos += 1
        return bit

    def get_bits(n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | get_bit()
        return value

    # Scratch-register end state after the LAST symbol, matching exactly what
    # a real trace leaves at the loop-exit JNB (1010:6715) so a full-memory/
    # register differential verifies clean, not just the decoded bytes. Per
    # 1010:64AB's disassembly, get_bit() does "xor dx,dx; rcl [41B0],1;
    # rcl dx,1" — DX is reset to 0 on EVERY call and only ever accumulates
    # the single bit just read, so after any get_bits(n) call DX always ends
    # up holding just the LAST bit consumed (== raw_value & 1, since MSB-
    # first accumulation puts the last bit in the LSB), regardless of n:
    #  - a LITERAL exit (675D "stosb") never touches SI, so it carries over
    #    from whatever the PREVIOUS symbol left (or the caller's own value,
    #    if this block is all-literal so far); AX = the literal byte itself;
    #    CX = 0xFFFF (get_bits' internal bit-loop counter, one past its last
    #    decrement, untouched by stosb); DX = literal & 1 (last bit of the
    #    get_bits(8) call, per above).
    #  - a MATCH exit runs the shared copy tail (6730-6742): CX always ends
    #    at 0 (the copy LOOP always decrements to exactly 0 before the extra
    #    unconditional movsb); DX = ss:[bp+8] - di_before_this_symbol (the
    #    remaining-space bounds check at 6733-6736 OVERWRITES get_bits'
    #    last-bit value and is never touched again); SI = the final source
    #    pointer, one past the last byte copied (plain MOVSB auto-
    #    increment); AX = the raw get_bits(WIDTH_LEN) value (length - 2,
    #    since 6730 "mov cx,ax" copies it out before CX is reused, and AX
    #    itself is never touched again).
    last_ax, last_cx, last_dx, last_si = s.ax, 0xFFFF, s.dx, s.si
    # BX's eventual value depends on the LAST width-consuming get_bits(n)
    # call only (see above) — its own entry SP, per the exact push/call
    # depth at that point (matches the disassembly of 671E-6742/674B-6753):
    # long/short match distance calls push 1 word (entry_sp-4 at "mov
    # bx,sp"), but that push is NOT cleaned up before the length call's own
    # push (the cleanup "add sp,4"/"add sp,2" happens only after BOTH), so
    # the length call always sees entry_sp-6; a literal's get_bits(8) pushes
    # just the one width byte, entry_sp-4.
    bx_call_refetch_pos = None
    bx_sp_baseline = entry_sp

    es = s.es
    di = cur_di
    while di != end_di:
        di_before = di
        if get_bit() == 0:
            distance = get_bits(widths.width_dist_long) + 2
        else:
            if get_bit() == 1:
                call_refetch_pos = None
                literal = get_bits(8)
                bx_call_refetch_pos, bx_sp_baseline = call_refetch_pos, (entry_sp - 4) & 0xFFFF
                mem.wb(es, di, literal)
                di = (di + 1) & 0xFFFF
                last_ax, last_cx, last_dx = literal, 0xFFFF, literal & 1
                continue
            distance = get_bits(widths.width_dist_short) + (1 << widths.width_dist_long) + 2
        call_refetch_pos = None
        raw_length = get_bits(widths.width_len)
        bx_call_refetch_pos, bx_sp_baseline = call_refetch_pos, (entry_sp - 6) & 0xFFFF
        length = raw_length + 2
        src = (di - distance) & 0xFFFF
        for _ in range(length):
            mem.wb(es, di, mem.rb(es, src))
            di = (di + 1) & 0xFFFF
            src = (src + 1) & 0xFFFF
        last_ax, last_cx, last_dx, last_si = raw_length, 0, (end_di - di_before) & 0xFFFF, src

    # Write the bit-reader state back so a later block's header-parse/refill
    # continues correctly. The real ASM only touches the DOS file position
    # and reloads the staging buffer when the bitstream actually crosses out
    # of the currently-loaded chunk — simulate that precisely (rather than
    # unconditionally forcing "buffer empty") so cursor/end/file-position
    # match a real trace exactly, not just "functionally work": a strict
    # full-memory diff caught the original always-force-refill version
    # leaving ds:[41B6] and file[handle].pos wrong on a call that never
    # actually needed a refill. Chunks are simulated as a list (not just the
    # final one) because BX (below) needs to know which chunk the SECOND-TO
    # -LAST byte-boundary fetch belonged to, which may differ from the final
    # chunk if a crossing happened on the very last fetch.
    chunks = [(buf_file_start, loaded_this_refill)]  # (chunk_start_abs, chunk_len)
    pos = buf_file_start + loaded_this_refill
    while pos < file_pos:
        chunk_len = min(loaded_this_refill, len(data) - pos)
        if chunk_len <= 0:
            break
        chunks.append((pos, chunk_len))
        pos += chunk_len
    final_chunk_start, final_chunk_len = chunks[-1]
    new_cursor = buf_start + (file_pos - final_chunk_start)
    if len(chunks) > 1:
        mem.ww(ds, _LZS_BUF_END, (buf_start + final_chunk_len) & 0xFFFF)
        # [41B8] holds the REQUESTED chunk size, not the actual (possibly
        # short, at EOF) bytes returned — confirmed by a real EOF-adjacent
        # record where buf_end correctly reflected the clipped 3940 bytes
        # while [41B8] still read 4096 (the constant per-refill request
        # size, same as `loaded_this_refill`). Equal to final_chunk_len in
        # every non-EOF case, so this only changes behavior at EOF.
        mem.ww(ds, _LZS_LAST_REFILL_LEN, loaded_this_refill & 0xFFFF)
        fh.pos = final_chunk_start + final_chunk_len
        # A real refill copies the fresh chunk's bytes into the staging
        # buffer itself (1010:6350 -> ... -> INT21h AH=3Fh writes to
        # ds:[buf_start..buf_start+len)) — this shortcut never "loads" that
        # memory since it reads straight from FileHandle.data, so without
        # this the buffer keeps STALE content from whatever was there
        # before, caught by a strict full-memory diff (only the final
        # chunk matters: any earlier one in `chunks` got overwritten again
        # before this hook call ends, so the diff never sees it) — EXCEPT
        # when the final chunk is a SHORT read (EOF): a real refill only
        # overwrites however many bytes it actually reads, so the buffer's
        # remaining capacity keeps showing the PREVIOUS chunk's own trailing
        # bytes underneath. Found via a real EOF-adjacent record (TREKDAT
        # .LZS's last record) where the divergent 158 bytes turned out to be
        # exactly the prior chunk's own tail, not stale pre-hook-call memory.
        if final_chunk_len < loaded_this_refill and len(chunks) >= 2:
            prev_start, prev_len = chunks[-2]
            for i, b in enumerate(data[prev_start:prev_start + prev_len]):
                mem.wb(ds, (buf_start + i) & 0xFFFF, b)
        for i, b in enumerate(data[final_chunk_start:final_chunk_start + final_chunk_len]):
            mem.wb(ds, (buf_start + i) & 0xFFFF, b)
    # else: buf_end/fh.pos/buffer memory unchanged — no real refill happened this call.

    mem.wb(ds, _LZS_CUR_BYTE, cur_byte)
    mem.ww(ds, _LZS_BITS_LEFT, bits_left)
    mem.ww(ds, _LZS_BUF_CURSOR, new_cursor & 0xFFFF)

    # BX: the LAST width-consuming get_bits(n) call's own "mov bx,sp"
    # (1010:64FF) gets overridden only if THAT call's own bit-loop triggered
    # a byte-boundary refetch — confirmed via a full ASM oracle trace
    # (OK_TRACE_HOOK): first attempt modeled BX purely from the whole
    # decode's last refetch, which is wrong whenever a later get_bits(n)
    # call executes without itself needing a refetch (it still clobbers BX
    # via its own "mov bx,sp" first thing, discarding any earlier refetch's
    # cursor value). When a refetch DID happen, only BX's LOW byte carries
    # the fetched byte's VALUE (the earlier "mov bl,[bx]" finding) — the
    # high byte is the cursor position's own high byte.
    if bx_call_refetch_pos is None:
        s.bx = bx_sp_baseline
    else:
        fetch_chunk_start = buf_file_start
        for c_start, c_len in chunks:
            if c_start <= bx_call_refetch_pos < c_start + c_len:
                fetch_chunk_start = c_start
                break
        cursor_at_fetch = buf_start + (bx_call_refetch_pos - fetch_chunk_start)
        fetched_byte = data[bx_call_refetch_pos] if bx_call_refetch_pos < len(data) else 0
        s.bx = ((cursor_at_fetch & 0xFF00) | fetched_byte) & 0xFFFF

    s.ax = last_ax & 0xFFFF
    s.cx = last_cx & 0xFFFF
    s.dx = last_dx & 0xFFFF
    s.si = last_si & 0xFFFF
    s.di = end_di
    # Flags as left by the real "cmp di,ss:[bp+8]" (1010:6712) that decides
    # loop exit — di == end_di here, so this is a CMP of equal 16-bit values.
    cpu.set_sub_flags(end_di, end_di, 0, 16)
    s.ip = _LZS_LOOP_EXIT_IP


@registry.replace(CODE_SEG, 0x6712, "lzs_decode_loop")
def lzs_decode_loop_hook(cpu: CPU8086) -> None:
    _lzs_decode_loop_hook(cpu)


# CS:IP 1010:6168 - VGA DAC palette-block upload (raw port I/O, VGA path) with a
# BIOS INT 10h AH=10h/AL=12h fallback for non-VGA-direct systems. Near proc,
# caller-cleanup convention (args read via `mov bx,sp` before any push, so the
# final `ret` alone -- no immediate -- pops just the 2-byte return address;
# the caller does `add sp,8` afterward to drop its 3 pushed args). Stack at
# entry: [sp]=return IP, [sp+2]=start index (word), [sp+4]=count (word),
# [sp+6]=far ptr offset, [sp+8]=far ptr segment (the LDS at 1010:6170 loads
# offset then segment, the standard far-pointer layout).
#
# The whole body is wrapped in `push ds; push es; pusha ... popa; pop es; pop
# ds; ret` (1010:616A-616C / 61E0-61E3), so from the CALLER's point of view
# EVERY general/segment register and FLAGS comes back exactly as it went in --
# confirmed by walking the full disassembly (1010:6168-61E3): the only
# CLI/pushf/popf pair (1010:61C6-61C9 paired with 61B9's popf, one per
# "blank the screen for a retrace-synced burst" cycle) is fully balanced
# across the routine's own lifetime, and dos_re's interrupt delivery model
# (dos_re.interrupts.deliver_interrupt) only ever runs between whole
# cpu.step() calls, never mid-hook, so CLI/STI has no observable effect here
# to replicate. Hooking this whole procedure -- not just its inner loop, like
# palette_fade_inner/lzs_decode_loop -- is what lets register/flags
# preservation collapse to "just don't touch them".
#
# The VGA-direct path (1010:618D-61CD) burns 20.9% of all interpreted
# instructions in a recorded gameplay demo (see tools/profile_demo.py):
# ~18 interpreted instructions per DAC-entry write (mov/lodsb/2x delay-jmp/
# inc/dec/test/jnz), called with count~256-320 roughly once per frame. Every
# 64th entry (1010:61A5 "test cx,3Fh") it also polls the input-status
# register (03DAh, "wait for vertical retrace") and toggles VGA sequencer
# register 1 bit 5 (Screen Off/On, ports 03C4h/03C5h) around the burst to
# avoid DAC-write snow -- both are pure hardware-port side effects with no
# CPU-register footprint, so they're replicated via the SAME cpu.port_reader/
# cpu.port_writer calls the interpreter would make (reusing dos_re.dos
# .DOSMachine's real state machine for vga_status_reads/vga_palette/
# _seq_regs) rather than hand-duplicated formulas.
#
# The color-write loop is a do-while (1010:618B jumps straight into the loop
# BODY, 618D, before any count check) not a while: a real count=0 call would
# still write one entry and then run cx=0 wrapped to 0xFFFF, matched here by
# structuring the Python loop the same "body first, decrement-and-check
# after" way rather than a pre-checked `while remaining:` (which would
# silently skip a count=0 call instead of replicating the wraparound).
_PALETTE_UPLOAD_BIOS_FLAG_ADDR = 0x0489  # BDA byte tested at 1010:617B ("test es:[0489],6h", es=0000)


def _palette_upload_blank_toggle(cpu: CPU8086, ah: int) -> None:
    """1010:6151-6167: AND/OR VGA sequencer reg 1 (Clocking Mode) bit 5.

    ah=0x00 clears it (screen on); ah=0x20 sets it (screen off/blanked)."""
    cpu.port_writer(cpu, 0x03C4, 0x01, 8)
    current = cpu.port_reader(cpu, 0x03C5, 8)
    cpu.port_writer(cpu, 0x03C5, (current & 0xDF) | (ah & 0xFF), 8)


def _palette_upload_wait_retrace_start(cpu: CPU8086, status_port: int) -> int:
    """1010:61BA-61C5: `loope` polling status_port bit 3, capped at 0xFFFF tries.

    Returns the LAST byte read from status_port -- the operand of the ASM's
    own final `TEST AL,8h` in this loop, which matters because that TEST is
    the last flag-setting instruction before this routine's next `pushf`
    (1010:61C6): the whole call's FINAL flags always come from whichever
    TEST/loop iteration was the most recent one across every dance this call
    performs (see the writeback comment in _palette_upload_hook)."""
    tries = 0xFFFF
    al = cpu.port_reader(cpu, status_port, 8)
    while not (al & 0x08):
        tries = (tries - 1) & 0xFFFF
        if tries == 0:
            break
        al = cpu.port_reader(cpu, status_port, 8)
    return al


def _palette_upload_retrace_dance(cpu: CPU8086, status_port: int, last_al: int) -> int:
    """1010:61AD-61CA: pause the burst for one vertical retrace, screen blanked.

    If already mid-retrace (61B0-61B2), skip straight back to writing (no port
    I/O beyond the one probe read, and no pushf) -- matches the ASM's own fast
    path, and ``last_al`` (the pending flags source) carries over unchanged."""
    if cpu.port_reader(cpu, status_port, 8) & 0x08:
        return last_al
    _palette_upload_blank_toggle(cpu, 0x00)
    last_al = _palette_upload_wait_retrace_start(cpu, status_port)
    _palette_upload_blank_toggle(cpu, 0x20)
    return last_al


def _palette_upload_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp

    ret_ip = mem.rw(ss, sp)
    start = mem.rw(ss, (sp + 2) & 0xFFFF)
    count = mem.rw(ss, (sp + 4) & 0xFFFF)
    far_off = mem.rw(ss, (sp + 6) & 0xFFFF)
    far_seg = mem.rw(ss, (sp + 8) & 0xFFFF)

    bios_flag = mem.rb(0, _PALETTE_UPLOAD_BIOS_FLAG_ADDR)
    if bios_flag & 0x06:
        # 1010:61D7-61DF: BIOS AH=10h AL=12h fallback (ES:DX -> table, BX/CX
        # unchanged). Registers are restored below regardless of this branch,
        # so it's fine to let the real BIOS handler touch cpu.s here.
        saved_ax, saved_bx, saved_cx, saved_dx, saved_es = s.ax, s.bx, s.cx, s.dx, s.es
        s.ax, s.bx, s.cx, s.dx, s.es = 0x1012, start, count, far_off, far_seg
        cpu.interrupt_handler(cpu, 0x10)
        s.ax, s.bx, s.cx, s.dx, s.es = saved_ax, saved_bx, saved_cx, saved_dx, saved_es
    else:
        crtc_base = mem.rw(0, 0x0463)
        status_port = (crtc_base + 6) & 0xFFFF
        last_al = _palette_upload_wait_retrace_start(cpu, status_port)
        _palette_upload_blank_toggle(cpu, 0x20)

        bx = start & 0xFFFF
        off = far_off & 0xFFFF
        remaining = count & 0xFFFF
        while True:
            cpu.port_writer(cpu, 0x03C8, bx & 0xFF, 8)
            r = mem.rb(far_seg, off); off = (off + 1) & 0xFFFF
            cpu.port_writer(cpu, 0x03C9, r, 8)
            g = mem.rb(far_seg, off); off = (off + 1) & 0xFFFF
            cpu.port_writer(cpu, 0x03C9, g, 8)
            b = mem.rb(far_seg, off); off = (off + 1) & 0xFFFF
            cpu.port_writer(cpu, 0x03C9, b, 8)
            bx = (bx + 1) & 0xFFFF
            remaining = (remaining - 1) & 0xFFFF
            if remaining & 0x3F:
                continue
            if remaining == 0:
                break
            last_al = _palette_upload_retrace_dance(cpu, status_port, last_al)

        _palette_upload_blank_toggle(cpu, 0x00)

        # BX: 1010:6168's OWN "mov bx,sp" (before ANY push) makes bx equal the
        # entry SP, and the very next instruction (616C `pusha`) then pushes
        # THAT clobbered bx -- so `popa` at 61E0 does NOT restore the caller's
        # real bx, it restores this routine's own entry-sp snapshot. Found via
        # a real ASM oracle trace (OK_TRACE_HOOK) after a first attempt
        # (assuming pusha/popa made the whole routine a no-op on registers)
        # diverged with bx off by a small, call-depth-sized constant.
        # FLAGS: popf at 61D4 (the "done" exit) always restores whatever was
        # pushed by the LAST pushf (61C6), which sits right after that dance's
        # own wait-loop's LAST `TEST AL,8h` -- so final flags are exactly that
        # TEST's result, not "unchanged" (the routine's own blank-toggle calls
        # after the last pushf all get discarded by this popf).
        s.bx = sp
        cpu.set_logic_flags(last_al & 0x08, 8)

    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x6168, "palette_upload")
def palette_upload_hook(cpu: CPU8086) -> None:
    _palette_upload_hook(cpu)


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

    si = s.si
    bx = s.bx
    dx = s.dx & 0xFFFF
    di = si
    last_al = None  # AL is only ever written by an actual mask==2 copy

    while True:
        di = si
        for _ in range(_SPRITE_BLIT_WIDTH):
            if mem.rb(ss, bx) == 0x02:
                last_al = mem.rb(ds, si)
                mem.wb(es, di, last_al)
            si = (si + 1) & 0xFFFF
            di = (di + 1) & 0xFFFF
            bx = (bx + 1) & 0xFFFF
        si_before_add = si
        si = (si + _SPRITE_BLIT_ROW_SKIP) & 0xFFFF
        dx_before_dec = dx
        dx = (dx - 1) & 0xFFFF
        if dx == 0:
            break

    s.si = si
    s.di = di
    s.bx = bx
    s.dx = dx
    s.cx = 0
    if last_al is not None:
        s.ax = (s.ax & 0xFF00) | last_al

    cpu.set_add_flags(si_before_add, _SPRITE_BLIT_ROW_SKIP, si_before_add + _SPRITE_BLIT_ROW_SKIP, 16)
    dec_cf = cpu.get_flag(CF)
    cpu.set_sub_flags(dx_before_dec, 1, dx_before_dec - 1, 16)
    cpu.set_flag(CF, dec_cf)

    s.ip = mem.rw(ss, s.sp)
    s.sp = (s.sp + 2) & 0xFFFF


@registry.replace(CODE_SEG, 0x3A22, "sprite_blit")
def sprite_blit_hook(cpu: CPU8086) -> None:
    _sprite_blit_hook(cpu)


# CS:IP 1010:3283 -- column-major stencil-limited compositor. Entry into the
# routine is 1010:325B, which:
# saves bx/di/ds/es, sets DS=SS and ES=ss:[0E36] (the dest segment), calls
# 32C1 to CLEAR the 29x24 occlusion mask at ss:[0E86] to 0, computes a dest
# start offset into DI (from a Y position at ss:[0E2C], `imul 0x140`, plus
# ss:[0E28], minus 0x6E), and stores it at ss:[0E6C]. This hook takes over at
# 1010:3283 -- the `lds si,ss:[0E2E]` that loads the far SOURCE pointer, right
# before the double loop -- and runs the whole 3283-32B7 double loop in one
# call, landing at 1010:32B9 (the `call 33FD` post-step) with exact register/
# flag/memory state. 32C1 (mask clear) and 33FD run as real ASM around the
# hook, so neither needs to be understood here.
#
# Geometry: DX counts 0x1D (29) columns (outer), CX counts 0x18 (24) rows
# (inner). Per pixel: `al = source[si++]` (lodsb); if al==0 skip (transparent);
# else if `ss:[bx+0E86] == 0` skip (stencil forbids this slot); else stamp
# `ss:[bx+0E86]=2` and write `es:[di]=al`. So a pixel is drawn only where the
# source is opaque AND the stencil byte is non-zero -- a permit-mask, NOT an
# occlusion/z-buffer reject (the ASM is `cmp mask,0; jz skip`, i.e. skip on
# ZERO). Then di += 0x140 (down one screen row), bx += 0x1D (down one mask
# row). After each column: di -= 0x1DFF (== -(24*0x140 - 1): back to the top,
# one screen column right), bx -= 0x2B7 (== -(24*0x1D - 1): same for the
# mask). Net effect maps pixel (col c, row r) to dest offset di0 + r*0x140 + c
# (a normal row-major 29-wide x 24-tall screen rectangle) and mask index
# r*0x1D + c, while READING the source column-major (source index c*24 + r).
#
# The stencil shares sprite_blit's table (same ss:[0E86] base, same 0x1D
# width) -- a region set up by an earlier pass that this draw is clipped to,
# with each drawn slot converted to 2. Correctness does NOT depend on the
# routine's own setup (32C1) having any particular prior contents: the hook
# READS the live stencil byte-for-byte like the ASM, so whatever is there
# produces identical (matching) behavior.
#
# Register/flags at the 32B9 exit (mirrors the ASM's final instructions):
#   - CX = 0 (the inner `loop` always decrements to exactly 0 on exit).
#   - DX = 0 (the outer `dec dx; jnz` falls through only at dx==0).
#   - AX: AL = the LAST source byte read (lodsb runs every iteration incl.
#     transparent/occluded ones, so it's source[last] regardless); AH is
#     untouched by lodsb, so it keeps its entry value (high byte of the
#     `imul 0x140` product the setup left in AX).
#   - DI / BX: tracked iteratively (not closed-form) so 16-bit wrap matches.
#   - SI = source offset + 0x2B8 (696 lodsb increments); DS = the source
#     segment the `lds` loaded (33FD then runs with that DS, exactly as the
#     unhooked routine would; the caller's DS is only restored later at 32BD).
#   - FLAGS: last two flag-setting ops are `sub bx,0x2B7` (32B2, sets CF) then
#     `dec dx` (32B6, sets SF/ZF/AF/PF/OF for the 0 result, PRESERVES CF --
#     same INC/DEC semantics as CPU8086's own 0x40-0x4F opcode handlers and
#     the sprite_blit hook). So final flags = dec-dx result with CF from the
#     sub bx.
_OCC_BLIT_MASK_BASE = 0x0E86     # ss-relative 29x24 occlusion mask (also sprite_blit's table)
_OCC_BLIT_SRC_FAR_PTR = 0x0E2E   # ss-relative dword: source offset then segment
_OCC_BLIT_COLS = 0x1D            # 29 columns (outer dx)
_OCC_BLIT_ROWS = 0x18            # 24 rows (inner cx)
_OCC_BLIT_DI_ROW = 0x140         # dest row stride (screen width)
_OCC_BLIT_DI_COL_RESET = 0x1DFF  # subtracted from di after each column (24*0x140 - 1)
_OCC_BLIT_BX_ROW = 0x1D          # mask row stride
_OCC_BLIT_BX_COL_RESET = 0x2B7   # subtracted from bx after each column (24*0x1D - 1)
_OCC_BLIT_EXIT_IP = 0x32B9


def _occluded_column_blit_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss = s.ss
    dest_seg = s.es  # es = ss:[0E36], loaded by the setup at 3263, unchanged here

    src_off = mem.rw(ss, _OCC_BLIT_SRC_FAR_PTR)
    src_seg = mem.rw(ss, (_OCC_BLIT_SRC_FAR_PTR + 2) & 0xFFFF)

    di = s.di  # the dest start offset computed at 327F, still live in DI
    bx = 0
    si = src_off
    last_al = s.ax & 0xFF

    for _col in range(_OCC_BLIT_COLS):
        for _row in range(_OCC_BLIT_ROWS):
            last_al = mem.rb(src_seg, si)
            si = (si + 1) & 0xFFFF
            if last_al != 0:
                mask_off = (bx + _OCC_BLIT_MASK_BASE) & 0xFFFF
                # ASM: `cmp mask,0; jz skip` -> draw only where the stencil is
                # NON-zero (a permit-mask), stamping each drawn slot to 2.
                if mem.rb(ss, mask_off) != 0:
                    mem.wb(ss, mask_off, 0x02)
                    mem.wb(dest_seg, di, last_al)
            di = (di + _OCC_BLIT_DI_ROW) & 0xFFFF
            bx = (bx + _OCC_BLIT_BX_ROW) & 0xFFFF
        bx_before_sub = bx
        di = (di - _OCC_BLIT_DI_COL_RESET) & 0xFFFF
        bx = (bx - _OCC_BLIT_BX_COL_RESET) & 0xFFFF

    s.cx = 0
    s.dx = 0
    s.si = si
    s.di = di
    s.bx = bx
    s.ds = src_seg
    s.ax = (s.ax & 0xFF00) | last_al

    # Final flags: sub bx (CF) then dec dx (everything else, CF preserved).
    cpu.set_sub_flags(bx_before_sub, _OCC_BLIT_BX_COL_RESET,
                      bx_before_sub - _OCC_BLIT_BX_COL_RESET, 16)
    sub_cf = cpu.get_flag(CF)
    cpu.set_sub_flags(1, 1, 0, 16)  # dec dx: 1 -> 0
    cpu.set_flag(CF, sub_cf)

    s.ip = _OCC_BLIT_EXIT_IP


@registry.replace(CODE_SEG, 0x3283, "occluded_column_blit")
def occluded_column_blit_hook(cpu: CPU8086) -> None:
    _occluded_column_blit_hook(cpu)


# CS:IP 1010:5D8C -- the C-runtime 32-bit unsigned long-division helper
# (`__aFuldiv`-style). Near proc, callee-cleanup (`ret 8`): entry stack is
# [sp]=return IP, [sp+2..+3]=dividend low, [sp+4..+5]=dividend high,
# [sp+6..+7]=divisor low, [sp+8..+9]=divisor high (two 32-bit longs pushed
# high-word-last, the usual C small-model layout). Returns the 32-bit
# quotient in DX:AX. Called ~68K times in the recorded demo (almost certainly
# the perspective divide in the 3D road renderer) -- pure arithmetic, no
# memory writes beyond its own saved-register stack scratch.
#
# Two code paths (confirmed by disassembly of 5D8C-5DEA):
#   - divisor high word == 0 (a 32/16 divide): 99.8% of demo calls. Does two
#     `div cx` steps (dividend_hi/divisor_lo, then the combined low divide),
#     leaving CX = divisor_lo and (crucially) FLAGS from its `xor dx,dx` at
#     5D9E -- the LAST flag-setting instruction on this path, since dos_re's
#     DIV leaves flags untouched (cpu.py reg==6: sets ax/dx only) and every
#     instruction after the xor is mov/div/jmp/pop/ret. So final flags == the
#     logic-flags of a 0 result (ZF=PF=1, CF=SF=OF=0), with AF preserved from
#     entry (nothing on this path ever SETS AF -- `or ax,ax`/`xor dx,dx` both
#     go through set_logic_flags, which leaves AF alone).
#   - divisor high word != 0 (a true 32/32 divide, quotient < 2^16): 0.2% of
#     demo calls. Its shift-normalize-and-correct algorithm leaves CX and the
#     final AF in path-dependent scratch states that would need the full
#     estimate reproduced to match a strict diff. Rather than carry that
#     complexity for 1-in-500 calls, this hook DELEGATES that case (and the
#     never-observed divisor==0 fault) to the real ASM via
#     interpret_current_instruction_without_hook -- correct by construction,
#     no speed lost where it doesn't matter.
#
# BX/SI/BP are saved and restored by the routine (push/pop), so this hook
# leaves them untouched. The routine's own pushed saved-registers land in the
# dead-stack zone below the post-`ret 8` SP, which the differential verifier
# already excludes, so not replaying those stack writes is invisible to it.
_ULONG_DIV_EXIT_RET_BYTES = 10  # `ret 8`: pop 2-byte return IP + drop 8 arg bytes


def _ulong_div_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp

    divisor_hi = mem.rw(ss, (sp + 8) & 0xFFFF)
    divisor_lo = mem.rw(ss, (sp + 6) & 0xFFFF)
    if divisor_hi != 0 or divisor_lo == 0:
        # 32/32 path or a divide-by-zero fault -> run the original routine.
        interpret_current_instruction_without_hook(cpu)
        return

    ret_ip = mem.rw(ss, sp)
    dividend = (mem.rw(ss, (sp + 4) & 0xFFFF) << 16) | mem.rw(ss, (sp + 2) & 0xFFFF)
    quotient = dividend // divisor_lo  # divisor_hi==0, so == dividend // full divisor

    s.ax = quotient & 0xFFFF
    s.dx = (quotient >> 16) & 0xFFFF
    s.cx = divisor_lo  # left in CX by the `mov cx,[bp+8]` at 5D98, never reloaded
    cpu.set_logic_flags(0, 16)  # the `xor dx,dx` at 5D9E is this path's last flag op

    s.sp = (sp + _ULONG_DIV_EXIT_RET_BYTES) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x5D8C, "ulong_div")
def ulong_div_hook(cpu: CPU8086) -> None:
    _ulong_div_hook(cpu)


# CS:IP 1010:5D4C -- the C-runtime 32-bit unsigned long-MULTIPLY helper
# (`__aFulmul`-style), the companion to ulong_div sitting right beside it and,
# like it, dominating the in-game profile (~37K calls in the driving demo,
# the fixed-point 3D-transform math at 1010:04xx). Same near proc, callee-
# cleanup (`ret 8`) ABI and same arg layout: [sp+2..3]=A low, [sp+4..5]=A high,
# [sp+6..7]=B low, [sp+8..9]=B high; 32-bit product returned in DX:AX.
#
# Simple path (5D4F-5D64), taken when BOTH high words are 0 (a 16x16 multiply,
# 99.7% of demo calls): `or bx,ax` tests (A_high|B_high), then on zero does a
# single `mul bx` of A_low*B_low into DX:AX and returns. This hook reproduces
# that exactly:
#   - DX:AX = A_low * B_low (the full 32-bit product; 16x16 always fits).
#   - BX = B_low (left there by the `mov bx,[bp+8]` at 5D57; the routine does
#     NOT preserve BX). CX/SI/DI/ES are never touched -> preserved; BP is
#     push/pop-restored.
#   - FLAGS: the `or bx,ax` (result 0) sets ZF=1/PF=1/SF=0/CF=0/OF=0 and
#     leaves AF; then `mul bx` overwrites only CF and OF with carry =
#     (product high word != 0). dos_re MUL touches nothing else (cpu.py
#     reg==4 sets CF/OF only), so those are the exact final flags.
# The rare true-32/32 path (both-high-nonzero cross-term multiply, 0.3%) is
# DELEGATED to the original ASM via interpret_current_instruction_without_hook,
# same as ulong_div's complex path -- correct by construction.
_ULONG_MUL_EXIT_RET_BYTES = 10  # `ret 8`: pop 2-byte return IP + drop 8 arg bytes


def _ulong_mul_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp

    a_high = mem.rw(ss, (sp + 4) & 0xFFFF)
    b_high = mem.rw(ss, (sp + 8) & 0xFFFF)
    if (a_high | b_high) != 0:
        # true 32x32 cross-term multiply -> run the original routine.
        interpret_current_instruction_without_hook(cpu)
        return

    ret_ip = mem.rw(ss, sp)
    a_low = mem.rw(ss, (sp + 2) & 0xFFFF)
    b_low = mem.rw(ss, (sp + 6) & 0xFFFF)
    product = a_low * b_low  # 16x16 -> fits in 32 bits

    s.ax = product & 0xFFFF
    s.dx = (product >> 16) & 0xFFFF
    s.bx = b_low  # left in BX by the `mov bx,[bp+8]` at 5D57

    cpu.set_logic_flags(0, 16)  # the `or bx,ax` at 5D55, result (A_high|B_high)==0
    carry = (product >> 16) != 0
    cpu.set_flag(CF, carry)     # `mul bx` at 5D5F sets CF=OF=(product high != 0)
    cpu.set_flag(OF, carry)

    s.sp = (sp + _ULONG_MUL_EXIT_RET_BYTES) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x5D4C, "ulong_mul")
def ulong_mul_hook(cpu: CPU8086) -> None:
    _ulong_mul_hook(cpu)


# CS:IP 1010:5E5A -- the C-runtime SIGNED 32-bit long-DIVIDE helper (`_ldiv`/
# `__aNldiv`-style), the signed companion to the unsigned ulong_div/ulong_mul.
# Called from 9 render routines (the layer-3 object passes). Standard MSC ABI:
# `push bp; mov bp,sp`, callee-cleanup `ret 8`; args [bp+4..5]=dividend low,
# [bp+6..7]=dividend high, [bp+8..9]=divisor low, [bp+A..B]=divisor high; the
# 32-bit signed quotient is returned in DX:AX. The routine first takes the
# magnitude of each operand (negating in place, counting negatives in DI), then
# runs an unsigned divide, then negates the result iff exactly one operand was
# negative (`dec di; jnz skip`).
#
# The unsigned core has two paths, mirroring ulong_div: a 32/16 fast path
# (|divisor| fits in 16 bits -- the common case) and a shift-normalize 32/32
# path. This hook lifts ONLY the all-non-negative 32/16 case -- by far the most
# common (every call in the recorded demo) -- and DELEGATES everything else
# (either operand negative, or a 32/32 divisor) to the original ASM via
# interpret_current_instruction_without_hook, correct by construction. On the
# lifted path: CX is left holding |divisor_low| (the `mov cx,[bp+8]` at 5E96,
# never reloaded); the two-negatives count is 0, so `dec di` (0 -> 0xFFFF) is
# the final flag op (CF untouched by the preceding divides == 0). BX/SI/DI/BP
# are push/pop-restored.
_SIGNED_LDIV_EXIT_RET_BYTES = 10  # `ret 8`: pop 2-byte return IP + drop 8 arg bytes


def _signed_long_div_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ss, sp = s.ss, s.sp

    div_hi = mem.rw(ss, (sp + 8) & 0xFFFF)
    num_hi = mem.rw(ss, (sp + 4) & 0xFFFF)
    if (num_hi & 0x8000) or (div_hi & 0x8000) or div_hi != 0:
        # negative operand, or a 32/32 divisor -> run the original routine.
        interpret_current_instruction_without_hook(cpu)
        return

    ret_ip = mem.rw(ss, sp)
    num = (num_hi << 16) | mem.rw(ss, (sp + 2) & 0xFFFF)   # >= 0 here
    div_lo = mem.rw(ss, (sp + 6) & 0xFFFF)                  # divisor, 16-bit, > 0
    quotient = num // div_lo

    s.ax = quotient & 0xFFFF
    s.dx = (quotient >> 16) & 0xFFFF
    s.cx = div_lo  # left in CX by `mov cx,[bp+8]` at 5E96
    # sign count DI = 0 here; the sign fixup's `dec di` (0 -> 0xFFFF) is the last
    # flag op, and the divides leave CF=0.
    cpu.set_sub_flags(0, 1, -1, 16)
    cpu.set_flag(CF, False)

    s.sp = (sp + _SIGNED_LDIV_EXIT_RET_BYTES) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x5E5A, "signed_long_div")
def signed_long_div_hook(cpu: CPU8086) -> None:
    _signed_long_div_hook(cpu)


# CS:IP 1010:3153 -- the FORWARD run-length sprite rasterizer (one of a mirror
# pair; the backward twin is at 1010:3190, hooked below). The dominant render
# cost in the in-game demo: 5,884 calls driving 41,162 inner-loop iterations
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

    si = s.si
    index = mem.rb(ds, si); si = (si + 1) & 0xFFFF
    bx = (index << 2) & 0xFFFF
    fill = mem.rb(ss, (bx + _RLE_FILL_TABLE) & 0xFFFF)
    di = mem.rw(ds, si); si = (si + 2) & 0xFFFF
    word = ((fill << 8) | fill) & 0xFFFF

    ran = False
    cx = s.cx & 0x00FF  # xor ch,ch; cl untouched until a run sets it
    while True:
        ctrl = mem.rb(ds, si); si = (si + 1) & 0xFFFF
        if ctrl == _RLE_TERMINATOR:
            break
        anchor = di
        di = (di - ctrl) & 0xFFFF
        runlen = mem.rb(ds, si); si = (si + 1) & 0xFFFF
        si = (si + 1) & 0xFFFF  # the `inc si` at 3175 skips one stream byte
        if runlen & 1:
            mem.wb(es, di, fill); di = (di + 1) & 0xFFFF
        for _ in range(runlen >> 1):
            mem.ww(es, di, word); di = (di + 2) & 0xFFFF
        di = (anchor + _RLE_ROW_STRIDE) & 0xFFFF
        ran = True
        cx = 0  # the final rep stosw leaves CX == 0

    s.ax = 0x00FF
    s.bx = bx
    s.cx = cx
    s.dx = (s.dx & 0xFF00) | fill
    s.si = si
    # DI and BP are push/pop-restored -> unchanged; ES/DS/SS untouched.
    cpu.set_sub_flags(_RLE_TERMINATOR, _RLE_TERMINATOR, 0, 8)  # cmp al,0xFF -> 0
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x3153, "rle_sprite_forward")
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

    si = s.si
    index = mem.rb(ds, si); si = (si + 1) & 0xFFFF
    bx = (index << 2) & 0xFFFF
    fill = mem.rb(ss, (bx + _RLE_FILL_TABLE_BACK) & 0xFFFF)
    di = (mem.rw(ds, si) - 1) & 0xFFFF  # lodsw then `dec di` at 31A6
    si = (si + 2) & 0xFFFF

    ran = False
    cx = s.cx & 0x00FF
    while True:
        ctrl = mem.rb(ds, si); si = (si + 1) & 0xFFFF
        if ctrl == _RLE_TERMINATOR:
            break
        anchor = di
        di = (di + ctrl) & 0xFFFF  # `add di,ax` (RIGHT), vs forward's `sub`
        runlen = mem.rb(ds, si); si = (si + 1) & 0xFFFF
        si = (si + 1) & 0xFFFF  # `inc si` at 31B3 skips one stream byte
        # std fill: runlen bytes of `fill` written downward from di.
        p = di
        for _ in range(runlen):
            mem.wb(es, p, fill); p = (p - 1) & 0xFFFF
        di = (anchor + _RLE_ROW_STRIDE) & 0xFFFF
        ran = True
        cx = 0

    s.ax = 0x00FF
    s.bx = bx
    s.cx = cx
    s.dx = (s.dx & 0xFF00) | fill
    s.si = si
    cpu.set_sub_flags(_RLE_TERMINATOR, _RLE_TERMINATOR, 0, 8)
    if ran:
        cpu.set_flag(DF, False)  # the per-iteration `cld` at 31C4
    s.sp = (sp + 2) & 0xFFFF
    s.ip = ret_ip


@registry.replace(CODE_SEG, 0x3190, "rle_sprite_backward")
def rle_sprite_backward_hook(cpu: CPU8086) -> None:
    _rle_sprite_backward_hook(cpu)


# CS:IP 1010:04C0 -- the fixed-point perspective transform, the KEYSTONE of the
# renderer island: every road/object render path funnels through it, and it now
# sits entirely on already-recovered primitives (its three 32-bit divides are
# the ulong_div helper). This is the first island layer wired to a clean
# skyroads/recovered/renderer.py function; the hook only adapts registers and
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


@registry.replace(CODE_SEG, 0x04C0, "perspective_transform")
def perspective_transform_hook(cpu: CPU8086) -> None:
    _perspective_transform_hook(cpu)


# CS:IP 1010:1732 -- the layer-2 per-segment cull, the renderer-island root that
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
    if seg > 0x25:
        return 0, bx, cx, dx
    new_dx = ((coord - 0x2200) & 0xFFFF) % 128
    t4 = t9 = 0
    if seg <= 0x25:
        t4 = cpu.mem.rw(ds, (0x4C + 2 * seg) & 0xFFFF)
        t9 = cpu.mem.rw(ds, (0x98 + 2 * seg) & 0xFFFF)
    ret = road_segment_clip(dir_sel, seg, coord, t4, t9)
    sel = dir_sel & 0x0F00
    new_bx = (seg * 2) & 0xFFFF if sel in (0x0100, 0x0300, 0x0500) else bx
    return ret, new_bx, 0x80, new_dx


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

    def done(ax, a, b):
        # a,b are the operands of the path's final 16-bit `cmp a,b`.
        s.ax = ax & 0xFFFF
        s.bx, s.cx, s.dx = bx & 0xFFFF, cx & 0xFFFF, dx & 0xFFFF
        cpu.set_sub_flags(a & 0xFFFF, b & 0xFFFF, (a & 0xFFFF) - (b & 0xFFFF), 16)
        s.sp = (sp + 2) & 0xFFFF
        s.ip = ret_ip

    r1, bx, cx, dx = _persp_exit(cpu, ds, x_lo, x_hi, (si + 0x700) & 0xFFFF, bx)
    r2, bx, cx, dx = _persp_exit(cpu, ds, x_lo, x_hi, (si - 0x700) & 0xFFFF, bx)

    near = (di + 0x600) & 0xFFFF
    if ((r1 & 0xF) or (r2 & 0xF)) and di < 0x2800 and near > 0x2480:
        return done(1, near, 0x2480)                         # 179A (1792 cmp)
    far = (di + 0x680) & 0xFFFF
    if far <= 0x2800:
        return done(0, far, 0x2800)                          # 1861 via 17A8 (17A5 cmp)
    if not ((r2 & 0xF00) or (r1 & 0xF00)):
        return done(0, r1 & 0xF00, 0)                        # 1861 via 17C6 (17C1 cmp)

    r3, bx, cx, dx = _persp_exit(cpu, ds, x_lo, x_hi, si, bx)
    rem = ((((si & 0xFFFF) >> 7) + 0xFFCF) & 0xFFFF) % 46
    seg = (0x17 - rem) & 0xFFFF
    delta = 0xE900
    if seg == 0 or seg > 0x7FFF:
        seg = (1 - seg) & 0xFFFF
        delta = 0x1700
    c1, bx, cx, dx = _clip_exit(cpu, ds, r3, seg, di, bx, cx, dx)
    if c1 != 0:
        return done(1, c1, 0)                                # 185B via 182F (182A cmp)
    r4, bx, cx, dx = _persp_exit(cpu, ds, x_lo, x_hi, (si + delta) & 0xFFFF, bx)
    c2, bx, cx, dx = _clip_exit(cpu, ds, r4, (0x2F - seg) & 0xFFFF, di, bx, cx, dx)
    return done(1 if c2 != 0 else 0, c2, 0)                  # 185B/1861 (1853 cmp)


@registry.replace(CODE_SEG, 0x1732, "road_object_visible")
def road_object_visible_hook(cpu: CPU8086) -> None:
    _road_object_visible_hook(cpu)


# CS:IP 1010:38BF -- the road-column strip compositor, the single most-called
# rasterizer in gameplay (34 callsites, ~13% of real render work). Bare routine
# (push bx/bp/ds at entry; cld; pop ds/bp/bx; ret at exit), so bx/bp/ds and DF
# are RESTORED to the caller; only AX/CX/DX/SI/DI/ES and flags are clobbered
# scratch. Uses ds/ss-relative globals (ds==ss==the game data segment in-game):
#   [0E44]/[0E46] row params, [0E48] scan direction (0=up/cld, !=0=down/std),
#   [0E60]/[0E62] the two stride-3 display-list segments, [0E64] a screen-offset
#   base, [0E66] source-bitmap segment, [0E68] dest (screen) segment, [0E74]=AX
#   the column descriptor (low byte = how many 0xFF-terminated records to skip
#   to reach this column; bit15 = "just position, don't composite").
#
# Flow: (1) compute a screen offset `di` from the row params + [0E64]; (2) scan
# BOTH display lists (segs [0E62] then [0E60]) forward in stride-3 records to
# the (AX&0xFF)-th 0xFF column marker -- the two hot 3901/3927 loops; (3) unless
# bit15 is set, walk the second list's records compositing horizontal pixel runs
# from the source bitmap onto the screen, one scanline (stride 0x140) per record,
# until a 0xFF length marker. Two copy variants (3978 vs 39A3) differ only by
# the [0E48] direction sign. Each record is 3 bytes: [0]=start offset back from
# the scanline base bp, [1]=run length in bytes, [2]=unused by the copy. The run
# is word-aligned (start &= ~1; words = ceil((len + startLowBit)/2)) then
# rep movsw'd from source:si to screen:si (di==si, same offset both segments).
#
def _road_column_strip_hook(cpu: CPU8086) -> None:
    s = cpu.s
    mem = cpu.mem
    ds0, ss = s.ds, s.ss
    rb, rw, ww = mem.rb, mem.rw, mem.ww

    ax_in = s.ax
    ww(ds0, 0x0E74, ax_in)                                  # 38C2 mov [0E74],ax
    di = (ax_in & 0x7FFF) >> 7                               # 38C5-38CD
    ax = (0x0B - rw(ds0, 0x0E44)) & 0xFFFF                   # 38D0-38D3
    ax = (ax * 4 + 4) & 0xFFFF                               # 38D7-38DC (mul 4, add 4)
    ax = (ax - rw(ds0, 0x0E46)) & 0xFFFF                     # 38DF
    ax = (ax * 0x0C) & 0xFFFF                                # 38E3-38E6 mul cx(12)
    setup_ax = ax                                            # ax's high byte survives to a 3944 exit
    di = (di + ax) & 0xFFFF                                  # 38E8
    MARK = 0xFF                                              # 38EA al=0xFF
    di = (di + rw(ds0, 0x0E64)) & 0xFFFF                     # 38EC

    def scan(seg: int, bx: int, count: int) -> int:
        # 3901/3927: `count` times, advance bx by 3 until seg:[bx]==0xFF, then +1
        for _ in range(count & 0xFF):
            bx = (bx + 3) & 0xFFFF
            while rb(seg, bx) != MARK:
                bx = (bx + 3) & 0xFFFF
            bx = (bx + 1) & 0xFFFF
        return bx

    col = rw(ss, 0x0E74) & 0xFF
    seg1 = rw(ds0, 0x0E62)                                   # 38F0
    bx = rw(seg1, di)                                        # 38F4
    bx = scan(seg1, bx, col)                                 # 38F6-3909
    si = rw(seg1, (bx + 1) & 0xFFFF)                         # 390B
    dx_scan1 = bx                                            # 390E mov dx,bx
    di = (di - rw(ss, 0x0E64)) & 0xFFFF                      # 3910
    seg2 = rw(ss, 0x0E60)                                    # 3915
    bx = rw(seg2, di)                                        # 391B
    bx = scan(seg2, bx, rw(ss, 0x0E74) & 0xFF)               # 391D-392F
    bp = rw(seg2, (bx + 1) & 0xFFFF)                         # 3931
    bx = (bx + 3) & 0xFFFF                                   # 3934

    # --- 3937 onward: optional skip-loop, then the composite copy loop. ---
    exited_3944 = False
    if not (rw(ss, 0x0E74) & 0x8000):                        # 3937 test bit15; 393E jnz 3954
        # 3940-3952: advance past records whose scanline base bp is < si, until
        # either a 0xFF marker (-> 3944, the whole routine exits early) or bp>=si.
        while True:
            if rb(seg2, bx) == MARK:                         # 3940 cmp[bx],al; ==FF -> 3944
                exited_3944 = True
                break
            if bp >= si:                                     # 3947 cmp bp,si; jnb 3954
                break
            bx = (bx + 3) & 0xFFFF                           # 394B
            bp = (bp + 0x140) & 0xFFFF                       # 394E

    if exited_3944:
        # 3944 jmp 39CF: exits BEFORE 3958-3967. AX keeps the mul-product high
        # byte but AL was loaded with 0xFF at 38EA; DX = bx after the first scan
        # (390E mov dx,bx); ES = entry ES (396B not reached); si/di post-scan;
        # cx=0 (the second scan's `loop` drained it, or col==0 leaves it 0).
        s.ax = (setup_ax & 0xFF00) | 0xFF
        s.dx = dx_scan1
        s.cx = 0
        s.si = si
        s.di = di
        cpu.set_sub_flags(MARK, MARK, 0, 8)                  # cmp [bx],0xFF (equal)
    else:
        bp = (bp + 0x2800) & 0xFFFF                          # 3954
        src_seg = rw(ss, 0x0E66)                             # 3967 (ax)
        dst_seg = rw(ss, 0x0E68)                             # 396B (es)
        down = rw(ss, 0x0E48) != 0                           # 3959/3970
        if down:
            bp = (bp - 1) & 0xFFFF                           # 3962 dec bp (std)
        last_si, last_di = si, di
        while True:                                          # copy loop 3978/39A3
            length0 = rb(seg2, bx)                           # mov cl,[bx]
            if length0 == MARK:                              # cmp cl,0xFF; jz 39CF
                break
            if not down:
                off0 = (bp - length0) & 0xFFFF               # 3983 sub si,cx
            else:
                off0 = (bp + length0) & 0xFFFF               # 39AE add si,cx
            run = rb(seg2, (bx + 1) & 0xFFFF)                # mov cl,[bx+1]
            low = off0 & 1
            si_word = off0 & ~1                              # shr si,1; shl si,1
            if not down:
                cx = run + low                               # adc cx,0
            else:
                cx = (run - low + 1) & 0xFFFF                # sbb cx,0; inc cx
            words = ((cx >> 1) + (cx & 1)) & 0xFFFF          # shr cx,1; adc cx,0
            step_w = -2 if down else 2                        # std vs cld movsw
            sp = si_word
            di_w = si_word
            for _ in range(words):                           # rep movsw
                mem.ww(dst_seg, di_w, rw(src_seg, sp))
                sp = (sp + step_w) & 0xFFFF
                di_w = (di_w + step_w) & 0xFFFF
            last_si, last_di = sp, di_w
            bp = (bp + 0x140) & 0xFFFF                       # add bp,0x140
            bx = (bx + 3) & 0xFFFF                           # add bx,3
        # exit at 39CF from the copy loop: ax=[0E66], dx=seg2 (3965 mov dx,ds),
        # es=[0E68], cx=0x00FF (ch=0, cl=the 0xFF just read), si/di post-movsw.
        s.ax = src_seg
        s.dx = seg2
        s.es = dst_seg
        s.cx = 0x00FF
        s.si = last_si
        s.di = last_di
        cpu.set_sub_flags(MARK, MARK, 0, 8)                  # cmp cl,0xFF (equal)

    s.ip = mem.rw(ss, s.sp)
    s.sp = (s.sp + 2) & 0xFFFF


@registry.replace(CODE_SEG, 0x38BF, "road_column_strip")
def road_column_strip_hook(cpu: CPU8086) -> None:
    _road_column_strip_hook(cpu)


# CS:IP 1010:4344 (one-time reset) + 1010:434A (loop top) -- the palette
# cross-fade driver (1010:4331, see "Palette-fade interpolation" in
# docs/skyroads/symbol_ledger.md). UNLIKE every other hook in this file, this
# pair is a BEHAVIORAL optimization, not a thin representational one: it
# skips real work the original ASM would have done, betting that the skipped
# work is provably a no-op. Read the reasoning below before touching either
# function; getting this wrong would be a silent visual-correctness bug, not
# a verifier-caught divergence.
#
# THE PROBLEM: 4331's loop (`434A` down to `4452 jmp 434A`) recomputes a
# blend percentage from an elapsed-tick counter (`ds:[1600]`), re-runs a full
# 256-entry palette_fade_inner pass, and re-uploads the whole thing via
# palette_upload -- every single iteration, with NO check for whether
# `ds:[1600]` actually changed since the last iteration. Both
# tools/profile_demo.py and dos_re.player run a FIXED instruction budget per
# frame (`rt.cpu.run(steps_per_frame)`), and ALL of a frame's timer IRQs are
# delivered up front, before any of that frame's instructions execute -- so
# `ds:[1600]` is architecturally CONSTANT for an entire frame's step budget.
# Once palette_upload/sprite_blit made everything else in a frame cheaper,
# this loop got to spin far more times within that frozen-tick window before
# running out of budget: a live trace over 300 frames found 97.1% of all
# visits to 434A see the EXACT SAME tick value as the immediately preceding
# visit -- i.e. 19 out of 20 full recompute+reupload passes are pure waste,
# and it's each pass's ~256 Python hook calls (not any interpreted ASM) that
# dominates the cost.
#
# THE FIX: since `ds:[1600]`, `bp+8` (duration, fixed for the whole call) and
# srcA/srcB (also fixed for the call) are the ONLY inputs the recompute
# depends on, an unchanged tick means an IDENTICAL percent, an IDENTICAL
# blend (same source bytes, same percent -> same output bytes overwriting
# the SAME destination range), and an IDENTICAL palette_upload call (the DAC
# ends up holding the same values it already held) -- a full no-op on all
# memory/DAC state. So: cache the last tick value for which REAL work ran,
# keyed by (ss, bp) (bp is 4331's own ENTER-allocated frame pointer, constant
# for the lifetime of one call); on a cache hit (tick unchanged), skip
# straight to `4449` (the keyboard-poll call at the bottom of the loop),
# bypassing the doomed-to-be-redundant setup+blend+upload entirely.
#
# WHY SKIPPING TO 4449 IS SAFE: traced every register the skipped code
# (434A-4448) would otherwise have touched --
#   - DS: never reassigned anywhere in 4331 (only ES gets LES'd, inside
#     palette_fade_inner) -- 4153/4167's own memory reads use the SAME DS
#     the caller already has, unaffected by skipping.
#   - ES/AX/BX/CX/DX: 4153 (disassembled in full -- it's just
#     `mov ah,0Bh; int 21h` to poll for a keypress, consuming one via
#     `int 21h ah=07h` if found) and 4167's `cmp ds:[AF32h],0` never read
#     any of these; the NEXT real iteration (if any) reloads all of them
#     fresh from bp-relative/immediate sources before touching palette_upload
#     again, so nothing downstream ever observes a "stale" value.
#   - SI/DI: whatever the skipped code would have left in them is irrelevant
#     regardless -- the ONLY path that returns to 4331's caller (4455) pops
#     them from the STACK (the values 4331's own prologue pushed at entry),
#     never from live registers.
#   - FLAGS: nothing between 4449 and the next 434A/4455 branches on a flag
#     that 434A-4448 would have set (the loop's own `jz`/`jnz`s all test
#     freshly-loaded values, e.g. `cmp [bp-4],0x64`, not carried-over flags).
#
# THE CACHE-STALENESS TRAP (why 4344 needs its own hook, not just 434A):
# `bp` is only unique WHILE one 4331 call is active -- two SEPARATE calls
# from the same call site reuse the identical stack depth, hence the
# identical bp, and `4344` (`mov ds:[1600],0`) unconditionally resets the
# tick to 0 at the start of EVERY real call. Without clearing the cache at
# that reset, call #2 starting at tick=0 with the SAME bp as call #1's own
# tick=0 entry would look like a cache HIT (redundant revisit) and
# incorrectly skip call #2's genuinely-necessary first iteration, leaving a
# stale palette from call #1's fade on screen. Hooking 4344 to invalidate the
# (ss, bp) cache entry at the one point that's guaranteed to run exactly once
# per real call closes that gap. A cache MISS always means "let it run for
# real" (never "skip") -- the failure mode of losing the cache entirely
# (e.g. across a snapshot restore, since it lives on the live `cpu` object,
# not in cloned CPUState) is at worst one extra non-redundant iteration,
# never an incorrect skip.
#
# VALIDATION: this pair cannot be checked with the strict per-call oracle
# diff every other hook in this file uses -- diverging from the oracle on
# skipped iterations is the entire point. Instead it's validated by running
# the full recorded demo twice (tick-gate on vs off) and diffing final
# memory/VGA-palette/CPU state end to end; see docs/skyroads/symbol_ledger.md
# for the result.
def _fade_loop_cache(cpu: CPU8086) -> dict[tuple[int, int], int]:
    cache = getattr(cpu, "_fade_loop_tick_cache", None)
    if cache is None:
        cache = {}
        cpu._fade_loop_tick_cache = cache
    return cache


_FADE_LOOP_TICK_ADDR = 0x1600
_FADE_LOOP_POLL_IP = 0x4449


def _fade_loop_reset_hook(cpu: CPU8086) -> None:
    s = cpu.s
    _fade_loop_cache(cpu).pop((s.ss, s.bp), None)
    interpret_current_instruction_without_hook(cpu)


@registry.replace(CODE_SEG, 0x4344, "fade_loop_tick_reset")
def fade_loop_tick_reset_hook(cpu: CPU8086) -> None:
    _fade_loop_reset_hook(cpu)


def _fade_loop_gate_hook(cpu: CPU8086) -> None:
    s = cpu.s
    key = (s.ss, s.bp)
    cache = _fade_loop_cache(cpu)
    tick = cpu.mem.rw(s.ds, _FADE_LOOP_TICK_ADDR)

    if cache.get(key) == tick:
        s.ip = _FADE_LOOP_POLL_IP
        return

    cache[key] = tick
    interpret_current_instruction_without_hook(cpu)


@registry.replace(CODE_SEG, 0x434A, "fade_loop_tick_gate")
def fade_loop_tick_gate_hook(cpu: CPU8086) -> None:
    _fade_loop_gate_hook(cpu)
