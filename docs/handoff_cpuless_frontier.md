# Handoff: CPUless cold-start frontier (skyroads, 2026-07-18)

Bootstrap for an agent picking this up, possibly coordinating with the Overkill
port. Written at the end of a long session; everything here is committed and
pushed unless marked otherwise.

**Read §4 first if you have read a previous version of this document.** The
frontier it described was a misdiagnosis, and the correction changes what the
next task is.

---

## 1. The model (read this first — it governs every decision below)

**The generated corpus is the SKELETON. Hand-recovered code is SKIN.**
Recorded as the third hard rule in [`architecture.md`](architecture.md).

The skeleton is the game's real control flow — not an approximation, but the flow
itself, lifted from the original's own code and proven byte-exact against the
oracle from cold start. Skin replaces what ONE address *means*, attaches at that
address only, and never holds flow. Every address without skin is served by the
generated body, so the composite always runs; there is no half-converted state.

Why it matters: the previous hand-written port DID re-implement flow, and its
menu flow drifted. `skyroads/native/menus.py` and `level_select.py` carry **zero**
recovered-address anchors between them — flow inferred from the screen, not
derived from the program. `tools/absorption_ledger.py --native` measures this.

**Two other hard rules**, both enforced by tests, both learned the expensive way:

- *Package boundary* — game specifics never enter `dos_re`.
- *Artifact boundary* — `artifacts/` never holds live or authoritative code, and
  **every gate must verify exactly the artifact that ships**. Enforced by
  `tests/test_artifact_discipline.py`. This was violated silently for weeks:
  `verify_vmless_demo` defaulted `--lift-dir` to an orphaned `artifacts/lifted_full`
  while the runner imported `skyroads/lifted/functions`. Both agreed byte-for-byte
  on 182 shared modules, so the gate stayed green until a census added three
  functions to one and not the other.

---

## 2. The method (owner directive — follow it, do not improvise)

Work the cold path **forward**:

1. Oracle + one cold demo = the authoritative path.
2. Run the CPUless candidate beside it.
3. The **first** divergence is the frontier. Nothing else is in scope.
4. Localize the earliest differing *observable effect*.
5. Instrument the function/boundary that **actually produces it** — not
   likely-looking areas, not arbitrary functions, not out of order.
6. Repair only that seam.
7. Rerun the **whole** demo from frame 0. The frontier advances only if the
   earliest divergence moves.
8. Repeat.

**Step 4 has a precondition that was missing, and its absence cost this project
an entire investigation: before you localize a divergence inside the CANDIDATE,
prove the ORACLE is the original program.** See §4 and §5.

Goal: ONE continuous oracle-proven path from startup, not a collection of
locally passing islands.

**Acceptance** is *not* "the generated corpus passes". It is: **an E2E cold demo
passes against the oracle with the manual overrides DRIVING** (`OVERRIDES`
non-empty). With `OVERRIDES` empty the composite is bit-for-bit the generated
program, so shadow mode alone does not clear the bar.

---

## 3. Current state

`dos_re` submodule tracks branch **`abi-recovered`** (not main) @ **`4dcf7d0`**.
Another agent works M3b→M4 there; fetch before touching it. `4dcf7d0` added
`create_runtime(install_replacements=)`, `registry.uninstall(cpu, keep=)` and
`hooks.assert_pure_oracle(cpu, allow=)` — see §4 for why.

```
skyroads/lifted/functions/   generated, VM-aware   (186 modules)
skyroads/recovered/          generated, CPU-free   (180 + 2 dead-unreachable)
skyroads/handrecovered/      42 islands, @oracle_link, address-keyed
skyroads/native/             21 subsystem modules, NOT address-keyed, 0 registered
```

Manifest: 180 generated-cpuless, runtime frontier 0, closure complete.

**`OVERRIDES` IS NO LONGER EMPTY.** `1010:04C0` drives, so a PASS below is no
longer a statement about generated code alone — for that one address. See §7.

**Both differentials pass, against a PROVEN-pure oracle**, over the frame ranges
they were actually run on (§4 — the attract demo's frames 1300+ were not among
them). `check_all` 6/6; port suite 475 passed / 1 skipped; lint passed. Port
HEAD: `a0904a4`.

### Commands

```sh
python scripts/rebuild_all.py     # THE pipeline, in the only correct order
python scripts/check_all.py       # every gate, one verdict (--quick skips diffs)
python scripts/coverage_audit.py  # dispatch-table gaps before a player finds them
python tools/absorption_ledger.py [--native|--unstitch]

# a differential by hand: pypy3 is a drop-in, -u keeps the heartbeat live
pypy3 -u scripts/verify_cpuless.py artifacts/demos/demo_attract_20260718_135434
```

**`check_all.py` runs the differentials under PyPy automatically** (2026-07-18)
and prints the interpreter per gate; `--no-pypy` forces CPython. Identical
gates, **7/7 in 1 m 50 s** (measured) against the ~6 min previously recorded —
see [`cpuless_standalone.md`](cpuless_standalone.md) for the full timing table
and the CPython/PyPy agreement evidence.

**Pipeline order is load-bearing**: `build_codemap` → `close_vmless_wall` →
`build_recovered`. Skipping the middle stage leaves new functions with no IR
entry; callers then refuse `contains-call`, which once cascaded to five refusals
including `1010:61F3` (the C-startup root) and broke the runner outright. The
build now also fails loud on an IR older than the census.

**The differentials are several times slower than they used to be, and that is
correct.** A genuinely pure oracle interprets the real blitters and decompressors
instead of calling their Python replacements. Measured peak **17.1M steps/frame**;
the default `--step-budget` is now **64M** and run-to-cut **raises** on
exhaustion. Do not lower it to make something fast.

### Demos (roles are distinct — do not consolidate)

| demo | frames | role |
|---|---|---|
| `demo_cold_20260718_134357` | 261 | spine; **PASSES**; fast acceptance vehicle (~160 s now) |
| `demo_attract_20260718_135434` | 5109 | **ZERO input**; **PASSES ALL 5109 frames** (2026-07-18, both interpreters) — §4. 47 s under PyPy, so it is now cheap enough to gate |
| `demo_cold_20260718_003412` | 672 | older spine; passes (the `verify_vmless` gate demo) |
| `demo_intro_20260717_125403` | 1832 | older attract; previously "failed f1115" — same misdiagnosis |

Record cold demos with `python scripts/play.py --record-demo NAME` — it starts at
frame 0, which is what makes them cold. Pressing F11 later writes a start
snapshot, and `verify_cpuless` rejects snapshot demos outright.

---

## 4. THE FRONTIER: RESOLVED, AND IT WAS A MISDIAGNOSIS

**The previous §4 was wrong. `1010:6168` was never defective. `skyroads/recovered/`
was correct throughout. The ORACLE was the deviant party.**

### What was actually happening

`skyroads/runtime.py`'s `create_game_runtime(install_replacements=False)` — the
"pure-ASM oracle" — gated the hooks **import**, not the **installation**:

```python
if install_replacements:
    from . import hooks          # gates nothing
rt = create_runtime(...)         # installed the registry unconditionally
```

Hooks register at **decoration time**, and `skyroads.hooks` is already in
`sys.modules` transitively by the time any harness calls this. So the registry
was populated regardless, and all **29** registered replacements were live on the
"pure" oracle — including **`1010:434A fade_loop_tick_gate`**, a *deliberately
behaviour-changing* optimisation that skips loop passes and thereby suppresses
one `1010:6168` invocation per frame.

That suppressed invocation *was* the entire claimed frame-1115 DAC divergence.
The differential was comparing the candidate against a **modified** original and
reporting the modification as a candidate defect.

`load_game_snapshot` had always done this correctly (import **and** explicit
`registry.install`), so only the **cold** path was affected — which is exactly
the path `verify_cpuless.py` uses.

Fixed in `a0904a4`: the flag is passed down to `dos_re`'s `create_runtime`, which
strips the registry **after** boot (order-independent). The import stays
unconditional because it is what populates the registry for the `True` case;
installation, not import, is what the flag gates. Pinned by
`tests/test_oracle_purity.py`, which imports `skyroads.hooks` **first** — a test
that only ever ran in a fresh process would have passed throughout the bug's life.

### The second bug, uncovered by fixing the first

With the accelerators (`lzs_decode_loop`, `intro_anim_unpack`, the blitters) gone
from the oracle, every frame blew the 4M step budget — and **both** harnesses'
run-to-cut loops returned **silently** on exhaustion. The oracle sat parked
mid-frame, still looking authoritative, and the differentials reported a frame-0
divergence that was entirely their own.

Both now **raise**, with a 64M default against the measured 17.1M peak.

### What was retracted, and what stands

- `1010:6168` is **innocent**. Do not "repair" it. The write-loop bound, count
  source, direction flag and termination were never wrong.
- The instruction *"Do NOT modify `skyroads/cpuless_driver.py`"* was **right for
  the wrong reason**. The driver was innocent — but so was the candidate
  generally. The prohibition still stands; the reasoning behind it does not.
- The port-3C8 write counts in the old §4 were **real measurements of a
  contaminated oracle**. They are not evidence of anything about the candidate.

### Acceptance evidence for the fix

```
$ python scripts/verify_cpuless.py artifacts/demos/demo_attract_20260718_135434 --frames 1300
[verify-cpuless] oracle peak 17126327 steps/frame (budget 64000000)
[verify-cpuless] PASS -- 1300 frames: VGA plane AND DAC palette identical to the
ASM oracle, NO CPU, over demo_attract_20260718_135434
```

Frame 1115 included. Spine demo passes its full 261 frames. `check_all` 6/6.

### The isolation that prevented a second wrong answer

When the fix first made *both* demos fail at frame 0, that failure was **not**
accepted at face value:

| configuration | result |
|---|---|
| strip nothing (the old contaminated oracle) | PASS |
| strip **only** `fade_loop_tick_gate` | **PASS** |
| strip everything **except** the fade hooks | **FAIL at frame 0** |
| strip everything, `--step-budget 200000000` | **PASS** |

Row 2 is the important one: it proves the fade hook was never the frame-0 cause,
and sent the investigation to the step budget instead of back into the candidate.

### RESOLVED 2026-07-18 — the attract demo passes ALL 5,109 frames

**The 3,809 unrun frames have now been run.** Under both interpreters,
end-to-end, no truncation:

```
[verify-cpuless] demo=demo_attract_20260718_135434 frames=5109 mouse_present=True;
                 cut = 2nd pass at 11 boundary heads
[verify-cpuless] oracle peak 17126327 steps/frame (budget 64000000)
[verify-cpuless] oracle captured 5109 frames
[verify-cpuless] candidate captured 5109 frames
[verify-cpuless] PASS -- 5109 frames: VGA plane AND DAC palette identical to the
                 ASM oracle, NO CPU, over demo_attract_20260718_135434
```

| run | wall clock | verdict |
|---|---|---|
| CPython 3.11 | 500.8 s (8m21) | PASS, 5109 frames |
| PyPy 3.11 v7.3.20 | **47.3 s** | PASS, 5109 frames |

Both logs hash identically (sha1 `1da10d27…`, timing heartbeats excluded), so
this is one result observed twice, not two results. The **20–50 min** estimate
recorded below was wrong in both directions: CPython came in under it, and PyPy
turns the whole thing into a 47-second gate.

**What this does and does not establish.** It establishes that over these 5,109
frames the CPUless corpus is byte-identical to a *proven-pure* oracle in both
the VGA index plane and the DAC palette, with `1010:04C0` DRIVING. It does NOT
establish that the corpus is clean: this is one demo with **zero input events**,
so it exercises the attract path and nothing a player does. §10's lesson stands
— "672 frames byte-exact" was restated as a general claim once already and a
second demo then found a divergence. The honest statement is **"no known
frontier remains on the demos that have been run"**, and the lever on that is
DEMO BREADTH, not more frames of this one.

<details><summary>The superseded section, kept because its reasoning still applies</summary>

The frontier was not "none", it was UNKNOWN, and the distinction was the whole
point. What had been **observed**:

| claim | status |
|---|---|
| attract demo, frames 0–1299 | **PASS**, observed |
| spine demo, all 261 frames | **PASS**, observed |
| `verify_vmless` 672-frame demo | **PASS**, observed (gate) |
| attract demo, frames 1300–5108 | **NOT RUN — NOTHING IS KNOWN** |

The full-length run was started and then **abandoned before producing any
output**; no partial result beyond frame 1300 was ever observed, so none was
recorded. `--frames 1300` was chosen originally only because the alleged
frontier sat at 1115 — a cut inherited from a misdiagnosis, not a meaningful
boundary. That "abandoned before producing any output" is itself a fixed bug:
the oracle phase printed NOTHING for its entire duration, so a live run, a
wedged run and a finished run all looked the same. `verify_cpuless.py` now
emits a flushed `[oracle]`/`[candidate]` heartbeat with frame index, rate and
ETA — run it with `-u` and you can see it working.

</details>

This document previously reported "672 frames byte-exact" as though it were a
general claim; it was demo-specific and a second demo then found a divergence.
§10 records that as a lesson. **"Passes to 1300" is exactly the same shape of
claim.** Do not restate it as "the attract demo passes", and do not conclude the
corpus is clean.

#### RECORDED NEXT STEP — not yet run

```sh
python scripts/verify_cpuless.py artifacts/demos/demo_attract_20260718_135434
```

**Estimated cost, extrapolated — NOT measured**: the 1300-frame run took roughly
5 minutes wall clock, essentially all of it oracle. Scaling linearly to 5,109
frames gives **~20 minutes**, and a slower per-frame figure taken from the
`check_all` gate timings would put it nearer **~50 minutes**. Call it **20–50
minutes**, oracle-dominated, and budget generously — the pure oracle is several
times slower than the contaminated one it replaced (§3). Run it in the
background; note that Python buffers stdout when redirected, so the log stays
empty until the process exits.

Then, in order:

1. **If it diverges**: *that* frame is the real frontier. Before localizing
   anything inside the candidate, confirm `assert_pure_oracle` passed on the
   oracle you are comparing against — that check is now built into the harness
   and is the precondition the last investigation lacked.
2. **If it holds to 5109**: there is no known frontier left in the corpus, and
   the bar moves entirely to §9 item 2 — acceptance with `OVERRIDES` **driving**.
   With `OVERRIDES` empty the composite is bit-for-bit the generated program, so
   every "PASS" in this document, at any frame count, is a proof about generated
   code only.

Do not go looking for a defect in `skyroads/recovered/` on the strength of the
old §4. There is currently no measurement pointing at one — but note that "no
measurement points at a defect" over a range **that was never measured** is not
evidence of absence.

---

## 5. Instruments: what works, and what lied

**WORKS — use this.** Hook `DOSMachine.port_write`, filter the port you care
about, and walk `traceback.extract_stack()` matching `func_1010_([0-9a-f]{4})$`
to capture the recovered call chain at the observable. Both machines can be
observed at the same point, so their numbers are comparable.

**Always run an UNGATED control first.** A frame-windowed aggregate that reads
zero is indistinguishable from "never happened". A 10-second ungated run
(`does this fire at all?`) would have prevented three wrong conclusions here.

**When two instruments disagree, first check they measure the same QUANTITY.**
Chain occurrences at port writes count **writes, not invocations**. Reading
write-multiplicity as call-multiplicity produced a confident, wrong localization.

### Negative evidence — do not rediscover

- **THE ORACLE CAN BE THE DEVIANT PARTY.** Before localizing a divergence inside
  the candidate, **prove the reference side is the original program**. A
  differential is only evidence if it is. `dos_re.hooks.assert_pure_oracle(cpu,
  allow=...)` exists for exactly this and is now called by both harnesses —
  **use it in any new one.** Guarding a hooks *import* does not make an oracle
  pure: the registry is populated at decoration time and something has always
  imported the module already. Strip after boot instead. This cost an entire
  investigation — a behaviour-changing optimisation hook on the reference side
  produced a divergence that was then attributed to the candidate.
- **A FAIL-QUIET BUDGET OR LIMIT IS INDISTINGUISHABLE FROM A PASSING
  MEASUREMENT.** Both bugs in this session were fail-quiet: one installed hooks
  where it claimed not to, the other truncated the oracle where it claimed to
  finish. Neither produced an error; both produced a plausible wrong answer.
  **Prefer raising over returning.** When you add a step budget, a frame cap, a
  retry count or a search bound, make exhaustion an exception, not a return.
- **Entry counters** (`install_shadow` wrappers + `cs:ip` step hooks) produced
  garbage in this investigation. Re-running after the seam fix changed nothing.
- **"Candidate runs an extra boundary pass"** — REFUTED by measurement.
- **"The extra invocation originates at or above `4591`"** — REFUTED by the
  ungated control; it was the write-vs-call misreading.
- **Do NOT modify `skyroads/cpuless_driver.py`.** Every gate depends on it and the
  byte-exact results rest on its cut semantics. Three hypotheses pointing there
  have now been refuted.
- **Do NOT infer execution counts** from boundary arrivals or write counts.

---

## 6. The stitch seam (shared with Overkill)

Lives in **`dos_re/lift/standalone.py`**: `install_overrides(package, overrides)`,
`generated(package, key)`, `uninstall_overrides(package)`.

Shadowing `sys.modules` alone is **unsound** — independently confirmed broken in
both ports:

- a caller that already imported the callee holds a direct reference
  (`from pkg.func_1010_XXXX import func_1010_XXXX` binds at import time);
- `_dyncall._cache` memoises the resolved closure on first call.

Both fail **silently**. Both ports' test suites passed beforehand because each
only exercised the favourable import order. `install_overrides` therefore
retro-patches already-bound references and clears the dispatch memo, so
installation order no longer matters.

**Scoping matters**: the retro-patch is keyed on the callee's own *name*. Overkill's
first fix was broader and rebound the oracle copy a delegating override calls —
which would have made the differential compare the override against **itself and
still pass**. Reach the original via `generated()`, never a cached direct
reference. Pinned by `dos_re/tests/test_lift_standalone_overrides.py` and, for the
over-broad-rebind case, by `4c3b505`.

Note the family resemblance to §4's bug: **an import-time binding that a later
guard cannot retract.** The stitch seam and the oracle-purity flag were defeated
by the same mechanism in two different subsystems, and both failed silently. If a
switch is meant to gate behaviour, gate the behaviour — not the import that
precedes it.

`skyroads/cpuless_overrides.py` is **now a thin registry** over the shared seam
(226 → 107 lines) and carries no mechanism of its own. The shared version was
already the better one: it normalises addresses through `int(x, 16)`, so
`'1010:4C0'` and `'1010:04C0'` name the same module where the local
string-lowercase silently did not, and it restores bindings **by name** rather
than by attribute presence.

Do **not** route a port through `run_recovered`: it calls `fn(mem, plat, **regs)`
and this port's pure-compute bodies take no `plat`.

**`dos_re/lift/shadow.py` is the other half of the seam** — see §7. Overkill
should adopt it rather than write a checker: the callback shape it replaces is
what let a checker compare one register out of ten and report a five-figure
agreement count.

---

## 7. Islands / absorption — 04C0 NOW DRIVES

42 islands: **3 VERIFIED, 39 ASM_MATCHED**. 20 anchor to an IR function; **22
anchor to nothing** — formats, helpers, data layouts. Those 22 are knowledge for
the Memory Schema (M4), never override bodies.

`ASM_MATCHED` ("diffed on captured cases") is **weaker** than the byte-exact
standard the generated corpus already meets, so stitching them on recorded status
would LOWER the proof standard. **Shadow mode is the rung between**, and it now
lives in `dos_re/lift/shadow.py` (Overkill needs it too).

### The shadow rung, rebuilt — read this before trusting any "calls agreed" number

The old shadow took a `checker(mem, kw, outputs, compat)` **callback**, which left
each port free to compare whatever it liked. The 04C0 checker compared **AX and
nothing else** — not the other six outputs, not flags, not fmask, and not the 25
words the body leaves on the stack — while tallying cost into a counter that *read*
like an assertion and asserted nothing. Its "14,802 calls agreed" was true of AX
alone. **A checker that quietly compares less is indistinguishable from one that
compares everything**; that is why the callback is gone.

A candidate is now a **drop-in** with the generated signature returning the
generated `(outputs, compat)` pair (`skyroads/island_bodies.py`), and
`dos_re.lift.shadow` does the comparing — total by construction, and the artifact
under proof is the artifact that ships. Comparing less requires an `Exemption`
whose `reason` is REQUIRED (an empty one raises). Memory is an ordered byte-write
log; the candidate runs first on an overlay proxy so it sees the exact pre-state
and cannot perturb the run. Verdicts come from `abi_diff`'s lattice, and **a
shadow that was never called is INCONCLUSIVE, never VERIFIED.**

That last rule earned itself immediately: the first negative control for 04C0 ran
over 120 frames, and 04C0 **is not called before frame ~200**. The control proved
nothing and looked like a pass; INCONCLUSIVE is what caught it.

### 1010:04C0 perspective_row_offset — ABSORBED, DRIVING

The contract, derived from the body and then measured:

| | out of range (idx ≥ 0x142) | in range |
|---|---|---|
| ax | 0 | `ds:[offset]` |
| bx | **caller's** (never written) | offset |
| cx | 0x80 | offset |
| dx | depth % 128 | idx % 46 |
| bp, si, di | caller's — callee-saved on BOTH paths | same |
| flags | from `cmp si,0x142` | from the final `add cx,ax` |
| fmask | 0x8D5 | 0x8D5 |
| cost | 19 | 104 |
| memory | 3 frame saves | + 15 argument words + 4 callee prologue saves |

**The "one genuinely unknown quantity" is measured and it widens nothing.** On the
frames 04C0 builds, 5D8C's divisor high word is always 0 and 5D4C's multiplicand
high word is always 0, so both always take their short path: fmask 0x8C5 / cost 21
and 0x8C5 / 12. The union 0x8C5 is a strict **subset** of 0x8D5 — neither ever
writes AF, neither touches DF or IF — and 12+34+4+21+21+12 = 104 exactly.

Evidence, all against the FULL contract, zero disagreements:

- 6,000 seeded random states, both paths forced (3,228 / 2,772) — `tests/test_island_bodies.py`
- `demo_cold_20260718_003412` — 14,802 calls `{104: 12822, 19: 1980}`
- `demo_colde2e_full_20260713_144604` — 125,728 calls `{104: 125604, 19: 124}`
- **E2E cold differential with it DRIVING**: 261 frames and 672 frames, VGA plane
  and DAC palette identical to the pure ASM oracle.

**Validating on the spine demo alone would have been useless**: it never takes the
short path, so its cost model is the constant 104 and is silently wrong.

### The negative control, and what the E2E differential CANNOT see

"Installed" and "reached" are different claims, so the override was perturbed:

| perturbation | result |
|---|---|
| in-range AX + 1 | **VGA DIVERGES at frame 307** — the body is genuinely on the path |
| short-path cost 19 → 20 | no divergence (only 53 of 4,065 calls in the window take it) |
| one exit flag bit | no divergence (flags are merged by the caller, not branched on) |

So **acceptance proves 04C0's VALUE and not its flags or its short-path cost.**
The shadow proves those, exactly, on all 140,530 real calls. The two gates are not
redundant, and `check_all` now runs both.

### 1010:3A96 — LEFT ALONE, and its status overstates its evidence

Do not stitch it. It has no drop-in body, and the checker that stood in for one
compared **one 64 KB segment per call** (the one named by the post-state `es`) and
**no register, flag, fmask or cost at all**. Its recorded "full 64 KB segment
byte-exact" is materially narrower than VERIFIED suggests. An inherited survey also
reports the pre-state snapshot overlooking ~39 KB of overlap accumulated by
preceding passes; **that figure was NOT re-measured here** and is recorded as a
caution, not a result.

## 8. Coordinating with Overkill

**Their blocker is gone.** `dos_re` `4dcf7d0` also taught the CPUless emitter to
represent `setjmp`/`longjmp` (`mov sp, m16` falling into `ret` is a non-local
exit; it is now terminal and fail-loud rather than cascading to `contains-call`).
Overkill is committed and pushed as **`9032dd6`**.

Their state now:

- CPUless census **591/626 → 623/626** promotable;
  `contains-call` 20→0, `boundary-head-on-transfer` 10→0, `sp-as-data` 2→0.
- Top level **`1010:96C8` promotes with ZERO overrides.**
- Runtime closure from `1010:97B2` is **253/253, frontier 0**.

**Their remaining gap**: the ten top-level entries are **STANDALONE-ONLY** — no
CPU-ABI adapter installed — and their **cold-start differential has still not
run**. They have a promoted, closed graph that has not yet been proven
frame-exact against an oracle.

That inverts the old division of labour: skyroads has the proven path and needs
the acceptance bar; Overkill has the promoted graph and needs the proof. Shared
concerns remain the `dos_re` seam, the islands ladder, and the §5 instrument
lessons — **especially** the oracle-purity one, since Overkill is about to stand
up a cold-start differential for the first time and will build its reference side
from scratch. They should call `assert_pure_oracle` on it from day one rather
than rediscovering §4 independently.

---

## 9. Open tasks

1. ~~**Frontier**: run `demo_attract_20260718_135434` to its full 5,109
   frames.~~ **DONE 2026-07-18** — PASS on all 5,109 frames under CPython
   (500.8 s) and PyPy (47.3 s), byte-identical output. See §4. The frontier is
   now *absent on the demos that have been run*, which is not the same as
   *absent*: this demo has ZERO input events. **The successor top item is DEMO
   BREADTH** — a demo that exercises player input end-to-end would prove
   something this one structurally cannot.
2. ~~**Acceptance** with `OVERRIDES` driving.~~ **DONE** — see §7. `1010:04C0`
   drives, and both cold differentials pass with it driving. Note carefully what
   that does and does not prove: it is ONE address, and the E2E differential is
   insensitive to its flags and short-path cost (§7's control table). The corpus
   remains overwhelmingly generated.
3. Add `demo_attract_20260718_135434` to `check_all.py` so the gate covers two
   cold demos. `check_all` is now **7/7 in ~6 min** (the port suite moved to
   `-n auto`, 263s → 47s, which paid for the new shadow gate at 17s).
4. ~~Reduce `skyroads/cpuless_overrides.py` to a wrapper.~~ **DONE** — 226 → 107
   lines over `dos_re.lift.standalone`.
5. **Next absorption candidate**: nothing is queued. The bar for the second one
   is the same as the first — a drop-in body, shadow-VERIFIED on demos that
   exercise every path, then driving. Note that 39 of the 42 islands are
   `ASM_MATCHED` and 22 anchor to no IR function at all, so the pool of
   absorbable addresses is much smaller than "42".

## 10. Things I got wrong (so they are not repeated)

- Claimed `3A96`'s contract was under-specified. It was not; my checker bound the
  entry `ds` while the body reloads it. **Withdrawn.**
- Claimed the boundary driver ran an extra pass. **Refuted by measurement.**
- Claimed the doubling originated at/above `4591`. **Refuted by an ungated control.**
- Reported "672 frames byte-exact" as a general claim. It was **demo-specific**;
  a second demo found a real divergence.
- **Localized a frontier inside the candidate for a defect the ORACLE was
  producing.** Sustained instrumentation went into `1010:6168`'s write loop —
  measuring ever more precisely a divergence that a behaviour-changing
  optimisation hook on the *reference* side had created. The candidate was
  correct the whole time. Every instrument worked; every measurement was
  accurate; the comparison was against the wrong program. **Withdrawn**, and the
  whole of the old §4 with it.
- **The write-vs-call lesson recurred, in the opposite direction.** The first
  time, a *write* count was misread as a *call* count and produced a wrong
  localization. The second time, the thing that genuinely differed *was* a
  suppressed call — and by then the instrument that would have shown it was
  distrusted because of the first error. Having been burned by an instrument is
  not evidence about what it is measuring now.

The pattern: every time I reasoned forward from partial instrumentation I was
wrong, and every time I added a control I was right. Prefer one more measurement
over one more hypothesis — and make sure one of those measurements is of the
thing you are measuring *against*.
