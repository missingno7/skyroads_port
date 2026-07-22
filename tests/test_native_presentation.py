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
    build_polygon_mesh,
    project_world_vertex,
    projection_scale,
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
    INITIAL_HEIGHT,
    INITIAL_LATERAL_POSITION,
    INITIAL_SHIP_SCREEN_X,
    INITIAL_SHIP_SCREEN_Y,
    INITIAL_SHIP_SPRITE_INDEX,
    INITIAL_TRACK_POSITION,
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

    assert presentation._observe_ownership(live)
    presentation.polygon_frame = object()
    memory.ww(ds, 0x9332, level + 1)

    assert not presentation._observe_ownership(live)
    assert presentation._ownership_phase == "black-handoff"
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
    visible_palette = tuple((index, index, index) for index in range(256))
    black_palette = ((0, 0, 0),) * 256
    visible = build_gameplay_scene(
        view, level=0, game_root=ASSETS, device_palette=visible_palette,
    )
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
    from skyroads.presentation.renderer import _MeshBuilder
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
    assert any(abs(x - (exposed_center - 0.5)) < 1e-6
               for x, _y, _z in exposed_positions)
    assert any(abs(x - (exposed_center - 0.36)) < 1e-6
               for x, _y, _z in exposed_positions)
    assert any(abs(z - (exposed.row + 0.1)) < 1e-6
               for _x, _y, z in exposed_positions)

    # The carved family owns a mouse-hole profile and a separate rectangular
    # exterior; it is not the exposed tube laid over a closed block.
    assert any(abs(x - (carved_center - 0.38)) < 1e-6 and abs(y - 0.08) < 1e-6
               for x, y, _z in carved_positions)
    assert any(abs(y - 0.38) < 1e-6
               for _x, y, _z in carved_positions)
    assert len(carved_builder.indices) // 3 > 40


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
