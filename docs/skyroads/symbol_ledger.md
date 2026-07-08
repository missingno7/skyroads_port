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
| `66E6`-`675E` | main decode loop: 3 header-derived bit-widths patched as self-modifying immediates at `6729`/`671F`/`674C`, then per-output-byte: flag bit -> long-distance match / (flag bit -> short-distance match / literal byte) | live register trace + forced linear disasm, corroborated structurally by an independent RE project's published findings (see `run_status.md` 2026-07-08) | OBSERVED — not yet round-trip verified against real file bytes |
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
| `43A9`-`442D` | **The hot loop** — confirmed by `tools/profile_hotspots.py` to dominate execution (~57K hits of a 3M-instruction profiling window from the intro-fade snapshot, i.e. this ~40-instruction loop body alone was ~2% of every instruction executed in that window; a background probe pumping 400+ timer IRQs never left this loop). Per byte: `out = srcB_byte + (srcA_byte - srcB_byte) * percent / 100` (signed, truncating idiv) written to the dest scratch buffer, all three pointers advanced by 1, until the byte index reaches `3 * count`. | `tools/profile_hotspots.py` output + live disasm | OBSERVED — general algorithm understood (a per-byte linear palette interpolation for a fade), but stack-slot bookkeeping around the pre/post-increment offsets (`bp-16`/`bp-18`/`bp-20`/`bp-22`) has enough subtlety that a hook must be validated with `dos_re.verification.install_hook_verifier` before being trusted, not hand-derived — see `run_status.md`'s 2026-07-08 performance entry and the LZS length-formula bug above for why that caution is not hypothetical on this codebase. |

**Why this loop, and not the gameplay road/pixel renderer, is what's confirmed hot so far:** the snapshot this was profiled from is mid-intro (fade-in), and — independently corroborated by the SkyRoads-Codex project's own DOSBox-X trace notes — the intro does not appear to auto-advance to the menu/gameplay on a fixed timer; repeated keypress injection (Enter, Space, Esc, held and tapped) across ~80M more instructions did not unstick it either. Reaching the actual road-rendering routine (SkyRoads-Codex's static analysis puts it near image offset `0x2D03`, unconfirmed by us) needs either a correctly-timed/sequenced input script or a snapshot the owner captures further into the game.

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
