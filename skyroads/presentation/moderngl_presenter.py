"""ModernGL polygon presenter for the recovered SkyRoads scene.

pygame owns the window/context; this object owns only disposable GPU resources.
It reads the immutable :class:`~skyroads.presentation.renderer.PolygonFrame`
prepared by ``SkyroadsPresentation`` and cannot advance or mutate simulation.
"""
from __future__ import annotations

from skyroads.presentation.renderer import (
    CALIBRATION, DASHBOARD_TOP, shadow_camera_depth, ship_camera_depth,
)


def mirrored_repeat_coordinate(value: float) -> float:
    """CPU reference for the shader's alternating horizontal reflection."""
    return 1.0 - abs((float(value) % 2.0) - 1.0)


def widescreen_edge_clamp_uv(
    viewport_width: float,
    reference_width: float,
) -> tuple[float, float]:
    """Map a centred reference image over a wider edge-clamped viewport."""
    if viewport_width <= 0 or reference_width <= 0:
        raise ValueError("widescreen dimensions must be positive")
    scale = float(viewport_width) / float(reference_width)
    margin = (float(viewport_width) - float(reference_width)) * 0.5
    return scale, -margin / float(reference_width)


class ModernGLFramePresenter:
    def __init__(self, presentation=None) -> None:
        self._presentation = presentation
        self._moderngl = None
        self._numpy = None
        self._ctx = None
        self._texture_program = None
        self._texture_vao = None
        self._quad_buffer = None
        self._mesh_program = None
        self._billboard_program = None
        self._billboard_vao = None
        self._mesh_vao = None
        self._mesh_vbo = None
        self._mesh_ibo = None
        self._projection_program = None
        self._projection_vao = None
        self._projection_vbo = None
        self._textures = {}
        self._texture_payloads = {}
        self._mesh_digest = None

    def initialize(self, _display) -> None:
        try:
            import moderngl
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(
                "--renderer native-3d requires ModernGL; install the viewer "
                "dependencies with: pypy -m pip install -e '.[viewer]'"
            ) from exc
        self._moderngl = moderngl
        self._numpy = np
        self._create_context()

    def _create_context(self) -> None:
        gl = self._moderngl
        self._ctx = gl.create_context(require=330)
        self._texture_program = self._ctx.program(
            vertex_shader="""
                #version 330
                in vec2 in_position;
                in vec2 in_uv;
                uniform vec2 uv_scale;
                uniform vec2 uv_offset;
                out vec2 uv;
                void main() {
                    gl_Position = vec4(in_position, 0.0, 1.0);
                    uv = in_uv * uv_scale + uv_offset;
                }
            """,
            fragment_shader="""
                #version 330
                uniform sampler2D image;
                uniform bool mirror_repeat_x;
                uniform float color_gain;
                in vec2 uv;
                out vec4 color;
                void main() {
                    float x = uv.x;
                    if (mirror_repeat_x) {
                        // 0..1 normal, 1..2 mirrored, 2..3 normal, including
                        // negative coordinates on the left extension.
                        x = 1.0 - abs(mod(x, 2.0) - 1.0);
                    }
                    color = texture(image, vec2(x, 1.0 - uv.y));
                    color.rgb *= color_gain;
                }
            """,
        )
        vertices = self._numpy.array((
            -1.0, -1.0, 0.0, 0.0,
             1.0, -1.0, 1.0, 0.0,
            -1.0,  1.0, 0.0, 1.0,
             1.0,  1.0, 1.0, 1.0,
        ), dtype="f4")
        self._quad_buffer = self._ctx.buffer(vertices.tobytes())
        self._texture_vao = self._ctx.vertex_array(
            self._texture_program,
            [(self._quad_buffer, "2f 2f", "in_position", "in_uv")],
        )
        self._texture_program["image"] = 0

        self._billboard_program = self._ctx.program(
            vertex_shader="""
                #version 330
                in vec2 in_position;
                in vec2 in_uv;
                uniform float clip_depth;
                out vec2 uv;
                void main() {
                    gl_Position = vec4(in_position, clip_depth, 1.0);
                    uv = in_uv;
                }
            """,
            fragment_shader="""
                #version 330
                uniform sampler2D image;
                uniform float color_gain;
                in vec2 uv;
                out vec4 color;
                void main() {
                    color = texture(image, vec2(uv.x, 1.0 - uv.y));
                    // 33FD's recovered palette-remap shadow is intentionally
                    // translucent.  The former 0.5 cutout threshold discarded
                    // every one of its pixels (max alpha is about 0.42).
                    if (color.a <= 0.0) discard;
                    color.rgb *= color_gain;
                }
            """,
        )
        self._billboard_vao = self._ctx.vertex_array(
            self._billboard_program,
            [(self._quad_buffer, "2f 2f", "in_position", "in_uv")],
        )
        self._billboard_program["image"] = 0

        self._mesh_program = self._ctx.program(
            vertex_shader="""
                #version 330
                in vec3 in_position;
                in vec3 in_color;
                uniform float camera_track;
                uniform float camera_height;
                uniform float horizon_y;
                uniform float lens_gain;
                uniform float near_bias;
                uniform float vanishing_depth;
                uniform float near_clip;
                uniform float x_scale;
                uniform float color_gain;
                out vec3 color;
                void main() {
                    float depth = in_position.z - camera_track;
                    float denominator = max(depth + near_bias, 0.05);
                    float scale = lens_gain
                                * max(vanishing_depth - depth, 0.0)
                                / denominator;
                    float logical_x = 160.0 + in_position.x * scale;
                    float logical_y = horizon_y
                                    + (camera_height - in_position.y) * scale;
                    float clip_z = 2.0 * (depth - near_clip)
                                 / (vanishing_depth - near_clip) - 1.0;
                    gl_Position = vec4(
                        (logical_x / 160.0 - 1.0) * x_scale,
                        1.0 - logical_y / 100.0,
                        clip_z, 1.0
                    );
                    color = in_color * color_gain;
                }
            """,
            fragment_shader="""
                #version 330
                in vec3 color;
                out vec4 out_color;
                void main() { out_color = vec4(color, 1.0); }
            """,
        )

        # Exact recovered 320x200 projection.  Coordinates are already the
        # output of TREKDAT/2D1F; this shader performs only viewport mapping.
        # The DOS 6:5 pixel aspect is represented by the physical 4:3
        # reference width, independently from widescreen expansion.
        self._projection_program = self._ctx.program(
            vertex_shader="""
                #version 330
                in vec2 in_position;
                in vec3 in_color;
                uniform float x_scale;
                uniform float color_gain;
                out vec3 color;
                void main() {
                    float x = (in_position.x / 160.0 - 1.0) * x_scale;
                    float y = 1.0 - in_position.y / 100.0;
                    gl_Position = vec4(x, y, 0.0, 1.0);
                    color = in_color * color_gain;
                }
            """,
            fragment_shader="""
                #version 330
                in vec3 color;
                out vec4 out_color;
                void main() { out_color = vec4(color, 1.0); }
            """,
        )
        self._projection_vbo = self._ctx.buffer(reserve=4)
        self._projection_vao = self._ctx.vertex_array(
            self._projection_program,
            [(self._projection_vbo, "2f 3f", "in_position", "in_color")],
        )

    @staticmethod
    def _gl_viewport(rect, window_height: int) -> tuple[int, int, int, int]:
        return (rect.x, window_height - rect.y - rect.height,
                rect.width, rect.height)

    @staticmethod
    def _presentation_rect(display, widescreen: bool):
        """Return the physical viewport for the DOS 320x200 (4:3) image.

        ``Display.letterbox`` already applies the DOS 6:5 pixel aspect ratio.
        Passing 16:9 to it applied that correction a second time, producing a
        40:27 viewport and stretching every enhanced layer.  Widescreen is an
        expansion of the recovered 4:3 camera, not another logical DOS mode.
        """
        reference = display.letterbox(320, 200)
        if not widescreen:
            return reference
        width, height = display.get_size()
        if width * 3 < height * 4:
            return reference
        return type(reference)(0, 0, width, height)

    def _texture(self, key, size, components, data, *, repeat_x=False):
        existing = self._textures.get(key)
        if existing is None or existing.size != size or existing.components != components:
            if existing is not None:
                existing.release()
            existing = self._ctx.texture(size, components, data)
            existing.filter = (self._moderngl.NEAREST, self._moderngl.NEAREST)
            self._textures[key] = existing
            self._texture_payloads[key] = data
        elif self._texture_payloads.get(key) is not data:
            existing.write(data)
            self._texture_payloads[key] = data
        existing.repeat_x = repeat_x
        existing.repeat_y = False
        return existing

    def _draw_texture(self, texture, viewport, *, uv_scale=(1.0, 1.0),
                      uv_offset=(0.0, 0.0), blend=False,
                      mirror_repeat_x=False, color_gain=1.0) -> None:
        self._ctx.viewport = viewport
        if blend:
            self._ctx.enable(self._moderngl.BLEND)
            self._ctx.blend_func = (
                self._moderngl.SRC_ALPHA, self._moderngl.ONE_MINUS_SRC_ALPHA,
            )
        else:
            self._ctx.disable(self._moderngl.BLEND)
        self._ctx.disable(self._moderngl.DEPTH_TEST)
        self._texture_program["uv_scale"].value = uv_scale
        self._texture_program["uv_offset"].value = uv_offset
        self._texture_program["mirror_repeat_x"].value = bool(mirror_repeat_x)
        self._texture_program["color_gain"].value = float(color_gain)
        texture.use(location=0)
        self._texture_vao.render(self._moderngl.TRIANGLE_STRIP)

    @staticmethod
    def _clip_depth(camera_depth: float) -> float:
        """Map one recovered relative track depth to OpenGL NDC depth."""
        return (2.0 * (camera_depth - CALIBRATION.near_clip)
                / (CALIBRATION.vanishing_depth - CALIBRATION.near_clip) - 1.0)

    def _draw_ship_billboard(self, texture, viewport,
                             camera_depth: float | None,
                             *, color_gain: float = 1.0) -> None:
        """Draw the ship at an exact painter seam or in the debug depth field."""
        self._ctx.viewport = viewport
        if camera_depth is None:
            self._ctx.disable(self._moderngl.DEPTH_TEST)
            clip_depth = 0.0
        else:
            self._ctx.enable(self._moderngl.DEPTH_TEST)
            clip_depth = self._clip_depth(camera_depth)
        self._ctx.enable(self._moderngl.BLEND)
        self._ctx.blend_func = (
            self._moderngl.SRC_ALPHA, self._moderngl.ONE_MINUS_SRC_ALPHA,
        )
        self._billboard_program["clip_depth"].value = clip_depth
        self._billboard_program["color_gain"].value = float(color_gain)
        texture.use(location=0)
        self._billboard_vao.render(self._moderngl.TRIANGLE_STRIP)

    def _draw_recovered_projection(self, vertices, viewport,
                                   reference_width: float,
                                   *, color_gain: float = 1.0) -> None:
        if not vertices:
            return
        data = self._numpy.asarray(vertices, dtype="f4")
        self._projection_vbo.orphan(data.nbytes)
        self._projection_vbo.write(data.tobytes())
        self._ctx.viewport = viewport
        self._ctx.disable(self._moderngl.DEPTH_TEST)
        self._ctx.disable(self._moderngl.BLEND)
        self._projection_program["x_scale"].value = (
            reference_width / viewport[2]
        )
        self._projection_program["color_gain"].value = float(color_gain)
        # The VAO was created over a tiny reusable buffer; ModernGL caches its
        # inferred vertex count at creation time and does not recompute it
        # after ``orphan`` grows the buffer.  Supply the exact count or the
        # recovered stream silently renders zero vertices.
        self._projection_vao.render(
            self._moderngl.TRIANGLES, vertices=len(vertices) // 5,
        )

    def _upload_mesh(self, mesh) -> None:
        if self._mesh_digest == mesh.digest:
            return
        if self._mesh_vao is not None:
            self._mesh_vao.release()
        if self._mesh_vbo is not None:
            self._mesh_vbo.release()
        if self._mesh_ibo is not None:
            self._mesh_ibo.release()
        vertices = self._numpy.asarray(mesh.vertices, dtype="f4")
        indices = self._numpy.asarray(mesh.indices, dtype="u4")
        self._mesh_vbo = self._ctx.buffer(vertices.tobytes())
        self._mesh_ibo = self._ctx.buffer(indices.tobytes())
        self._mesh_vao = self._ctx.vertex_array(
            self._mesh_program,
            [(self._mesh_vbo, "3f 3f", "in_position", "in_color")],
            index_buffer=self._mesh_ibo,
            index_element_size=4,
        )
        self._mesh_digest = mesh.digest

    def _present_reference(self, rgb, display) -> None:
        frame = self._numpy.asarray(rgb, dtype=self._numpy.uint8)
        height, width = frame.shape[:2]
        texture = self._texture("reference", (width, height), 3, frame.tobytes())
        rect = display.letterbox(width, height)
        display.set_presented_rect(rect)
        self._draw_texture(texture, self._gl_viewport(rect, display.get_size()[1]))

    @staticmethod
    def _reference_only(packet) -> bool:
        """Whether this host frame intentionally presents the DOS oracle.

        The oracle is a separate diagnostic mode, never a layer in the native
        composition. Keeping this decision explicit prevents a future
        fidelity experiment from silently covering the enhanced frame again.
        """
        return packet is None or packet.debug_mode == "original"

    def present(self, rgb, display) -> None:
        packet = (None if self._presentation is None
                  else self._presentation.polygon_frame)
        if self._reference_only(packet):
            self._ctx.screen.use()
            self._ctx.clear(0.0, 0.0, 0.0, 1.0)
            self._present_reference(rgb, display)
            return

        window_width, window_height = display.get_size()
        rect = self._presentation_rect(display, packet.widescreen)
        display.set_presented_rect(rect)
        self._ctx.screen.use()
        self._ctx.clear(0.0, 0.0, 0.0, 1.0)

        assets = packet.scene.assets
        background = self._texture(
            ("background", assets.digest),
            (assets.background_width, assets.background_height), 3,
            packet.background_rgb,
        )
        whole = self._gl_viewport(rect, window_height)
        background_height = max(1, round(rect.height * 138 / 200))
        background_rect = type(rect)(rect.x, rect.y, rect.width, background_height)
        # WORLD graphics use the same 6:5 DOS pixel aspect as the framebuffer.
        source_aspect = assets.background_width / (
            assets.background_height * 1.2
        )
        target_aspect = rect.width / background_height
        scale_x = target_aspect / source_aspect
        reference_width = min(rect.width, rect.height * 4.0 / 3.0)
        has_widescreen_extension = (
            packet.widescreen and rect.width > round(reference_width)
        )
        self._draw_texture(
            background, self._gl_viewport(background_rect, window_height),
            uv_scale=(scale_x, 1.0), uv_offset=((1.0 - scale_x) * 0.5, 0.0),
            mirror_repeat_x=has_widescreen_extension,
            color_gain=packet.palette_gain,
        )

        scene = packet.scene
        recovered_projection = packet.debug_mode == "exact-projection"
        if recovered_projection:
            self._draw_recovered_projection(
                packet.projection_before_ship, whole, reference_width,
                color_gain=packet.palette_gain,
            )
        else:
            # Final and diagnostic views share one immutable world mesh and
            # one recovered continuous projector.  Debug policy changes color
            # or wireframe only; it cannot change vertex placement.
            self._ctx.viewport = whole
            self._ctx.enable(self._moderngl.DEPTH_TEST)
            self._ctx.disable(self._moderngl.BLEND)
            self._ctx.wireframe = packet.debug_mode == "wireframe"
            self._mesh_program["camera_track"].value = (
                scene.track_position / 65536.0
            )
            self._mesh_program["camera_height"].value = CALIBRATION.camera_height
            self._mesh_program["horizon_y"].value = CALIBRATION.horizon_y
            self._mesh_program["lens_gain"].value = CALIBRATION.lens_gain
            self._mesh_program["near_bias"].value = CALIBRATION.near_bias
            self._mesh_program["vanishing_depth"].value = (
                CALIBRATION.vanishing_depth
            )
            self._mesh_program["near_clip"].value = CALIBRATION.near_clip
            self._mesh_program["x_scale"].value = (
                reference_width / whole[2]
            )
            self._mesh_program["color_gain"].value = packet.palette_gain
            if packet.mesh.indices:
                self._upload_mesh(packet.mesh)
                self._mesh_vao.render(self._moderngl.TRIANGLES)
            self._ctx.wireframe = False

        y_unit = rect.height / 200.0
        x_unit = rect.height / 240.0
        reference_width = 320.0 * x_unit
        reference_left = rect.x + (rect.width - reference_width) * 0.5

        if packet.shadow_rgba:
            shadow = self._texture(
                "ship-shadow", (packet.shadow_width, packet.shadow_height), 4,
                packet.shadow_rgba,
            )
            shadow_rect = type(rect)(
                round(reference_left + packet.shadow_x * x_unit),
                round(rect.y + packet.shadow_y * y_unit),
                max(1, round(packet.shadow_width * x_unit)),
                max(1, round(packet.shadow_height * y_unit)),
            )
            self._draw_ship_billboard(
                shadow, self._gl_viewport(shadow_rect, window_height),
                # 33FD supplies exact pixel admission, while 2D1F's painter
                # order lets subsequent near tunnel faces cover those pixels.
                # The continuous renderer expresses that same second rule in
                # its shared depth field; otherwise a screen-space overlay
                # incorrectly paints the shadow over tunnel shells.
                None if recovered_projection else shadow_camera_depth(scene),
                color_gain=packet.palette_gain,
            )

        if packet.ship_rgba:
            ship = self._texture(
                "ship", (packet.ship_width, packet.ship_height), 4,
                packet.ship_rgba,
            )
            # Preserve the original 320x200 projection inside the expanded
            # widescreen view.  Extra width reveals background/terrain; it
            # must not stretch or recenter the ship independently of AF1C.
            ship_w = max(1, round(packet.ship_width * x_unit))
            ship_h = max(1, round(packet.ship_height * y_unit))
            ship_rect = type(rect)(
                round(reference_left + packet.ship_x * x_unit),
                round(rect.y + packet.ship_y * y_unit),
                ship_w, ship_h,
            )
            self._draw_ship_billboard(
                ship, self._gl_viewport(ship_rect, window_height),
                None if recovered_projection else ship_camera_depth(scene),
                color_gain=packet.palette_gain,
            )

        if recovered_projection:
            # 2D1F resumes after the 325B ship seam; tunnel rims and near
            # terrain therefore occlude the sprite in the exact original
            # painter order without a guessed depth plane.
            self._draw_recovered_projection(
                packet.projection_after_ship, whole, reference_width,
                color_gain=packet.palette_gain,
            )

        dashboard = packet.dashboard_rgba
        dash_h, dash_w = dashboard.shape[:2]
        dash = self._texture("dashboard", (dash_w, dash_h), 4, dashboard)
        dashboard_y = max(1, round(rect.height * DASHBOARD_TOP / 200))
        target_dash_h = max(1, rect.height - dashboard_y)
        # Preserve the exact recovered 4:3 dashboard in the centre. Widescreen
        # is a separate enhancement: clamp UV outside that centre so the
        # outermost HUD columns extend to the window edges without stretching
        # any original instrument geometry.
        core_dash_w = min(rect.width, round(rect.height * 4 / 3))
        target_dash_w = rect.width if has_widescreen_extension else core_dash_w
        dash_rect = type(rect)(
            rect.x + (rect.width - target_dash_w) // 2,
            rect.y + rect.height - target_dash_h,
            target_dash_w, target_dash_h,
        )
        dash_uv_scale = (1.0, 1.0)
        dash_uv_offset = (0.0, 0.0)
        if has_widescreen_extension:
            scale, offset = widescreen_edge_clamp_uv(
                target_dash_w, core_dash_w,
            )
            dash_uv_scale = (scale, 1.0)
            dash_uv_offset = (offset, 0.0)
        self._draw_texture(
            dash, self._gl_viewport(dash_rect, window_height), blend=True,
            uv_scale=dash_uv_scale, uv_offset=dash_uv_offset,
            color_gain=packet.palette_gain,
        )
        self._ctx.viewport = (0, 0, window_width, window_height)

    def capture_rgb(self, display):
        """Read the actually presented OpenGL image for screenshots/tests."""
        width, height = display.get_size()
        raw = self._ctx.screen.read(
            viewport=(0, 0, width, height), components=3, alignment=1,
        )
        image = self._numpy.frombuffer(raw, dtype=self._numpy.uint8).reshape(
            height, width, 3,
        )
        return self._numpy.ascontiguousarray(image[::-1])

    def resize(self, _display) -> None:
        # pygame may have replaced the OpenGL context in Display.resize.
        # Releasing handles through the replacement context is undefined;
        # the old context owns and destroys them. Forget the stale Python
        # wrappers and build all resources against the current window context.
        self._textures.clear()
        self._texture_payloads.clear()
        for name in (
            "_mesh_vao", "_mesh_vbo", "_mesh_ibo", "_texture_vao",
            "_billboard_vao", "_quad_buffer", "_mesh_program",
            "_billboard_program", "_texture_program", "_projection_program",
            "_projection_vao", "_projection_vbo",
        ):
            setattr(self, name, None)
        self._mesh_digest = None
        self._ctx = None
        self._create_context()

    def close(self) -> None:
        for texture in self._textures.values():
            texture.release()
        self._textures.clear()
        self._texture_payloads.clear()
        for name in (
            "_mesh_vao", "_mesh_vbo", "_mesh_ibo", "_texture_vao",
            "_billboard_vao", "_quad_buffer", "_mesh_program",
            "_billboard_program", "_texture_program", "_projection_program",
            "_projection_vao", "_projection_vbo",
        ):
            value = getattr(self, name)
            if value is not None:
                value.release()
            setattr(self, name, None)
        self._mesh_digest = None
        self._ctx = None
