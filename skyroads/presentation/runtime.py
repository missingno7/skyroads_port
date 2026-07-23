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
    load_presentation_assets,
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
        self._prepared_visual_key = None
        self._prepared_frame: PolygonFrame | None = None
        self._native_placeholder = None
        self._prewarmed_levels: set[int] = set()
        if self._renderer is not None:
            # Immutable asset decoding, TREKDAT expansion and numpy import are
            # startup/menu work.  Deferring them until the first audible
            # gameplay frame caused a reproducible 0.4-0.7 s main-thread stall.
            self._prewarm_selected_level()

    # Recovered gameplay presentation heads.  Palette routine 4331 parks at
    # 434A for every screen in the program, so 434A alone is deliberately not
    # an ownership identity.  Its caller's caller is the stable distinction:
    # 2B3D's 2C5B/2CBE continuations fade the loaded gameplay frame, whereas
    # 5180's 5295/5377 continuations fade the level selector.
    _PRESENTATION_HEADS = {
        0x22F8: "gameplay",
        0x0EF8: "gameplay-exit",
        0x4468: "gameplay-exit-wait",
    }
    _GAMEPLAY_FADE_RETURNS = {
        0x2C5B: "gameplay-start-fade",
        0x2CBE: "gameplay-exit-fade",
    }
    _SELECTOR_FADE_RETURNS = {
        0x5295: "selector-fade-in",
        0x5377: "selector-fade-out",
    }
    _SHELL_PRESENTATION_HEADS = {
        0x5FED: "level-selector-input",
    }

    def _active_gameplay_island(self) -> bool:
        dispatcher = getattr(self.runtime, "execution_regions", None)
        return bool(dispatcher is not None and dispatcher.active_region_id == GAMEPLAY_REGION)

    def _prewarm_selected_level(self, view: GameView | None = None) -> None:
        """Materialize immutable level presentation data before gameplay.

        The generated selector publishes DS:[9332] while the user navigates,
        well before 2B3D enters the gameplay fade.  Caching that selection here
        keeps decompression/import work out of the simulation and audio window.
        Failure remains lazy and explicit at actual acquisition; menu states
        whose selected-level word is not yet initialized are simply ignored.
        """
        cpu = self.runtime.cpu
        if view is None:
            view = GameView(
                cpu.mem.data, base=(int(cpu.s.ds) & 0xFFFF) << 4,
            )
        try:
            level = int(view.rw(0x9332))
            if level in self._prewarmed_levels:
                return
            geometry = load_road_geometry(str(self.args.game_root), level)
            assets = load_presentation_assets(str(self.args.game_root), level)
            forward = tuple(
                int(view._backend.rb(0x0352 + index * 4))
                for index in range(256)
            )
            backward = tuple(
                int(view._backend.rb(0x0353 + index * 4))
                for index in range(256)
            )
            if not any(forward[1:74]) or not any(backward[1:74]):
                from skyroads.handrecovered.rle_sprite import (
                    RECOVERED_FILL_BACKWARD,
                    RECOVERED_FILL_FORWARD,
                )
                if not any(forward[1:74]):
                    forward = RECOVERED_FILL_FORWARD
                if not any(backward[1:74]):
                    backward = RECOVERED_FILL_BACKWARD
            self._renderer.prewarm_level(
                geometry, assets, forward, backward,
            )
        except (OSError, ValueError):
            return
        self._prewarmed_levels.add(level)

    def prewarm_current(self) -> None:
        """Warm disposable presentation caches after continuation restore."""
        if self._renderer is None:
            return
        cpu = self.runtime.cpu
        view = GameView(
            cpu.mem.data, base=(int(cpu.s.ds) & 0xFFFF) << 4,
        )
        self._prewarm_selected_level(view)
        if self._observe_ownership(view):
            # A replay may begin inside gameplay rather than pass through the
            # selector. Decode its first dashboard/ship/shadow packet while
            # restoring, not inside replay point 1's presentation deadline.
            scene = self._scene(view)
            self._renderer.prepare(scene)

    def _fade_outer_return(self) -> int | None:
        """Return 4B8E's recovered caller identity while parked in 4331.

        Both frame parking and replay snapshots preserve SS:BP and the guest
        stack.  Unlike a host-only region-exit diagnostic, this identity can
        therefore be reconstructed after restoring an arbitrary cached point.
        """
        cpu = self.runtime.cpu
        if ((int(cpu.s.cs) & 0xFFFF) != 0x1010
                or (int(cpu.s.ip) & 0xFFFF) != 0x434A):
            return None
        try:
            ss = int(cpu.s.ss) & 0xFFFF
            fade_bp = int(cpu.s.bp) & 0xFFFF
            wrapper_bp = int(cpu.mem.rw(ss, fade_bp)) & 0xFFFF
            return int(cpu.mem.rw(ss, (wrapper_bp + 2) & 0xFFFF)) & 0xFFFF
        except (AttributeError, IndexError, TypeError, ValueError):
            # Small adapter tests and non-x86 semantic carriers may not expose
            # a guest stack. They can still use explicit region identities.
            return None

    def _release_gameplay(self, reason: str) -> None:
        """Atomically hand presentation back to the generated shell."""
        self._owns_gameplay = False
        self._owned_level = None
        self._ownership_phase = reason
        self._geometry = None
        self.previous = self.current = None
        self._scene_source_key = None
        self._source_scene = None
        self._prepared_visual_key = None
        self._prepared_frame = None
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
        fade_return = self._fade_outer_return()
        if fade_return in self._GAMEPLAY_FADE_RETURNS:
            phase = self._GAMEPLAY_FADE_RETURNS[fade_return]
        selector_phase = self._SELECTOR_FADE_RETURNS.get(fade_return)
        shell_phase = (
            self._SHELL_PRESENTATION_HEADS.get(ip)
            if cs == 0x1010 else None
        )
        selected = int(view.rw(0x9332))
        palette = getattr(self.runtime.dos, "vga_palette", ())
        palette_peak = max(
            (max(int(channel) for channel in color[:3]) for color in palette),
            default=0,
        )
        active_gameplay_island = self._active_gameplay_island()
        region_exit = getattr(self.runtime, "_skyroads_last_region_exit", None)
        if selector_phase is not None or shell_phase is not None:
            # 5180 has already composed the selector before its fade-in call.
            # Release at that recovered call identity while the palette is
            # black, not one frame later and not based on framebuffer pixels.
            reason = selector_phase or shell_phase
            if self._owns_gameplay:
                self._release_gameplay(reason)
            else:
                self._ownership_phase = reason
            return False
        if self._owns_gameplay and region_exit and not active_gameplay_island:
            if phase is None:
                # The generated caller has left every recovered gameplay
                # presentation head.  It can retain DS:[9332] after replacing
                # the framebuffer and palette, so level identity alone cannot
                # identify this external-shell seam.
                self._release_gameplay(f"region-exit:{region_exit}")
                return False
            # A region exit is a persistent diagnostic, not an edge-triggered
            # presentation command.  Crash/restart, departure, and abort all
            # return through generated gameplay-owned fade/wait heads before
            # the level selector actually takes the screen. Releasing here
            # used to make every subsequent frame reacquire from valid road
            # geometry and then release from the same stale exit marker,
            # alternating native/original renderers throughout the fade.
            self._ownership_phase = phase
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
            # The selector fade publishes the next selected-level word before
            # it finishes. Building a complete resident mesh at that point
            # creates a deterministic transition stall. 5FED is the recovered
            # stable selector-input boundary and gives this disposable work a
            # deadline-free menu phase.
            if self._ownership_phase == "level-selector-input":
                self._prewarm_selected_level(view)
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
        visual_key = _scene_state_key(visual)
        if visual_key != self._prepared_visual_key or self._prepared_frame is None:
            self._prepared_frame = self._renderer.prepare(visual)
            self._prepared_visual_key = visual_key
        self.polygon_frame = self._prepared_frame
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
