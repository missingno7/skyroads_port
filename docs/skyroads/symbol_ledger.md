# SkyRoads symbol ledger — addresses -> evidence

CS is `0x1010` for every entry below unless noted (the executable's single
code segment). Status ladder per `dos_re.islands` (GUESS < OBSERVED <
RECOVERED < ASM_MATCHED < VERIFIED < CANONICAL).

## Asset decompressor (LZS)

| Address | Role | Evidence | Status |
|---|---|---|---|
| `64AB` | `get_bit()` — MSB-first single-bit read, refills `ds:[41B0]` from the input stream every 8 bits | live register trace, 2026-07-08 | OBSERVED |
| `64FF`/`6508` | `get_bits(n)` — n calls to `get_bit()`, MSB-first accumulate | live register trace, forced linear disasm | OBSERVED |
| `6326` | raw (non-bit) byte fetch: refill `ds:[41B0]` from the input stream directly | forced linear disasm | OBSERVED |
| `6490` | read one raw header byte (byte-aligned, via `6326`), used only before the bitstream starts | forced linear disasm | OBSERVED |
| `6350`/`63A3`/`63D3` | refill the 4KB input staging buffer from disk (`ds:[41B2]`/`[41B4]`/`[41B6]` = window start/end/cursor) when exhausted — ultimately reaches `INT 21h AH=3Fh` | file-load trace (`skyroads/probes/trace_file_loads.py`) + forced linear disasm | OBSERVED |
| `6712`-`675E` | main decode loop: 3 header-derived bit-widths patched as self-modifying immediates at `6729`/`671F`/`674C`, then per-symbol: `b1=get_bit()`; `b1==0` -> long match, `distance=get_bits(WIDTH_DIST_LONG)+2`; else `b2=get_bit()`; `b2==1` -> literal `get_bits(8)`; else short match, `distance=get_bits(WIDTH_DIST_SHORT)+(1<<WIDTH_DIST_LONG)+2`; either match then `length=get_bits(WIDTH_LEN)+2`, copy byte-by-byte from `output_pos-distance` | full oracle-memory dumps of `TREKDAT.LZS` (records 0/1 directly, all 9 records via the differential hook verifier), `MUZAX.LZS`, and `INTRO.LZS` — 100.00% exact, zero divergence, across 2 different `WIDTH_DIST_LONG` values | VERIFIED (`TREKDAT.LZS` all 9 records, `MUZAX.LZS`, `INTRO.LZS`) |
| `6750`-`6753` | short-distance branch: `ADD AX,imm16` (immediate = `1<<WIDTH_DIST_LONG`, patched per file/record at `1010:6751/6752`) then unconditional jump into the long-distance branch's shared tail at `6723` (`lea si,[bp-2]; sub si,ax`) — both branches share one distance-to-source-pointer computation | direct disassembly of live oracle code bytes for both `TREKDAT.LZS` (`WIDTH_DIST_LONG=10`, immediate=`0x0400`) and `INTRO.LZS` (`WIDTH_DIST_LONG=9`, immediate=`0x0200`) | VERIFIED |
| `6350` tail (`638F`-`639C`) | staging-buffer refill: `ds:[41AC]`=DOS file handle used for the `INT 21h AH=3Fh` read, `ds:[41B2]`=buffer start (constant, `0x31A8`), `ds:[41B4]`=buffer end (start + last refill's byte count, or the *requested* size at `ds:[41B8]` if the read was short at EOF), `ds:[41B6]`=cursor (points one PAST the byte currently loaded into `ds:[41B0]`) | direct disassembly + a live-memory read confirming `1010:6751/6752` (the short-distance `ADD`'s immediate) tracks `1<<WIDTH_DIST_LONG` per file/record | VERIFIED |
| `6595`, `65E0`, `6543` | NOT on the traced TREKDAT decode path (never hit during a ~6M-instruction trace covering config/muzax/oxy_disp/ful_disp/speed/demo.rec/trekdat loading) — likely alternate/legacy copy helpers | forced linear disasm only, no execution evidence | GUESS |

## File loading (not decompression itself)

Confirmed boot-time open order (via `skyroads/probes/trace_file_loads.py`):
`skyroads.cfg` -> `muzax.lzs` -> `oxy_disp.dat` -> `ful_disp.dat` ->
`speed.dat` -> `demo.rec` -> `trekdat.lzs` (streamed in 4096-byte chunks,
multiple ~26KB output allocations — i.e. multiple compressed records per
file, matching the external project's documented 8-record `TREKDAT.LZS`
layout — not yet independently confirmed record-by-record on our side).

## Palette-fade interpolation (performance hot spot)

| Address | Role | Evidence | Status |
|---|---|---|---|
| `4331` | Function entry (`enter 0x16,0`, i.e. 22 bytes of locals). Args (near-call, caller-pops convention — every exit is a plain `ret`, never `ret N`): `bp+4` = ptr to a small struct whose word+0 is a palette-B segment and word+4 is an entry count (×3 = byte count, RGB triples); `bp+6` = ptr to a similar struct for palette-A; `bp+8` = a duration/step-total divisor. Skips entirely (jumps to its `leave;ret` tail at `4455`) when the flag at `ds:[003C]` is 0. | live snapshot disasm (2026-07-08, from `artifacts/snapshot_skyroads_20260708_165846`, an intro-fade-in capture) | OBSERVED |
| `4344`-`43A6` | Per-call setup: `percent = clamp(100 * ds:[1600] / bp+8, max 100)` (or 100 outright if `bp+8==0`), then initializes 3 running pointers (dest in the fixed scratch buffer at `1686:31A8`, srcA/srcB each a fixed segment from the struct args + a 0-based incrementing offset) and a byte counter. | same | OBSERVED |
| `43A9`-`442D` | **The hot loop, now hooked** — confirmed by `tools/profile_hotspots.py` to dominate execution (~57K hits of a 3M-instruction profiling window). Per byte: `out = srcB_byte + (srcA_byte - srcB_byte) * percent / 100` (signed, truncating idiv) written to the dest scratch buffer, all three pointers advanced by 1, until the byte index reaches `3 * count`. Hooked as `palette_fade_inner` (`skyroads/hooks.py`, pure rule in `skyroads/recovered/palette_fade.py`), one hook call = one loop iteration (not the whole `4331` outer animation, which does its own real-time pacing across many calls to this inner loop). | `tools/profile_hotspots.py` + live disasm + `dos_re.verification` strict differential verifier: 34,439 hook calls (~45 full 768-byte passes incl. many pass-boundary transitions) with zero divergence, 2026-07-08. Three real bugs found and fixed during verification: (1) forgot to write back the final AX/BX/CX/DX register state at the loop-back continuation (only memory was updated); (2) `idiv`'s remainder lands in DX, not just its quotient in AX; (3) `LES` loads BOTH the offset into BX and the segment into ES — two `les bx,[bp-8]`/`les bx,[bp-12]` instructions I'd only modeled for their BX side-effect turned out to also govern the final ES, and the loop-exit path (bound check fails before reaching those LES instructions) leaves ES holding an *earlier* segment (`src_a_seg` from `43D2`) than the loop-continue path (`src_b_seg` from the last LES at `4417`) — two different correct answers depending on which path is taken. Measured wall-clock win (`artifacts/snapshot_skyroads_20260708_165846`, processing 5000 palette bytes ≈ 6.5 passes): 1.13s → 0.17s, **6.7x wall-clock, 7.5x fewer interpreter steps**. | VERIFIED |

**Why this loop, and not the gameplay road/pixel renderer, is what's confirmed hot so far:** the snapshot this was profiled from is mid-intro (fade-in), and — independently corroborated by the SkyRoads-Codex project's own DOSBox-X trace notes — the intro does not appear to auto-advance to the menu/gameplay on a fixed timer; repeated keypress injection (Enter, Space, Esc, held and tapped) across ~80M more instructions did not unstick it either. Reaching the actual road-rendering routine (SkyRoads-Codex's static analysis puts it near image offset `0x2D03`, unconfirmed by us) needs either a correctly-timed/sequenced input script or a snapshot the owner captures further into the game.

## Timer ISR + generic elapsed/keypress wait (the real driver-level bottleneck)

| Address | Role | Evidence | Status |
|---|---|---|---|
| `3B17`-`3B5E` | SKYROADS' installed INT 08h ISR. Chains to a sound-service call (`5A55`, decrements a counter at `ds:[0C83]` — an AdLib/timer-based audio tick, unrelated to `ds:[1600]`), then runs a **software prescaler**: `ds:[3192]` is checked against 0 and 5 (both trigger the "due" branch), decremented every call regardless; only on the "due" branch does it `inc ds:[1600]` (the same elapsed-ticks counter the palette-fade duration math reads) and reprogram PIT channel 2 (`out 42h`/`43h`, `ds:[0BD0]`-indexed — a music/SFX note-length service). Net effect confirmed by live-tracing 6 consecutive real INT 08h deliveries: `ds:[1600]` advances by exactly 1 per 6 real timer interrupts — an intentional ~3 Hz game-tick rate, not a bug. | live ISR trace (2026-07-09, instrumented `cpu.step` around 6 back-to-back `deliver_interrupt(0x08)` calls) | OBSERVED |
| `4465`-`417D` | Generic "wait until `ds:[1600] >= bp+4` OR a key is ready" poll: `cmp ds:[1600],[bp+4]; jb ...` gates on the elapsed counter; if not yet due, calls `4153` -> `5FCC` (`mov ah,0Bh; int 21h` — DOS check-stdin-status, `skyroads`'s own AH=0Bh handler) to check for a pending key; loops back via `447E: jmp 4465` if neither condition is met. Two exit paths not yet fully mapped: `446E: jmp 4481` (elapsed satisfied) and `4164` (key ready, calls `5FEB`) — deliberately NOT hooked (see below). | live disasm + register trace (2026-07-09) | OBSERVED — exit-path targets (`4481`, `4164`/`5FEB`) not traced |

**Why this wasn't hooked (and the driver-level fix instead):** unlike the
palette-fade inner loop, this routine has multiple call targets and two
unexplored exit paths — hooking it correctly to the same standard as
`palette_fade_inner` needs mapping `4481` and `4164`/`5FEB` first, a bigger
scope than "straightforward." Because the ISR's 6:1 prescaler is now
understood precisely, the *actual* win was available at the driver level
with zero CPU-hook risk: burst-deliver 6 INT 08h calls per frame (matching
the real prescaler) with a smaller step budget so the bursts land more often
per wall-clock second. See `run_status.md`'s 2026-07-09 entry for the
measured 7.5x wall-clock improvement. If gameplay profiling later shows this
wait loop is STILL hot after the driver fix, mapping the two exit paths and
hooking it the same way as the palette fade is the natural follow-up.

## Open / unconfirmed

- The purpose of the extra `1 << byte` value computed during header parsing
  and stored at `6751` — read but not observed consumed by the main loop in
  the traced window.
- `ds:[41AA]` / `ds:[41BB]` flag checks in `6595`/`6350` — plausibly
  "stored/uncompressed block" and "last chunk" flags; not traced to a
  concrete effect yet.
- The exact on-disk byte offset where the 3 width bytes / bitstream begin,
  relative to the `CMAP`/length header fields visible in raw file
  inspection — not yet correlated field-by-field against our own trace.
