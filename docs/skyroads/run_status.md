# SkyRoads run status

> Dated progress log — sections state what was true at their date. For the
> ledger of per-routine evidence see [`symbol_ledger.md`](symbol_ledger.md);
> open issues are in [`blockers.md`](blockers.md).

## 2026-07-08 — bring-up: boots, first island (asset decompressor) recovered

**Boots and runs stably.** `assets/SKYROADS.EXE` boots and runs in the `dos_re`
VM (confirmed over a 300M-instruction / 1500-simulated-frame soak, rendering
the real title/attract-mode checkerboard road in VGA mode 13h). Framework-level
gaps fixed to get there (all in `dos_re/`, not game-specific):
- INT 10h AH=1Ah (Get/Set Display Combination Code) — `dos_re/dos.py`.
- PIT channel-0 counter read-back (SKYROADS busy-waits on the raw hardware
  counter via a latch command on port 43h + `IN AL,40h`, not through IRQ0) —
  `dos_re/dos.py`. Deterministic default ages the counter from
  `cpu.instruction_count` when no wall-clock `time_source` is set.
- 80186 `PUSHA`/`POPA` (opcodes 0x60/0x61) — `dos_re/cpu.py`, not implemented
  at all before this.
- INT 21h AH=0Bh (check stdin input status) — `dos_re/dos.py`.
- The title-screen idle loop separately needs the INT 08h timer tick
  delivered (real IRQ0, not just the PIT counter model) — a raw
  `create_runtime` + `cpu.run()` probe with no IRQ pump appears to hang here;
  it isn't a bug, see `scripts/play.py --timer-irqs-per-frame`.

**Adapter scaffold.** `skyroads/` package created (`runtime.py`, `hooks.py`,
`verification.py`, `frame_verify.py`, `input_waits.py`, `recovered/`,
`bridge/`, `codecs/`, `probes/`), wired into `tools/lint.py`, covered by
`tests/test_skyroads_boot.py` (skips without `assets/`). No hooks recovered
yet — everything currently runs as pure ASM oracle.

**Interactive runner.** `scripts/play.py` — a thin `GameFrontend` over the
unified `dos_re.player` runner (standard CLI: viewer by default, `--headless`
to disable; F11 demo record/stop, F12 snapshot save, F10 screenshot;
`--snapshot` resume, `--play-demo` replay). Verified end-to-end:
record → replay reaches byte-identical CS:IP/registers/instruction-count;
snapshot-save → resume continues correctly (matches a fresh continuous run).
Deterministic by construction: both live play and replay use the same fixed
(steps-per-frame, timer-irqs-per-frame) budget per frame with no wall clock,
so — unlike a fully-tuned adapter — record/replay determinism needed no
extra clock-model work.

**First island: the `.LZS`/`.DAT` asset decompressor.** Traced live via
`tools/profile_hotspots.py` (hottest region CS:IP `1010:64A0`-`1010:675E`
while loading `TREKDAT.LZS`) + a forced linear disassembly + register-level
single-step trace of the *live* code (this routine is copied/patched into the
code segment at runtime — it reads as all zero bytes in the static EXE
image). Recovered into `skyroads/codecs/lzs.py`, status **OBSERVED** (traced
from the oracle, not yet round-trip verified — see `symbol_ledger.md` and
task tracking for the open ends).

Algorithm: an MSB-first bit reader refilled from a 4KB file-backed staging
buffer, feeding a 3-way LZ77-style loop — one flag bit selects a
long-distance match, else a second flag bit selects a short-distance match or
a raw 8-bit literal; match length and both distance variants use bit-widths
read as 3 raw header bytes and patched into the decoder as self-modifying
immediates before the loop starts.

**External cross-reference (2026-07-08):** the independent RE project
[ammaarreshi/SkyRoads-Codex](https://github.com/ammaarreshi/SkyRoads-Codex)
(a from-scratch native Rust port, DOSBox-X + static-analysis based, not
affiliated with this project) published structurally matching findings —
notably "3 bytes: SkyRoads compression widths" per compressed block, and
concrete widths `(4, 10, 13)` for `TREKDAT.LZS` / `(6, 10, 12)` for
`MUZAX.LZS`. This corroborates but does **not** replace our own oracle
verification (pitfalls.md #21 — an external write-up is a hypothesis to
check against our VM, never a source to copy blind); it is however a very
useful map of the surrounding file formats we have not yet traced ourselves:
`CMAP`/`PICT` image chunks, `ROADS.LZS`'s 31-entry offset table, `MUZAX.LZS`'s
song table, `DEMO.REC`'s control-byte decode, and the dashboard `*.DAT`
fragment format. Treat every one of its claims as a lead to verify against
our own trace, not a ground truth — see `blockers.md`/task tracking for what
that verification pass should check first (their published `TREKDAT` record
header layout: `load_buff_end:u16, bytes_to_read:u16, widths:3 bytes,
payload` lines up with what we traced independently and is the natural next
thing to confirm byte-for-byte).

## 2026-07-08 (cont'd) — LZS bug fix + performance hot-spot found

**LZS decoder: found and fixed a real bug via oracle byte-diff.** Round-trip
verifying `skyroads/codecs/lzs.py` against `TREKDAT.LZS` record 0's actual
decompressed memory (dumped from a fresh boot, segment `2B12`) found the
match-length formula was wrong: `get_bits(WIDTH_LEN)+1` should be `+2` (the
ASM's `LOOP` body does `get_bits(WIDTH_LEN)+1` copies, plus one more
unconditional `movsb` afterward). Fixing it took the exact-byte match from
933/18072 to 8964/18072 (~50%). A further, precisely localized divergence at
output-relative byte 2938 (in a short-distance match) remains open — logged
in `blockers.md` with the full symbol-trace evidence rather than guessed at
further, per the project's own "two focused attempts, then log it" rule.

**Performance: found the dominant hot loop, it's a palette fade, not (yet
confirmed) pixel drawing.** From an owner-captured snapshot at the intro
fade-in (`artifacts/snapshot_skyroads_20260708_165846`),
`tools/profile_hotspots.py` found a ~40-instruction loop at CS:IP
`1010:43A9`-`442D` dominating a 3M-instruction profiling window (~57K hits).
Disassembly + a snapshot-based trace identified it precisely: a per-byte
linear interpolation between two palette arrays for a fade transition (see
`symbol_ledger.md`). The intro does not appear to auto-advance past this
fade on a timer (confirmed independently by SkyRoads-Codex's own DOSBox-X
trace notes) and repeated keypress injection didn't unstick it either within
our probing budget, so we have not yet reached the actual gameplay
road/pixel renderer to confirm whether IT is also a big win. This fade loop
is nonetheless a real, well-evidenced, high-value hook target on its own —
not yet hooked, because its stack-frame indexing has the same kind of subtle
off-by-one risk the LZS bug just demonstrated is real on this codebase; the
right next step is writing the hook AND running it under
`dos_re.verification.install_hook_verifier` (strict/auto-continuation mode)
before trusting it, not hand-verifying by inspection.

## Next up
- Round-trip verify `skyroads/codecs/lzs.py` against the oracle's own
  decompressed memory (not just plausibility) — pick a small single-record
  asset, pull the real header bytes from VM memory, diff.
- Find the frame boundary (present/blit routine) so the frame verifier can be
  stood up (`docs/porting_new_game.md` step 3-4).
- Build the input-wait registry for the title/menu polls (step 5) before
  recording any demo intended as a regression asset.
