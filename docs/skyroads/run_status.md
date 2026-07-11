# SkyRoads run status

> Dated progress log — sections state what was true at their date. For the
> ledger of per-routine evidence see [`symbol_ledger.md`](symbol_ledger.md);
> open issues are in [`blockers.md`](blockers.md).

## 2026-07-11 — LOCKSTEP: the native loop runs in sync with the VM and never drifts

The accumulated-state convergence proof (stronger than the per-step test): seed
a NativeGameState + GameplayScratch ONCE from the VM at a gameplay sub-step,
then run `native_gameplay_substep` over and over carrying its OWN scratch,
injecting only the INPUT fields (steer/jump/speed/keys/tick) the outer loop sets
between sub-steps, and check every other gameplay field stays byte-identical to
the VM at every step.

Result: the native loop runs **13, 19, 20 consecutive steps in perfect
lockstep** and **NEVER silently drifts** — the only thing that ends a run is the
stepper hitting a not-yet-recovered path (a `game_state != 0` transition, or the
`1DFA` effect frame) where it RAISES a gap. Zero field divergences on any
recovered path. Landed `tests/test_native_loop_lockstep.py`, which asserts both
a real accumulated streak AND that runs only ever end on gaps, never drift.

This is the real thing: the recovered islands, composed over a session scratch,
are a self-contained native gameplay loop that reproduces the VM exactly for as
long as it stays on recovered paths. The streaks are bounded only by how often
gameplay transitions into the `game_state != 0` (frozen-ship) path, which isn't
recovered yet — recovering it is what extends the streaks toward a whole level.

## 2026-07-11 — the forward advance is the 1B49 call: native sub-step now COMPLETE (230/232 all fields, incl. ship_pos)

Correction + closure of the previous entry's open question. I'd concluded the
per-frame ship_pos advance happened in the OUTER frame loop, outside the
sub-step. That was wrong. Watchpointing `[54AC]` pinned the +0x12F advance to
`1010:1BE2` — inside `1B49`, the function recovered as
`menu.dispatch_menu_action`. **The forward motion IS the classification's
`1B49` call**: the classification passes the reduced perspective word to
`dispatch_menu_action` (`2385-238B`), and action `0xA` (scroll-right) advances
`ship_pos += SCROLL_STEP` (`0x12F`) when `[456A] == 0` (`1BDC`). So this is the
"1B49 gameplay side effect" I'd flagged in `classify.py` — not a side effect,
the core forward-motion mechanism, and it lives IN the sub-step.

`native_gameplay_substep` now applies `dispatch_menu_action` in its
classification stage (using the `calls_1b49`/`reduced_word` the classifier
already surfaces). Result: the differential match jumped from 148/232 (all
fields) to **230/232 — the full gameplay DGROUP including `ship_pos` and
`lateral`**. The 2 residual misses are documented edge cases (a rare `[AF2E]`
landing back-off; the `1DFA` effect frame, which the stepper raises a gap for).
`tests/test_native_substep.py` now compares every field (no outer-field
exclusion) and asserts a ≥95% match.

So `native_gameplay_substep` is a COMPLETE self-contained gameplay step: the
recovered islands, composed in spine order over `GameplayScratch`, reproduce
real VM gameplay — forward motion, steering, jumping, gravity, collision,
landing, crash, and level progression — with no VM. What remains for a fully
playable native loop: a driver that calls it per input frame (the
`play_native.py` equivalent), the frozen `game_state != 0` path, the
out-of-bounds death check (`23CA-2421`), and the `1DFA` effect.

## 2026-07-11 — ASSEMBLED the native gameplay sub-step: the islands run as one stepper (228/232 sub-step fields vs VM)

The convergence step. With the whole physics/collision sub-step recovered as
individual VM-verified islands, composed them — in confirmed ASM spine order,
over a session-persistent `GameplayScratch` — into a single running native
stepper: `skyroads/native/loop.py::native_gameplay_substep(view, scratch)`.

Spine (empirically traced, `game_state == 0` active gameplay):

    classify_ship -> gate_bounce_decay -> advance_ship -> step_jump_steer_gravity
      -> compute_movement_targets -> resolve_move -> lateral_wall_bump
      -> resolve_lateral_crash -> af1c_contact_fixup -> resolve_landing
      -> vertical_center_nudge (if landed) -> step_level_progression

`GameplayScratch` carries the cross-sub-step `ss:[bp-N]` locals the one
continuous `2280-2B0B` handler reads before writing each sub-step: the
`JumpScratch` (`bp-6/8/10`), `bp12` (gameplay-active), `bp14` (persisted class
flag), `bp24` (last vscan cell, read by the decay gate), and `tgt_af2c`
(`bp-28`, read by the decay gate before recompute).

**Differential result vs the VM** (seed a NativeGameState + scratch at each
`game_state==0` loop top `2324`, run one native sub-step, compare DGROUP at the
next loop top): **228/232 sub-step fields match**. The 4 misses are all
already-documented edge cases (the `1DFA` effect frame — now raises a gap; the
rare `[AF2E]` landing adjustment; a `game_state -> 2` transition). Landed
`tests/test_native_substep.py` asserting a ≥95% sub-step-field match rate.

**Key discovery — the forward advance is per-FRAME, not per-sub-step.** The
233/sub-step ship_pos advance (and lateral, timer_a) does NOT happen inside the
sub-step: at `24C4` `advance_ship` runs with `speed ([9330]) == 0` (a no-op),
and the real advance happens in the OUTER frame loop (`2280-2317`), which runs
once per displayed frame around many sub-steps. So `native_gameplay_substep`
faithfully steps ONE sub-step; those three outer-driven fields are excluded
from the match (they diverge exactly when a sample pair straddles a frame
boundary). Recovering that outer per-frame advance + the state dispatch
(`2280-2317`) is what remains to step a whole displayed frame.

This is the pre2_port convergence in miniature: the recovered leaves now
compose into a native stepper that reproduces real gameplay, VM-free, with the
remaining gaps precisely named (outer-frame advance, the frozen-ship
`game_state != 0` path, the out-of-bounds death check, the `1DFA` effect).

## 2026-07-11 — recovered the pre-move bounce-decay gate (2421-24BA), 682/682 — the core physics sub-step is now whole

Recovered `gate_bounce_decay` (`dynamics.py`, `1010:2421-24BA`): the gating
around `decay_bounce` (already recovered) that runs just before the jump/gravity
block each sub-step. If `af2c == tgt_af2c` the bounce passes through untouched;
otherwise it's zeroed when `([5496] != 0 and scan_cell < 2)`, or `|bounce|`
falls below `low16(0x104 * jump_gate) // 8`, or `[456A] != 0` (grounded); else
`bounce := decay_bounce(bounce)`. **Verified 682/682** with good branch coverage
(unchanged 236, small-kill 439, decay 6, 5496-kill 1). Landed
`tests/test_decay_gate.py` + a 57-case fixture. The grounded-kill branch was
decoded but unexercised; the landing SFX (`03C2(1)`, gated by an `0476`
predicate) is audio-only and not modelled.

**Milestone: the whole physics/collision sub-step (2421-2AE2) is recovered.**
With the decay gate in place, every stage from the vertical decay through the
movement pipeline, the full collision response, and level progression is now
recovered and VM-verified:

    2421-24BA  gate_bounce_decay        (dynamics)        682/682
    2324-23BF  classify_perspective     (classify)        682/682
    252B-2635  step_jump_steer_gravity  (dynamics)        415/416
    2635-26E9  compute_movement_targets + resolve_move    300/300
    26EC-2A24  collision response (5 fns)                  full region
    2A35-2AE2  step_level_progression   (progression)     682/682

What's left of the per-frame handler is the *framing* around this core: the
out-of-bounds/fall death check (`23CA-2421`, calls `0533`/`0F05`, gated on the
transitional state so it falls through in normal gameplay), the outer state
dispatch (`2280-2317`), and the sub-step loop (`2317-2B08`, `bp-2 < [1600]`).
The next major step is ASSEMBLY: thread the session scratch
(`bp-2/6/8/10/12/24` + the `bp-14/16/18` classification flags) through these
recovered stages in spine order to build a self-contained `native_gameplay_frame`,
then multi-frame-verify it against the VM (the pre2_port tick-keyed-harness
convergence proof).

## 2026-07-11 — recovered the landing check (28D7-295D), 224/224 — jump-latch lifecycle complete

Recovered `resolve_landing` (`collision_response.py`, `1010:28D7-295D`): the
post-move landing detection. A landing resolves iff `af2c != tgt_af2c` AND
`bounce < 0` (descending, off the vertical target); on a landing it clears
`ds:[455A]`, the effect latch `bp-6`, and **the jump latch `bp-8`**, sets the
gameplay-active flag `bp-12 := 1`, and backs `ship_pos` off by the 32-bit
`[AF30:AF2E]` (clamped to `[0, 0x2AAA]`). This **completes the jump-latch
lifecycle** — `dynamics.step_jump_steer_gravity` sets `bp-8` on the impulse,
this clears it on landing (answering the long-standing `JumpGateGap` question).

**Verified 224/224** real landing frames byte-exact (collision demo
`demo_skyroads_20260710_213019`) on `(bp-6, bp-8, bp-12, [455A], ship_pos)`.
The non-landing branch just leaves `bp-12 = 0` and is trivial by construction.
Learned `[AF2E]/[AF30]` were nonzero in only 1/224 frames — the ship_pos
back-off is a practical no-op but faithfully applied (and the one real case
matched). Landed 4 pure unit tests + a collision-demo live-oracle test.

Then recovered that last piece too: `resolve_lateral_crash` (`27A3-2830`), the
**wall-crash handler**. On a lateral collision (`lateral != tgt_lateral` = the
ship was blocked sideways into a wall) it restarts the ship (`ship_pos := 0`)
and, once past forward position `0x0E38`, flags the crash (`[456A]:=1`, and
`[456E]:=1` if it was 0). Verified 511/511 on the collision demo — though only
2 were real crashes (both past the gate), so the pre-gate and already-flagged
branches are decoded-but-unexercised (flagged in the `@oracle_link`). With this,
**the entire `26EC-2A24` collision-response region is recovered**, and the whole
post-move tail (`26EC-2AE2`) with it.

**The gameplay SUB-STEP is now essentially complete**: classification
(`2324-23BF`), dynamics (`252B-2635`), movement pipeline (`2635-26E9`), the full
collision response (`26EC-2A24`), and level progression (`2A35-2AE2`) are all
recovered and VM-verified. What's left of the per-frame handler is the parts
BEFORE the movement step: the `decay_bounce` region (`2421-24BA`) and the early
visibility/height classification (`23CA-2421`), plus the outer state dispatch
(`2280-2317`) and the `1B49`/`1DFA` side effects. After those, the pieces can be
assembled into a self-contained `native_gameplay_frame` and multi-frame-verified
against the VM (the pre2_port tick-keyed-harness convergence proof).

## 2026-07-11 — recovered the lateral wall-bump + af1c contact fix-up (26EC-27A0, 283C-28AE)

Two more collision-response pieces, into `collision_response.py`:
- `lateral_wall_bump` (`26EC-27A0`): when the ship's lateral was blocked short
  of target but `af1c` reached target and the target cell is blocked, nudge
  `af1c` down `0x3A0` (else up `0x3A0`) to slip past, snapping `tgt_lateral` to
  the current lateral;
- `af1c_contact_fixup` (`283C-28AE`): on an `af1c` collision (`af1c != tgt_af1c`),
  clear `lateral_accel`, conditionally zero `[5496]` (when its sign agrees with
  the still-needed `af1c` direction), and brake `ship_pos` by `0x97` (clamped ≥0).

**Verification note — needed a collision demo.** The E2E demo is a clean run
that almost never collides: the wall-bump's active branch fired 0×, the contact
fix-up 1×. So while the entry/no-op paths verified 682/682 there, that's weak.
Scanned all 14 demos for the collision IPs and found
`demo_skyroads_20260710_213019` exercises both (1 real wall-bump, 4 real af1c
collisions). Verified the **active** branches against it: wall-bump 511/511
(incl. the real down-bump), contact fix-up 511/511 (incl. all 4 collisions).
Landed pure-logic unit tests + a live-oracle test bound to that collision demo
that asserts the active branches actually fire (`stats["bump_active"] >= 1`
etc.), so the recovery can't silently regress to only testing the no-op path.
The wall-bump's UP branch (`2788`) is decoded but was not itself triggered by
any sampled demo — flagged in its `@oracle_link` note.

Remaining in the `26EC-2A24` collision middle: the position milestones
(`27A3-2800`, the `[54AC]>=0xE38` → `[456A]/[456E]:=1` transition) and the
landing check that clears the jump latch (`28DC-2901`, mapped). After those, the
entire post-move tail is recovered.

## 2026-07-11 — recovered the vertical collision-depth scan (2963-2A24), 314/314

Started into the collision-RESPONSE middle of the post-move tail
(`26EC-2A24`), the `1732`-heavy region that resolves the ship against the track
after `resolve_move`. First self-contained piece: the vertical centering scan
(`1010:2963-2A24`) that maintains `ds:[5496]` — the vertical term
`compute_movement_targets` adds into `tgt_af1c` (so this closes a real loop: the
movement target's `[5496]` input is now itself recovered).

The scan probes `road_object_visible` (`1732`) at `af1c ± k*128` for `k = 1..14`,
finds the first UNBLOCKED cell above and below the ship, nets that into `{-1, 0,
+1}`, and moves `[5496]` by `net * 17` (or zeroes it when net is 0). Recovered as
`skyroads/recovered/collision_response.py::vertical_center_nudge` (pure, takes
the same `visible` predicate `resolve_move` uses), **verified 314/314** real
E2E scans byte-exact — every probe computed through
`renderer.road_object_visible` bound to the frame's real DGROUP tables. Landed
`tests/test_collision_response.py` (5 pure-branch unit tests + 1 live-oracle).

New module `collision_response.py` is where the rest of this region will accrete.
Still to recover in `26EC-2A24`: the lateral wall-bump (`26EC-27A0`, nudges
`[AF1C]` ±0x3A0 when the ship's lateral is blocked, plays an SFX), the position
milestones (`27A3-2800`, the `[54AC]>=0xE38` → `[456A]/[456E]:=1` transition),
the `[AF1C]`/`[5496]` contact fix-up (`283C-28AE`), and the landing check that
clears the jump latch (`28DC-2901`, mapped — clears `bp-6/bp-8`, sets `bp-12`,
adjusts `ship_pos` by `[AF2E]`).

## 2026-07-11 — recovered the level-progression state machine (2A35-2AE2), 682/682 + fixed an inverted resume-gate bug

Went into the post-move tail (`26E9-2B0B`) and recovered its state-machine end,
`1010:2A35-2AE2` — the level timers and `ds:[456E]` game-state transitions that
end or resume a level. Landed as `skyroads/recovered/progression.py::
step_level_progression`, **verified 682/682** real E2E sub-steps byte-exact on
`(game_state, level_timer_a, level_timer_b, frame_ctr)`, including the demo's
real `0->3` resume transitions.

The logic (only when `game_state == 0`, i.e. transitional/just-respawned):
- **level_timer_b** (`ds:[B13C]`, time/"oxygen") -= `0x7530/(0x24*[4566])`;
- **level_timer_a** (`ds:[5494]`, distance/"fuel") -=
  `slong_div(ulong_mul(0x7530/[54A2], ship_pos), 0x10000)` (ship_pos-proportional);
- both unsigned-clamped at 0;
- then `game_state := 3` if `af2c < 0x2800` (resumed), `:= 4` if timer_a hit 0,
  `:= 5` if timer_b hit 0 (later override earlier).
While `game_state != 0`, none of that runs — the frame counter `ds:[4558]`
increments instead. This is the level-complete / out-of-time death logic
`vmless_roadmap` item 1 lists.

**Found and fixed a real bug in an earlier "ASM_MATCHED" recovery.**
`player.is_landed_for_resume` returned `af2c >= 0x2800`, but the ASM's `jb` at
`2AB7` resumes when `af2c < 0x2800` (the ship has DESCENDED past the gate). The
earlier recovery inferred `>=` from all 3 respawns writing `af2c = 0x2800` and
assuming that "immediately satisfied" resume — but at exactly `0x2800` resume
does NOT fire; the ship stays transitional until `af2c` drops below the gate.
The 682/682 progression match (with the real `0->3` transitions) is the
authoritative evidence. Corrected the function, its `@oracle_link` note, and its
test (`test_player.py`); it was only used by that test, so no downstream impact.

Two derivation details worth noting: the fuel decrement's divisor is `0x10000`
(from the `5E5A` call's `bx=1,cx=0` operand), not 1; and both timers are gated
entirely on `game_state == 0`, so normal gameplay (state 3) never touches them —
they only tick in the transitional/death window.

**State of the native gameplay frame**: classification, dynamics, movement
pipeline, AND now the level-progression tail are all recovered and proven. The
remaining un-recovered part of the `26E9-2B0B` tail is the **collision-response**
middle (`26EC-2A24`: the lateral wall-bump nudges and the vertical `1732`-probe
scan that adjusts `[5496]`/`[AF1C]`, plus the `bp-8`-clear landing check at
`28DC-2901`) — a larger, `1732`-heavy island for a future pass. Plus the
upstream `decay_bounce` region (`2421-24BA`) and the early visibility check
(`23CA-2421`).

## 2026-07-11 — recovered the perspective classification (2324-23BF), 682/682 + located where the jump latch clears

Took the classification block feeding the dynamics block's `bp-14`/`bp-18`
inputs (below). It projects the ship's own `(lateral, af1c)` through the
perspective transform (`04C0` = `renderer.perspective_row_offset`) to a table
word `bp-20`, then:
- `bp-18 (class_zero) = (bp-20 == 0)`;
- if `bp-12 == 0`: `bp-16 = 0`, `bp-14` **unchanged** (persists across frames —
  it's session state, not a pure per-frame value);
- else: reduce `bp-20` (if `af2c > 0x2800`, look up `ds:[0x228 + 2*(bp-20>>8)]`
  and set `bp-20 = bp-20>>4` if `af2c` matches else 0), make a side-effect
  `1B49` call, then `bp-14 = (bp-20 & 0xF == 8)`, `bp-16 = (bp-20 & 0xF == 2)`.

Recovered as `skyroads/recovered/classify.py::classify_perspective` (pure, takes
the perspective word + a table reader) + `skyroads/native/classify.py::classify_ship`
(binds `perspective_row_offset` + DGROUP reads, like `collision.make_visible`).
**Verified 682/682** real E2E frames byte-exact on `(bp-14, bp-16, bp-18)`,
computing the perspective word natively. Landed `tests/test_classify.py` (6
pure-branch unit tests + 1 live-oracle test driving the demo).

**Also found where the jump latch clears.** Chasing `bp-12`'s source
(`classify`'s one remaining upstream input) into the post-move tail led to
`1010:28F2-2901`: `bp-6 := 0`, **`bp-8 := 0` (jump latch cleared)**, `bp-12 := 1`
— reached when `ds:[AF2C] != bp-28` (the af2c target) AND `ds:[9336] < 0`
(descending), i.e. the landing/collision-resolved condition. This answers the
long-standing `JumpGateGap` "where does bp-8 reset" question (previously only
inferred from the `af2c→0x2800` correlation).

**Two documented subtleties** (in `classify.py`): (1) `bp-14` persists when
`bp-12 == 0`, so it's session state the caller must thread; (2) the `1B49`
side-effect call during gameplay (same address as `menu.dispatch_menu_action`,
called with the reduced perspective word) — the flags don't depend on its
result so `classify` reproduces them without it, but its DGROUP side effect
during gameplay is flagged (`calls_1b49`), not modelled. Worth resolving what
`1B49` actually does with a perspective-derived arg mid-gameplay.

**State of the native gameplay frame now**: classification, dynamics (jump +
steering + gravity), and the movement pipeline (targets + resolve_move) are ALL
recovered and proven against the VM. Remaining to stand up a full self-contained
frame: the `26E9-2B0B` post-move **tail state machine** (drives `bp-12`, clears
`bp-8` on landing, handles level-end/death — `28C0` has the `[54AC]==0x2AAA`
level-end clamp), the upstream `decay_bounce` region (`2421-24BA`), the early
visibility check (`23CA-2421`), and the `1B49` side effect.

## 2026-07-11 — recovered the jump-latch + steering + gravity block (252B-2635), 415/416

Followed the movement-pipeline proof (below) into the block right before it —
`1010:252B-2635`, the per-frame jump/steering/gravity update — because its
output `lateral_accel` was the movement pipeline's one un-derivable input.
Disassembled the whole thing and found the "jump latch" I'd been calling an
unrecovered gap is right here: `2570-25A9` fires the up-impulse
(`bounce := 0x480`), latches `bp-8 := 1`, and records the jump-start height
`bp-10 := af2c`, gated by `bp-8==0 && bp-18==0 && [547A]!=0 && [4562]<0x14`.

Recovered the block as `skyroads/recovered/dynamics.py::step_jump_steer_gravity`,
operating on a small session-persistent `JumpScratch` (`bp-6`/`bp-8`/`bp-10`)
plus two per-frame classification flags (`bp-14`/`bp-18`) and DGROUP scalars.
It covers three things the earlier naive functions couldn't gate correctly:
- **steering momentum** (`2534-256D`): `lateral_accel = steer*29`, but latched —
  only recomputed when `class_skip==0` and either `(not jumping && class_zero==0)`
  or `(lateral_accel==0 && bounce>0 && af2c-jump_start_y < 0xF00)`. This is why
  60/682 frames had `lateral_accel != steer*29` (momentum persisting a frame
  after the steer key released);
- **the jump latch** (`2570-25A9`), above;
- **gravity/velocity** (`25DB-2635`): airborne → `+gravity` (or clamp to
  terminal `-106` below the height gate); grounded → ramp to `+0x47`.

**Verified 415/416** real E2E-demo frames byte-exact on `(bounce,
lateral_accel, bp-8, bp-10)`. The single miss is one frame where the rare
`25AC-25D6` effect path (a `1DFA` call gated by `[4570]`/`bp-6`/`af2c>=0x3700`,
fired only 5× in the whole demo) separately rewrote `lateral_accel` — the
function detects and flags that path (`hit_effect_path`) rather than
mis-modelling it. Landed with `tests/test_dynamics.py` + a 89-case fixture
(`dynamics_trace.json`, all jump-fire/1DFA/steering frames + a spread).

This **supersedes** the `decay_bounce` + `update_vertical_velocity` composition
that `VerticalVelocityGap` guarded — the "`[9336]` frozen for 8 frames" mystery
was just this block's gating (grounded/af2c/jump-latch), now modelled. Updated
all three native gaps (`JumpGateGap`/`VerticalVelocityGap`/`MovementPhysicsGap`)
to point here.

**Remaining before a full native gameplay frame** (each a scoped island):
1. the perspective **classification** (`2324-23BF`) that produces `bp-14`/`bp-18`
   — `bp-20` = the perspective-table word for the ship's own `(lateral, af1c)`
   via `renderer.perspective_row_offset`, then `bp-18=(bp-20==0)` and
   `bp-14=(bp-20 & 0xF==8)` after an `af2c`/`ds:[0x228]`-table reduction that
   also makes a side-effect `1B49` call (the messy part);
2. where `bp-8` **clears** on landing (traced to the frame `af2c` snaps to
   `0x2800`, exact write not yet located);
3. the upstream `decay_bounce` region (`2421-24BA`);
4. the `1DFA` effect (`25AC-25D6`) and the death/level-end event paths.
Not wired into `native_gameplay_frame` yet (it still needs 1 & 2); the block is
proven in isolation.

## 2026-07-11 — movement MATH complete (pipeline proven 300/300) + af1c_base_offset corrected to a constant

Two results pushing native gameplay forward.

**1. The af1c_base_offset was never an open selector gap — it's the constant
`0x0618`.** The "movement-target formula recovered" entry (and the follow-up
that "ruled out a hypothesis for the selector") both leaned on an empirical
"offset is 0 for non-steering, 0x0618 for steering" reading. That was a
measurement artifact. Probing the real `ss:[bp-16]` (the ASM's actual selector,
`1010:2650`: `bp-16==0 → +0x0618`) directly at the decision point found it `0`
in **every one of 682 real E2E calls** — so the multiply's base is always
`ship_pos + 0x0618`. The apparent "offset 0" for non-steering frames was just
`lateral_accel == 0` making the multiply `0 * base == 0`, so the base value was
irrelevant there and the fixture-builder's "try 0 first" recorded 0. Held
`lateral_accel` nonzero, only `0x0618` matches (58/58). The alternate `0` needs
the `af2c > 0x2800` + `ds:[0x228]`-table-match path (`1010:2340-23BF`), which
never fired in the demo — a real but UNEXERCISED branch, not a gap. Corrected
`skyroads/recovered/physics.py` (default `af1c_base_offset=0x0618`, docstring
rewritten), re-patched the fixture, updated `tests/test_physics.py`. This
retires the "open selector" caveat from all three earlier places it appeared.

**2. The lateral/vertical movement MATH is complete — the whole pipeline
reproduces the VM 300/300.** Composed the two already-ASM_MATCHED halves —
`compute_movement_targets` (`2635-26E6`) → `resolve_move` (`186B`) — with the
`skyroads/native/collision.make_visible` predicate bound to a `NativeGameState`'s
DGROUP tables, and diffed the result against the real VM's post-move
`(lateral, af1c, af2c)` captured at `26E9`. **300/300 exact, 39 with real
steering.** Landed as `tests/test_native_movement_pipeline.py` (live-oracle,
gated on the game files). This establishes there is NO remaining gap in the
movement math itself.

**What still blocks a full native gameplay frame** (precisely bounded now, down
from "the 2560-26E9 block is unrecovered"): the pipeline's `lateral_accel`
(`ds:[4568]`) input is **stateful steering momentum**, not a stateless
`steer*29` — 60/682 real frames have `lateral_accel != steer*29` (e.g. `-29`
persisting a frame after the steer key released). It's updated mid-frame at
`1010:2568` under the jump-latch-gated steering block (`1010:2534-256D`), whose
gates depend on the perspective classification (`1010:2324-23BF` →
`bp-14`/`bp-16`/`bp-18` from `perspective_row_offset`, already recovered) and
the session-persistent jump-latch state (`ss:[bp-8]`/`[bp-10]`). So
`native_gameplay_frame` still raises `MovementPhysicsGap` — but the gap is now
specifically "derive `lateral_accel`", not "recover the movement math."
Deliberately did NOT wire `resolve_move` in with `lateral_accel=steer*29`: it
would silently diverge on those 60 frames, violating the fail-loud rule. Next
concrete island: the `2534-256D` steering-momentum update (+ its `2324-23BF`
classification dependency).

## 2026-07-11 — fixed the audio stutter + >1s sound delay: it was pacing, not missing hooks

User report: music/sound stutters, and sound is delayed by more than a second.
Confirmed empirically it is a **pacing/audio-architecture** problem, not
under-hooking (though the two are linked — see below).

**Measurement** (full E2E demo, real frontend cost per frame): 496/1719 frames
(29%) exceed the 33.3ms budget a 30Hz loop allows — p90 71ms, p99 230ms, max
450ms; on those frames the loop drops to 14Hz (p10) down to 2.2Hz.
`clock.tick(present_hz)` only pads a *fast* frame up to the budget, never
speeds a slow one up, so `AudioSink.pump()` is called well below 30Hz on
nearly a third of frames. The stock sink generates AND drains a **fixed**
`chunk = rate // present_hz` samples per pump, on the assumption pump() runs at
a steady `present_hz`. Over the demo that means the consumer can only emit
57.3s of audio (1719 pumps x 1470 samples) while 83.6s of wall-clock playback
is needed — a **26s structural deficit** that surfaces two ways:

- **Stutter**: the OPL music channel underruns on every slow stretch, goes
  idle, and restarts with a fresh 0.1s lead (an audible gap).
- **>1s SFX delay**: a captured SB-DMA effect is resampled to real-time
  duration and dumped into `self._sfx` all at once, but drained at only
  `chunk` samples *per pump* — coupled to pump frequency, not the wall clock.
  On every sub-30Hz frame the backlog grows and never clears, so effects play
  seconds after their visual.

**Fix** (`skyroads/audio.py::SkyroadsAudioSink.pump`, an override — kept in the
port repo, observer-only, so demos/tests/determinism are untouched): size each
pump by **real elapsed wall-clock time** (`n = round(dt * rate)`, clamped)
instead of a fixed chunk, so samples produced/drained always track what the
mixer consumes; and hard-cap the pre-mixer buffer (200ms) and SFX backlog (1s
safety) so a long stall resyncs to "now" (a brief glitch) rather than
accumulating delay. Verified end-to-end on the real demo through the real sink
with a fake mixer + real clock: **peak SFX backlog 951ms and self-recovering
(the 1s cap never even engaged), pre-mixer buffer bounded at 200ms** — vs the
pre-fix 26s ratchet. 4 new deterministic regression tests
(`tests/test_audio_pacing.py`, injected clock + fake mixer) lock in that the
backlog stays bounded and generation tracks wall-clock, not pump count. 226
tests pass.

**What this does NOT fix** (stated honestly): when the VM itself runs below
30Hz, the game emits OPL note changes at its own slow tick rate, so the music
*tempo/sequence* genuinely drags — no audio pacing can fix that, only a faster
VM (i.e. more hooking) can. So the two questions the user raised are both
"yes, partly": pacing was the direct cause of the stutter and the SFX delay
(now fixed); remaining tempo unevenness on heavy transition frames is the
un-hooked-work side, and every renderer/logic island still to be hooked
reduces how often frames blow the 33ms budget. Note the same fixed-chunk bug
lives in the shared `dos_re/dos_re/audio_sink.py::AdlibSpeakerSink` base (all
games using it); left as an upstream-candidate rather than re-pinning the
submodule from a port-side fix.

## 2026-07-11 — ruled out one hypothesis for the af1c_base_offset selector

Quick follow-up to the "movement-target formula recovered" entry's open
question: does the `ss:[bp-16]` selector (`1010:2340-23BF`) actually reduce
to `perspective_row_offset(lateral, af1c)` plus a `ds:[0x228]`-table lookup,
as that disassembly reading suggested? Implemented it directly (reusing the
already-recovered `perspective_row_offset`, matching `hooks.py`'s
`_persp_exit` argument convention) and checked it against all 682 real
`186B` calls from the movement-target fixture's source demo, predicting
`bp-16` and comparing to the real `af1c_base_offset` (0 vs `0x618`) deduced
from each sample's actual `tgt_af1c`.

**Result: wrong exactly where it matters.** 624/682 correct — but ALL 58 are
the real-steering (`lateral_accel != 0`) samples, and the hypothesis predicts
`bp-16=0` (offset 0) for every single one of them, when the real value is
always `0x618`. Not a near-miss or an off-by-one; the `af2c > 0x2800` +
table-match branch never fires when it should for these samples, so either
the argument order/quantity fed into `perspective_row_offset` here is wrong,
or `ss:[bp-20]`'s value at this point in the ASM isn't what
`1010:2324-2336`'s disassembly suggested. Ruling this out so a future attempt
doesn't re-derive the same dead end — the only currently-known signal for
`af1c_base_offset` remains the empirical `lateral_accel != 0` correlation
already documented in `skyroads/recovered/physics.py`'s docstring (682/682
in this demo, structurally unconfirmed).

## 2026-07-11 — CONFIRMED: the jump-latch locals are session-persistent, not per-frame (architecture, not just a hypothesis anymore)

Follow-up to the "movement-target formula recovered" entry below, which
flagged the `ss:[bp-8]` etc. persistence question as informed speculation.
Settled it directly: monkeypatched `CPU8086.step` to snapshot `SS:BP` and
`ss:[bp-8]`/`[bp-10]`/`[bp-18]` every time `cs:ip` hit `1010:26E6` (the
`resolve_move` call site, known-reachable from the movement-target probe)
over the full E2E demo — no hook, no replacement, pure observation.

**Result: `SS:BP` was `(0x1686, 0xB910)` on all 274 visits across every one
of the demo's ~1900 frames — never once different.** This settles it: the
per-frame handler at `1010:2280-2B0B` is not re-entered each displayed
frame; it is ONE continuous execution context (a single `enter`, presumably
at level start) that loops across frames via `jmp`, exactly as the tick-wait
spin `skyroads/pacing.py` already parks at `1010:22F8` (*inside* this same
block) implied. `ss:[bp-N]` locals are therefore genuine session state, not
per-call scratch — confirming what the movement-target entry could only
infer from bp-8 outliving a key release.

**bp-8's full lifecycle**, traced across the whole demo: sets to 1 exactly
when the jump impulse fires (`1010:25A1`, already known), and reset to 0 was
observed at 3 independent points (frames 746, 915, 1366) — in every case,
the SAME frame `ds:[AF2C]` snapped back to exactly `0x2800`
(`player.RESUME_HEIGHT_GATE`) via `resolve_move`'s own collision clamp, with
`ds:[456A]` staying 0 throughout (so `456A` is NOT the "landed" signal here
— it's a rarer, separate flag; found it set by an unrelated side-wall
collision case at `1010:27DF`, not investigated further). bp-8 is NOT
recomputable as `af2c != 0x2800` (it's already 1 the same frame af2c is
still 0x2800, the very frame the jump fires) — it is a true latch, just one
whose reset trigger (landing) is now empirically pinned down even though the
exact ASM instruction doing the reset write wasn't located (checked
`1010:2704-2800`, the af1c-target/lateral-wall-collision block right after
`resolve_move` returns — not there; the reset write is elsewhere, not yet
found).

**Implication for skyroads.native**: confirms the gaps.py architecture note
was right, not just plausible. `native_gameplay_frame` cannot model bp-8/
bp-10/bp-14/bp-16/bp-18 as either DGROUP fields (they aren't) or per-call
locals (they don't reset per call) — it needs a companion session-scoped
scratch object threaded across frame calls, reset only at whatever re-enters
this handler (level load, most likely), mirroring pre2_port's
`NativeGameState.__slots__` side channels (`sfx_queue`, `particle_capture`,
etc. — "this session's" state that isn't memory-backed). Not built yet; this
entry exists so the next session doesn't have to re-derive the persistence
question from scratch.

## 2026-07-11 — the movement-target formula recovered (1010:2635-26E6), closing most of MovementPhysicsGap

Continued from the native-loop work below: disassembled forward from the
per-frame state dispatcher (`1010:2280-2317`, the top-level `[456A]/[456E]/
[4558]` orchestration `vmless_roadmap.md` item 2 calls out as fully missing —
mapped but not yet lifted/verified this session) through the jump-latch gate
(`1010:2570-25AC`, confirming `ss:[bp-8]` is exactly the self-latching
"jumped already" flag `player.py`'s docstring predicted) to
`1010:2635-26E6`: the block that computes the `(tgt_lateral, tgt_af1c,
tgt_af2c)` triple `resolve_move` (`1010:186B`) sweeps toward — previously
mapped only at a high level ("Vertical/lateral physics", earlier today) and
explicitly called "the tee-up, not recovered source."

Cross-checked the derived formula against 682 real `186B` call arguments
captured over the full E2E demo (58 with real steering held) by monkeypatching
`CPU8086.step` to snapshot state whenever `cs:ip == 1010:26E6` (the `call
186B` instruction, args already pushed) — no hook needed, since this was pure
observation, not replacement. First pass wrongly concluded "the lateral
offset is always 0" from an incomplete 400-sample/frame<1193 window; the full
682-sample pass falsified that and led to the real structure:

- `tgt_af2c = af2c + vvel` (vvel = `ds:[9336]` as of the call site, i.e.
  after that frame's decay/gravity/jump already ran) — **682/682 exact**.
- `tgt_lateral = ship_pos + lateral` (32-bit, ship's forward position
  re-centers the lateral target each frame as the curving track advances) —
  **682/682 exact, no offset term** (an earlier draft of this finding wrongly
  conflated this with the af1c multiply's separate offset below — different
  DGROUP accumulator, computed at a different point in the ASM,
  `1010:263C-2647` vs `1010:2650-2673`).
- `tgt_af1c = af1c + slong_div(ulong_mul(lateral_accel, ship_pos +
  af1c_base_offset), 0x200) + [5496]`, clamped to `af1c` unchanged if the raw
  result and `af1c` straddle a `[0x2F80, 0xD080)` wrap-seam band from
  opposite sides (`1010:26AA-26D7`) — **682/682 exact given the real
  af1c_base_offset per sample** (0 or `0x618`, see below). First consumer of
  `ds:[4568]` (`lateral_accel`, `steer*29`) — previously only a documented
  *write* target (`player.RespawnState`'s comment).

**What's still open**: `af1c_base_offset`'s real ASM selector (a stack-local
`ss:[bp-16]`, set at `1010:2340-23BF` when `af2c > 0x2800` AND a `ds:[0x228]`
-indexed table lookup on a `04C0` perspective-transform result for the ship's
own position matches `af2c` exactly — machinery that, confusingly, ALSO
fires a live side-effect call into `menu.dispatch_menu_action` with a
related action code before the low nibble is inspected again for this same
flag) is traced but not implemented. Empirically, `af1c_base_offset ==
0x618` in exactly the 58 real samples where `lateral_accel != 0`, and `== 0`
in all 624 others — a clean, perfect correlation in this demo, but the two
conditions are structurally independent circuits in the ASM, so it may be
coincidental to this demo rather than the true rule. Landed as
`skyroads/recovered/physics.py::compute_movement_targets`, requiring the
caller to supply `af1c_base_offset` explicitly rather than defaulting to the
correlation (tests: `tests/test_physics.py`, fixture
`tests/fixtures/movement_target_trace.json`, 98 samples: all 58 real-steering
ones + a spread of 40 non-steering).

**Not wired into `skyroads/native/loop.py` yet** — `MovementPhysicsGap`
still fires unconditionally. Closing it for real needs: (1) the
`af1c_base_offset` selector properly implemented (not the correlation
heuristic), (2) `lateral_accel` (`ds:[4568]`)'s own write-gate
(`1010:2550-256D`, mapped: only when `[9336] > 0` and
`[AF2C]-heightref < 0x0F00`, not yet independently verified as a pure
function), and (3) the jump-latch's session-persistence architecture: `ss:
[bp-8]`/`[bp-10]`/`[bp-14]`/`[bp-16]`/`[bp-18]` clearly persist ACROSS
frames (bp-8 stayed latched for the 8-frame freeze `VerticalVelocityGap`'s
finding described below), which a per-call stack local can't do unless this
whole per-frame handler (`1010:2280-2B0B`) is actually one continuous
execution context looping internally (via `jmp`, not `call`/`ret`) across
displayed frames — the tick-wait spin `skyroads/pacing.py` already parks at
`1010:22F8` sits INSIDE this same block, consistent with that theory. If
true, `skyroads.native`'s per-frame steppers need a companion session-scoped
scratch object alongside `GameView` (mirroring pre2_port's non-memory
`NativeGameState.__slots__` side channels: `sfx_queue`, `particle_capture`,
etc.) to hold these latches across `native_gameplay_frame` calls — not
something `GameView`'s DGROUP fields can represent. This is now a
concrete, scoped follow-up rather than an open question.

## 2026-07-11 — first native (VM-less) frame steppers, and a real vertical-velocity bug found through them

Started wiring "the entire game loop towards native vmless game" (the
pre2_port endgame model, see `vmless_roadmap.md`). Landed the state-mirror
plumbing and two frame steppers over it:

- `skyroads/native/state.py::NativeGameState` — the game's DGROUP owned as a
  plain 64 KB `bytearray` (no VM), with `from_vm(rt)` seeding. Smaller than
  pre2's 1 MB image on purpose: every SkyRoads island recovered so far only
  touches DGROUP.
- `skyroads/state_view.py` — a re-export shim (mirrors `skyroads/islands.py`
  for `oracle_link`) so `skyroads/bridge/dgroup_view.py::GameView` can use
  the shared `dos_re.state_view` backend/descriptor machinery (promoted from
  pre2_port) without a direct `dos_re` import — keeps skyroads/bridge under
  the same pitfall-#17 bar as skyroads/recovered. `GameView` names every
  DGROUP field the current islands touch (ship_pos, lateral, speed, bounce,
  af1c/af2c, game_state, entered/grounded (the same offset, two names for two
  modes), the timers, the keyboard row) as one dword/word property per field,
  reading raw (unsigned) words — the recovered functions each sign-extend
  their own inputs, so a view field must hand them the raw word, not an
  already-Python-signed one, or a function like `decay_bounce` double-converts.
- `skyroads/native/collision.py::make_visible` — wires
  `renderer.road_object_visible`/`perspective_row_offset`/`road_segment_clip`
  into the `visible(lateral32, depth, screen_y)` callback
  `movement.resolve_move` needs, mirroring `hooks.py`'s `_persp_exit`/
  `_clip_exit` minus their register-exit bookkeeping. Cross-checked against
  an independent reimplementation of the same wiring over 500 random table/
  probe samples (`tests/test_native_collision.py`) — not yet CALLED from the
  gameplay stepper (see below).
- `skyroads/native/loop.py::native_menu_frame` — complete, gap-free: every
  transition `dispatch_menu_action` needs is recovered. Verified against 4
  real E2E-demo frames where the ASM's own dispatch was confirmed a no-op
  (menu.py's "heartbeat" case) — the action code itself isn't observable
  without a dedicated capture hook, so only no-op frames are valid samples.
- `skyroads/native/loop.py::native_gameplay_frame` — commits forward motion
  (`advance_ship`) unconditionally (real-demo-proven, 0 mismatches), then
  raises a typed gap (`skyroads/native/gaps.py`) the instant it needs
  something not safe to compute: `JumpGateGap` if a jump is held (the
  impulse latch isn't recovered), `MovementPhysicsGap` for the lateral/
  vertical movement-target block (`1010:2560-26E9`, mapped but not recovered
  — see the entry below), or, new today, `VerticalVelocityGap`.

**The `VerticalVelocityGap` finding.** Composing `decay_bounce` then
`update_vertical_velocity` unconditionally every frame — the natural reading
of player.py's own docstring ("applied AFTER decay_bounce") — was the first
thing tried. Cross-checking it against real E2E-demo data
(`demo_e2e_20260710_132930`, frames ~765-772) falsified it: `ds:[9336]`
(bounce/vertical velocity) stayed **frozen** at a fixed value for 8 straight
frames while airborne with `af2c < 0x2800` — exactly the branch player.py's
`update_vertical_velocity` docstring already flagged as an untested,
"ASM-derived, dark" terminal-clamp. Composing the two recovered functions on
that frozen value predicts an immediate flip-and-clamp to `TERMINAL_VVEL`
instead; real ASM did nothing to that field for 8 frames. So the whole
decay+gravity/clamp block is evidently GATED by something not yet
recovered — most likely the same jump-in-flight state the (also unrecovered)
impulse latch tracks, since the freeze starts the frame the jump key was
pressed and outlives the frame it was released. This is stronger than "an
unexercised branch": it disproves the "runs every frame unconditionally"
assumption itself, not just one arm of the clamp.

`native_gameplay_frame` now only computes `decay_bounce`/
`update_vertical_velocity` inside the ONE envelope player.py's existing
verification actually covers (airborne, `af2c >= GRAVITY_HEIGHT_GATE`,
`grounded == 0`) and raises `VerticalVelocityGap` otherwise. Re-run against
the E2E demo: 10/10 "outside the envelope" samples now correctly gap with
`ship_pos` still matching real ASM (0 mismatches, vs. 2/8 silent wrong
values before this fix); the demo never happened to exercise the envelope
case itself (0 samples in the first ~1700 frames), so that narrow branch's
only evidence remains player.py's own earlier "238/238 deaths-demo frames"
claim from a different demo — not re-confirmed here.

**Honest coverage today**: every real gameplay frame in the E2E demo hits
either `JumpGateGap` or `VerticalVelocityGap` or `MovementPhysicsGap` before
`native_gameplay_frame` could call `resolve_move` — there is no frame yet
where a full native gameplay step completes without a gap. What IS proven:
the state-mirror plumbing (`NativeGameState` <-> `GameView` <-> recovered
function <-> writeback) is correct end-to-end for every field it touches,
`native_menu_frame` is a complete gap-free island, and the exact next
recovery targets are now precisely bounded (the jump latch, the vertical-
velocity gate, and the `2560-26E9` movement-target block) rather than
vaguely "game logic, none recovered yet". Tests: `tests/test_native_state.py`,
`tests/test_native_collision.py`, `tests/test_native_loop.py` (synthetic,
no demo needed), `tests/test_native_loop_integration.py` (real E2E demo,
skips if assets/demo are absent), `tests/test_layer_audit.py` (wires
`tools/audit_layers.py` into the suite for skyroads/recovered + native +
bridge, pitfall #17).

## 2026-07-11 — recovered + wired the intro animation-frame unpacker (1010:3A96), lift-first, one more real bug caught

User-reported: the intro ship/logo animation looked like un-hooked rendering.
Profiled the true intro frames (0-99, before any menu interaction) and found
page `3A00xx` completely dominating a run of consecutive frames, each burning
the entire step budget — confirming the report. Traced it to `1010:3A96`, an
**animation-frame unpacker**, not a renderer: it decompresses the intro's
sprite/logo data once at startup, not per displayed frame.

Used lift-then-refactor again (now the established process after the
stencil-blit lesson): `dos_re.tools.liftverify` proved a literal
transcription byte-exact first (after bumping the emitted lift's own
runaway-safety cap for one local, throwaway verification run — this function
does 8 x 1040 = 8320 rows of real work per call, tripping the same
block-count guard `buffer_relocate` hit). The proven lift revealed the exact
algorithm: 8 independent 64K segments (a fixed table at `ss:[bx+0xE76]`),
each self-relocating its own first 624 bytes from a self-referential header
offset, then unpacking 1040 fixed rows — a 3-byte verbatim prefix followed by
2-byte tokens expanded into `[b1,b2,0x00]` triplets until a `0xFF`
terminator.

**Even working from the proven lift, transcribing it into clean Python
introduced a real bug** (caught by cross-checking against real captured
segment data, not by the live hook verifier — the strict/lift/hand-checks
form layers, and this is the layer that caught it): the row prefix is
`movsb` then `movsw`, two *separate* instructions, not atomic with each
other. `movsb`'s write can land at a position `movsw` is about to read from
(`di` grows faster than `si` once a row has tokens, so it can catch up
mid-segment) — real hardware sees `movsb`'s fresh write; an implementation
that reads the whole 3-byte prefix before writing any of it does not.
Confirmed by tracing all 1040 row-boundary `(si, di)` pairs against real
hardware — they matched exactly once the instruction ordering was fixed.

Recovered as `skyroads/recovered/intro_anim.py::unpack_animation_segment`
(operating through `rb`/`wb` callbacks, not an isolated buffer copy — table
segments are less than 64K apart in real memory and physically overlap, so
writes must land on live memory to behave like real hardware regardless of
whether the game relies on that). Wired as
`skyroads/hooks.py::intro_anim_unpack_hook`. Getting the hook's own register
state right caught **one more bug** — SP was read to get the return address
but never actually advanced past it, an omission the strict verifier's
register-diff caught immediately (`SP` off by exactly one word, everything
else — memory, every other register, flags — already matched).

**Verified byte-exact**: 1/1 real call (it fires once per game session, not
per frame) over both the E2E demo and the cold-sound demo, zero divergences,
via `HookVerifierConfig.strict()`. All 1040 row boundaries of the actually-
processed segment cross-checked directly against real hardware too. Guarded
by `tests/test_intro_anim.py` (+ fixture). ~1.9x fewer interpreted steps over
the intro window (a modest number for a one-shot call, but the several
consecutive full-budget frames it used to cause are gone). 190 tests pass.

## 2026-07-11 — the level-select/menu dispatcher recovered (1010:1B49)

Followed up the state-2 finding by fully mapping and recovering `1010:1B49`,
the dispatcher `1010:1B68` (state-2 entry) turned out to belong to. It's a
clean, linear action dispatcher (`cmp ax,N; jnz next; jmp handler`, not a
jump table) on a 4-bit action code passed by the caller, always ending in a
common tail. Four known action codes:

- **`2`** scroll left: `scroll_pos -= 0x12F`, only if not yet "entered"
- **`0xA`** scroll right: `scroll_pos += 0x12F`, same guard
- **`0xC`** enter level-select: `[456E]:=2`; latches an "entered" flag once
- **`9`** confirm/start: if `[456E]==0` and either post-level timer is still
  under a threshold, reset both timers to `0x7530` (the same reset value
  `RespawnState` uses)
- any other code: no state change (the common "heartbeat" case — called every
  menu frame)
- **always**: clamp `scroll_pos` to `[0, LEVEL_END]` (`0x2AAA`) — the exact
  same constant `advance_ship`'s clamp uses

The key discovery: **`ds:[54AC:54AE]` — the same field `advance_ship` calls
`pos` — is reused as the level-select scroll position** while not in
gameplay. Confirmed directly: `54AC` increased by exactly `0x12F` (303) per
scroll-right call, tracked across 100+ consecutive samples.

Recovered as `skyroads/recovered/menu.py::dispatch_menu_action` (clean rule,
sampled verification — this is UI-tier code, not performance-hot, so no live
VM hook). **ASM_MATCHED: 318/318 real E2E-demo calls byte-exact**, across
every action code the demo actually exercises (`0`, `1`, `3` — all no-op/
default; `0xA` scroll-right; `0xC` enter). Actions `2` (scroll-left) and `9`
(confirm) are transcribed from the identical disassembly pattern as the
verified ones but never exercised by any demo — documented as ASM-derived,
not independently verified. Also not modeled: the conditional calls to
`1010:03C2(0)`/`03C2(4)` (side effects on other state). Guarded by
`tests/test_menu.py` (+ fixture). 188 tests pass.

## 2026-07-11 — a third tick-wait parked (menu/animation timer at 1010:47CD); `[456E]` state 2 identified

Continued the perf work autonomously. Re-profiled the E2E demo with both new
hooks (`0F62`, `4052`) installed and found several menu frames still burning
the *entire* step budget on page `4700xx`. Traced it to a **third tick-wait
spin**, structurally identical to the two `frame-park` already handles:
`1010:47CD` (`cmp ds:[1600],0002h; jnb 47D7; jmp 47CD`) — a menu/animation
frame-timer waiting for `[1600] >= 2` rather than "changed". Same reasoning
applies (`[1600]` is frozen for the whole frame, so once it's under the
threshold it cannot cross it before the next frame) — added as a third park
hook in `skyroads/pacing.py::install_frame_park`. This is **runtime-loaded
code** (invisible in the static EXE, same gotcha as the sound driver from
earlier — disassembled from a live snapshot).

**Byte-equivalence proof**: replaying the full E2E demo (1719 frames) park-ON
vs the full-spin baseline (`--no-frame-park`) — every one of 1719 rendered
frames byte-identical (`frames_hash` matches exactly across two separate
runs), **5.03x fewer interpreted steps** (43,556,327 -> 8,649,674). Locked in
by a new `tests/test_frame_park.py::test_menu_anim_wait_is_byte_equivalent_and_cheaper`
using a captured mid-spin snapshot (the gameplay snapshot the existing park
tests use never reaches menu code). 181 tests pass.

Also checked the other recurring hot pages (`6000`/`6300`/`6500`) for a
similar win: `1010:6013-601A` is a VGA vertical-retrace hardware poll
(`in al,0x3DA; test al,8; loope`) — a categorically different, riskier kind of
wait (it polls emulated *hardware* state, which — unlike `[1600]` — is not
necessarily frozen for the whole frame, so "park until next frame" is not
provably safe the same way; pre2_port's own pitfalls doc warns against
conflating a deterministic skip with live pacing for exactly this reason).
Checked `dos_re.dos.DOSMachine._vga_status`: in this emulator the retrace bit
toggles on **read-count parity**, not wall-clock/instruction time, so the poll
already resolves in 1-2 iterations in practice — not actually a bottleneck.
Left alone; flagging the reasoning here so a future session doesn't have to
re-derive it.

**`ds:[456E]` (top-level game-state) gains a mapped value.** Re-traced state
transitions over the E2E demo with the segment-filter bug fixed (an earlier
probe captured `cpu.s.ds` at hook-install time, before the game had even set
up its DGROUP — comparing against `0x1000` instead of the real `0x1686`,
so it silently matched nothing). The real E2E demo cycles cleanly:
`0->2 (1010:1B68) -> 2->0 (1010:2060) -> 0->3 (1010:2AC2, gameplay start) ->
3->0 (1010:2060, gameplay end)`, repeating once per level played. `1010:1B49`
is a dispatcher (`enter 0x0000,0`, dispatch on `(bp+4)&0xF` through a jump
table at `1BED`) whose relevant action case writes `[456E]:=2` and, if
`[456A]==0`, sets it to 1 and calls `1010:03C2(0)` — consistent with
**state 2 = level-select/menu entry**. Not further recovered this session
(a full dispatcher recovery is comparable in scope to the earlier `074C`
controls work) — flagging it mapped, not claiming it recovered.

## 2026-07-11 — recovered + wired the buffer-relocation hook (perf cause #2), lift-first this time

Picked up perf cause #2 from the diagnosis: the un-hooked buffer scan/patch
loop at `1010:4052`, hot at level-transition frames. This time used
**lift-then-refactor** instead of hand-deriving from disassembly, per the
process correction from the stencil-blit work: ran `dos_re.tools.liftverify`
against a snapshot first, got a proven-correct literal transcription
(ORACLE_PASSING, a bounded-count sample, 8/9 blocks — the lifter's own
runaway-safety cap tripped on the real unbounded call, since one real
occurrence scans a full 64K-underflow pass; patched the count argument down
to a bounded value in a snapshot copy purely to get a liftable sample, then
verified the real large-count behavior separately against actual gameplay),
then wrote the recovered function + hook from the lift's PROVEN block
structure rather than reading the raw disassembly by eye.

That mattered concretely: the lift revealed `ss:[bp+0xA]` is a **second,
in-place-decremented counter** controlling additional full-64K scan passes —
reading the static disassembly alone made it look like a caller-owned local
the function never touches, an easy miss (the same class of mistake the
0F62 hook made twice). It also confirmed the segment-wrap check
(`inc bx; jz`) runs unconditionally on every byte, independent of the count
check that follows it.

Recovered as `skyroads/recovered/relocate.py::patch_nonzero_bytes(source,
delta) -> bytes` (a DOS relocation-fixup pattern: `0` is a "leave alone"
sentinel, everything else gets `delta` added mod 256) plus
`skyroads/hooks.py::buffer_relocate_hook`, which ports the lift's proven
pass/segment-wrap/register-exit mechanics directly (not re-derived) while
batching the byte-patch step through the pure function.

**Verified byte-exact on the first attempt** — no correction rounds needed,
unlike stencil_blit: 252/252 calls over the full E2E demo + 230/230 over a
cold-sound-demo window (482/482 total), zero divergences, via
`HookVerifierConfig.strict()`. (Verifying the *whole* cold-sound demo timed
out — this function scans up to 64K bytes per call and the strict verifier
re-runs the real ASM interpreter to build its oracle side, so its cost scales
with how much of that scanning the demo exercises; the E2E demo + a
cold-sound window already give strong, wide-ranging coverage.)

**Honest coverage gap**: neither demo happens to make a call whose scan
crosses a 64K segment boundary or arms the extra-pass counter — checked
directly (0/252 E2E calls trigger either). Those two branches are
mechanically proven correct by the lift's own bounded sample (whose
`ss:[bp+0xA]` value did drive at least one extra-pass check) but not
exercised end-to-end against real gameplay data. Guarded by
`tests/test_relocate.py` (+ fixture) for the pure function. 181 tests pass.

**Process note**: this hook needed ZERO debugging rounds against the live
differential verifier, vs. two for stencil_blit (which skipped the lift
step). Lift-first is faster in wall-clock terms too — most of the effort goes
into a cheap, fast, bounded lift-verify run instead of iterating against the
much slower full-gameplay strict verifier.

## 2026-07-11 — recovered + wired the stencil-blit hook (perf cause #1), verified 244/244 zero-divergence

Picked up perf cause #1 from the diagnosis below: the un-hooked menu text/glyph
rendering primitive at `1010:0F62`. Recovered as
`skyroads/recovered/blit.py::stencil_blit(source, template_color,
other_color) -> bytes`: a pure 3-value stencil remap (`0->0, 1->template_color,
else->other_color`), the low-level primitive behind menu font/glyph drawing.
No port I/O (unlike the music engine), so — unlike that hook, which got
shelved — full register-exact parity against the project's strict differential
verifier was tractable, and worth doing since this routine showed up
repeatedly in the perf profile.

Wired as `skyroads/hooks.py::stencil_blit_hook`, a real `registry.replace` for
`1010:0F62`. Getting it register-exact took two rounds of the strict verifier
catching real mistakes that hand-reasoning from the static disassembly missed
(same lesson as the earlier renderer hooks — trust the verifier, not the eye):

1. First attempt assumed `SI`/`DI` end up as "final cursor position"
   (`source+count`, `count`) — wrong. The function opens with `push si; push
   di` and closes with `pop di; pop si`: they are the **caller's original
   values**, fully preserved, not touched by the loop at all.
2. Second attempt computed `AH` (and the flags' `AF` bit) from only the *last*
   source byte. Both actually **thread through the whole loop**: `AH` only
   changes on a template/other substitution (a plain zero byte's `or al,al`
   only touches `AL`), and `AF` is *undefined-preserved* by `or` on real
   8086 (`cpu.set_logic_flags` mirrors that convention) — only a `cmp al,1`
   iteration (any nonzero byte) redefines it. A source ending in zeros after
   a substitution exposes both bugs; the very first live call the verifier
   checked happened to end that way.

**Verified byte-exact: 213/213 calls over the full E2E demo + 31/31 over the
cold-sound demo (244/244 total), zero divergences**, using
`dos_re.verification.HookVerifierConfig.strict()` (full machine-state diff:
every register, segment, flag, and DOS/device state — not just memory or
output). Guarded by `tests/test_blit.py` (+ fixture) for the pure function;
the hook's register mechanics are what the strict verifier proved and aren't
re-asserted in unit tests (matching how the other complex hooks — `1732`,
`lzs_decode_loop` — are documented: the differential-verifier run **is** the
proof). 178 tests pass.

## 2026-07-11 — found + fixed two more music-engine bugs; shelved wiring it as a live hook (wrong tool for the perf goal)

Continued from the perf diagnosis below by attempting cause #3: wire the
verified `music.py::Engine` as a real `registry.replace` hook for `1010:5A55`,
replacing the `emulate_call` in `master_timer_isr`. Two things came out of
this attempt:

**Two more real bugs found and fixed**, both invisible to the existing
per-tick ASM-comparison fixtures (which always have the real ASM running
alongside, keeping memory in sync regardless of what the Python engine's own
state holds) and only exposed by simulating what a *live* hook must do —
drive itself off nothing but its own committed state across many ticks:

1. The delay-decrement off-by-one documented in the commit above (the loop-back
   target is the delay check itself, not the word-fetch — the arming tick
   also performs the first decrement).
2. (From the prior session entry) cursor/loop not persisted to memory.

Both fixed and regression-tested with a synthetic multi-tick simulation (no
VM) whose expected sequence was computed, not hand-derived, after hand-tracing
produced the very bug being fixed. Re-verified byte-exact against the ASM
over the whole cold-sound demo (12,882/12,882 ticks) after each fix.

**Wiring it as a live hook is shelved — wrong tool for this goal.** The
project's differential verifier (`HookVerifierConfig.strict()`) compares the
*entire* machine state after a hook call: every register (AX/BX/CX/DX/SI/DI/
BP/SP), segments, flags, and DOS/OPL device state — not just memory or
observable output. Tracing what `1010:5A55`'s handlers leave in scratch
registers on every exit path turned up a deeper problem than incidental
bookkeeping: the ASM's `opl_write` primitive (`1010:5892`) ends with `in
al,dx` — a real hardware status-port *read* — so the exact value left in AL
depends on the emulated OPL device's live status byte at that instant, not on
game logic at all. Getting register-exact parity would mean replicating that
port-read side effect (and equivalents for every opcode's exit path), which
has nothing to do with the actual sound behavior and would turn the clean
recovered `Engine` into hardware-timing bookkeeping.

Even if that effort were spent, it likely **wouldn't fix the user's actual
complaint**: an earlier finding in this project (the `34AE` renderer hook)
already established that a mechanically-exact lift runs at roughly
interpreter speed under CPython — only a refactor into genuinely different
Python control flow (or a PyPy JIT) yields a real speedup. `music.py::Engine`
*is* that refactor, but achieving strict-verifier parity would mean adding
back the ASM's own register/port-timing bookkeeping, undermining the reason
it would be fast in the first place.

**Conclusion: `music.py::Engine` stays as verified-by-output recovered logic**
(the right tier for it — same as `advance_ship`/`decay_bounce`, sampled/output
verified rather than a live differential-machine-state replacement), valuable
for the eventual native port, not wired as a CPython speed hook. The real
fix for causes #1 and #2 below (the un-hooked text-render and buffer-scan
loops) is more promising: likely simpler register footprints, no hardware-
timing reads, and they're the actual source of the multi-hundred-ms frame
hitches — sound delay is a symptom of those, not of the sound engine's own
per-tick cost. 175 tests pass.

## 2026-07-11 — diagnosed the reported perf drops + "sound delay": three distinct un-hooked causes

User report: visible performance drops during some transitions, and sound
feels delayed, "probably poor performance." Profiled the full E2E demo
(`demo_e2e_20260710_132930`) frame-by-frame with the real frontend (frame-park
on, current defaults) and found the slow frames are **not** one root cause —
three distinct un-hooked things, all during menu/transition screens (gameplay
is already fast):

1. **Un-hooked menu text/string rendering** (`1010:0F75`, `1010:41E7` and
   siblings) — classic `lodsb/cmp/stosb/loop` character-blit loops, real
   CPU-bound work (not idle spin, so frame-park can't help). These are menu
   text drawing, not asset loading.
2. **An un-hooked buffer scan/patch loop** (`1010:4062-406C`, called from a
   utility at `1010:4052`: `lds bx,farptr; cx,ax=count,delta; loop{ if
   [bx]!=0: [bx]+=al; inc bx (segment-wraps +0x1000 on overflow) }`) — seen
   heavily at level-transition frames (997, 1517). Likely a palette/index
   rebase over a large buffer; not yet characterized enough to know if it's
   asset-related.
3. **The recovered OPL music engine is verified but never wired as a VM
   hook** — the timer ISR (`master_timer_isr`) still calls through
   `emulate_call` to run the *original* ASM at `1010:5A55` every tick (pages
   `5800`/`5900` show up prominently in some slow frames, e.g. frame 14's
   201ms). This one is very plausibly the direct cause of "sound feels
   delayed": a live player pumps audio on the wall clock, so a 150-230ms
   interpreted-frame hitch (any of the causes above) blocks the audio pump for
   that same span, causing an audible stutter/lag regardless of how fast the
   sound engine itself is.

Slowest E2E frames measured (wall time under CPython, headless): 231ms (frame
770, page `1D00`, unidentified), 201ms (frame 14, sound driver init/patch
load), 196-170ms (several `4000xx`-dominated transition frames), down to a
~150ms tail of similar transition frames. Gameplay frames are consistently
fast by comparison (frame-park + the recovered render/physics hot path).

**While investigating whether cause #3 could be fixed immediately** (wire the
already-recovered `music.py::Engine` as a real hook), found and fixed a real
correctness bug first (see the commit right above this entry): the engine
never persisted its song cursor or decremented the delay counter back to
memory, which is invisible in pure per-tick verification (the real ASM keeps
memory in sync regardless) but would silently break a live hook (replay the
same song forever). Fixed and regression-tested. **The hook itself is not yet
installed** — doing so safely needs full register-state differential-verifier
proof (the project's standing rule), which is a separate, sizeable follow-up
from a diagnostic session.

**Not done this session** (flagging for prioritization): recovering #1 and #2
as clean, hooked Python (the same methodology as the render islands); wiring
+ differentially verifying the music-engine hook (#3). All three are
well-scoped, tractable next steps, comparable in size to earlier renderer-
island work — not something to rush without proper verification.

## 2026-07-10 — game logic: respawn/reset + resume-gate recovered; death-flow architecture corrected

Continued the game-logic thread with the death/respawn side. Corrected a
misread from the physics-mapping session: the jump-impulse gate at `258C` is
`jb` (jump-if-below), so it fires when `[4562] < 0x14`, not `>=` as previously
written — `[4562]` turned out to be a **per-level constant** (pinned at 8 for
the whole deaths demo, not a per-frame counter), read once via `1FFA-200A` to
compute the level's gravity constant `[54AA]` (`= -([4562]*0x1680/0x190)`),
confirming `[4562]` is a per-level physics parameter, not gameplay state.

Traced the actual respawn machinery empirically (writer/caller tracing, not
static guessing — a naive static disasm of "the block starting near 2020" was
misaligned and gave garbage). Findings:

- **The gameplay update genuinely is one monolithic per-frame handler**, as
  `player.py`'s module docstring already said — `1010:1FD9` is not a separately
  called "reset function" but a label inside that same handler; its apparent
  `call ... ret=2C61` is just the handler's own single call-from-the-main-loop
  return address, constant across every internal label.
- **Respawn/reset** (`1010:201F-20A7`): recovered as
  `player.py::respawn() -> RespawnState`, a **pure constant** — 19 DGROUP
  fields (ship position, lateral, vertical, game_state, level timers, tick
  counter) all reset to fixed values, no branching on prior state in the
  sampled span. **ASM_MATCHED — 3/3 real deaths-demo respawns, all 19 fields
  byte-exact.**
- **Resume gate** (`1010:2AB1`): `player.py::is_landed_for_resume(af2c)` =
  `af2c >= 0x2800` gates `[456E]:=3` (resume gameplay) after a respawn. Since
  `respawn()` writes `AF2C := 0x2800` exactly, a fresh respawn is immediately
  resume-eligible.
- **The jump gate is only partially recovered.** Beyond
  `[547A]!=0 and [4562]<0x14`, there are **two more guards**, `ss:[bp-8]` and
  `ss:[bp-18]` (`2570`/`2579`) — frame-local flags that skip the whole jump
  block if either is nonzero, set earlier in the same handler (likely from the
  collision/height classification around `2340-2385`). This is *why* the
  impulse fired only 3 times despite the jump key being held for 29 frames in
  the deaths demo — it fires once per press, not once per held frame,
  almost certainly an "already airborne" latch. `update_vertical_velocity`'s
  `jumped` parameter stays an external input until bp-8/bp-18 are traced.
- **The `[456E]` state machine is wider than previously documented** — the
  outer pacing block (`2A90-2B08`) cycles it through 0/1/3/4/5 via countdown
  timers `[5494]`/`[B13C]` (post-level-complete sequencing, not death-related);
  not further mapped this session.

Guarded by `tests/test_player.py` (respawn + resume-gate cases). 174 tests pass.

## 2026-07-10 — sound/music island COMPLETE (OPL music engine recovered + verified)

Recovered the whole AdLib/OPL music engine into
`skyroads/recovered/music.py::Engine.run_tick` — a pure, VM-free song-bytecode
interpreter (all 8 opcodes incl. the intricate note/instrument/pitch/volume
register math). **Verified byte-exact**: its OPL register-write stream matches
the ASM over **all 12,882 cold-sound-demo ticks (intro + menu), zero
divergences** — lockstep per tick, same proof style as the SB-PCM work. Status
`VERIFIED`. Guarded by `tests/test_music.py` (+ fixture); the transcription was
byte-exact on the first lockstep run.

Recovery notes for the trickier handlers: op1 loads an 11-register FM patch
(operator regs `slot[ch] + offset[i]`, op-2 registers skipped on an add-carry,
the 11th/connection register gated on a `0xFF` sentinel); op2 computes octave =
`note/12 + 2` and F-number from `note%12` tables, writes `A0` then `B0|key-on`,
and channels whose `B0` reg reaches `0xB6` fall through into the rhythm path;
op4 scales operator total-level with a per-level bias and `0x3F` clamp. The
song data + tables are *data the port loads*, not code (see below).

Also recovered the one-time **OPL reset / percussion init** (`1010:58A5-5913`,
run once at driver start before any song plays): silence all 22 operator
registers, key-off channels 7..0, enable waveform-select + rhythm mode, load 4
fixed percussion patches via the same `op1` path, fix the 2 percussion
channels' pitch. `Engine.reset_opl()`, **VERIFIED** — byte-exact against its
one occurrence in the cold-sound demo, confirmed the *only* occurrence over the
full 2157-frame replay. Gotcha found while isolating it: `58A5` (the
silence+keyoff subroutine) is also called **standalone** elsewhere just to
silence the chip, not only as step 1 of the full init — trace the call site
`58CD` to isolate the complete sequence.

Also settled: **SFX needs no recovery island.** It's digital PCM over Sound
Blaster DMA; `skyroads/audio.py` already plays it correctly as a *pure
observer* of the raw DMA bytes (same pattern as render hooks watching OPL
writes) — there's no trigger-condition logic to reimplement.

**The sound/music subsystem is now fully retired for the VM-less port** —
sequencer, one-time init, and SFX all covered. 172 tests pass.

The reverse-engineering that made this possible (unchanged, kept for reference):

Reverse-engineered the whole AdLib/OPL music driver — see
[`sound_engine.md`](sound_engine.md). It is a compact **music-bytecode
interpreter** at `1010:5A55` (per timer tick): walk a song event stream, decode
`op = word & 7` / args, dispatch through an 8-entry table at `DG:0x0C5B`, program
the OPL2 via the `opl_write(reg=AL,val=AH)` primitive at `5892`. Eight opcodes:
delay, note+instrument (11-register FM patch), note-on pitch (F-number/octave),
key-off, volume, loop, set-loop-point, flag. State + data tables documented.

Key discovery that unblocked this: the driver is **runtime-loaded** (zero in the
static EXE), so it must be disassembled from a *post-intro* snapshot, and
`lindis`'s text column mis-renders some `[disp]` values (read the byte column).

The register-group capture confirmed the full OPL2 map is written (0x20–0xF0
operators, 0xA0/0xB0 freq+key-on, 0xBD rhythm). Remaining to *complete* the
island: transcribe the engine + 8 handlers + the note-frequency math into clean
VM-free Python and verify it emits the **byte-identical OPL register-write
stream** as the ASM over the cold-sound demo (lockstep per tick). Architecture
is done; the byte-exact build is the well-defined next step.

## 2026-07-10 — game logic: vertical-velocity physics (jump impulse + gravity)

With the user's **deaths demo** (`demo_skyroads_20260710_213019` — 29 jump-frames,
3 jump impulses, states 0/1/3), recovered the jump+gravity stage of the vertical
velocity `ds:[9336]` update (`2582-2635`) as
`skyroads/recovered/player.py::update_vertical_velocity`, **ASM_MATCHED 238/238
deaths-demo frames byte-exact** (incl. the 3 jump frames). Per frame, after
`decay_bounce`: jump fires `[9336]:=0x480` (`2596`), then airborne
(`[456A]==0`) `[AF2C]>=0x2800` adds gravity `[54AA]` (`25F0`). Guarded by
`tests/test_player.py`.

Corrected a branch-direction misread along the way: gravity is the
`[AF2C] >= 0x2800` side (`jnb`), not `<`.

**Still dark, even in the deaths demo:** the terminal-velocity clamp
(`[AF2C]<0x2800` → −106) and the grounded ramp (`[456A]!=0` → +0x47). The demo's
deaths are all *collisions* (`[AF2C]` stays `>=0x2800`, `[456A]` stays 0), so
those branches are transcribed from the ASM but unverified. Also still open: the
**jump gate** itself (`2582/258C`: what latches "can jump" — frame-local state
not yet resolved) and the **death / level-complete state transitions**
(`456E` writes at `2060/27FD/2AC2`; `[AF2C]` vs `0x2800` fall-test at `2357`).

## 2026-07-10 — game logic: keyboard control decode recovered (input → speed/steer/jump)

Started the input side of the gameplay handler. Mapped it empirically first —
traced which code writes each gameplay-state field over the input-carrying demo
rather than reading disassembly and guessing (RE-hallucination guard). That gave
clean single-writer islands: speed `[9330]` ← `08E6`, vertical `[AF1C]/[AF2C]`
← `1965/197D`, lateral target ← `1949`, and the game-state transitions (death/
complete) ← `2060/27FD/2AC2`.

The input handler is `1010:074C`, a dispatcher on the selected control device
`ds:[95F6]`: **0 = keyboard**, 1/2 = other devices, 2 = joystick (reads axes via
`06B9` vs thresholds), **3 = attract-mode autopilot** (reads a packed control
track at `ds:0x961E`, indexed by `lateral_pos / 0x666`, unpacking speed/steer/
jump from bitfields). Live play is the keyboard case (`0758`); the whole demo
runs `95F6==0`.

Recovered the keyboard case as `skyroads/recovered/controls.py::decode_keyboard`
— **ASM_MATCHED, 1466/1466 full-demo `074C` calls byte-exact** (497 with keys
held). It reads the per-key row the timer ISR maintains at `ds:0x0BD0` (bit 7 =
held) and folds nine keys (an 8-direction pad + jump) into three axes:
`speed=[9330]`, `steer=[95F4]`, `jump=[547A]`, each `(OR of positive dirs) -
(OR of negative dirs)`; diagonals drive both axes. Guarded by
`tests/test_controls.py` (+ fixture). Scancode→row-offset mapping (in the ISR
poll `3BE5`) is separate host-input plumbing, not yet recovered.

⚠️ **`artifacts/gameplay_snap_f520` is attract mode (`95F6==3`), not live play.**
Lifting `074C` from it exercises the autopilot track decoder, not the keyboard
case — a trap for future game-logic recovery. Capture snapshots from a demo
replay at a `95F6==0` frame instead (helper pattern in this session's scratch).

### Vertical/lateral physics — mapped, recovery gated on a jumps+death demo

Mapped the per-frame movement physics block (`1010:2560-26E9`, inline in the
gameplay handler) that computes the targets fed to `186B`/`resolve_move`. Field
semantics (some correcting earlier labels):

- **`ds:[9336]` is the vertical VELOCITY**, not merely a "landing bounce".
  `decay_bounce` (`24A1`) damps it; gravity accelerates it; jump impulses it.
- **`ds:[547A]` (jump) IS read** at `2582` (a word `cmp`, which is why an
  `rb`-only reader trace missed it) — the demo just never sets it.
- `ds:[4568] = steer[95F4] * 29`, **guarded** (`2550-256D`: only when
  `[9336] > 0` and `[AF2C]-heightref < 0x0F00`) and it feeds the **vertical**
  target term (`2676`: `[4568] * … / 0x200`), not the lateral axis.
- Gravity (`25DB-2635`, when `[456A]==0`): airborne (`[AF2C] < 0x2800`)
  `[9336] += [54AA]`; past `0x2800` it snaps to terminal `0xFF96` (−106); the
  `[456A]!=0` path ramps `[9336]` up to `+0x47` (grounded/rising).
- Jump (`2582-25A6`): when `jump && [4562]>=0x14 && …`, set `[9336]=0x480`
  (up impulse) and latch a "jumping" flag.
- Death test at `2357` (`[AF2C]` vs `0x2800`) and level-complete at `2514`
  (`[54AC]` vs `0x2AAA` = `LEVEL_END`).

The jump impulse, terminal-velocity clamp, grounded ramp, and the death path are
all **dark in the current demo** (no jumps, no death — the run is a clean
start→finish). Recovering this block byte-exact needs a demo that exercises
them; **the user is recording a jumps+death demo** to unlock it. Until then the
map above is the tee-up, not recovered source.

## 2026-07-10 — gameplay perf: it was a pacing/steps issue, not hook coverage (frame-park)

"Gameplay performance is still not good" turned out **not** to be a
hook-coverage problem — the render/math hot path is already hooked. Profiling
the gameplay window (running the `game_state==3` snapshot forward) showed where
the interpreted budget actually goes, per frame:

| bucket | share of steps |
|---|---|
| **idle tick-wait spin** (side-effect-free) | **~88%** |
| real un-hooked work (render + update) | ~8% |
| recovered hooks | ~4% |

The game paces itself off `ds:[1600]`, the elapsed-tick counter its INT 08h ISR
bumps. But the viewer delivers **all** of a frame's timer IRQs at frame start
(`advance_frame`), so `ds:[1600]` is **architecturally constant for the whole
step budget** — it can't change again until the next frame. Any loop waiting on
it therefore spins out the entire remaining budget doing nothing. Two loops do:
`1010:22F8` (main gameplay pacing spin) and `1010:434A`/`4449` (the fade/pacing
wait). So the VM was grinding 30000 steps/frame of which ~26000 were pure spin
→ ~5 fps under CPython.

**This is exactly what pre2_port already solves** (the endgame reference). Its
`scripts/play.py` classifies known busy-wait loops (`pre2.recovered.vga_timing`,
the PIT `1C6F` wait) and fast-forwards them: *"touches no game logic … the
trajectory stays byte-equivalent — only the wall clock improves."* SkyRoads'
port had the empty equivalent — `skyroads/input_waits.py` was never populated.

**Fix: `skyroads/pacing.py` frame-park** (on by default; `--no-frame-park` to
force the full spin). Two hooks at `22F8`/`434A` raise `FrameIdle` the instant
the game parks in its tick-wait; `SkyroadsFrontend.advance_frame` catches it and
ends the frame. The `434A` park defers to the existing verified fade-loop gate
whenever there are keys to drain, so input timing is unchanged.

**Byte-equivalence proof** (the bar for a pacing shim): replaying the full E2E
demo (the whole **1906-frame** level, start to finish) park-ON vs the full-spin
baseline through the real replay path — **every rendered frame identical and all
named game-state fields identical**, at **3.4× fewer steps** (17.8M vs 61.2M)
and **3.0× faster wall**. The E2E ratio is diluted by menus/fades/input; the
gameplay window alone is **~6–8× fewer steps** since it is nearly all spin. A
full-memory diff over the run shows the *only* bytes that differ are **11 bytes
of fade-loop scratch at DGROUP+0xB87C** (a blend/poll counter written by
`43A9`/`415x`, never read into game state or any rendered frame). Locked in by
`tests/test_frame_park.py`.

**Budget resized to a ceiling above peak work (30000 → 48000).** With the park
on, `steps_per_frame` is no longer the per-frame cost — it is a ceiling for the
frames that *don't* park. Measured real work over the level: p50 ~9.2k, p99
~34.8k, **peak 37,309** (113/1906 frames exceed 30000 and were being cut
mid-tick). So the budget must be sized *above* the peak, not toward the average:
48000 clears 37,309 with ~28% headroom. Shrinking it (e.g. to 5000) is the wrong
instinct — a budget below peak makes the original ASM see itself lagging and
engage its own lag compensation (deterministic but not original pacing; the
lesson is in `pre2_port/scripts/play.py`, which warns below chunk 20000). Safe
to change: `steps_per_frame` lives in `demo_metadata`, so existing demos replay
at their recorded budget regardless of the default. (The title/menu idle loops
are a *different* set of un-parked waits — `skyroads/input_waits.py` is still
empty — so a fresh boot still spins near the ceiling; parking those is future
work.)

Also fixed a **pre-existing CI break** surfaced along the way: the updated
dos_re submodule now ships `dos_re/tests/`, whose top-level package name
`tests` collided with this repo's `tests/` on `pythonpath`, breaking collection
outright (`No module named 'tests.test_*'`). Pinned `testpaths=["tests"]` +
`--import-mode=importlib` in `pyproject.toml`; `pytest -q` is green again.

## 2026-07-10 — physics recovery verified full-demo + a negative-speed bug fixed

Brought the recovered ship physics (`skyroads/recovered/player.py`) up to the
movement-island standard: captured real `advance_ship` (`24C4`) and
`decay_bounce` (`24A1`) I/O by watching those inline IPs over the whole demo and
verified `player.py` reproduces every sample byte-exact — **1610/1610
advance_ship, 63/63 decay_bounce**.

The capture found a real bug: the ASM sign-extends speed (`cwd` at `24C7`) into a
32-bit value before `ulong_mul(speed, 75)`, but `advance_ship` used
`(speed & 0xFFFF) * 75` (unsigned 16-bit). They diverge for negative speed — the
ship moving *backward* — which happens **33 of 1610 calls** (e.g. speed `0xFFFF`
= −1 should step pos back by 75; the old code clamped it wrong). Fixed to
sign-extend; all 33 now match. Guarded by `tests/test_player.py` (fixture
includes the negative-speed cases). Same lesson as the `186B` unsigned/signed
edge case — full-trace verification catches what sampled checks miss.

## 2026-07-10 — lifted the 186B road-segment stepper (movement + swept collision)

`1010:186B` — the "largest single remaining recovery" per rendering_architecture
— is now a verified island. It is the game's **swept movement + collision
resolver**: a 274-instruction, 80-block, 5-phase iterative solver that steps the
ship's accumulators (`ds:[9618:961A]` lateral, `ds:[AF1C]`, `ds:[AF2C]`) from
their current values toward a requested target in sub-steps, using `1732`
(`road_object_visible`) as the collision predicate and refining each axis to the
exact contact boundary. Phases: (1) early-out if already at target; (2) 5-step
forward sweep, find the first sub-step `1732` blocks; (3) commit the furthest
safe sub-step; (4) binary-search the lateral axis (step ÷16); (5) refine `AF1C`
then `AF2C` (step ±125, ÷5). Calls only already-recovered helpers
(`1732`/`5D4C`/`5E5A`/`5D8C`). Core **movement/collision game logic**, and it
drives the repeated `1732`+`04C0` road-segment work.

Recovered with the **automatic lifter**: `liftgen` census → 100% liftable (274
insts, 4 calls, 0 INTs); `liftverify` emitted the byte-exact lift. Its
`enter 0x000a,0` prologue exercises dos_re's just-landed entry-fallback
recursion fix (submodule bump `11917f2`). Installed as
`skyroads/lifted/lifted_1010_186b.py` + `registry.replace(0x186B)`.

**Verification — 1760/1760 full-demo calls byte-exact (71/80 blocks),
ORACLE_PASSING.** This needed the *compositional* differential mode: `186B`
calls four already-verified child hooks, and the lift's `emulate_call` runs
their Python hooks while the ASM oracle (auto-continuation, hooks dropped) runs
real ASM — so the two leave different **dead stack below SP** (the nested-call
arg-push scratch), which a naive full-memory strict diff flags as a
"divergence". Marking the children passthrough (`asm_keeps_passthrough_hooks` +
`hook_verifier_passthrough`) makes both sides run identical child code, leaving
only `186B`'s own instructions to diff — and then all 1760 calls match exactly.
(Caution: `liftverify`'s default 40-sample PASS was *misleading* here — the
divergent deep-stack path first appears around call 41; always verify past the
sample cap for functions that call other hooks.)

An **end-to-end memhash test diverged** (+132K steps, memory differs, but
**registers identical**) — this is NOT a correctness failure. It is the known
fixed-step-budget / busy-wait interaction (see the `palette_fade_inner` note
below): replacing `186B`'s ~274 interpreted instructions/call with a Python hook
frees per-frame step budget, so the game's idle elapsed-tick spins (`22F8`,
`4153`) iterate a different number of times and the arbitrary frame-boundary
state drifts. Registers-identical + the 1760-call per-call proof confirm game
*logic* is unchanged; the e2e memhash is not a valid invariant for any
step-count-changing hook (every installed lift/hook fails it identically —
*confirmed*: toggling the already-accepted `34AE` lift in the same e2e diverges
even harder, −6M steps, memory differs, registers identical). The per-call
differential verifier is authoritative. All 159 port tests pass with `186B`
installed.

**Now refactored into a clean recovered island** (metrics-honesty debt paid):
`skyroads/recovered/movement.py::resolve_move` is the swept movement+collision
solver as pure, VM-free Python + `@oracle_link` — the native-port destination
(roadmap gap #1, movement/collision logic). Verified `ASM_MATCHED` **1760/1760
full-demo calls** by a *predicate-oracle* method: replay the exact `1732` results
the ASM saw and check both the output accumulators AND that the reconstruction
probes the exact same positions (an unrecorded probe = a diverged interpolation).
This caught a real edge-case bug the 250-sample missed — the axis-refine
direction uses an **unsigned** compare (`cmp [bp+8],ax; ja`), not signed; it only
matters when the depth accumulator and its target straddle 0x8000 (2 of 1760
calls). Guarded by `tests/test_movement.py` (fixture includes those cases). The
`186B` **lift stays installed as the byte-exact VM hook** (it reproduces the
exact register/stack state the differential verifier needs); `movement.py` is the
clean logic that replaces it when the VM is retired. Note neither is a CPython
perf win (`186B` is only ~2-4% of interpreted work; the lift runs at
~interpreter speed) — the value here is correctness + native-port coverage.

## 2026-07-10 — audio: digital SB PCM effects + AdLib-on-PyPy + correct 30 Hz frame rate

Three sound/timing fixes.

### 1. Native frame rate is 30 Hz (PIT reprogrammed to 180 Hz) — `present_hz` was 2× too fast

SKYROADS reprograms **PIT channel-0 to divisor 6628** at boot (`OUT 40h`), i.e.
`1193182 / 6628 = 180.0 Hz` IRQ0 — *not* the 18.2 Hz BIOS default (confirmed by
tracing port-40h writes; the frequent `43h=B6h`/`42h` writes are channel-2, the
PC speaker). Its INT 08h ISR software-prescales `/6` (`ds:[3192]`), so game
logic ticks at `180/6 = 30 Hz` — the native frame rate. (This **corrects** the
earlier note that read the `/6` prescaler against 18.2 Hz and wrongly concluded
"~3 Hz"; the PIT reprogramming had been missed.)

The viewer delivers `timer_irqs_per_frame` (6) INT 08h per presented frame and
paces frames at `present_hz`, so IRQ0 Hz = `6 × present_hz` and logic Hz =
`present_hz`. The base default `present_hz=60` therefore ran IRQ0 at 360 Hz and
logic at 60 Hz — **everything (music tempo, physics) at 2× speed**. Fixed:
`SkyroadsFrontend.default_present_hz = 30` → 180 Hz IRQ0 / 30 Hz logic, one game
tick per presented frame. Wall-clock pacing only; headless demo replay ignores
`present_hz`, so determinism is unchanged. (User-reported: music was too fast;
~30 Hz matches DosBox.)

### 2. Sound Blaster digital PCM sound effects (were silent)

SkyRoads plays music through the AdLib/OPL FM chip but its **sound effects are
digitized 8-bit-unsigned PCM** streamed to the SB via **single-cycle DMA (DSP
`0x14`)**, fire-and-forget (it never waits on the block-complete IRQ — which is
why the detection-only stub worked). Sample banks on disk:
- `SFX.SND` (25807 B): 12-byte header = 6× `u16` offsets `[12, 3996, 9150,
  17235, 18036, 25807=EOF]` → 5 effects, then raw unsigned-8 PCM.
- `INTRO.SND` (32100 B): headerless raw unsigned-8 PCM (the intro sample).

Per effect the driver issues `D0` (pause) → `40` (time constant = rate) → `14`
(single-cycle DMA-out, length). Rates seen: intro `tc=90` → 6024 Hz; the
recurring gameplay effect `tc=131` → 8000 Hz, 5153 B; also `tc=236` → 50000 Hz.
The full E2E demo fires **57 effects** (306,264 B PCM).

These were dropped because the emulated SB ran in `detection_only` mode (no PCM
streaming). Now captured as a **pure observer**:
- `skyroads.runtime.create_game_runtime(..., capture_sb_pcm=True)` attaches a
  full SB that copies each DMA block into `sb.pcm_out` and logs its rate — but
  **no block-complete IRQ is delivered**, so the CPU timeline is untouched.
- `skyroads/audio.py::SkyroadsAudioSink` (extends the stock AdLib/speaker sink)
  drains those blocks, linear-resamples each from its DSP rate to the mixer
  rate, and sums them into the output alongside OPL + PC speaker.
- Wired in `SkyroadsFrontend`: capture is enabled only for the viewer with
  `--audio adlib` (off for headless/demo/test, so those keep the exact
  detection-only path and accumulate no PCM).

**Determinism proof (the observer guarantee):** replaying the full 1906-frame
demo in detection-only vs capture mode is **byte-identical** — same 61,050,603
instructions, same registers, same SHA-256 of the whole 1 MB memory image —
while capture pulls all 57 effects (306,264 B). Locked in by
`tests/test_sb_pcm_audio.py` (resample/mix unit tests + a byte-exact
capture-vs-detect boot integration test). Audible artifact:
`artifacts/skyroads_sfx_demo.wav`.

### 3. AdLib works under PyPy

The Nuked-OPL3 cffi extension was only built for CPython (cp311); PyPy reported
"Nuked-OPL3 not built". Built the PyPy-ABI extension
(`pynuked_opl3/_opl3_cffi.pypy311-pp73-win_amd64.pyd`, a gitignored build
artifact) via `pynuked_opl3._ffi_build` under PyPy + MSVC. It loads
(`is_available() → True`) and renders **byte-identically to CPython** (same
SHA-256 on a test note). Build gotchas worked around: cffi's cross-drive
`os.path.relpath` (put the build `TMP` on the same drive as the sources) and a
trailing-space in the `TMP` env var (use cmd's quoted `set "TMP=…"`). The
vendored `_ffi_build.py` cross-drive bug is left untouched (nested submodule).

## 2026-07-10 — full-level perf drop root-caused: the 34AE tile renderer (lifted)

A full start→finish level demo (`artifacts/demos/demo_skyroads_20260710_145303`,
1,906 frames, 54.5M steps — the user flagged in-level performance drops) profiled
to a new dominant un-hooked cost: **page `3500` = 29.4% of interpreted work**
(the hot loop at `356B`), not prominent in earlier demos. It is the
`[0E38]`-dispatched tile renderer `1010:34AE` (reached via the `34A7` wrapper) —
a different tile-render variant this world uses heavily.

Recovered with the **automatic lifter** (`dos_re.lift`): `34AE` is 100% liftable
(130 insts, 28 blocks, one indirect call run through the VM); `liftverify`
proved it `ORACLE_PASSING` — 401 calls, 26/28 blocks byte-exact, and a further
400 full-level-demo calls under the strict differential verifier, zero
divergence. Installed as `skyroads/lifted/lifted_1010_34ae.py` +
`registry.replace(0x34AE)`.

Honesty notes:
- **The raw lift gives ~no CPython speedup** (full-demo wall ~20.4s with vs
  without) — a literal per-instruction lift runs at roughly interpreter speed.
  The real perf win needs the hot `356B` loop **refactored into efficient
  Python** (as `38BF`/`325B` were), and/or PyPy JIT-compiling the lift. The
  install is correct scaffolding; the refactor into a clean
  `skyroads/recovered/` island (metrics-honesty rule) is the to-do.
- A cautionary self-note: a first verification of the lift falsely "diverged"
  — the ad-hoc harness had installed the `1732` hook function at address
  `0x34AE` (a sed slip). Always verify the ACTUAL lifted function; `liftverify`
  (purpose-built) is the trustworthy path.

## 2026-07-10 — first AUTO-LIFTED island: the master timer ISR (1010:3B17)

The game's INT 08h handler (master clock + music tempo) is the port's first
island recovered with the **automatic lifter** (`dos_re.lift`) rather than by
hand. Workflow, end to end:

1. `dos_re/tools/liftverify.py --entry 1010:3B17 --timer-irqs 6` emitted a
   literal, per-instruction Python hook and verified it in situ — **199 calls,
   byte-exact** against the interpreted original (this also drove the new
   `--timer-irqs` option: a plain forward run never fires the ISR).
2. The mechanical lift was refactored into the port's pure-rule + thin-adapter
   shape: `skyroads/recovered/timer_isr.py::advance_music_timer` (VM-free, the
   prescaler/song/PIT-divisor decision, `@oracle_link ASM_MATCHED`) plus
   `skyroads/hooks.py::master_timer_isr` (the pusha/popa/iret frame, the
   sound-engine call, the PIT/PIC port writes).
3. A unit oracle (`tests/test_master_timer_isr.py`) drives **every prescaler
   value 0..9 x song-continue/end** and diffs full machine state against the
   interpreted `1010:3B17` — full basic-block coverage, incl. the wrap →
   reset-to-9 → chain-to-BIOS path whose `dec [3192]` flags survive to the far
   exit (the IRET path pops them away). 22/22 byte-exact.

Notable: the lift was correct on the flag detail above out of the box — the
kind of thing hand translation gets wrong. Suite green (154). This is the M3
proof of the lifter thesis on a real game: ASM → auto-lift → verify → refactor
to clean recovered source, same oracle throughout.

## 2026-07-10 — whole-game E2E validation of the recovered island

Replayed a full cold-start end-to-end demo (`artifacts/demos/
demo_e2e_20260710_132930`: intro-skip → main menu → level select → play a level
→ die → exit → play another level → exit to menu → quit) through the
fully-hooked runtime. **All 1,719 frames ran to the game's own `exit(0)`**
(HaltExecution at `1010:630F`, the `mov ah,4Ch; int 21h` terminate — the demo's
intended final action), with every recovered hook firing across the whole
lifecycle: `palette_fade` 408K, `fade_gate` 858K, `road_column_strip` 26.9K,
`road_object_visible` 17.3K, RLE sprites 32.5K, `tile_rasterizer` 615, the
three long-arith helpers, `lzs` 266 (multiple level/asset loads), etc. No hook
raised, no divergence, no hang — a strong whole-game integration pass across
menu, two gameplay levels, death, and exit.

Byte-exact spot-check on the E2E's (different) level data: `road_object_visible`
(`1732`) re-verified against the ASM oracle for 439 calls, zero divergence —
the recovery holds on levels beyond the ones it was developed against.

Also confirmed via the busier `world7` gameplay + level-load demos: the
projection LUT (`ds:0x162C`) is static across 956 active-gameplay frames and is
**loaded as data from the level file** (not computed); the "3D" is table-driven
throughout. See [`rendering_architecture.md`](rendering_architecture.md) and
[`level_format.md`](level_format.md).

## 2026-07-09 (cont'd) — in-game profiling + the renderer-island plan

**Why gameplay is ~2-3 FPS (measured, not guessed).** On this machine the
interpreter sustains ~626K 8086-steps/second. Frame decode (VGA→RGB, 0.4ms),
pygame present (0.5ms) and AdLib OPL3 pump (0.3ms) are all negligible — 98% of
a frame is interpreting instructions. The catch: a *viewer frame* (30,000
steps) is not a *visual frame*. Measuring steps between actual screen updates
gives ~57,000 steps/visual-frame average (heavy frames >130,000). 626K ÷ 57K ≈
low-single-digit FPS. So the bottleneck is purely "8086 instructions executed
per rendered frame", and the only lever is removing them (hooks) — presentation
is already free. A 10× speedup to smooth play cannot come from per-loop hooks
shaving percentages; it needs the whole render path lifted out of the
interpreter.

**Hooks installed this session (all differential-verified against the ASM
oracle, zero divergence):** `palette_upload` (6168), `sprite_blit` (3A22),
`occluded_column_blit` (3283), `ulong_div` (5D8C), `ulong_mul` (5D4C),
`rle_sprite_forward` (3153), `rle_sprite_backward` (3190), plus the behavioral
`fade_loop_tick_gate` (4344/434A). See `symbol_ledger.md`.

**Render call tree (mapped via caller-chain tracing on the in-game demo
`demo_skyroads_20260709_225824`).** Shallow → deep:

```
main loop (~22xx)
  render dispatch (~0C26/0C32/0C98/0CA2)
    per-object / road-segment render (~1732/1747/175C/17CD/1821/1846)   [NOT YET RECOVERED]
      fixed-point perspective transform  04C0                          [NOT YET RECOVERED - keystone]
        ulong_mul 5D4C / ulong_div 5D8C                                 [HOOKED]
      leaf rasterizers 3153 / 3190 / 3283 / 3A22                        [HOOKED]
```

**The renderer-island plan.** Goal: a clean, VM-agnostic recovered renderer
(a `skyroads/recovered/renderer.py` module) that, given game state, produces
the exact framebuffer the ASM does — wired in behind ONE thin hook at the
render root, verified whole against the oracle. Bottom-up, the leaf + math
layers are DONE. Remaining, in dependency order:
1. **`04C0` fixed-point perspective transform** — the keystone; every render
   path calls it, and it now depends only on the already-hooked long-arithmetic.
   DONE (2026-07-09): recovered as `skyroads/recovered/renderer.py::
   perspective_row_offset`, wired via a thin `perspective_transform` hook,
   VERIFIED byte-exact over all 34,786 in-game calls. First recovered-code
   layer of the island. (The recovery corrected a decode error — the third
   stage is a ×14 multiply via ulong_mul, not a divide.)
2. **`17xx` per-object/road-segment render** — the layer that projects a
   road segment / object via `04C0` and dispatches to the rasterizers. The
   root is `1732` (`enter 0xA`), which calls `04C0` four times AND the leaf
   `1631` twice. `1631` (a self-contained per-segment visibility/clip test,
   NO calls) is DONE (2026-07-10): recovered as `renderer.py::
   road_segment_clip`, ASM_MATCHED over all 9,238 in-game calls (selectors
   0x100/0x200/default exercised; 0x300/0x400/0x500 decoded but not hit in
   this demo). Per the island strategy, leaves are recovered as clean
   functions WITHOUT their own hook; the single hook goes at the island root
   (`1732`), where the whole subtree — `04C0` + `1631` + the clamp/dispatch
   glue — collapses into one verified Python call. The `1732` ROOT itself is
   now DONE as a clean function (2026-07-10): `renderer.py::
   road_object_visible`, ASM_MATCHED over all 12,152 in-game calls (both
   return values exercised). It projects the segment's near/far edges via
   `04C0`, runs the nibble + screen-band cull, and on survivors does a
   mirrored two-sided `1631` clip — pure, no memory writes, returns 0/1.
   DONE (2026-07-10): the `1732` hook is wired + VERIFIED byte-exact over all
   12,152 in-game calls (collapsing its four nested `04C0` calls plus the cull
   glue into one Python call). Exit BX/CX/DX are reproduced by threading the
   nested `04C0`/`1631` calls' exit registers through the taken path. With
   this, layers 1+2 of the island are fully hooked; `04C0` dropped out of the
   top hooks (most of its 34K calls came from `1732`). Layer 3 (the `0Cxx`
   render dispatch that would become the island's single top-level boundary)
   is the remaining upward step.
   Separately, the biggest single in-game render cost, the `38BF` road-column
   strip compositor, is now hooked + VERIFIED (14,896 calls, ~1.4x demo
   wall-clock); the RLE leaf rasterizers (`3153`/`3190`) were already hooked.
   Profiling note: excluding the `22F8` pacing spin (28% of interpreted
   steps, an idle timer-tick wait — the game finishes a tick's work then
   spins for the rest of the fixed step budget), the real render work is
   ~24% in the `17xx`/`18xx` glue and ~26% in the `35xx`/`39xx` stride-3
   display-list rasterizer scans (the biggest single un-hooked leaves).
3. **`0Cxx` render dispatch** — the per-frame "draw the whole scene" entry;
   this becomes the island's single hook boundary once 1–2 are recovered.
Each layer is recovered + verified before the next, so the island grows upward
with the differential verifier guarding every step — the same methodology used
for LZS and the leaves.

## 2026-07-09 (cont'd) — the menu "halt" was a VM bug (phantom Esc); + AdLib audio

**Root cause of the spurious main-menu exit: a framework input bug, not a game
decision.** The game reads menu keys with `INT 21h AH=07h` (blocking `getch`,
recovered at `1010:5FEB`) and treats Esc as "quit". `DOSMachine` defaulted
`console_input_fallback` to `0x011B` (**Esc**) so a bare headless `cpu.run()`
wouldn't hang on a blocking read — but nothing in the player NEEDS that: every
driver path routes blocking reads through `_step_frame`, which already catches
`ConsoleInputWouldBlock` and reports "waiting for DOS key" without hanging. So
the Esc synthesis was pure downside: with no real key queued, `getch` returned
Esc, the game read "quit", and called `exit(0)` — surfacing as "program
halted" at the menu a few seconds in, with no keypress. Traced by walking the
exit path from the owner's pre-halt snapshot: `58C3` AdLib-register-clear loop
→ `005A` (`call 5BC0` SB DMA/DSP cleanup) → `005D` (`push 0`) → `6001`
(`pop/pop; jmp 630B`) → `630B` (`mov ah,4Ch; int 21h`) — the textbook C-runtime
`exit(0)` epilogue (silence AdLib, shut down SB, restore text mode, exit),
reached because `5FEB`'s `getch` returned `0x1B`.

Fix (`dos_re/dos_re/player.py::_use_real_console_input`): the player clears
`console_input_fallback` to `None` for all modes right after runtime
creation, so blocking console reads wait for a real key (interactive) or the
demo/queue (headless/replay). Verified: from the main menu the game now blocks
at `5FEB` waiting for input instead of exiting; delivering Enter advances it
through the fade into the road-select / level-intro screens it never reached
before. Both suites green (154 dos_re + 123 skyroads_port).

**AdLib audio (`--audio adlib` was silent):** the OPL register-write plumbing
(`0x388`/`0x389` → `_notify_adlib` → `AdlibSpeakerSink._on_adlib` →
`OPL3.write`) was fine — the `pynuked_opl3` C extension simply wasn't built,
so `AdlibSpeakerSink._opl` was `None` and rendered nothing. Built it once
(`python -m pynuked_opl3._ffi_build`, needs MSVC Build Tools, which are
present); it lands in the shared `ancient_port/dos_re/pynuked_opl3/` copy
(that's where the editable install resolves `pynuked_opl3` for every sibling
port). Confirmed the game's own 117 boot/menu OPL writes now synthesize
audible PCM (peak 2721 / rms 1228, was total silence). Re-run `play.py
--audio adlib` to hear it.

## 2026-07-09 (cont'd) — LZS decode-loop hook finished: installed and verified

Finished the LZS decoder performance island. The codec fix (`1<<
WIDTH_DIST_LONG` short-distance base, see `blockers.md`) only surfaced fully
once a *third* file (`INTRO.LZS`, `WIDTH_DIST_LONG=9`) was tested — `TREKDAT
.LZS` and `MUZAX.LZS` both use `WIDTH_DIST_LONG=10`, so two files' worth of
testing had coincidentally never distinguished "fixed 0x400 constant" from
"computed per file." Lesson: a fix that passes on N files sharing a parameter
value is not verified against that parameter — the discriminating test needed
a file where it actually varies.

The hook itself (`skyroads/hooks.py::lzs_decode_loop_hook`) needed six
additional real bugs fixed in its own state bookkeeping (see `blockers.md`
for the full list) before `dos_re.verification`'s strict full-memory
differential verifier came back clean — 15 hook calls, zero divergence,
across `TREKDAT.LZS` (all 9 records), `MUZAX.LZS`, and `INTRO.LZS`. Now
installed by default (`@registry.replace` active).

Measured impact: pure-ASM interpretation needs 144,515 to 1,176,774
instructions per LZS block (11+ blocks during boot) — in a fixed
3,000,000-instruction budget, pure-ASM is still stuck decoding the *first*
file (`CS:IP 1010:6508`) while the hook gets completely through *all*
boot-time LZS decompression and into subsequent loading logic (`CS:IP
1010:6197`). Full test suite: 123 passed.

## 2026-07-09 (cont'd) — menu "halt" investigated: not a bug, an idle timeout

A user-reported halt (`gap_snapshot_skyroads_20260709_163042`, CS:IP
`1010:630F`, `AX=4C00` right after `INT 21h AH=4Ch`) turned out to be the
game's own **normal** exit-to-DOS sequence (palette fade-out, then a Sound
Blaster DMA-halt/DSP-command cleanup at `1010:5BC0`-`5BDA`, then a clean
`exit(0)`) — not a crash. Reproduced deterministically from the "right before
halt" snapshot (`snapshot_skyroads_20260709_160101`, confirmed via
`tools/render_frame.py` to be sitting at the main menu) two ways: pressing
Enter alone, or providing **no input at all**, both lead to the exact same
exit within ~73,000 steps. This means the main menu has an idle timeout
(likely tied to demo/attract-mode playback finishing) that exits to DOS if no
navigation key registers quickly enough after the menu appears.

Pressing an arrow key (e.g. Down) before Enter avoids it entirely — traced
200 frames with `Down` then `Enter` with zero halts, ending on the level
-select screen (`Red Heat` / `Asteroid Belt` / ... each with `Road 1/2/3`),
confirming asset loading and menu-to-gameplay progression both work cleanly.
**Practical takeaway for interactive play: press a navigation key (arrow)
promptly after the menu appears, before Enter/Start.** No framework or hook
fix needed here.

## 2026-07-09 (cont'd) — real halts fixed: memory allocator + sound detection

Two real bugs found via user-reported halts (both now fixed and confirmed
clean over 90M+ instructions each):

**Memory allocator never reclaimed freed blocks.** `dos_re`'s AH=49h (free
memory) handler dropped the tracking record but never made that address
range reusable — a bump-pointer allocator with no reuse. SKYROADS cycles
scratch buffers heavily (269 allocs vs 255 frees in one session), so this
silently exhausted the ~576KB conventional-memory budget well before a real
DOS machine would, producing a genuine "Not enough memory" exit mid
`intro.lzs` decode. Fixed in the canonical `dos_re` repo
(`D:\Games\DOS\dos_recosystem\dos_re`, then synced into this submodule
checkout): `DOSMachine._free_gaps()`/`_find_free_gap()`/`_largest_free_gap()`
implement deterministic first-fit allocation over the current live
allocations, so a freed block's address range becomes reusable immediately
— matching how a real DOS MCB chain behaves by default. Confirmed reclaiming
a real 188KB gap that was previously wasted; both `dos_re`'s own 153 tests
and this repo's 121 pass.

**Sound Blaster never attached, so detection legitimately found nothing.**
SKYROADS probes SB ports 0x220-0x270 (standard DSP reset handshake) at boot
and, once one responds, assumes its onboard OPL is present too and starts
loading FM instrument patches — there's no separate AdLib-only probe. With
no SB attached in our runtime, all six candidates fail and the game
hard-exits (`mov ah,4Ch`) with no printed message, sometimes well past the
intro (reached the menu before hitting it in the reported case). Traced the
*entire* SB+OPL sequence live (DSP reset -> `0xAA` ack -> `Speaker On` -> OPL
instrument register writes) to confirm this reads as completely normal,
successful hardware init once a Sound Blaster is actually present — not a
detection-handshake mismatch as first suspected. Fixed by wiring
`dos_re.runtime.enable_sound_blaster(detection_only=True)` into
`skyroads/runtime.py`'s `create_game_runtime`/`load_game_snapshot` (on by
default, `enable_sound=False` to reproduce the original exit for study).
**Must run on a fresh boot** — attaching it to an already-halted snapshot
does nothing, since "no sound" is already recorded in the game's own memory
by the time detection ran. Confirmed clean over 90M instructions from a
fresh boot with no further halt.

**Halt diagnostics** (also `dos_re.player`, canonical + synced): any
`HaltExecution`/`UnsupportedInstruction`/exception now prints DOS console
stdout (many DOS programs print a plain-text reason before exiting — this is
exactly what revealed "Not enough memory"), a compact memory-allocator
summary, and open file handles, and always auto-saves a resumable gap
snapshot — previously only generic exceptions got a snapshot, and the
message was just "program halted" with zero context.

## 2026-07-09 (cont'd) — the real bottleneck: a 6:1 software timer prescaler

After installing the palette-fade hook, re-profiling the same snapshot
surfaced a much bigger, structurally different cost: a generic "wait until
ds:[1600] (elapsed ticks) reaches a threshold OR a key is pressed" poll loop
(`1010:4465`-`417D`, called between palette-fade passes and presumably
elsewhere). Live-tracing SKYROADS' own INT 08h ISR (`1010:3B17`) found the
real mechanism: a software prescaler at `ds:[3192]` that only increments
`ds:[1600]` once every **6** real timer interrupts — an intentional ~3 Hz
game-tick rate divided down from the 18.2 Hz BIOS timer. This is *correct,
original pacing*, not a bug — a real DOS machine would also only see this
counter advance ~3 times/second. The bug is in how a driver delivers INT 08h:
`scripts/play.py` (and every benchmark/probe script in this session) had
been delivering exactly 1 IRQ before a large step budget, so 5 out of every
6 driver frames advanced this counter not at all while still burning a full
interpreted step budget spinning uselessly in the wait loop.

**Fix (driver-level, no CPU hook, no verification risk):** `scripts/play.py`'s
`SkyroadsFrontend` now delivers 6 IRQs per frame (matching the real
prescaler exactly) with a smaller per-frame step budget (200,000 -> 30,000,
empirically tuned — see `symbol_ledger.md`) so those bursts land far more
often per wall-clock second. Measured head-to-head via `scripts/play.py`
itself, both from the same intro-fade snapshot, 100 frames each:

| | steps | wall time | ending state |
|---|---|---|---|
| old (1 IRQ / 200K steps) | 20,004,362 | 95.3s | still in the same fade phase it started in |
| new (6 IRQ / 30K steps) | 3,018,728 | 12.7s | progressed into an entirely new code region |

**~7.5x faster wall-clock, using 6.6x fewer total instructions** — the win
comes from eliminating wasted busy-wait cycles, not from running faster in
any crude sense. This is very likely the dominant cause of the "1 frame
every 3 seconds" symptom originally reported. Not yet re-validated against
real gameplay (still unreached) — the tuning (30,000 steps/frame) was
optimized against this specific intro-fade snapshot and may need revisiting
once gameplay code is reachable, since a non-wait-bound game-logic frame
might need a larger budget to complete meaningful work.

## 2026-07-09 — first verified + installed hook: palette-fade inner loop (6.7x)

The palette-fade inner loop (`1010:43A9`-`442D`, see `symbol_ledger.md`) is
now hooked, verified (34,439 calls, zero divergence), and installed. Fixed
three real bugs along the way (missing register writeback, `idiv` remainder,
`LES` also loading ES) — each caught immediately by the differential
verifier with a precise register/segment diff, never guessed. Measured
**6.7x wall-clock speedup** processing the same amount of fade animation.

**Also found and fixed a real bug in `skyroads/runtime.py`:**
`load_game_snapshot` called `dos_re.snapshot.load_snapshot` directly without
ever calling `registry.install(cpu)` on the restored CPU — so a hook
installed via `@registry.replace` (like this one) silently never ran on any
snapshot-resumed session, only on a fresh `create_game_runtime` boot. This
would have made every future hook look like a no-op whenever tested against
a snapshot (which is most of the time, since fresh cold boots are slow).
`scripts/play.py`'s `--snapshot` resume path uses `load_game_snapshot`, so it
was silently affected too — now fixed, no caller changes needed.

**Process note on verification cost:** the strict differential verifier
(`HookVerifierConfig.strict()`) clones the full 1MB memory image, re-runs the
original ASM a second time, and diffs the whole memory image — *per hook
call*, by design (its own docstring: "for small targeted investigations, not
fast gameplay"). An initial 30M-instruction verification budget was wildly
oversized for what's needed to build confidence; ~250K instructions (34K+
hook calls, ~45 full passes with many pass-boundary transitions) is plenty
and runs in well under two minutes. Scope future verification runs
accordingly rather than defaulting to a huge budget.

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

## 2026-07-09 (cont'd) — LZS decoder root-caused and fixed; decode-loop hook drafted

The startup-speed investigation ("cold boot takes quite a long time before I
can see anything") led back to the LZS decoder's long-standing residual
divergence (logged in `blockers.md` since 2026-07-08 at "output-relative
position 2938"). Two rounds of bit-level tracing this session initially
produced *contradictory* results against the earlier symbol-level trace —
root cause: every earlier capture attempt (including this session's first
two) aligned to the target record via a blind instruction-count guess or an
"already patched" poll, both of which are fragile since many unrelated
decode calls across many files share the exact same width-patch address
(`1010:671F`) and even the same values. Fixed by anchoring instead to the
actual `INT 21h AH=3Dh` open of `TREKDAT.LZS` (watching `dos.files` for the
real DOS file-open event, not a memory-write heuristic) — this reproduced the
earlier 8964/18072 match figure exactly, confirming that number was real,
just mis-diagnosed as "divergence at 2938" when the true first divergence is
at byte 1111.

With reliable alignment, a live disassembly of the divergent symbol
(`1010:6750`: `05 00 04` = `ADD AX,0x0400`) found the actual bug: the
short-distance match formula is `get_bits(WIDTH_DIST_SHORT) + 0x400 + 2`, not
the previously-assumed `+3` (a guess-by-analogy with the long-distance
branch that was never actually verified). Full-record verification: 18072/
18072 bytes of `TREKDAT.LZS` record 0, and 3000/3000 bytes of record 1, both
100.00% exact against `skyroads/codecs/lzs.py`. Status raised OBSERVED ->
VERIFIED. Regression tests added (`tests/test_lzs_codec.py`).

Drafted the decode-loop hook (`skyroads/hooks.py::lzs_decode_loop_hook`,
`1010:6712`) to decode an entire block in one Python call instead of one
interpreted iteration per symbol (the actual startup-speed payoff). Required
reverse-engineering the staging-buffer refill mechanism in full (`1010:6350`,
`ds:[41AC]`=file handle, `ds:[41B2]/[41B4]/[41B6]`=buffer start/end/cursor)
to correctly simulate DOS file-position advancement and buffer-cursor state
across chunk boundaries, plus per-symbol scratch-register reconstruction
(AX/CX/DX/SI/FLAGS) to satisfy the strict full-memory differential verifier —
all of which now verify byte-exact except one register, BX, off by a fixed
delta on the one call tested so far (likely a dead scratch value, not yet
proven). **Not installed** pending that last gap — see `blockers.md`.

## Next up
- Find the frame boundary (present/blit routine) so the frame verifier can be
  stood up (`docs/porting_new_game.md` step 3-4).
- Build the input-wait registry for the title/menu polls (step 5) before
  recording any demo intended as a regression asset.
