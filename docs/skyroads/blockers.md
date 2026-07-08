# SkyRoads blockers

## LZS decoder: residual bit-stream divergence after ~2938 output bytes (open)

**Evidence.** Round-trip verifying `skyroads/codecs/lzs.py` against the oracle's
own decompressed `TREKDAT.LZS` record 0 (segment `2B12`, dumped via a targeted
probe) found and fixed a real bug: the match-length formula was
`get_bits(WIDTH_LEN) + 1`; the ASM copy loop at `673C`-`6742` actually does
`CX = get_bits(WIDTH_LEN) + 1` matches inside the `LOOP` body **plus one more
unconditional `movsb` at `6740`** — total copies = `get_bits(WIDTH_LEN) + 2`.
Fixing this took the exact-byte match from 933/18072 to 8964/18072 (~50%).

A precise symbol-by-symbol trace (oracle: single-stepped register trace
classifying each loop iteration as literal/long-match/short-match by which of
`671E`/`6744`+`6755`/`6744`+`674B` executes, with `di` before/after; Python:
an instrumented copy of `decompress_block` yielding the same tuple) confirms
the two decoders agree bit-for-bit through many literals and matches, then
diverge at output-relative position 2938: a **short-distance match**. Oracle's
raw `get_bits(WIDTH_DIST_SHORT)` value (read from `cpu.s.ax` at `6723`, minus
the `+1` from `6750`) = 1115; the Python decoder's raw value at the identical
stream position = 92. Everything before this symbol (literals need
bit-exact sync to match at all) is confirmed correct, so the bit reader itself
is right up to this point — something about this ONE symbol (or state
carried into it) is wrong.

**Ruled out** (empirically, not guessed): a brute-force sweep of the three
formula constants (long-distance +0..+4, short-distance +0..+4, length +0..+4)
found no combination that improves on the current fix — so this is not a
second simple off-by-N constant.

**Suspected but unconfirmed:** the refill path has an asymmetry I noticed but
haven't pinned to this exact position — `get_bit()`'s inline refill (at
`64B9`-`64D6`) increments `ds:[41B6]` once itself before calling `6350`, and
`6350`'s own tail (`638F`-`639C`) increments `ds:[41B6]` AGAIN and reloads
`ds:[41B0]` independently — a real 4KB-window refill could plausibly
skip/duplicate one byte relative to a flat single-buffer model like
`skyroads/codecs/lzs.py`'s `_BitReader` (which never models chunk refills at
all, since it's handed the whole payload up front). But the divergence is at
payload byte ~2173, nowhere near the first window's actual exhaustion point
(first read is 4096 bytes covering the record's own 7-byte header + payload,
so first refill is expected around payload byte ~4089) — so this specific
theory doesn't cleanly explain THIS divergence either. Also not yet checked:
the unexplained `ds:[41AA]`/`ds:[41BB]`/`ds:[41BC]` flags in `6350`'s helpers.

## Palette-fade hook: can't yet catch a clean verifier boundary (open)

`skyroads/hooks.py::_palette_fade_hook` + `skyroads/recovered/palette_fade.py`
implement CS:IP `1010:4331` per the traced algorithm in `symbol_ledger.md`, but
are **not verified and not installed** (no `@registry.replace` decorator
active). Two attempts to catch a live re-entry to `0x4331` for
`dos_re.verification.install_hook_verifier` to check against — one scanning
200M instructions from a fresh boot, one scanning 400M instructions forward
from the owner's intro-fade snapshot (which is already mid-call to this exact
function) — both found zero further entries. Combined with never leaving a
~43-address working set across 800M+ cumulative instructions in earlier
probing, this contradicts the "called once per outer game frame" mental model
implied by the `4B90`/`4BDD`/`4BF1` call sites traced earlier — either this
specific screen calls it far less often than assumed, or something about the
exit path (`4455`/`4457`/`4458` region, not yet traced) loops back without
going through a `call 4331` at all for a very long stretch. Do not trust the
hook until an actual verifier run against the real ASM succeeds — next
attempt should either construct a synthetic call frame at a known-good state
(risk: guessing the stack layout wrong defeats the point of verifying) or
resume from a snapshot captured well past this specific screen.

**Rule per docs/pitfalls.md #20:** this has resisted more than two focused
trace attempts — logged here rather than continuing to guess. Next attempt
should single-step the OWN oracle bit-by-bit (not just symbol-by-symbol)
starting a few symbols before position 2938, printing every raw bit value
`get_bit()` returns, and diff that bit sequence directly against
`_BitReader`'s bit sequence for the same payload range — narrower than
comparing decoded symbols, and would immediately show whether the desync is a
bit-reader issue (refill-related) or a branch-selection issue.
