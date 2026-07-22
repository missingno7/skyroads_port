# Native gameplay presentation

SkyRoads has one runtime and one authoritative gameplay clock. The generated
frontend and selector enter the faithful `skyroads.gameplay` execution island;
the island owns the recovered gameplay body over shared DOS memory and returns
only through its declared generated continuations. Optional modern presentation
is a read-only consumer of that same island. It is not another player or another
game simulation.

## Run configuration

```text
# faithful game with the original framebuffer and device-backed audio
python scripts/play.py --composition faithful-product

# faithful GPU presentation at 30 semantic ticks, presented at 90 Hz
python scripts/play.py --composition faithful-product --renderer native-3d --widescreen --tweening --simulation-hz 30 --present-hz 90 --audio native-faithful

# the same faithful audio with recovered-position stereo for ship-local SFX
python scripts/play.py --composition faithful-product --renderer native-3d --widescreen --tweening --audio native-stereo

# direct-launch every available course through the normal loader/island path
python scripts/play.py --composition faithful-product --level 29
```

`--renderer native-3d`, `--widescreen`, `--tweening`, `--audio
native-faithful`, and `--audio native-stereo` are presentation features. They cannot write authoritative
gameplay state or be treated as behavioral modifications. The original
framebuffer and AdLib/SB device output remain available with `--renderer
original --audio adlib` for diagnosis.

SkyRoads declares a 30 Hz authoritative clock independently of the host display
rate. Its original PIT setup produces 180 IRQ0 deliveries per second and the ISR
software-divides those by six. The default viewer therefore presents at 60 Hz
while advancing gameplay at 30 Hz; changing only `--present-hz` never changes
physics, music, replay positions, or the six-IRQ semantic tick.

The default GPU view owns the complete presented gameplay frame. It draws the
recovered WORLD background, road scene, live CARS ship frame, and dashboard;
it never places the original 320x200 framebuffer over the enhanced scene.
That ownership begins during the loaded level's start fade, spans the
generated road-departure and end-of-level continuations, and ends only at the
black handoff back to the level selector. It is based on the selected level's
exact live road digest and recovered transition identities, not framebuffer
recognition or whether the gameplay execution region happens to be active.
The same recovered precomposed `2B3D` call signature owns both initial entry
and crash restart. During a restart the original intentionally retains the
crashed live simulation fields while it draws the reset frame at full black;
the reset fields are published only after fade-in. Native presentation follows
the call signature, so the reset frame is already under the entire fade and
does not jump when those live fields are finally published.
Presentation ownership follows recovered call identities rather than the
persistent `last_region_exit` diagnostic. Routine `4331` and its frame head
`434A` are shared by every screen; they do not identify the owner. The caller
continuations on the preserved `SS:BP` chain do: gameplay start/restart and
exit fades return through `2C5B`/`2CBE`, while selector fade-in/fade-out return
through `5295`/`5377`. Road-departure rendering (`0EF8`) and its wait (`4468`)
therefore remain in the continuous native interval, but ownership is released
before the first selector fade point and remains original at selector input
`5FED`. Because the identity is continuation state, restoring a cached replay
boundary in the middle of either fade reaches the same decision without host
history or framebuffer heuristics.
In `final`, one immutable whole-level lane/row/elevation mesh remains resident
on the GPU and passes through the recovered continuous lens at the real window
resolution. Track interpolation changes one camera uniform, never object
topology or dimensions; crossing a road row does not rebuild or upload a new
moving geometry window.
Original pixel art remains pixel art by design.
The cockpit is independently reconstructed from `DASHBRD.LZS`'s zero-keyed
mask plus the recovered speed/oxygen/fuel DAT stencils, progress bar, and
grav-o-meter. The upper bezel's holes are therefore truly transparent over the
native road rather than a low-resolution oracle-frame crop. The rocket shadow
uses the original five-band `33FD` road-darkening stencil and recovered screen
placement; it is not an inferred blob beneath the sprite. Its exact recovered
alpha remains authoritative, while its ship-row depth participates in the
same native depth field as the road and tunnel. Near tunnel faces therefore
occlude both the ship and shadow, matching the original painter order.

Immutable level files, parsed world assets, decoded presentation assets, and
the whole-level GPU mesh are cached by stable level/content identity. They are
prewarmed while the generated selector owns presentation (and immediately
after replay continuation restore), rather than decoded or uploaded on the
first audible gameplay frame.

The transparency boundary follows the original compositor exactly. The road
renderer can overwrite rows 129..137, so zero-keyed `DASHBRD` pixels expose the
road only in those nine rows (their nonzero coverage grows from 176 to 316
pixels per row). Rows 138..199 are outside the road band: their zero-keyed
asset pixels reveal the cockpit's black backing, not gameplay, and are emitted
as opaque black by the native packet. Gauge and LCD stencils then update that
solid lower layer.
`--render-debug exact-projection` retains the literal RLE-step projection and
`--render-debug original` retains the complete oracle composition. The
presenter applies the DOS 6:5 pixel aspect exactly once when fitting any view
to a square-pixel display.

Widescreen policy is downstream of that faithful 4:3 composition. The WORLD
background extends with alternating normal/mirrored horizontal samples whose
reflection seams coincide with the original viewport edges. The temporary HUD
extension keeps the original centred instrument panel unchanged and clamps its
outermost columns across the added margins. Neither policy changes scene,
collision, camera, HUD values, or original-resolution verification.

SkyRoads renders its original road through precomputed `TREKDAT` strips and
distance-selected art, not through an embedded floating-point camera. Offline
recovery associates all eight phase outputs with the known seven-lane grid and
both recovered block heights. One fitted pseudo-perspective scale explains
lane width, ground position, and vertical height. The final GPU view projects
stable world primitives through that lens; it does not use phase-specific
silhouettes as geometry.
Tunnel geometry follows the structural selector rather than a shared visual
shortcut: exposed tubes are thick open-bottom shells, while tunnel+block cells
are single solids with an arched passage cut through them. Exterior, rim and
interior surfaces retain the original palette-role separation and participate
in the same world depth field as the ship. Extra far source rows are available
before they cross the vanishing plane, eliminating visible block admission
without an opacity fade.
The high nibble of each road word remains the exterior top-material selector
for raised solids, including carved passages. Replay `155438` covers level 18
words `0x0520`: their shell uses the recovered grey side/rim roles while their
top deliberately selects palette 2 (green). This is a material seam on one
stable solid, not a separate block shape.
`exact-projection`, `terrain`, `wireframe`, `source-ids`, and `collision`
expose that recovered geometry for diagnosis. See
[rendering recovery](rendering_recovery.md).

## Fixed simulation, interpolated presentation

The canonical player advances `advance_frame` only at `--simulation-hz` fixed
semantic ticks. The host may present more often at `--present-hz`. Between two
authoritative scenes, it supplies a fraction in `[0,1]` to the presentation
adapter:

```text
previous GameplayScene -> current GameplayScene -> interpolate_scene -> renderer
       authoritative          authoritative          read-only       host RGB
```

Interpolation is never written to `GameView`, DOS memory, input state, replay
events, collision state, or verification projection. A host stall is clamped to
one next simulation tick instead of inventing catch-up simulation from
interpolated values.

## Semantic scene contract

`skyroads.presentation.scene.build_gameplay_scene` contains authoritative
track/cross-road/height state, timing, palette, every decoded seven-word road
record, exact DGROUP source offsets, and a stable identity for every non-empty
road cell. `trace_original_projection` adds derived screen-space evidence for
each original draw call: source identity, role, phase, stream offset, palette,
clipping, order and exact spans. `RecoveredPolygonRenderer` prepares a cached
absolute-coordinate mesh; `ModernGLFramePresenter` owns the common camera,
depth field, viewport mapping and composition. The exact trace is a reference
and calibration authority, not the final mesh.

Scene equivalence is strict about contents: removing a source road object is a
diagnostic failure even though pixel equality with the DOS framebuffer is not a
claim. The gameplay verifier continues to compare the native island's declared
semantic projection and observable effects; full continuation state remains
required at the generated return seam.

## Audio contract

`--audio adlib` is the original emulated-device reference. `--audio
native-faithful` observes exactly the same command stream, forces music through
`dos_re.opl3_fast.OPL3Fast`, and accepts digital audio only when its bytes,
sample rate, and digest identify an original `SFX.SND` effect or `INTRO.SND`.
SkyRoads programs one single-cycle Sound Blaster voice; a new `D0`/`0x14`
transfer interrupts the preceding effect instead of mixing effects together.

The recovered original sources have no positional stereo model. Faithful host
playback is therefore centred dual-mono for both OPL2 and unsigned-8 PCM.
`--audio native-stereo` is a separate non-authoritative enhancement. It pans
only crash, landing/drop, and wall-bump PCM from the exact original ship sprite
centre captured at the recovered `03C2` call; music and non-spatial UI audio
remain centred. Offline OPL-to-note decoding remains a
diagnostic analysis tool and is never a faithful playback authority. Audio
device commands and their replay positions remain part of deterministic
observable-effect verification; host samples never affect gameplay timing.
The gameplay thread only emits compact deterministic audio commands. A bounded
output worker owns `OPL3Fast` and keeps two immutable SDL blocks queued, so
sample generation and continuous device consumption do not wait for simulation
or rendering. Diagnostics expose buffer depth, synthesis and callback timing,
command rate, output gaps, and underruns.

## Corpus and gaps

Every recording now persists a derived `gameplay_coverage` summary: entered
levels, semantic ticks, and named island exits. It is derived evidence only;
the immutable ReplayArtifact input stream remains authoritative and can be
enriched again from the oracle.

```text
python scripts/report_gameplay_corpus.py artifacts/replays
python scripts/report_gameplay_corpus.py artifacts/replays --require-complete
```

The report intentionally lists missing levels and lifecycle exits rather than
promoting a replay because it happened to record. A corpus item becomes trusted
only after its complete selected interval verifies against the original oracle.
The current direct-launch test covers all 31 archive entries. The known Escape
path leaves the island through generated DOS input confirmation and then
re-enters the selected level; it is deliberately not mislabeled as a native
selector transition. Campaign completion and road-departure routing remain
generated-shell seams until an oracle replay records and verifies them.
