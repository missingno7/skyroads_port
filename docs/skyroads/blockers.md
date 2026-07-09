# SkyRoads blockers

## LZS decoder: RESOLVED — short-distance formula, verified across 3 files (2026-07-09)

**Root cause found and fixed**, through several rounds — each round's fix was
correct as far as it was tested, but testing only ever covered files whose
`WIDTH_DIST_LONG` happened to be 10 until `INTRO.LZS` (width 9) exposed the
next layer:

1. Match length was `get_bits(WIDTH_LEN) + 1`; the ASM copy loop at
   `673C`-`6742` actually does `+2` (one more unconditional `movsb` at
   `6740` after the `LOOP` body).
2. Short-distance was assumed `get_bits(WIDTH_DIST_SHORT) + 3` by analogy
   with the long-distance branch. The real formula found by disassembling
   `1010:6750` directly (`05 00 04` = `ADD AX,imm16`) is `get_bits(
   WIDTH_DIST_SHORT) + <base> + 2`, where `<base>` is read from the patched
   immediate at `1010:6751/6752`.
3. `<base>` was first assumed a fixed `0x400` (1024) — a write-watch on
   `1010:6751/6752` across `TREKDAT.LZS`'s own header-parse window found zero
   writes, wrongly generalized to "never patched, for any file." It's
   actually `1 << WIDTH_DIST_LONG`, computed per file/record — `TREKDAT.LZS`
   and `MUZAX.LZS` both use `WIDTH_DIST_LONG=10` (giving 0x400 either way,
   which is why two files' worth of testing never caught this), but
   `INTRO.LZS` uses `WIDTH_DIST_LONG=9` and its patched operand reads
   `0x0200 = 1<<9` live from oracle memory — direct proof.

**Verification:** full oracle-memory dumps of `TREKDAT.LZS` records 0/1 and
all 9 records via the differential hook verifier, plus `MUZAX.LZS` and
`INTRO.LZS` (15 calls total, 3 files, 2 different `WIDTH_DIST_LONG` values) —
100% exact, zero divergence. Status raised to VERIFIED. Regression tests:
`tests/test_lzs_codec.py` (fixtures in `tests/fixtures/lzs/`).

**Still open, lower priority:** the fourth header byte's purpose remains
unidentified (confirmed NOT the short-distance base, since that's computed);
not yet cross-checked against a file with a different `WIDTH_LEN` distinctly
proving that field's role either (though it's directly consumed as the
match-length width and has never been ambiguous).

## LZS decode-loop hook: RESOLVED — verified and installed (2026-07-09)

`skyroads/hooks.py::lzs_decode_loop_hook` (CS:IP `1010:6712`) decodes an
entire block in one Python call instead of one interpreted iteration per
symbol — the performance island for asset-loading startup speed. Installed
(`@registry.replace` active) after 15 hook calls verified with zero
divergence (full-memory diff, `dos_re.verification.HookVerifierConfig
.strict`, auto-continuation) across `TREKDAT.LZS` (all 9 records),
`MUZAX.LZS`, and `INTRO.LZS`.

Getting to a clean verify took six real, distinct bugs beyond the codec
formula itself (all in the hook's own state bookkeeping, not the decode
algorithm):
- **BX byte-blend on refetch**: `1010:64BF/651C "mov bx,[41B6]"` then (no
  -refill-needed case) `"mov bl,[bx]"` — only BX's LOW byte gets the fetched
  byte's *value*; the high byte keeps the cursor position.
- **BX is per-call, not global**: `get_bits(n)` does `"mov bx,sp"` at its own
  entry (1010:64FF), unconditionally clobbering BX — so BX's final value
  depends only on the LAST width-consuming `get_bits(n)` call of the LAST
  symbol, not "the whole decode's last refetch."
- **Staging-buffer memory writeback**: this hook reads compressed bytes
  straight from `FileHandle.data`, bypassing the staging buffer entirely —
  but a refill in the real ASM also physically copies fresh bytes into
  `ds:[31A8..41A8)`, which a full-memory diff checks.
- **EOF short-read chunk-tail bleed-through**: when the final chunk of a
  block is a short (EOF) read, the real ASM's buffer only gets overwritten
  as far as the actual read went — the tail still shows the *previous*
  chunk's own trailing bytes, not "stale" pre-hook-call memory.
- **`ds:[41B8]`** holds the *requested* chunk size (constant, matches
  `loaded_this_refill`), not the actual (possibly short) bytes returned.
- **Bogus large-remaining bail-out**: an early defensive guard rejected
  `remaining >= 0x8000` as "probably a wraparound bug," which silently
  skipped decoding entirely for `INTRO.LZS`'s legitimately-large (~64000
  -byte) block. Removed; the only real "already done" case is exact equality.

**Measured impact:** pure-ASM (hooks disabled) needs 144,515 to 1,176,774
interpreted instructions *per LZS block* (11+ blocks during boot: `MUZAX
.LZS`, all 9 `TREKDAT.LZS` records, `INTRO.LZS`, ...) — millions of
instructions total. With the hook, the same 3,000,000-instruction budget that
leaves pure-ASM still stuck decoding the very first file (`CS:IP 1010:6508`,
mid-`get_bits` loop) gets completely through *all* boot-time LZS
decompression and into subsequent loading logic (`CS:IP 1010:6197`) with the
hook installed.

## Palette-fade hook: RESOLVED, verified and installed (2026-07-09)

`skyroads/hooks.py::palette_fade_inner_hook` + `skyroads/recovered/palette_fade.py`
(`blend_byte`, `status="VERIFIED"`) implement the inner blend loop at CS:IP
`1010:43A9` (narrower than the originally-suspected `4331` outer routine — the
outer function calls this inner loop per-byte, which is where the real
per-pixel cost lives). `install_hook_verifier` confirmed 34,439 hook calls
(~45 full passes) with zero divergence after fixing three real bugs: a
register-writeback miss, an `IDIV` remainder bug, and `LES` only being modeled
for its offset side-effect and not its `ES` side-effect. Installed via
`@registry.replace`; measured 6.7x speedup on the intro fade. See
`symbol_ledger.md` for the full bug list and `run_status.md` for the
measurement. No further action needed here.
