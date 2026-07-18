# Handoff: CPUless cold-start frontier (skyroads, 2026-07-18)

Bootstrap for an agent picking this up, possibly coordinating with the Overkill
port. Written at the end of a long session; everything here is committed and
pushed unless marked otherwise.

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

Goal: ONE continuous oracle-proven path from startup, not a collection of
locally passing islands.

**Acceptance** is *not* "the generated corpus passes". It is: **an E2E cold demo
passes against the oracle with the manual overrides DRIVING** (`OVERRIDES`
non-empty). With `OVERRIDES` empty the composite is bit-for-bit the generated
program, so shadow mode alone does not clear the bar.

---

## 3. Current state

`dos_re` submodule tracks branch **`abi-recovered`** (not main) @ `4c3b505`.
Another agent works M3b→M4 there; fetch before touching it.

```
skyroads/lifted/functions/   generated, VM-aware   (186 modules)
skyroads/recovered/          generated, CPU-free   (180 + 2 dead-unreachable)
skyroads/handrecovered/      42 islands, @oracle_link, address-keyed
skyroads/native/             21 subsystem modules, NOT address-keyed, 0 registered
```

Manifest: 180 generated-cpuless, runtime frontier 0, closure complete.

### Commands

```sh
python scripts/rebuild_all.py     # THE pipeline, in the only correct order
python scripts/check_all.py       # all 6 gates, one verdict (--quick skips diffs)
python scripts/coverage_audit.py  # dispatch-table gaps before a player finds them
python tools/absorption_ledger.py [--native|--unstitch]
```

**Pipeline order is load-bearing**: `build_codemap` → `close_vmless_wall` →
`build_recovered`. Skipping the middle stage leaves new functions with no IR
entry; callers then refuse `contains-call`, which once cascaded to five refusals
including `1010:61F3` (the C-startup root) and broke the runner outright. The
build now also fails loud on an IR older than the census.

### Demos (roles are distinct — do not consolidate)

| demo | frames | role |
|---|---|---|
| `demo_cold_20260718_134357` | 261 | spine; **PASSES**; ~15 s — fast acceptance vehicle |
| `demo_attract_20260718_135434` | 5109 | **ZERO input**; **FAILS f1115** — the frontier demo |
| `demo_cold_20260718_003412` | 672 | older spine; passes |
| `demo_intro_20260717_125403` | 1832 | older attract; fails f1115 identically |

Record cold demos with `python scripts/play.py --record-demo NAME` — it starts at
frame 0, which is what makes them cold. Pressing F11 later writes a start
snapshot, and `verify_cpuless` rejects snapshot demos outright.

---

## 4. THE FRONTIER (the actual task)

`demo_attract_20260718_135434`, **frame 1115**, `DAC[161..169]`.
Deterministic; reproduces identically in `demo_intro_20260717_125403` from a
different session; that demo has **zero input events**, so nothing about input
timing is involved.

```sh
python scripts/verify_cpuless.py artifacts/demos/demo_attract_20260718_135434 --frames 1300   # ~7 min
```

### Localization (trusted, cross-checked)

DAC writes come from **`1010:6168`**, reached:

```
fade path (f1113-1115)   61F3 → 01B8 → 4591 → 4331 → 6168
full load (f1116)        61F3 → 01B8 → 4591 → 4B8E → 4331 → 6168
```

Port-3C8 (DAC index) writes per frame:

```
f1114   oracle   7    candidate  14      ← 2×
f1115   oracle   7    candidate   7 (fade) + 256 (4B8E full-load, A FRAME EARLY)
f1116   oracle 512    candidate 512      ← IDENTICAL
```

**`1010:6168` is invoked ONCE PER FRAME on both sides** (verified by an ungated
control: the shadow fired 60× across 60 frames). So the divergence is **inside
`6168`'s write loop** — its bound, count source, direction flag, or termination.
Exactly 2× suggests a doubled iteration count or a byte/word step confusion.

The f1115 symptom is a consequence: the candidate *also* begins the `4B8E`
full-palette-load a frame early, and that load writes black over `DAC[161..169]`.
The fade loop itself was never wrong.

**Next step**: read `skyroads/recovered/func_1010_6168.py` against the ASM at
`1010:6168`. Single-function inspection, not a search. Confirm with a counter
*inside* that function, repair only it, rerun the whole attract demo from frame 0.

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
This was the actual bug: chain occurrences at port writes count **writes, not
invocations**. Reading write-multiplicity as call-multiplicity produced a
confident, wrong localization.

### Negative evidence — do not rediscover

- **Entry counters** (`install_shadow` wrappers + `cs:ip` step hooks) produced
  garbage in this investigation. Re-running after the seam fix changed nothing.
- **"Candidate runs an extra boundary pass"** — REFUTED by measurement (oracle
  sees 3 head arrivals/frame at `1010:434A`, candidate 2; oracle 4 at f1115).
- **"The extra invocation originates at or above `4591`"** — REFUTED by the
  ungated control; it was the write-vs-call misreading.
- **Do NOT modify `skyroads/cpuless_driver.py`.** Every gate depends on it and the
  byte-exact results rest on its cut semantics. Two hypotheses pointing there
  have already been refuted.
- **Do NOT infer execution counts** from boundary arrivals or write counts.

---

## 6. The stitch seam (shared with Overkill)

Now lives in **`dos_re/lift/standalone.py`**: `install_overrides(package,
overrides)`, `generated(package, key)`, `uninstall_overrides(package)`.

Shadowing `sys.modules` alone is **unsound** — this was independently confirmed
broken in both ports:

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
reference. Pinned by `dos_re/tests/test_lift_standalone_overrides.py`.

`skyroads/cpuless_overrides.py` carries the same fix locally and can be reduced to
a thin wrapper over the shared version — deliberately left alone so the frontier
investigation isn't disturbed mid-flight.

---

## 7. Islands / absorption

42 islands: **3 VERIFIED, 39 ASM_MATCHED**. 20 anchor to an IR function; **22
anchor to nothing** — formats, helpers, data layouts. Those 22 are knowledge for
the Memory Schema (M4), never override bodies.

`ASM_MATCHED` ("diffed on captured cases") is **weaker** than the byte-exact
standard the generated corpus already meets, so stitching them on recorded status
would LOWER the proof standard.

**Shadow mode** is the rung between: `verify_cpuless <demo> --shadow-islands` runs
the generated body (so behaviour is provably unchanged) with the island checked
against it on every real call, reporting calls-agreed and cost distribution.

```
1010:04C0  perspective_row_offset     14,802 + 49,461 + 1,765 calls agreed
1010:3A96  unpack_animation_segment   full 64 KB segment byte-exact + cursors
```

Both islands were **correct**; both times the defect shadow mode found was in my
**checker** (asserting a callee-saved register; binding the wrong segment because
the body reloads `ds`). That is exactly the mistake a direct swap makes silently.

**Virtual time is not the blocker it looked like.** `04C0`'s cost is two-valued
(104 / 19) and the island already computes the discriminant (`in_range`). But note
the trap: on the *spine* demo the cost is a single constant, because the short path
never occurs there — deriving the model from one demo would pass acceptance and be
silently wrong. Validate against both.

Still required before any island drives: reproduce the **full** contract —
outputs `ax/bp/bx/cx/di/dx/si` plus `flags` and `fmask` — not just `AX`.

---

## 8. Coordinating with Overkill

They confirmed both bypass paths were real in their stitch and fixed them.

Their blocker is **upstream of ours**: `96C8` unpromoted behind the `065C`
longjmp, so their cold-start differential **has not run yet**. They are correctly
stating their claim narrowly ("the boot root runs on `DOSMachine` for this image").

So the two ports are doing different things right now: **they are proving a seam
works; skyroads is walking a proven path forward.** Shared concerns are the
`dos_re` seam, the islands ladder, and the instrument lessons above — not the
frontier itself, which is game-specific.

---

## 9. Open tasks

1. **Frontier** (task #11): `1010:6168`'s write loop. See §4.
2. **Acceptance**: derive `04C0`'s full output+flag mapping, declare cost from
   `in_range`, put it in `OVERRIDES`, rerun the spine demo with it *driving*.
3. Add `demo_attract_20260718_135434` to `check_all.py` once it passes, so the
   gate covers two cold demos.
4. Reduce `skyroads/cpuless_overrides.py` to a wrapper over `dos_re.lift.standalone`.

---

## 10. Things I got wrong (so they are not repeated)

- Claimed `3A96`'s contract was under-specified. It was not; my checker bound the
  entry `ds` while the body reloads it. **Withdrawn.**
- Claimed the boundary driver ran an extra pass. **Refuted by measurement.**
- Claimed the doubling originated at/above `4591`. **Refuted by an ungated control.**
- Reported "672 frames byte-exact" as a general claim. It was **demo-specific**;
  a second demo found a real divergence.

The pattern: every time I reasoned forward from partial instrumentation I was
wrong, and every time I added a control I was right. Prefer one more measurement
over one more hypothesis.
