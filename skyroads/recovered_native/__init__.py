"""Manually recovered SkyRoads code that owns the DOS memory image — KEEP THIS.

It was called ``skyroads.native`` and described as "pure orchestration only",
and both were wrong in the direction that nearly cost it: when the hand-written
driver (``scripts/play_native.py``) was discarded on 2026-07-17, this package
read like the driver's guts and came within one command of going with it. It is
not orchestration. Most of it is RECOVERED ROUTINES, decoded from the real game
and verified against the oracle, each named for the address it came from:

    hud.py            1010:12F8 (per-frame gauge updater) + 1010:0F8C (widget
                      draw) -- the grav-o-meter is byte-exact vs the VM
    render_params.py  1010:0C98, the per-frame render orchestrator, as a pure
                      function
    tile_dispatch.py  1010:2D1F, the road-tile dispatch loop, transcribed
    render_frame.py   1010:34AE, the road render
    sfx.py            1010:03C2, the SFX trigger + the SFX.SND sample bank
    menus.py          cold-boot screen/menu transitions, VERIFIED against the
                      oracle demo
    level_select.py   the level-select grid, semantics VERIFIED against oracle
    exe_image.py      the game's own packer stub, reimplemented -- and
                      scripts/build_boot_image.py depends on it reproducing the
                      unpack byte-exactly
    anim.py           ANIM.LZS, the intro's dirty-rectangle animation
    boot.py           the cold-boot DGROUP builder
    level_load.py     VM-free level loading
    world_load.py     per-level world graphics + MUZAX song loading

WHY IT IS SEPARATE FROM ``skyroads.recovered``
    ``skyroads.recovered`` is pure logic over VALUES and imports nothing (its
    own docstring: "NEVER imports dos_re/cpu/memory/hooks/offsets") -- dos_re's
    Stage 4 shape. The modules HERE are equally CPU-free, but they address the
    historical DOS memory image by raw offset, through NativeGameState /
    NativeGameImage. That is dos_re's Stage 2 (CPULESS LIFTED) shape exactly:
    "may still address the historical DOS memory image by raw offset".

    So the two packages are two recovery TIERS, not code and scaffolding.

WHY IT MATTERS FOR M3
    Everything here is hand-written CPUless code with an oracle proof. The M3
    promotion (``dos_re/tools/cpuless_promote.py``) generates CPUless code for
    the same routines from the recovery IR. These are therefore the SEMANTIC
    TARGET for that output, and an independent cross-check: two implementations
    of one routine, one hand-recovered and verified, one machine-generated.
    Deleting them would throw away the only reference the generated code has.

    Generated CPUless output must land somewhere else (``--recovered-dir`` is a
    parameter; dos_re's own example points it at ``mygame/recovered``, which
    here would land on top of hand-written work).

The genuine composition/glue in here -- frame.py, loop.py, classify.py,
collision.py, gaps.py, state.py, image.py, pcx.py -- existed to serve the
discarded driver and may have no remaining caller. That is a separate question
from the routines above; check before assuming either way.

Layer rule (unchanged): this package imports skyroads.recovered + skyroads.bridge
and composes them against a NativeGameState, never dos_re/cpu/mem. Audited by
``python tools/audit_layers.py skyroads/recovered skyroads/recovered_native
skyroads/bridge`` (pitfall #17; see tests/test_layer_audit.py). Where a per-frame
step needs a routine that is not yet recovered, it raises a typed gap
(skyroads.recovered_native.gaps) instead of guessing.
"""
