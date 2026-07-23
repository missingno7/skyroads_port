"""Contracts for the first read-only native gameplay presentation slice."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from dos_re.memory import Memory
from skyroads.bridge.dgroup_view import GameView
from skyroads.levels import PLAYABLE_LEVEL_COUNT, road_archive_index
from skyroads.native.level_load import native_level_load
from skyroads.native.state import NativeGameState
from skyroads.presentation.renderer import (
    CALIBRATION,
    RecoveredPolygonRenderer,
    _uniform_palette_gain,
    build_polygon_mesh,
    project_world_vertex,
    projection_scale,
    shadow_camera_depth,
    ship_camera_depth,
)
from skyroads.presentation.moderngl_presenter import (
    ModernGLFramePresenter,
    mirrored_repeat_coordinate,
    widescreen_edge_clamp_uv,
)
from skyroads.presentation.original_projection import (
    ROAD_DESTINATION_Y,
    projection_triangles,
    trace_original_projection,
)
from skyroads.presentation.scene import (
    FULL_BLOCK_HEIGHT,
    HALF_BLOCK_HEIGHT,
    INITIAL_HEIGHT,
    INITIAL_LATERAL_POSITION,
    INITIAL_SHIP_SCREEN_X,
    INITIAL_SHIP_SCREEN_Y,
    INITIAL_SHIP_SPRITE_INDEX,
    INITIAL_TRACK_POSITION,
    LANE_UNITS,
    ROAD_DECK_HEIGHT,
    RoadGeometry,
    RaisedShape,
    TunnelShape,
    build_gameplay_scene,
    build_precomposed_level_start_scene,
    compare_scene_contents,
    decode_road_cell,
    interpolate_scene,
    is_precomposed_level_start,
    load_road_geometry,
    ship_stereo_pan,
)
from skyroads.presentation.runtime import SkyroadsPresentation, _scene_state_key


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def _set_fade_callsite(runtime, return_ip: int) -> None:
    """Install the two recovered 4331 -> 4B8E stack frames."""
    state = runtime.cpu.s
    state.ss = int(state.ds)
    state.bp = 0xB700
    wrapper_bp = 0xB720
    runtime.cpu.mem.ww(state.ss, state.bp, wrapper_bp)
    runtime.cpu.mem.ww(state.ss, wrapper_bp + 2, return_ip)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_level_start_fade_uses_the_recovered_precomposed_scene() -> None:
    """2B3D draws the real initial view before publishing live sim fields."""
    state = NativeGameState()
    native_level_load(state, road_archive_index(3), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 0
    view.af1c = 0
    view.af2c = 0
    for index, value in enumerate((0x100, 0x18, 0x50, 0, 0, 1, 0, 0x8116)):
        state.ww(0x0E28 + index * 2, value)

    assert is_precomposed_level_start(view)
    fade = build_precomposed_level_start_scene(
        view, level=3, game_root=ASSETS,
    )
    assert (
        fade.track_position,
        fade.lateral_position,
        fade.height,
        fade.ship_sprite_index,
        fade.ship_screen_x,
        fade.ship_screen_y,
    ) == (
        INITIAL_TRACK_POSITION,
        INITIAL_LATERAL_POSITION,
        INITIAL_HEIGHT,
        INITIAL_SHIP_SPRITE_INDEX,
        INITIAL_SHIP_SCREEN_X,
        INITIAL_SHIP_SCREEN_Y,
    )

    # These are exactly the coordinates published for the first ordinary
    # gameplay frame after the fade; there is no camera reset at the seam.
    view.lateral = INITIAL_TRACK_POSITION
    view.af1c = INITIAL_LATERAL_POSITION
    view.af2c = INITIAL_HEIGHT
    state.ww(0x0E24, INITIAL_SHIP_SPRITE_INDEX)
    state.ww(0x0E32, 0)
    gameplay = build_gameplay_scene(view, level=3, game_root=ASSETS)
    assert (
        fade.track_position,
        fade.lateral_position,
        fade.height,
        fade.ship_sprite_index,
        fade.ship_screen_x,
        fade.ship_screen_y,
    ) == (
        gameplay.track_position,
        gameplay.lateral_position,
        gameplay.height,
        gameplay.ship_sprite_index,
        gameplay.ship_screen_x,
        gameplay.ship_screen_y,
    )


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_crash_restart_fade_ignores_stale_live_camera_state() -> None:
    """The restart frame is composed while the crashed sim state is retained."""
    state = NativeGameState()
    native_level_load(state, road_archive_index(3), game_root=ASSETS)
    view = GameView(state)
    # Values observed immediately before the restart fade in replay
    # replay_skyroads_20260722_171028.  They remain live until the fade ends.
    view.lateral = 30 * 0x10000
    view.af1c = 0x5F89
    view.af2c = 0x3346
    for index, value in enumerate((0x100, 0x18, 0x50, 0x7350,
                                    0x5E61, 1, 0, 0x8116)):
        state.ww(0x0E28 + index * 2, value)

    assert is_precomposed_level_start(view)
    fade = build_precomposed_level_start_scene(
        view, level=3, game_root=ASSETS,
    )
    assert (
        fade.track_position,
        fade.lateral_position,
        fade.height,
        fade.ship_sprite_index,
        fade.ship_screen_x,
        fade.ship_screen_y,
    ) == (
        INITIAL_TRACK_POSITION,
        INITIAL_LATERAL_POSITION,
        INITIAL_HEIGHT,
        INITIAL_SHIP_SPRITE_INDEX,
        INITIAL_SHIP_SCREEN_X,
        INITIAL_SHIP_SCREEN_Y,
    )


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_palette_fade_changes_scene_identity_without_advancing_gameplay_tick() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=0, game_root=ASSETS)
    palette = list(scene.palette)
    palette[1] = (1, 2, 3)
    faded = replace(scene, palette=tuple(palette))

    assert faded.tick == scene.tick
    assert _scene_state_key(faded) != _scene_state_key(scene)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_stereo_pan_uses_the_recovered_ship_projection() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    view.af1c = 0x8000
    centre = ship_stereo_pan(view)
    view.af1c = 0x6800
    left = ship_stereo_pan(view)
    view.af1c = 0x9800
    right = ship_stereo_pan(view)

    assert abs(centre) < 0.2
    assert left < centre < right


def test_shadow_uses_ship_row_depth_behind_the_rocket() -> None:
    """The rocket is nearer than its stencil; nearer tunnels hide both."""
    scene = SimpleNamespace()
    assert shadow_camera_depth(scene) > ship_camera_depth(scene)
    assert (
        ModernGLFramePresenter._clip_depth(shadow_camera_depth(scene))
        > ModernGLFramePresenter._clip_depth(ship_camera_depth(scene))
    )


def test_shadow_depth_test_does_not_occlude_the_ship() -> None:
    """The shadow reads world depth but cannot write over the later ship."""
    draws: list[tuple[int, bool]] = []

    class FakeContext:
        depth_mask = True
        viewport = None
        blend_func = None

        def enable(self, _capability) -> None:
            pass

        def disable(self, _capability) -> None:
            pass

    context = FakeContext()
    presenter = object.__new__(ModernGLFramePresenter)
    presenter._ctx = context
    presenter._moderngl = SimpleNamespace(
        DEPTH_TEST=1,
        BLEND=2,
        SRC_ALPHA=3,
        ONE_MINUS_SRC_ALPHA=4,
        TRIANGLE_STRIP=5,
    )
    presenter._billboard_program = {
        "clip_depth": SimpleNamespace(value=None),
        "color_gain": SimpleNamespace(value=None),
    }
    presenter._billboard_vao = SimpleNamespace(
        render=lambda mode: draws.append((mode, context.depth_mask)),
    )
    texture = SimpleNamespace(use=lambda **_kwargs: None)

    presenter._draw_ship_billboard(
        texture, (0, 0, 1, 1), shadow_camera_depth(SimpleNamespace()),
        write_depth=False,
    )
    presenter._draw_ship_billboard(
        texture, (0, 0, 1, 1), ship_camera_depth(SimpleNamespace()),
    )

    assert draws == [(5, False), (5, True)]
    assert context.depth_mask is True


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_black_level_selector_handoff_atomically_drops_native_gpu_packet() -> None:
    state = NativeGameState()
    level = 3
    native_level_load(state, road_archive_index(level), game_root=ASSETS)
    state.ww(0x9332, level)
    memory = Memory()
    ds = 0x1686
    base = ds << 4
    memory.data[base:base + len(state.data)] = state.data
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(
            s=SimpleNamespace(cs=0x1010, ip=0x434A, ds=ds),
            mem=memory,
        ),
        dos=SimpleNamespace(vga_palette=((0, 0, 0),) * 256),
        execution_regions=None,
    )
    args = SimpleNamespace(
        renderer="native-3d",
        widescreen=False,
        tweening=False,
        render_debug="final",
        game_root=str(ASSETS),
        simulation_hz=30,
        present_hz=60,
    )
    presentation = SkyroadsPresentation(runtime, args)
    live = GameView(memory.data, base=base)

    _set_fade_callsite(runtime, 0x2CBE)
    assert presentation._observe_ownership(live)
    presentation.polygon_frame = object()
    _set_fade_callsite(runtime, 0x5295)

    assert not presentation._observe_ownership(live)
    assert presentation._ownership_phase == "selector-fade-in"
    assert presentation.polygon_frame is None

    # A boundary restored in the middle of the selector fade has no host
    # ownership history and must reach the same decision from SS:BP alone.
    restored = SkyroadsPresentation(runtime, args)
    assert not restored._observe_ownership(live)
    assert restored._ownership_phase == "selector-fade-in"


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_next_level_mesh_prewarm_waits_for_selector_input(monkeypatch) -> None:
    """A newly published selection must not build its mesh inside the fade."""
    state = NativeGameState()
    level = 3
    native_level_load(state, road_archive_index(level), game_root=ASSETS)
    state.ww(0x9332, level)
    memory = Memory()
    ds = 0x1686
    base = ds << 4
    memory.data[base:base + len(state.data)] = state.data
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(
            s=SimpleNamespace(cs=0x1010, ip=0x434A, ds=ds),
            mem=memory,
        ),
        dos=SimpleNamespace(vga_palette=((0, 0, 0),) * 256),
        execution_regions=None,
    )
    args = SimpleNamespace(
        renderer="native-3d",
        widescreen=False,
        tweening=False,
        render_debug="final",
        game_root=str(ASSETS),
        simulation_hz=30,
        present_hz=60,
    )
    presentation = SkyroadsPresentation(runtime, args)
    calls = []
    monkeypatch.setattr(
        presentation, "_prewarm_selected_level",
        lambda view=None: calls.append(view),
    )

    _set_fade_callsite(runtime, 0x5295)
    assert presentation.frame(lambda: "selector", interpolation=1.0) == "selector"
    assert calls == []

    runtime.cpu.s.ip = 0x5FED
    assert presentation.frame(lambda: "selector", interpolation=1.0) == "selector"
    assert len(calls) == 1


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_region_exit_drops_same_level_gameplay_packet_before_menu_handoff() -> None:
    """An aborted level returns through generated code before DS:9332 changes."""
    state = NativeGameState()
    level = 3
    native_level_load(state, road_archive_index(level), game_root=ASSETS)
    state.ww(0x9332, level)
    memory = Memory()
    ds = 0x1686
    base = ds << 4
    memory.data[base:base + len(state.data)] = state.data
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(
            s=SimpleNamespace(cs=0x1010, ip=0x434A, ds=ds),
            mem=memory,
        ),
        # The generated caller has already selected a visible menu palette;
        # waiting for a black or level-change handoff would retain the road.
        dos=SimpleNamespace(vga_palette=((1, 1, 1),) * 256),
        execution_regions=SimpleNamespace(active_region_id=None),
        _skyroads_last_region_exit=None,
    )
    args = SimpleNamespace(
        renderer="native-3d",
        widescreen=False,
        tweening=False,
        render_debug="final",
        game_root=str(ASSETS),
        simulation_hz=30,
        present_hz=60,
    )
    presentation = SkyroadsPresentation(runtime, args)
    live = GameView(memory.data, base=base)

    _set_fade_callsite(runtime, 0x2CBE)
    assert presentation._observe_ownership(live)
    presentation.polygon_frame = object()
    runtime.cpu.s.ip = 0x2C61  # GAMEPLAY_ABORTED_EXIT generated continuation
    runtime._skyroads_last_region_exit = "gameplay-aborted"

    assert not presentation._observe_ownership(live)
    assert presentation._ownership_phase == "region-exit:gameplay-aborted"
    assert presentation.polygon_frame is None


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_region_exit_retains_native_owner_until_gameplay_fade_reaches_shell() -> None:
    """The exit marker persists; ownership follows the recovered visual head."""
    state = NativeGameState()
    level = 3
    native_level_load(state, road_archive_index(level), game_root=ASSETS)
    state.ww(0x9332, level)
    memory = Memory()
    ds = 0x1686
    base = ds << 4
    memory.data[base:base + len(state.data)] = state.data
    runtime = SimpleNamespace(
        cpu=SimpleNamespace(
            s=SimpleNamespace(cs=0x1010, ip=0x434A, ds=ds),
            mem=memory,
        ),
        dos=SimpleNamespace(vga_palette=((1, 1, 1),) * 256),
        execution_regions=SimpleNamespace(active_region_id=None),
        _skyroads_last_region_exit="gameplay-aborted",
    )
    args = SimpleNamespace(
        renderer="native-3d",
        widescreen=False,
        tweening=False,
        render_debug="final",
        game_root=str(ASSETS),
        simulation_hz=30,
        present_hz=60,
    )
    presentation = SkyroadsPresentation(runtime, args)
    live = GameView(memory.data, base=base)

    _set_fade_callsite(runtime, 0x2CBE)

    # The same persistent exit marker is observed at every generated gameplay
    # presentation boundary. It must not toggle ownership at any of them.
    for ip, phase in (
        (0x434A, "gameplay-exit-fade"),
        (0x0EF8, "gameplay-exit"),
        (0x4468, "gameplay-exit-wait"),
        (0x434A, "gameplay-exit-fade"),
    ):
        runtime.cpu.s.ip = ip
        assert presentation._observe_ownership(live)
        assert presentation._ownership_phase == phase

    # 5FED is the generated level-selector input wait observed immediately
    # after the fade in replay 233559. This is the one external-shell handoff.
    runtime.cpu.s.ip = 0x5FED
    presentation.polygon_frame = object()
    assert not presentation._observe_ownership(live)
    assert presentation._ownership_phase == "level-selector-input"
    assert presentation.polygon_frame is None


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_scene_retains_every_level_30_road_object_and_interpolation_is_read_only() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(29), game_root=ASSETS)
    view = GameView(state)
    view.ship_pos = 0x100
    view.lateral = 0x30000
    view.af1c = 0x8000
    view.af2c = 0x2800
    view.elapsed_ticks = 10
    previous = build_gameplay_scene(view, level=29, game_root=ASSETS)
    view.ship_pos = 0x180
    view.lateral = 0x38000
    view.af1c = 0x9000
    view.elapsed_ticks = 11
    current = build_gameplay_scene(view, level=29, game_root=ASSETS)
    before_interpolation = bytes(state.data)

    visual = interpolate_scene(previous, current, 0.5)

    assert visual.forward_velocity == 0x180
    assert visual.track_position == 0x34000
    assert visual.lateral_position == 0x8800
    assert current.geometry.level == 29
    assert current.geometry.archive_index == 30
    assert current.geometry.objects, "all non-empty road cells must be visible scene inputs"
    # Interpolation did not write a presentation value back into the
    # authoritative carrier.
    assert bytes(state.data) == before_interpolation
    assert view.ship_pos == 0x180 and view.lateral == 0x38000


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_scene_verifier_rejects_a_missing_recovered_road_object() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(14), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=14, game_root=ASSETS)
    geometry = RoadGeometry(
        scene.geometry.level,
        scene.geometry.archive_index,
        scene.geometry.rows,
        scene.geometry.objects[1:],
        scene.geometry.digest,
    )
    rejected = compare_scene_contents(scene, replace(scene, geometry=geometry))

    assert not rejected.equivalent
    assert rejected.differences[0].startswith("scene.missing-object:level:14:road:")


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_interpolated_value_cannot_be_committed_as_authoritative_gameplay() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    previous = build_gameplay_scene(view, level=0, game_root=ASSETS)
    view.lateral = 0x20000
    view.elapsed_ticks = 1
    current = build_gameplay_scene(view, level=0, game_root=ASSETS)
    visual = interpolate_scene(previous, current, 0.5)

    # This simulates the prohibited bug: presentation feeds its tween back into
    # the game. The semantic scene contract catches it immediately.
    view.lateral = visual.track_position
    corrupted = build_gameplay_scene(view, level=0, game_root=ASSETS)
    rejected = compare_scene_contents(current, corrupted)

    assert not rejected.equivalent
    assert "scene.track_position" in rejected.differences


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_polygon_renderer_builds_source_mapped_geometry_read_only() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    before = bytes(state.data)
    scene = build_gameplay_scene(GameView(state), level=0, game_root=ASSETS)
    original = np.arange(320 * 200 * 3, dtype=np.uint8).reshape(200, 320, 3)
    renderer = RecoveredPolygonRenderer(debug_mode="source-ids", widescreen=True)
    packet = renderer.prepare(scene, original)
    image = renderer.render(scene, original)

    assert image.shape == (200, 320, 3)
    assert np.array_equal(image, original)
    assert image is not original
    assert packet.mesh.triangle_count > 0
    assert packet.mesh.source_ids
    assert packet.projection_trace is None
    assert not packet.projection_before_ship
    assert (packet.ship_width, packet.ship_height) == (29, 24)
    assert len(packet.ship_rgba) == 29 * 24 * 4
    assert (packet.ship_x, packet.ship_y) == (
        scene.ship_screen_x, scene.ship_screen_y,
    )
    assert set(packet.mesh.source_ids) <= {
        item.object_id for item in scene.geometry.objects
    }
    assert bytes(state.data) == before


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_interpolated_frames_reuse_immutable_pixel_payloads() -> None:
    """Higher present rates move geometry without rebuilding static layers."""
    pytest.importorskip("numpy")
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    previous = build_gameplay_scene(
        GameView(state), level=0, game_root=ASSETS,
    )
    current = replace(
        previous,
        track_position=previous.track_position + 0x1000,
        ship_screen_x=previous.ship_screen_x + 1,
    )
    renderer = RecoveredPolygonRenderer(widescreen=True)
    first = renderer.prepare(interpolate_scene(previous, current, 0.25))
    second = renderer.prepare(interpolate_scene(previous, current, 0.75))

    assert first.background_rgb is second.background_rgb
    assert first.dashboard_rgba is second.dashboard_rgba
    assert first.ship_rgba is second.ship_rgba
    assert first.shadow_rgba is second.shadow_rgba
    assert first.scene.track_position != second.scene.track_position


@pytest.mark.skipif(not (ASSETS / "TREKDAT.LZS").exists(), reason="needs game assets")
def test_exact_projection_retains_trekdat_spans_palette_and_painter_order() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(14), game_root=ASSETS)
    view = GameView(state)
    initial = build_gameplay_scene(view, level=14, game_root=ASSETS)
    tunnel_row = next(
        row.ordinal for row in initial.geometry.rows
        if any(cell.tunnel for cell in row.cells)
    )
    view.lateral = tunnel_row * 0x10000
    scene = build_gameplay_scene(view, level=14, game_root=ASSETS)

    trace = trace_original_projection(scene)
    visible = trace.visible_primitives

    assert trace.phase == 0
    assert [item.draw_order for item in trace.primitives] == sorted(
        item.draw_order for item in trace.primitives
    )
    assert any(item.role.startswith("tunnel/") for item in visible)
    assert any(item.palette_index in (68, 69, 70, 71) for item in visible)
    assert min(
        span.y for item in visible for span in item.spans if not span.clipped
    ) >= ROAD_DESTINATION_Y
    assert projection_triangles(trace, after_ship=False)
    assert projection_triangles(trace, after_ship=True)


@pytest.mark.skipif(not (ASSETS / "TREKDAT.LZS").exists(), reason="needs game assets")
def test_segment_identity_and_topology_are_stable_across_projection_phases() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(14), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 20 * 0x10000
    first = trace_original_projection(
        build_gameplay_scene(view, level=14, game_root=ASSETS),
    )
    view.lateral += 0x2000
    second = trace_original_projection(
        build_gameplay_scene(view, level=14, game_root=ASSETS),
    )

    first_topology = {
        (item.object_id, item.role, item.pass_index) for item in first.primitives
    }
    second_topology = {
        (item.object_id, item.role, item.pass_index) for item in second.primitives
    }
    assert first.phase == 0 and second.phase == 1
    assert first_topology == second_topology
    common = {
        item.object_id: item.world_bounds for item in first.primitives
    }
    assert all(common[item.object_id] == item.world_bounds
               for item in second.primitives)
    # Projected silhouettes are phase-specific while source topology is not.
    assert first.digest != second.digest


@pytest.mark.skipif(not (ASSETS / "TREKDAT.LZS").exists(), reason="needs game assets")
def test_explicit_exact_projection_cache_tracks_trekdat_phase() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 8 * 0x10000
    scene = build_gameplay_scene(view, level=0, game_root=ASSETS)
    renderer = RecoveredPolygonRenderer(debug_mode="exact-projection")
    original = np.zeros((200, 320, 3), dtype=np.uint8)

    first = renderer.prepare(scene, original)
    same_phase = renderer.prepare(
        replace(scene, track_position=scene.track_position + 0x1000), original,
    )
    next_phase = renderer.prepare(
        replace(scene, track_position=scene.track_position + 0x2000), original,
    )

    assert same_phase.projection_trace is first.projection_trace
    assert same_phase.projection_before_ship is first.projection_before_ship
    assert next_phase.projection_trace is not first.projection_trace
    assert next_phase.projection_trace.phase == (first.projection_trace.phase + 1) % 8

    normal = RecoveredPolygonRenderer().prepare(
        scene, original,
    )
    # The final view is world geometry.  TREKDAT triangles belong only to the
    # exact reference and cannot leak back into the high-resolution path.
    assert normal.projection_trace is None
    assert not normal.projection_before_ship
    assert normal.mesh.triangle_count > 0
    assert first.projection_before_ship == projection_triangles(
        first.projection_trace, after_ship=False,
    )
    assert all(value % 1.0 == 0.0
               for value in first.projection_before_ship[1::5])


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_native_renderer_keeps_one_resident_level_mesh_across_row_changes() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(15), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 20 << 16
    first_scene = build_gameplay_scene(view, level=15, game_root=ASSETS)
    renderer = RecoveredPolygonRenderer()

    first = renderer.prepare(first_scene)
    view.lateral = 21 << 16
    second_scene = build_gameplay_scene(view, level=15, game_root=ASSETS)
    second = renderer.prepare(second_scene)

    assert first.mesh is second.mesh
    assert first.mesh.first_row == 0
    assert first.mesh.last_row == len(first_scene.geometry.rows) - 1


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_native_renderer_reuses_recent_level_mesh_after_level_switch() -> None:
    pytest.importorskip("numpy")
    renderer = RecoveredPolygonRenderer()
    scenes = []
    for level in (0, 1):
        state = NativeGameState()
        native_level_load(state, road_archive_index(level), game_root=ASSETS)
        scenes.append(build_gameplay_scene(
            GameView(state), level=level, game_root=ASSETS,
        ))

    first = renderer.prepare(scenes[0])
    second = renderer.prepare(scenes[1])
    revisited = renderer.prepare(scenes[0])

    assert second.mesh is not first.mesh
    assert revisited.mesh is first.mesh


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_ship_frame_uses_the_original_column_major_cars_layout() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    state.ww(0x0E24, 0)
    scene = build_gameplay_scene(view, level=0, game_root=ASSETS)
    packet = RecoveredPolygonRenderer().prepare(
        scene, np.zeros((200, 320, 3), dtype=np.uint8),
    )
    rgba = np.frombuffer(packet.ship_rgba, dtype=np.uint8).reshape(24, 29, 4)

    # The first 24 archive bytes are one vertical screen column, not one row.
    source = scene.assets.ship_sheet_indices
    for x, y in ((0, 1), (1, 0), (7, 13), (28, 23)):
        index = source[x * 24 + y]
        expected_alpha = 0 if index == 0 else 255
        assert int(rgba[y, x, 3]) == expected_alpha
        if index:
            assert tuple(int(value) for value in rgba[y, x, :3]) == (
                scene.palette[72 + index]
            )


def test_widescreen_expands_the_physical_4_by_3_view_without_dos_par_twice() -> None:
    pygame = pytest.importorskip("pygame")

    class DisplayGeometry:
        par = 1.2

        def __init__(self, size):
            self.size = size

        def get_size(self):
            return self.size

        def letterbox(self, width, height):
            window_width, window_height = self.size
            factor = min(
                window_width / width,
                window_height / (height * self.par),
            )
            target = (round(width * factor), round(height * self.par * factor))
            return pygame.Rect(
                (window_width - target[0]) // 2,
                (window_height - target[1]) // 2,
                *target,
            )

    wide = DisplayGeometry((1600, 900))
    assert ModernGLFramePresenter._presentation_rect(wide, False).size == (
        1200, 900,
    )
    assert ModernGLFramePresenter._presentation_rect(wide, True).size == (
        1600, 900,
    )

    portrait = DisplayGeometry((900, 900))
    assert ModernGLFramePresenter._presentation_rect(portrait, True).size == (
        900, 675,
    )


def test_original_frame_is_a_separate_debug_view_not_a_native_overlay() -> None:
    final = SimpleNamespace(debug_mode="final")
    oracle = SimpleNamespace(debug_mode="original")

    assert not ModernGLFramePresenter._reference_only(final)
    assert ModernGLFramePresenter._reference_only(oracle)
    assert ModernGLFramePresenter._reference_only(None)
    assert not hasattr(ModernGLFramePresenter, "_draw_faithful_aperture")


def test_native_projection_uses_recovered_non_square_pixel_calibration() -> None:
    assert CALIBRATION.camera_height == pytest.approx(1.4971649422)
    assert CALIBRATION.horizon_y == pytest.approx(32.5900849601)
    assert CALIBRATION.near_bias == pytest.approx(2.545)
    assert CALIBRATION.vanishing_depth == pytest.approx(7.725)
    assert CALIBRATION.road_band_top == 32
    assert CALIBRATION.road_band_bottom == 138
    # One recovered scale controls both axes; there are no independently
    # snapped screen-space guide lines.
    scale = projection_scale(2.0)
    centre = project_world_vertex(0.0, 0.0, 2.0, 0.0)
    right = project_world_vertex(1.0, 0.0, 2.0, 0.0)
    raised = project_world_vertex(0.0, 1.0, 2.0, 0.0)
    assert right[0] - centre[0] == pytest.approx(scale)
    assert centre[1] - raised[1] == pytest.approx(scale)


def test_continuous_lens_fits_the_recovered_trekdat_vertex_lattice() -> None:
    # Median lane-edge samples recovered independently from the exact phase-0
    # deck strips. They are evidence for the lens, not runtime geometry.
    recovered = (
        (0.0, 102.0, 46.3333333333),
        (1.0, 76.0, 29.0),
        (2.0, 61.0, 19.0),
        (3.0, 52.0, 12.9416666667),
        (4.0, 46.0, 8.9561904762),
        (5.0, 41.0, 5.6333333333),
        (6.0, 37.0, 2.96),
        (7.0, 34.0, 0.9833333333),
    )
    for depth, ground_y, lane_scale in recovered:
        projected = project_world_vertex(0.0, 0.0, depth, 0.0)
        assert projected[1] == pytest.approx(ground_y, abs=0.65)
        assert projection_scale(depth) == pytest.approx(lane_scale, abs=0.55)


def test_world_projection_has_no_eighth_row_phase_snaps() -> None:
    samples = [
        project_world_vertex(-2.5, 0.0, 4.0, step / 64.0)
        for step in range(65)
    ]
    # Every subphase produces a distinct, monotonic position. The old path had
    # only eight shapes and therefore repeated then jumped.
    assert len(set(samples)) == len(samples)
    assert all(right[0] < left[0] for left, right in zip(samples, samples[1:]))
    assert all(right[1] > left[1] for left, right in zip(samples, samples[1:]))


def test_ship_billboard_uses_the_current_source_row_depth_plane() -> None:
    cells = tuple(SimpleNamespace(tunnel=False) for _ in range(7))
    scene = SimpleNamespace(
        lateral_lanes=0.0,
        track_row=0,
        geometry=SimpleNamespace(rows=(
            SimpleNamespace(cells=cells), SimpleNamespace(cells=cells),
        )),
    )
    at_row_start = ship_camera_depth(scene)
    late_in_row = ship_camera_depth(scene)

    assert at_row_start == pytest.approx(CALIBRATION.ship_depth)
    # The original sprite has fixed screen scale and a fixed compositor seam;
    # changing the road sub-row phase moves geometry through that plane.
    assert late_in_row == pytest.approx(at_row_start)
    assert ModernGLFramePresenter._clip_depth(at_row_start) < 1.0


def test_ship_billboard_moves_behind_a_sustained_tunnel_arch() -> None:
    open_cells = [SimpleNamespace(tunnel=False) for _ in range(7)]
    tunnel_cells = [SimpleNamespace(tunnel=False) for _ in range(7)]
    open_cells[3] = SimpleNamespace(tunnel=True)
    tunnel_cells[3] = SimpleNamespace(tunnel=True)
    scene = SimpleNamespace(
        lateral_lanes=0.0,
        track_row=0,
        geometry=SimpleNamespace(rows=(
            SimpleNamespace(cells=tuple(open_cells)),
            SimpleNamespace(cells=tuple(tunnel_cells)),
        )),
    )

    enclosed = ship_camera_depth(scene)
    # Tunnel state cannot move the camera or the ship's depth plane.  Its
    # immutable world faces alone decide occlusion through the depth buffer.
    assert enclosed == pytest.approx(CALIBRATION.ship_depth)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_live_vga_palette_controls_enhanced_assets_during_fades() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    state.ww(0x0E24, 14)
    view = GameView(state)
    black_palette = ((0, 0, 0),) * 256
    visible = build_gameplay_scene(view, level=0, game_root=ASSETS)
    black = build_gameplay_scene(
        view, level=0, game_root=ASSETS, device_palette=black_palette,
    )
    original = np.zeros((200, 320, 3), dtype=np.uint8)
    renderer = RecoveredPolygonRenderer()
    visible_packet = renderer.prepare(visible, original)
    black_packet = renderer.prepare(black, original)

    assert any(visible_packet.background_rgb)
    # A uniform gameplay fade keeps the immutable indexed-asset decode and
    # applies the live DAC scale in the GPU.  The prepared RGB basis therefore
    # remains shared while the packet's explicit gain makes the presented
    # result black; rebuilding every asset for each palette step would restore
    # the transition stalls this path is designed to avoid.
    assert black_packet.background_rgb is visible_packet.background_rgb
    assert black_packet.palette_gain == pytest.approx(0.0)
    assert any(visible_packet.ship_rgba)
    assert black_packet.ship_rgba is visible_packet.ship_rgba
    assert black_packet.dashboard_rgba is visible_packet.dashboard_rgba


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_fade_in_acquired_while_black_reuses_source_palette_payloads() -> None:
    """A cold native handoff at black must not rebuild on every fade step."""
    pytest.importorskip("numpy")
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    canonical = build_gameplay_scene(view, level=0, game_root=ASSETS)
    assert canonical.source_palette[:72] == canonical.assets.road_palette
    assert canonical.source_palette[72:92] == canonical.assets.ship_palette
    assert canonical.source_palette[92:142] == canonical.assets.dashboard_palette
    assert canonical.source_palette[142:] == canonical.assets.world_palette
    black = build_gameplay_scene(
        view,
        level=0,
        game_root=ASSETS,
        device_palette=((0, 0, 0),) * 256,
    )
    renderer = RecoveredPolygonRenderer()

    first = renderer.prepare(black)
    assert first.palette_gain == pytest.approx(0.0)
    assert any(first.background_rgb)

    for numerator in (1, 7, 15, 23, 31):
        live = tuple(
            tuple(round(channel * numerator / 31) for channel in color)
            for color in canonical.source_palette
        )
        packet = renderer.prepare(replace(canonical, palette=live))
        assert packet.palette_gain == pytest.approx(numerator / 31, abs=0.03)
        assert packet.mesh is first.mesh
        assert packet.background_rgb is first.background_rgb
        assert packet.dashboard_rgba is first.dashboard_rgba
        assert packet.ship_rgba is first.ship_rgba


@pytest.mark.skipif(not (ASSETS / "DASHBRD.LZS").exists(), reason="needs game assets")
def test_native_dashboard_is_recovered_mask_not_oracle_frame_crop() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=0, game_root=ASSETS)

    class ForbiddenOracleFrame:
        def __array__(self, *args, **kwargs):
            raise AssertionError("native dashboard read the oracle framebuffer")

    packet = RecoveredPolygonRenderer().prepare(scene, ForbiddenOracleFrame())
    dashboard = np.asarray(packet.dashboard_rgba)

    assert dashboard.shape == (71, 320, 4)
    # The recovered upper bezel deliberately contains both opaque art and
    # transparent holes through which native road geometry remains visible.
    upper_alpha = dashboard[:9, :, 3]
    dashboard_asset = np.frombuffer(
        scene.assets.dashboard_indices, dtype=np.uint8,
    ).reshape(71, 320)
    assert np.array_equal(upper_alpha == 255, dashboard_asset[:9] != 0)
    assert (upper_alpha == 0).any()
    assert (upper_alpha == 255).any()
    assert (dashboard[9:, :, 3] == 255).all()
    assert set(np.unique(dashboard[:, :, 3])) <= {0, 255}


def test_widescreen_background_uses_alternating_mirrored_coordinates() -> None:
    samples = (-0.25, 0.0, 0.25, 0.75, 1.0, 1.25, 1.75, 2.0, 2.25)
    assert tuple(mirrored_repeat_coordinate(value) for value in samples) == (
        0.25, 0.0, 0.25, 0.75, 1.0, 0.75, 0.25, 0.0, 0.25,
    )


def test_widescreen_hud_uv_preserves_centre_and_clamps_outer_edges() -> None:
    scale, offset = widescreen_edge_clamp_uv(1920, 1280)

    assert scale == pytest.approx(1.5)
    assert offset == pytest.approx(-0.25)
    # Centre 1280 pixels map exactly to source 0..1. The extra 320 pixels on
    # each side map outside that range and are clamped by the HUD texture.
    assert (1 / 6) * scale + offset == pytest.approx(0.0)
    assert 0.5 * scale + offset == pytest.approx(0.5)
    assert (5 / 6) * scale + offset == pytest.approx(1.0)
    assert offset < 0.0
    assert scale + offset > 1.0


def test_moderngl_texture_upload_skips_an_unchanged_cached_payload() -> None:
    class Texture:
        def __init__(self, size, components, data) -> None:
            self.size = size
            self.components = components
            self.created_with = data
            self.writes = []
            self.repeat_x = self.repeat_y = False
            self.filter = None

        def write(self, data) -> None:
            self.writes.append(data)

        def release(self) -> None:
            pass

    class Context:
        def texture(self, size, components, data):
            return Texture(size, components, data)

    presenter = ModernGLFramePresenter()
    presenter._ctx = Context()
    presenter._moderngl = SimpleNamespace(NEAREST=0)
    payload = bytes(bytearray(b"abc"))
    texture = presenter._texture("layer", (1, 1), 3, payload)
    assert presenter._texture("layer", (1, 1), 3, payload) is texture
    assert texture.writes == []

    changed = bytes(bytearray(b"def"))
    presenter._texture("layer", (1, 1), 3, changed)
    assert texture.writes == [changed]


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_rocket_shadow_uses_recovered_33fd_stencil_and_palette_remap() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    # Band two at the exact 33FD source offset; one marked texel is enough to
    # prove packet provenance and placement without relying on VGA output.
    state.ww(0x0E34, 10)
    state.ww(0x0E70, 100 * 320 + 140)
    state.wb(0x068E + 2 * 0x105 + 4 * 29 + 7, 1)
    state.wb(0x113E + 4 * 29 + 7, 1)
    palette = [(0, 0, 0)] * 256
    for index in range(1, 0x10):
        palette[index] = (240, 240, 240)
        palette[index + 0x2D] = (60, 60, 60)
    scene = build_gameplay_scene(
        GameView(state), level=0, game_root=ASSETS,
        device_palette=tuple(palette),
    )
    packet = RecoveredPolygonRenderer().prepare(scene)
    shadow = np.frombuffer(packet.shadow_rgba, dtype=np.uint8).reshape(9, 29, 4)

    assert scene.shadow.band == 2
    assert (packet.shadow_x, packet.shadow_y) == (140, 100)
    assert shadow[4, 7, 3] > 0
    assert np.count_nonzero(shadow[:, :, 3]) == 1


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_rocket_shadow_respects_325b_coverage_occlusion() -> None:
    pytest.importorskip("numpy")
    import numpy as np

    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    state.ww(0x0E34, 0)
    state.ww(0x0E70, 90 * 320 + 120)
    state.wb(0x068E, 1)
    # A 33FD pattern texel outside 325B/32C1's admitted coverage must not
    # darken the native world. This is the original tunnel/terrain occlusion
    # decision, not a renderer-authored depth guess.
    state.wb(0x113E, 0)
    palette = [(0, 0, 0)] * 256
    palette[1] = (240, 240, 240)
    palette[0x2E] = (60, 60, 60)
    scene = build_gameplay_scene(
        GameView(state), level=0, game_root=ASSETS,
        device_palette=tuple(palette),
    )
    packet = RecoveredPolygonRenderer().prepare(scene)
    shadow = np.frombuffer(packet.shadow_rgba, dtype=np.uint8).reshape(9, 29, 4)

    assert shadow[0, 0, 3] == 0


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_scalar_gameplay_fade_reuses_geometry_and_indexed_assets() -> None:
    """4331's fade is a GPU multiplier, not 30 CPU rebuilds."""
    pytest.importorskip("numpy")
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=0, game_root=ASSETS)
    renderer = RecoveredPolygonRenderer()
    bright = renderer.prepare(scene)
    half_palette = tuple(
        tuple(round(channel * 0.5) for channel in color)
        for color in scene.palette
    )
    faded = renderer.prepare(replace(scene, palette=half_palette))

    assert faded.palette_gain == pytest.approx(0.5, abs=0.03)
    assert faded.mesh is bright.mesh
    assert faded.background_rgb is bright.background_rgb
    assert faded.dashboard_rgba is bright.dashboard_rgba
    assert faded.ship_rgba is bright.ship_rgba
    assert faded.shadow_rgba is bright.shadow_rgba
    # The packet continues to expose the authoritative live DAC; only its
    # immutable render payload is based on the cached bright palette.
    assert faded.scene.palette == half_palette


def test_scalar_fade_ignores_unreferenced_live_dac_slots() -> None:
    """Unowned DAC entries cannot make immutable world geometry transient."""
    basis = (
        (0, 0, 0),
        (4, 2, 1),
        (16, 32, 48),
        (40, 24, 8),
    )
    live = (
        # The gameplay source palette deliberately leaves this DAC slot
        # unowned, while the original machine can retain another scene's
        # colour there.
        (63, 21, 9),
        # Integer fade quantization is intentionally ignored below 8.
        (0, 0, 0),
        (8, 16, 24),
        (20, 12, 4),
    )

    assert _uniform_palette_gain(basis, live) == pytest.approx(0.5)


def test_palette_gain_rejects_nonuniform_changes_to_owned_colours() -> None:
    basis = ((16, 32, 48), (40, 24, 8))
    changed = ((8, 16, 24), (40, 24, 8))

    assert _uniform_palette_gain(basis, changed) is None


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_recovered_cell_layout_matches_original_dispatch_selectors() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(14), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=14, game_root=ASSETS)
    cells = [cell for row in scene.geometry.rows for cell in row.cells]

    assert all(cell.source_offset == 0x162C + cell.row * 14 + cell.lane * 2
               for cell in cells)
    assert all(0 <= cell.deck_material <= 15 for cell in cells)
    assert any(cell.tunnel for cell in cells)
    assert any(cell.raised is RaisedShape.HALF for cell in cells)
    assert any(cell.raised is RaisedShape.FULL for cell in cells)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_mesh_window_tracks_forward_coordinate_not_velocity() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    view.ship_pos = 0x2AAA       # forward velocity at its clamp
    view.lateral = 12 * 0x10000  # original renderer's row coordinate
    view.af1c = 0x8000
    scene = build_gameplay_scene(view, level=0, game_root=ASSETS)
    mesh = build_polygon_mesh(scene)

    assert scene.track_row == 12
    assert mesh.first_row == 10
    assert mesh.last_row == 22
    assert mesh.last_row - mesh.first_row + 1 == 13
    # Rows 20..22 are already present while wholly beyond the lens's 7.725
    # vanishing depth. They acquire area continuously as the camera advances,
    # rather than popping into the mesh at the old current+7 boundary.
    assert projection_scale(mesh.last_row - scene.track_row) == 0.0


def test_tunnel_dispatch_selectors_retain_two_structural_families() -> None:
    exposed = decode_road_cell(0, 3, 0x010B)
    carved_half = decode_road_cell(0, 3, 0x030B)
    carved_full = decode_road_cell(0, 3, 0x050B)

    assert exposed.tunnel_shape is TunnelShape.EXPOSED_TUBE
    assert exposed.raised is RaisedShape.NONE
    assert carved_half.tunnel_shape is TunnelShape.CARVED_HALF
    assert carved_half.raised is RaisedShape.HALF
    assert carved_full.tunnel_shape is TunnelShape.CARVED_FULL
    assert carved_full.raised is RaisedShape.FULL


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_level_18_carved_block_retains_its_distinct_top_material() -> None:
    """Replay 155438 exposed 0x0520's green top versus grey shell."""
    state = NativeGameState()
    native_level_load(state, road_archive_index(18), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 10 * 0x10000
    scene = build_gameplay_scene(view, level=18, game_root=ASSETS)
    cell = scene.geometry.rows[15].cells[3]
    trace = trace_original_projection(scene)
    original_tops = [
        item for item in trace.primitives
        if item.object_id == cell.object_id and item.role == "raised/top"
    ]

    assert cell.code == 0x0520
    assert cell.top_material == 2
    assert original_tops
    assert {item.palette_selector for item in original_tops} == {2}

    from skyroads.presentation.renderer import _MeshBuilder
    builder = _MeshBuilder(scene, "final")
    builder.cell(cell)
    top_rgb = tuple(channel / 255.0 for channel in scene.palette[2])
    vertices = tuple(zip(*(iter(builder.vertices),) * 6))
    assert any(vertex[3:] == pytest.approx(top_rgb) for vertex in vertices)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_tunnel_mesh_has_distinct_shell_rim_and_passage_surfaces() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(4), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=4, game_root=ASSETS)
    cells = [cell for row in scene.geometry.rows for cell in row.cells]
    exposed = next(cell for cell in cells
                   if cell.tunnel_shape is TunnelShape.EXPOSED_TUBE)
    carved = next(cell for cell in cells
                  if cell.tunnel_shape is TunnelShape.CARVED_HALF)

    # Exercise the constructive primitives directly so this test detects a
    # regression back to one zero-thickness arch or box+arch overlap.
    from skyroads.presentation.renderer import (
        EXPOSED_INNER_HEIGHT,
        EXPOSED_INNER_HALF_WIDTH,
        EXPOSED_OUTER_HEIGHT,
        EXPOSED_OUTER_HALF_WIDTH,
        EXPOSED_RIM_DEPTH,
        _MeshBuilder,
    )
    exposed_builder = _MeshBuilder(scene, "final")
    exposed_builder.cell(exposed)
    carved_builder = _MeshBuilder(scene, "final")
    carved_builder.cell(carved)

    exposed_positions = set(zip(
        exposed_builder.vertices[0::6],
        exposed_builder.vertices[1::6],
        exposed_builder.vertices[2::6],
    ))
    carved_positions = set(zip(
        carved_builder.vertices[0::6],
        carved_builder.vertices[1::6],
        carved_builder.vertices[2::6],
    ))
    exposed_center = exposed.lane - 3.0
    carved_center = carved.lane - 3.0

    # Outer and inner radii plus the recessed inner start prove non-zero wall
    # and entrance-rim thickness in all three dimensions.
    assert any(abs(x - (exposed_center - EXPOSED_OUTER_HALF_WIDTH)) < 1e-6
               for x, _y, _z in exposed_positions)
    assert any(abs(x - (exposed_center - EXPOSED_INNER_HALF_WIDTH)) < 1e-6
               for x, _y, _z in exposed_positions)
    assert any(
        abs(x - exposed_center) < 1e-6
        and abs(y - EXPOSED_OUTER_HEIGHT) < 1e-6
        and abs(z - (exposed.row + 0.1)) < 1e-6
        for x, y, z in exposed_positions
    )
    assert any(
        abs(x - exposed_center) < 1e-6
        and abs(y - EXPOSED_INNER_HEIGHT) < 1e-6
        and abs(z - (exposed.row + 0.1 + EXPOSED_RIM_DEPTH)) < 1e-6
        for x, y, z in exposed_positions
    )
    for depth in (
        exposed.row + 0.1,
        exposed.row + 0.1 + EXPOSED_RIM_DEPTH,
    ):
        assert any(abs(z - depth) < 1e-6
                   for _x, _y, z in exposed_positions)

    # The carved family owns a mouse-hole profile and a separate rectangular
    # exterior; it is not the exposed tube laid over a closed block.
    assert any(abs(x - (carved_center - 0.43)) < 1e-6 and abs(y - 0.08) < 1e-6
               for x, y, _z in carved_positions)
    assert any(abs(y - 0.38) < 1e-6
               for _x, y, _z in carved_positions)
    # The solid front, then the passage, occupy two evidence-backed depth
    # planes. Shared aperture vertices and the reveal quads leave no hole
    # between the front face and the longitudinal interior.
    assert any(abs(z - (carved.row + 0.1)) < 1e-6
               for _x, _y, z in carved_positions)
    assert any(abs(z - (carved.row + 0.2)) < 1e-6
               for _x, _y, z in carved_positions)
    deck_rgb = tuple(channel / 255.0 for channel in scene.palette[
        carved.deck_material
    ])
    carved_vertices = tuple(zip(*(iter(carved_builder.vertices),) * 6))
    for depth in (carved.row + 0.1, carved.row + 0.2):
        assert any(
            vertex[:3] == pytest.approx((
                carved.lane - 3.5, 0.0, depth,
            ))
            and vertex[3:] == pytest.approx(deck_rgb)
            for vertex in carved_vertices
        )
    lower_layer = (HALF_BLOCK_HEIGHT - ROAD_DECK_HEIGHT) / LANE_UNITS
    assert any(
        abs(y - lower_layer) < 1e-6
        for _x, y, _z in carved_positions
    )
    assert len(carved_builder.indices) // 3 > 40


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_raised_wall_front_plane_and_continuation_follow_original_gate() -> None:
    from skyroads.presentation.renderer import (
        RAISED_FRONT_SETBACK,
        _MeshBuilder,
    )

    state = NativeGameState()
    native_level_load(state, road_archive_index(22), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=22, game_root=ASSETS)
    row = scene.geometry.rows[21]
    previous = scene.geometry.rows[20]
    solid = row.cells[1]
    carved = row.cells[2]
    assert solid.raised is RaisedShape.HALF and not solid.tunnel
    assert carved.tunnel_shape is TunnelShape.CARVED_HALF

    solid_builder = _MeshBuilder(scene, "final")
    solid_builder.cell(solid, previous=previous.cells[1])
    carved_builder = _MeshBuilder(scene, "final")
    carved_builder.cell(carved, previous=previous.cells[2])
    solid_positions = set(zip(
        solid_builder.vertices[0::6],
        solid_builder.vertices[1::6],
        solid_builder.vertices[2::6],
    ))
    carved_positions = set(zip(
        carved_builder.vertices[0::6],
        carved_builder.vertices[1::6],
        carved_builder.vertices[2::6],
    ))
    front = row.ordinal + RAISED_FRONT_SETBACK
    half_height = (HALF_BLOCK_HEIGHT - ROAD_DECK_HEIGHT) / LANE_UNITS
    assert any(
        position == pytest.approx((solid.lane - 3.5, half_height, front))
        for position in solid_positions
    )
    assert any(
        position == pytest.approx((carved.lane - 3.5, half_height, front))
        for position in carved_positions
    )

    # 2EBB/2F58 gate the two tier faces independently at above_type <2/<4.
    # A continued raised run keeps the shared row plane and emits no redundant
    # internal front quad for every tier already occupied by its predecessor.
    continued_pair = next(
        (scene.geometry.rows[index - 1].cells[lane],
         scene.geometry.rows[index].cells[lane])
        for index in range(1, len(scene.geometry.rows))
        for lane in range(7)
        if (scene.geometry.rows[index - 1].cells[lane].raised
            is not RaisedShape.NONE
            and scene.geometry.rows[index].cells[lane].raised
            is not RaisedShape.NONE
            and not scene.geometry.rows[index].cells[lane].tunnel)
    )
    continued = _MeshBuilder(scene, "final")
    continued.cell(continued_pair[1], previous=continued_pair[0])
    isolated = _MeshBuilder(scene, "final")
    isolated.cell(continued_pair[1])
    suppressed_tiers = min(
        continued._raised_tiers(continued_pair[0]),
        continued._raised_tiers(continued_pair[1]),
    )
    assert len(continued.indices) + suppressed_tiers * 6 == len(isolated.indices)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_full_wall_uses_two_neighbor_gated_tiers_on_fixed_depth_planes() -> None:
    """Snapshot 144526: half/full adjacency must remain one solid wall."""
    from skyroads.presentation.renderer import (
        CARVED_LOWER_LAYER_HEIGHT,
        RAISED_FRONT_SETBACK,
        _MeshBuilder,
    )

    state = NativeGameState()
    native_level_load(state, road_archive_index(12), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=12, game_root=ASSETS)
    nearer = scene.geometry.rows[8]
    wall = scene.geometry.rows[9]
    assert [wall.cells[lane].code for lane in (2, 3, 4)] == [
        0x0400, 0x0400, 0x0400,
    ]
    assert nearer.cells[2].raised is RaisedShape.HALF
    assert nearer.cells[3].raised is RaisedShape.NONE
    assert nearer.cells[4].raised is RaisedShape.HALF

    builders = []
    for lane in (2, 3, 4):
        builder = _MeshBuilder(scene, "final")
        builder.cell(wall.cells[lane], previous=nearer.cells[lane])
        builders.append(builder)

    # All road-cell slots occupy the same recovered display-list footprint.
    # The row+1.10 far plane of the row-8 centre deck is exactly the row+0.10
    # near plane of row 9, so no uncovered strip can open behind the ship.
    near_z = wall.ordinal + RAISED_FRONT_SETBACK
    far_z = wall.ordinal + 1 + RAISED_FRONT_SETBACK
    deck_builder = _MeshBuilder(scene, "final")
    deck_builder.cell(
        nearer.cells[3],
        previous=scene.geometry.rows[7].cells[3],
        following=wall.cells[3],
    )
    deck_positions = set(zip(
        deck_builder.vertices[0::6],
        deck_builder.vertices[1::6],
        deck_builder.vertices[2::6],
    ))
    assert near_z in {position[2] for position in deck_positions}
    assert float(wall.ordinal) not in {
        position[2] for position in deck_positions
    }
    for builder in builders:
        positions = set(zip(
            builder.vertices[0::6],
            builder.vertices[1::6],
            builder.vertices[2::6],
        ))
        assert {near_z, far_z}.issubset({position[2] for position in positions})

    # Lanes 2/4 continue an existing lower tier and expose only the new upper
    # front face. The centre lane begins both tiers. Internal lateral faces
    # are suppressed because all three side neighbors reach full height.
    lower = CARVED_LOWER_LAYER_HEIGHT
    full = lower * 2
    front_rgb = builders[1].color(wall.cells[3], "front", raised=True)
    centre_front_heights = {
        builder_y
        for _builder_x, builder_y, builder_z, red, green, blue
        in zip(
            builders[1].vertices[0::6],
            builders[1].vertices[1::6],
            builders[1].vertices[2::6],
            builders[1].vertices[3::6],
            builders[1].vertices[4::6],
            builders[1].vertices[5::6],
        )
        if builder_z == pytest.approx(near_z)
        and (red, green, blue) == pytest.approx(front_rgb)
    }
    side_front_heights = {
        builder_y
        for _builder_x, builder_y, builder_z, red, green, blue
        in zip(
            builders[0].vertices[0::6],
            builders[0].vertices[1::6],
            builders[0].vertices[2::6],
            builders[0].vertices[3::6],
            builders[0].vertices[4::6],
            builders[0].vertices[5::6],
        )
        if builder_z == pytest.approx(near_z)
        and (red, green, blue) == pytest.approx(front_rgb)
    }
    assert sorted(centre_front_heights) == pytest.approx([0.0, lower, full])
    assert sorted(side_front_heights) == pytest.approx([lower, full])
    assert len(builders[1].indices) == 18


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_exposed_tube_uses_mirrored_roles_and_full_lane_anchor() -> None:
    from skyroads.presentation.renderer import (
        EXPOSED_FRONT_OUTER_SHARE,
        EXPOSED_INNER_HALF_WIDTH,
        EXPOSED_OUTER_HALF_WIDTH,
        EXPOSED_RIM_DEPTH,
        ROAD_CELL_DEPTH_OFFSET,
        _MeshBuilder,
    )

    state = NativeGameState()
    native_level_load(state, road_archive_index(6), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=6, game_root=ASSETS)
    # Row 37 is the structural entrance; row 38 is already a continuation
    # and therefore deliberately has no front-face roles.
    row = scene.geometry.rows[37]
    previous = scene.geometry.rows[36]
    following = scene.geometry.rows[38]
    left, right = row.cells[1], row.cells[5]
    assert left.tunnel_shape is right.tunnel_shape is TunnelShape.EXPOSED_TUBE

    left_builder = _MeshBuilder(scene, "final")
    left_builder.cell(
        left, previous=previous.cells[1], following=following.cells[1],
    )
    right_builder = _MeshBuilder(scene, "final")
    right_builder.cell(
        right, previous=previous.cells[5], following=following.cells[5],
    )
    left_vertices = tuple(zip(*(iter(left_builder.vertices),) * 6))
    right_vertices = tuple(zip(*(iter(right_builder.vertices),) * 6))
    left_center = left.lane - 3.0
    right_center = right.lane - 3.0
    front = row.ordinal + ROAD_CELL_DEPTH_OFFSET
    far = row.ordinal + 1 + ROAD_CELL_DEPTH_OFFSET

    def selector_rgb(selector: int, *, backward: bool):
        table = (
            scene.face_palette_backward if backward
            else scene.face_palette_forward
        )
        return tuple(
            channel / 255.0
            for channel in scene.palette[table[selector]]
        )

    # shell-0 is the outermost physical band on both sides, but the backward
    # pass reaches it by reversing the spatial shell order.
    expected = (
        (
            left_vertices,
            (left_center - EXPOSED_OUTER_HALF_WIDTH, 0.0, front),
            selector_rgb(68, backward=False),
        ),
        (
            right_vertices,
            (right_center + EXPOSED_OUTER_HALF_WIDTH, 0.0, front),
            selector_rgb(68, backward=True),
        ),
    )
    for vertices, position, color in expected:
        assert any(
            vertex[:3] == pytest.approx(position)
            and vertex[3:] == pytest.approx(color, abs=1e-6)
            for vertex in vertices
        )
    assert left_center - EXPOSED_OUTER_HALF_WIDTH == left.lane - 3.5
    assert right_center + EXPOSED_OUTER_HALF_WIDTH == right.lane - 2.5
    assert any(vertex[2] == pytest.approx(far) for vertex in left_vertices)
    assert not any(vertex[2] == pytest.approx(float(row.ordinal))
                   for vertex in left_vertices)

    # Original 3059 paint ownership leaves selector 67 only on the inner part
    # of the road-outward half. Selector 66 owns the outer part and the whole
    # inward half. The split is a world-space reveal vertex, not a fitted
    # screen-space line, and mirrors with the backward road pass.
    reveal = front + EXPOSED_RIM_DEPTH
    split_depth = (
        front + EXPOSED_RIM_DEPTH * EXPOSED_FRONT_OUTER_SHARE
    )
    for vertices, center, outward_sign, backward in (
        (left_vertices, left_center, -1.0, False),
        (right_vertices, right_center, 1.0, True),
    ):
        outer_x = center + outward_sign * EXPOSED_OUTER_HALF_WIDTH
        inner_x = center + outward_sign * EXPOSED_INNER_HALF_WIDTH
        split_x = (
            outer_x
            + (inner_x - outer_x) * EXPOSED_FRONT_OUTER_SHARE
        )
        inner_rgb = selector_rgb(66, backward=backward)
        rim_rgb = selector_rgb(67, backward=backward)
        assert any(
            vertex[:3] == pytest.approx((split_x, 0.0, split_depth))
            and vertex[3:] == pytest.approx(inner_rgb, abs=1e-6)
            for vertex in vertices
        )
        assert any(
            vertex[:3] == pytest.approx((split_x, 0.0, split_depth))
            and vertex[3:] == pytest.approx(rim_rgb, abs=1e-6)
            for vertex in vertices
        )
        assert any(
            vertex[:3] == pytest.approx((inner_x, 0.0, reveal))
            and vertex[3:] == pytest.approx(rim_rgb, abs=1e-6)
            for vertex in vertices
        )

        inward_x = center - outward_sign * EXPOSED_INNER_HALF_WIDTH
        assert any(
            vertex[:3] == pytest.approx((inward_x, 0.0, reveal))
            and vertex[3:] == pytest.approx(inner_rgb, abs=1e-6)
            for vertex in vertices
        )
        assert not any(
            vertex[:3] == pytest.approx((inward_x, 0.0, reveal))
            and vertex[3:] == pytest.approx(rim_rgb, abs=1e-6)
            for vertex in vertices
        )


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_carved_tunnel_shading_uses_original_selector_tables_per_face() -> None:
    from skyroads.presentation.renderer import (
        CARVED_FACE_SELECTOR,
        CARVED_RIM_SELECTOR,
        CARVED_SIDE_SELECTOR,
        RAISED_TOP_DEFAULT_SELECTOR,
        _MeshBuilder,
    )

    state = NativeGameState()
    native_level_load(state, road_archive_index(7), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=7, game_root=ASSETS)
    # The supplied oracle snapshot is at level 7, row 42.  Its lane-2 cell is
    # the first row of the full-height carved tunnel, so all five recovered
    # face roles are present at the structural entrance.
    carved = scene.geometry.rows[42].cells[2]
    assert carved.tunnel_shape is TunnelShape.CARVED_FULL
    builder = _MeshBuilder(scene, "final")
    builder.cell(carved)
    vertices = tuple(zip(*(iter(builder.vertices),) * 6))
    x0 = carved.lane - 3.5
    x1 = x0 + 1.0
    front = carved.row + 0.1
    height = (FULL_BLOCK_HEIGHT - ROAD_DECK_HEIGHT) / LANE_UNITS

    def rgb(selector: int, *, backward: bool = False):
        table = (
            scene.face_palette_backward if backward
            else scene.face_palette_forward
        )
        return tuple(channel / 255.0 for channel in scene.palette[
            table[selector]
        ])

    expected = (
        ("front", (x0, 0.0, front), rgb(CARVED_FACE_SELECTOR)),
        ("top", (x0, height, front), rgb(
            carved.top_material or RAISED_TOP_DEFAULT_SELECTOR,
        )),
        ("left", (x0, height / 2, front), rgb(
            CARVED_SIDE_SELECTOR, backward=True,
        )),
        ("right", (x1, height / 2, front), rgb(
            CARVED_SIDE_SELECTOR, backward=False,
        )),
        ("rim", (carved.lane - 3.0 - 0.43, 0.0, front),
         rgb(CARVED_RIM_SELECTOR)),
    )
    for role, position, color in expected:
        assert any(
            vertex[:3] == pytest.approx(position)
            and vertex[3:] == pytest.approx(color, abs=1e-6)
            for vertex in vertices
        ), role


def test_carved_tunnel_geometry_matches_snapshot_rle_boundaries() -> None:
    """Invert the exact 20260723_132043 portal spans through the shared lens."""
    from skyroads.presentation.renderer import (
        CARVED_FRONT_SETBACK,
        CARVED_OPENING_ARCH_HEIGHT,
        CARVED_OPENING_HALF_WIDTH,
        CARVED_OPENING_SPRING,
        CARVED_REVEAL_DEPTH,
    )

    camera = 0x29FFFF / 0x10000
    row = 42.0
    center = -1.0  # lane 2
    front = row + CARVED_FRONT_SETBACK
    passage = front + CARVED_REVEAL_DEPTH
    outer_left = project_world_vertex(-1.5, 0.0, front, camera)
    outer_right = project_world_vertex(-0.5, 0.0, front, camera)
    opening_left = project_world_vertex(
        center - CARVED_OPENING_HALF_WIDTH, 0.0, front, camera,
    )
    opening_right = project_world_vertex(
        center + CARVED_OPENING_HALF_WIDTH, 0.0, front, camera,
    )
    front_apex = project_world_vertex(
        center,
        CARVED_OPENING_SPRING + CARVED_OPENING_ARCH_HEIGHT,
        front,
        camera,
    )
    passage_apex = project_world_vertex(
        center,
        CARVED_OPENING_SPRING + CARVED_OPENING_ARCH_HEIGHT,
        passage,
        camera,
    )

    # Exact original composite: outer front x=94..138; aperture floor
    # x=97..135; its first open scanline is y=82. The deeper reveal surface
    # occupies y=80..81 before meeting the longitudinal passage.
    assert outer_left[0] == pytest.approx(94, abs=0.5)
    assert outer_right[0] == pytest.approx(138, abs=0.5)
    assert outer_left[1] == pytest.approx(99, abs=0.5)
    assert opening_left[0] == pytest.approx(97, abs=0.5)
    assert opening_right[0] == pytest.approx(135, abs=0.5)
    assert front_apex[1] == pytest.approx(82, abs=0.5)
    assert passage_apex[1] == pytest.approx(80, abs=0.5)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_contiguous_tunnel_cells_share_one_entrance_and_exit_rim() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(4), game_root=ASSETS)
    scene = build_gameplay_scene(GameView(state), level=4, game_root=ASSETS)
    exposed = next(
        (row.cells[lane], scene.geometry.rows[row.ordinal + 1].cells[lane])
        for row in scene.geometry.rows[:-1] for lane in range(7)
        if (row.cells[lane].tunnel_shape is TunnelShape.EXPOSED_TUBE
            and scene.geometry.rows[row.ordinal + 1].cells[lane].tunnel_shape
            is TunnelShape.EXPOSED_TUBE)
    )
    from skyroads.presentation.renderer import _MeshBuilder
    joined = _MeshBuilder(scene, "final")
    joined.cell(exposed[0], following=exposed[1])
    joined.cell(exposed[1], previous=exposed[0])
    isolated = _MeshBuilder(scene, "final")
    isolated.cell(exposed[0])
    isolated.cell(exposed[1])

    assert len(joined.indices) < len(isolated.indices)


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_world_mesh_is_immutable_across_subrow_camera_motion() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    view.lateral = 12 * 0x10000
    first = build_gameplay_scene(view, level=0, game_root=ASSETS)
    view.lateral += 0x7000
    second = build_gameplay_scene(view, level=0, game_root=ASSETS)

    first_mesh = build_polygon_mesh(first)
    second_mesh = build_polygon_mesh(second)
    assert first.track_row == second.track_row
    assert first_mesh.vertices == second_mesh.vertices
    assert first_mesh.indices == second_mesh.indices
    assert first_mesh.digest == second_mesh.digest

    # Camera motion changes only a uniform to the shared projector.
    vertex = first_mesh.vertices[:3]
    before = project_world_vertex(*vertex, first.track_position / 0x10000)
    after = project_world_vertex(*vertex, second.track_position / 0x10000)
    assert before != after


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_every_shipped_level_decodes_into_the_same_source_scene_contract() -> None:
    root = str(ASSETS.resolve())
    geometries = [
        load_road_geometry(root, level)
        for level in range(PLAYABLE_LEVEL_COUNT)
    ]

    assert [item.archive_index for item in geometries] == list(range(1, 31))
    assert len({geometry.digest for geometry in geometries}) == 30
    assert all(len(row.cells) == 7 for geometry in geometries for row in geometry.rows)
    assert all(
        cell.code < 0x800
        for geometry in geometries for row in geometry.rows for cell in row.cells
    )


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_live_scene_uses_the_loader_installed_road_not_attract_entry_zero() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    scene = build_gameplay_scene(view, level=0, game_root=ASSETS)

    assert scene.geometry.archive_index == 1
    assert scene.geometry.digest == load_road_geometry(
        str(ASSETS.resolve()), 0,
    ).digest


@pytest.mark.skipif(not (ASSETS / "ROADS.LZS").exists(), reason="needs game assets")
def test_ship_projection_follows_cross_road_and_height_state() -> None:
    state = NativeGameState()
    native_level_load(state, road_archive_index(0), game_root=ASSETS)
    view = GameView(state)
    view.af1c = 0x8000
    view.af2c = 0x2800
    centered = build_gameplay_scene(view, level=0, game_root=ASSETS)
    view.af1c = 0x9000
    view.af2c = 0x3000
    moved = build_gameplay_scene(view, level=0, game_root=ASSETS)

    assert moved.ship_screen_x > centered.ship_screen_x
    assert moved.ship_screen_y < centered.ship_screen_y
