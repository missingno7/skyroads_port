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
"""
from __future__ import annotations

from dos_re.cpu import CPU8086
from dos_re.hooks import registry

from skyroads.codecs.lzs import LzsWidths
from skyroads.recovered.palette_fade import blend_byte

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
