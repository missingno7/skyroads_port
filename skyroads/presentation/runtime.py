"""Canonical-player integration for optional native SkyRoads presentation."""
from __future__ import annotations

from dataclasses import dataclass

from skyroads.bridge.dgroup_view import GameView
from skyroads.identities import GAMEPLAY_REGION
from skyroads.presentation.renderer import PolygonFrame, RecoveredPolygonRenderer
from skyroads.presentation.scene import (
    GameplayScene,
    build_gameplay_scene,
    build_precomposed_level_start_scene,
    interpolate_scene,
    is_precomposed_level_start,
    load_road_geometry,
    project_live_road_geometry,
)


def _scene_state_key(scene: GameplayScene):
    """Compact exact key for changes visible to presentation.

    Gameplay ticks alone are insufficient: palette fades and both carrier
    handoffs intentionally occur without advancing the gameplay clock.
    Geometry and assets are immutable project assets, so their stable digests
    stand in for their large payloads here.
    """
    return (
        scene.tick,
        scene.level,
        scene.track_position,
        scene.lateral_position,
        scene.height,
        scene.forward_velocity,
        scene.vertical_velocity,
        scene.game_state,
        scene.ship_sprite_index,
        scene.ship_screen_x,
        scene.ship_screen_y,
        scene.palette,
        scene.face_palette_forward,
        scene.face_palette_backward,
        scene.dashboard,
        scene.shadow,
        scene.geometry.digest,
        scene.assets.digest,
    )


@dataclass(frozen=True)
class PresentationDiagnostics:
    renderer: str
    widescreen: bool
    tweening: bool
    simulation_hz: int
    presentation_hz: int
    active: bool
    execution_region_active: bool
    ownership_phase: str
    last_semantic_tick: int | None
    debug_view: str
    world_vertices: int
    world_triangles: int
    world_digest: str
    projection_draw_calls: int
    projection_spans: int
    projection_phase: int | None
    projection_digest: str
    visible_rows: str
    level: int | None
    road_archive_index: int | None
    ship_screen: str
    ship_sprite_index: int | None


class SkyroadsPresentation:
    """Read-only presentation owner installed by the one execution plan."""

    def __init__(self, runtime, args) -> None:
        self.runtime = runtime
        self.args = args
        self.renderer_name = str(args.renderer)
        self.widescreen = bool(args.widescreen)
        self.tweening = bool(args.tweening)
        self.debug_view = str(getattr(args, "render_debug", "final"))
        self._renderer = (
            RecoveredPolygonRenderer(
                debug_mode=self.debug_view,
                widescreen=self.widescreen,
            )
            if self.renderer_name == "native-3d" else None
        )
        self.previous: GameplayScene | None = None
        self.current: GameplayScene | None = None
        self.polygon_frame: PolygonFrame | None = None
        self._last_mesh = None
        self._last_projection = None
        self._geometry = None
        self._owns_gameplay = False
        self._owned_level: int | None = None
        self._ownership_phase = "outside-gameplay"
        self._scene_source_key = None
        self._source_scene: GameplayScene | None = None
        self._native_placeholder = None

    # Recovered gameplay presentation heads.  434A owns palette fades around
    # the loaded road; 22F8 is the native gameplay seam; 0EF8/4468 are the
    # generated finish/departure continuation observed in the replay corpus.
    _PRESENTATION_HEADS = {
        0x434A: "gameplay-fade",
        0x22F8: "gameplay",
        0x0EF8: "gameplay-exit",
        0x4468: "gameplay-exit-wait",
    }

    def _active_gameplay_island(self) -> bool:
        dispatcher = getattr(self.runtime, "execution_regions", None)
        return bool(dispatcher is not None and dispatcher.active_region_id == GAMEPLAY_REGION)

    def _release_gameplay(self, reason: str) -> None:
        """Atomically hand presentation back to the generated shell."""
        self._owns_gameplay = False
        self._owned_level = None
        self._ownership_phase = reason
        self._geometry = None
        self.previous = self.current = None
        self._scene_source_key = None
        self._source_scene = None
        # Clear the GPU packet at the state seam itself, not one caller later.
        # A menu palette must never be applied to a retained gameplay packet.
        self.polygon_frame = None

    def _observe_ownership(self, view: GameView):
        """Resolve presentation ownership from recovered state, not pixels.

        A selected level may already be fully loaded while execution is still
        in the generated fade head.  Conversely, after the fade reaches black
        the selector changes before stale road memory is replaced.  Exact road
        identity is therefore the safe acquisition token.  A changed selector
        releases a stale token only while black, but a completed native region
        is an independent, explicit release seam: its generated continuation
        may keep the same selected level while it prepares the selector/menu.
        """
        cpu = self.runtime.cpu
        cs = int(cpu.s.cs) & 0xFFFF
        ip = int(cpu.s.ip) & 0xFFFF
        phase = self._PRESENTATION_HEADS.get(ip) if cs == 0x1010 else None
        selected = int(view.rw(0x9332))
        palette = getattr(self.runtime.dos, "vga_palette", ())
        palette_peak = max(
            (max(int(channel) for channel in color[:3]) for color in palette),
            default=0,
        )
        active_gameplay_island = self._active_gameplay_island()
        region_exit = getattr(self.runtime, "_skyroads_last_region_exit", None)
        if self._owns_gameplay and region_exit and not active_gameplay_island:
            # The gameplay region has returned to generated code.  The caller
            # can retain DS:[9332] until after it has replaced the framebuffer
            # and palette, so level identity alone cannot distinguish this
            # transition from active gameplay.  Do not let the last native GPU
            # packet survive into that generated continuation.
            self._release_gameplay(f"region-exit:{region_exit}")
            return False
        if (self._owns_gameplay
                and self._owned_level is not None
                and selected != self._owned_level
                and palette_peak == 0):
            # The original replaces A000 with the selector while black, then
            # publishes the newly selected level.  Level identity is the
            # semantic handoff token; do this before considering whether some
            # subsequently loaded road happens to match the new selection.
            self._release_gameplay("black-handoff")
            return False
        if self._owns_gameplay and selected == self._owned_level:
            if phase is not None:
                self._ownership_phase = phase
            elif active_gameplay_island:
                self._ownership_phase = "gameplay-region"
            return True

        # Road bytes are immutable for the lifetime of a loaded level.  Only
        # reconstruct and validate them while acquiring ownership; doing this
        # on every 60/120 Hz presentation frame was pure duplicate work.
        try:
            live = project_live_road_geometry(view, level=selected)
            expected = load_road_geometry(str(self.args.game_root), selected)
            matches = live.digest == expected.digest
        except (OSError, ValueError):
            live = None
            matches = False
        if active_gameplay_island or (phase is not None and matches):
            self._owns_gameplay = True
            self._owned_level = selected
            self._ownership_phase = phase or "gameplay-region"
            self._geometry = live
        elif self._owns_gameplay and phase is not None:
            if not matches and palette_peak == 0:
                self._release_gameplay("black-handoff")
            else:
                self._ownership_phase = phase
        elif not self._owns_gameplay:
            self._ownership_phase = "outside-gameplay"
        return self._owns_gameplay

    def _scene(self, view: GameView | None = None) -> GameplayScene:
        cpu = self.runtime.cpu
        if view is None:
            view = GameView(cpu.mem.data, base=(cpu.s.ds & 0xFFFF) << 4)
        # DS:9332 is the original selected-level authority and becomes valid
        # before the native execution region is entered.
        level = int(view.rw(0x9332))
        if self._geometry is None or self._geometry.level != level:
            self._geometry = project_live_road_geometry(view, level=level)
        builder = (
            build_precomposed_level_start_scene
            if is_precomposed_level_start(view)
            else build_gameplay_scene
        )
        return builder(
            view,
            level=level,
            game_root=self.args.game_root,
            geometry=self._geometry,
            device_palette=self.runtime.dos.vga_palette,
        )

    def frame(self, fallback, *, interpolation: float):
        """Present one frame through exactly one authoritative renderer."""
        if self._renderer is None:
            self.polygon_frame = None
            return fallback()
        cpu = self.runtime.cpu
        view = GameView(cpu.mem.data, base=(cpu.s.ds & 0xFFFF) << 4)
        if not self._observe_ownership(view):
            self.polygon_frame = None
            return fallback()
        # A host presentation frame can run several times between fixed
        # semantic ticks.  CPU progress plus the recovered gameplay tick form
        # a cheap generation token; fades advance CPU progress even though the
        # gameplay tick intentionally remains fixed.
        source_key = (
            int(cpu.instruction_count), int(view.elapsed_ticks),
            int(cpu.s.cs) & 0xFFFF, int(cpu.s.ip) & 0xFFFF,
            int(view.rw(0x9332)),
        )
        if source_key != self._scene_source_key or self._source_scene is None:
            self._source_scene = self._scene(view)
            self._scene_source_key = source_key
        scene = self._source_scene
        if (self.current is None
                or _scene_state_key(scene) != _scene_state_key(self.current)):
            self.previous, self.current = self.current, scene
        visual = self.current
        if self.tweening and self.previous is not None:
            visual = interpolate_scene(self.previous, self.current, interpolation)
        self.polygon_frame = self._renderer.prepare(visual)
        self._last_mesh = self.polygon_frame.mesh
        self._last_projection = self.polygon_frame.projection_trace
        # The explicitly selected oracle diagnostic is the one permitted
        # gameplay path that asks for VGA pixels. It remains a separate whole
        # frame, never a layer in the native composition.
        if self.debug_view == "original":
            return fallback()
        # The GPU presenter consumes ``polygon_frame``.  Do not decode or pass
        # through the original VGA frame while native gameplay owns output.
        if self._native_placeholder is None:
            import numpy as np
            self._native_placeholder = np.zeros((200, 320, 3), dtype=np.uint8)
        return self._native_placeholder

    def diagnostics(self) -> PresentationDiagnostics:
        mesh = (self.polygon_frame.mesh if self.polygon_frame is not None
                else self._last_mesh)
        projection = (
            self.polygon_frame.projection_trace
            if self.polygon_frame is not None else self._last_projection
        )
        return PresentationDiagnostics(
            renderer=self.renderer_name,
            widescreen=self.widescreen,
            tweening=self.tweening,
            simulation_hz=int(self.args.simulation_hz),
            presentation_hz=int(self.args.present_hz),
            active=self._owns_gameplay,
            execution_region_active=self._active_gameplay_island(),
            ownership_phase=self._ownership_phase,
            last_semantic_tick=(None if self.current is None else self.current.tick),
            debug_view=self.debug_view,
            world_vertices=0 if mesh is None else mesh.vertex_count,
            world_triangles=0 if mesh is None else mesh.triangle_count,
            world_digest=("none" if mesh is None else mesh.digest[:12]),
            projection_draw_calls=(
                0 if projection is None else len(projection.primitives)
            ),
            projection_spans=(
                0 if projection is None
                else sum(len(item.spans) for item in projection.primitives)
            ),
            projection_phase=None if projection is None else projection.phase,
            projection_digest=(
                "none" if projection is None else projection.digest[:12]
            ),
            visible_rows=("none" if mesh is None or mesh.first_row < 0
                          else f"{mesh.first_row}..{mesh.last_row}"),
            level=None if self.current is None else self.current.level,
            road_archive_index=(
                None if self.current is None
                else self.current.geometry.archive_index
            ),
            ship_screen=(
                "none" if self.current is None
                else f"{self.current.ship_screen_x},{self.current.ship_screen_y}"
            ),
            ship_sprite_index=(
                None if self.current is None
                else self.current.ship_sprite_index
            ),
        )


def install_presentation(runtime, args) -> None:
    runtime._skyroads_presentation = SkyroadsPresentation(runtime, args)
