"""Canonical-player adapter for the authored SkyRoads gameplay island.

The generated provider reaches the original gameplay loop at ``1010:2317``.
At that stable point this adapter transfers ownership to the recovered native
gameplay driver for successive semantic ticks.  State remains authoritative in
the same DOS memory image. Terminal outcomes reproduce the original ``1FD9``
epilogue and return to its generated caller at ``1010:2C61``.

Generated device services remain outside the gameplay island.  A narrow
carrier adapter invokes the already-selected generated ``03C2`` sound effect
provider while preserving the island's CPU-independent semantic body.
"""
from __future__ import annotations

from dos_re.execution import ResolvedExecutionRegion
from dos_re.lift.runtime import emulate_call
from dos_re.regions import RegionProgress, ensure_region_dispatcher

from skyroads.bridge.dgroup_view import GameView
from skyroads.handrecovered.controls import decode_attract, decode_keyboard
from skyroads.identities import CODE_SEG
from skyroads.native.dashboard import (
    DASHBOARD_BEZEL_OVERLAP,
    SEG_DASHBRD,
    paint_dashboard,
)
from skyroads.native.frame import render_native_frame
from skyroads.native.hud import draw_grav_meter, update_hud, update_progress_bar
from skyroads.native.image import NativeGameImage
from skyroads.native.loop import GameplayScratch, NativeGameplayDriver
from skyroads.handrecovered.dynamics import JumpScratch


GAMEPLAY_ENTRY_IP = 0x2317
GAMEPLAY_CALLER_IP = 0x2C61
GAMEPLAY_TICK_BOUNDARY = "skyroads:main-loop-or-input-boundary:v1"
GAMEPLAY_ENTRY_ID = "start-level"
LEVEL_COMPLETED_EXIT = "level-completed"
WALL_CRASH_EXIT = "wall-crash"
FUEL_EXPIRED_EXIT = "fuel-expired"
OXYGEN_EXPIRED_EXIT = "oxygen-expired"
FELL_OFF_ROAD_EXIT = "fell-off-road"
GAMEPLAY_ABORTED_EXIT = "gameplay-aborted"

_SFX_FUNCTION_IP = 0x03C2
_FALL_HANDLER_IP = 0x0F05
_FALL_HANDLER_RETURN_IP = 0x241E
_ESCAPE_KEY_OFFSET = 0x0BDA
_KEY_DOWN_BIT = 0x80
_REGISTER_FIELDS = (
    "ax", "bx", "cx", "dx", "sp", "bp", "si", "di",
    "cs", "ds", "es", "ss", "ip", "flags", "fsw", "fcw",
)

_TRANSITION_EXITS = {
    "finish": LEVEL_COMPLETED_EXIT,
    "crash": WALL_CRASH_EXIT,
    "timeout_fuel": FUEL_EXPIRED_EXIT,
    "timeout_oxygen": OXYGEN_EXPIRED_EXIT,
    "fall": FELL_OFF_ROAD_EXIT,
}


def _stack_word(cpu, distance: int) -> int:
    return cpu.mem.rw(cpu.s.ss, (cpu.s.bp - distance) & 0xFFFF)


def _capture_scratch(cpu) -> GameplayScratch:
    return GameplayScratch(
        jump=JumpScratch(
            _stack_word(cpu, 8),
            _stack_word(cpu, 10),
            _stack_word(cpu, 6),
        ),
        bp12=_stack_word(cpu, 12),
        bp14=_stack_word(cpu, 14),
        bp24=_stack_word(cpu, 24),
        tgt_af2c=_stack_word(cpu, 28),
    )


def _materialize_scratch(cpu, scratch: GameplayScratch) -> None:
    """Mirror native session locals into the original gameplay stack frame.

    ReplayArtifact continuation state already owns this stack memory.  Keeping
    it current makes every native tick boundary independently restorable: a
    fresh session can recover its only non-DGROUP state with
    :func:`_capture_scratch`, without a backend-private snapshot side channel.
    """
    values = {
        8: scratch.jump.jumping,
        10: scratch.jump.jump_start_y,
        6: scratch.jump.effect_latch,
        12: scratch.bp12,
        14: scratch.bp14,
        24: scratch.bp24,
        28: scratch.tgt_af2c,
    }
    for distance, value in values.items():
        cpu.mem.ww(
            cpu.s.ss,
            (cpu.s.bp - distance) & 0xFFFF,
            int(value) & 0xFFFF,
        )


class _GeneratedGameplayServices:
    """True external seams from native gameplay to the generated carrier."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.cpu = runtime.cpu
        hooks = getattr(self.cpu, "replacement_hooks", None)
        if hooks is not None and (CODE_SEG, _SFX_FUNCTION_IP) not in hooks:
            raise RuntimeError(
                "the gameplay region requires the selected generated 1010:03C2 "
                "sound-effect carrier"
            )

    def emit_sfx(self, effect_id: int) -> None:
        hooks = getattr(self.cpu, "replacement_hooks", None)
        if hooks is None:
            # Minimal state-only test runtimes have no CPU dispatcher.  The
            # semantic layer has already applied 03C2's authoritative AF38
            # timestamp before invoking this presentation/device seam.
            return
        cpu = self.cpu
        state = cpu.s
        saved = {name: getattr(state, name) for name in _REGISTER_FIELDS}
        saved_fst = list(state.fst)
        saved_count = cpu.instruction_count
        saved_depth = cpu.call_depth
        saved_boundary = cpu.boundary_hook
        try:
            cpu.push(int(effect_id) & 0xFFFF)
            # The sound routine is short and does not own a semantic replay
            # boundary. Disable the gameplay frame observer while its selected
            # generated call tree applies device and memory effects.
            cpu.boundary_hook = None
            emulate_call(
                cpu, CODE_SEG, _SFX_FUNCTION_IP, saved["ip"],
            )
            state.sp = (state.sp + 2) & 0xFFFF
        finally:
            for name, value in saved.items():
                setattr(state, name, value)
            state.fst = saved_fst
            cpu.instruction_count = saved_count
            cpu.call_depth = saved_depth
            cpu.boundary_hook = saved_boundary


class SkyroadsGameplaySession:
    """Long-lived native gameplay owner over the generated runtime's memory."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.cpu = runtime.cpu
        self.image = NativeGameImage(self.cpu.mem.data)
        self.data_segment = self.cpu.s.ds
        self.view = GameView(self.image, base=self.data_segment << 4)
        self.driver = NativeGameplayDriver(
            self.view,
            self.view.jump_level_gate,
            _capture_scratch(self.cpu),
            on_sfx=runtime._skyroads_gameplay_services.emit_sfx,
            auto_respawn=False,
        )

    def _decode_input(self) -> None:
        control_device = self.view._backend.rw(0x95F6)
        if control_device == 0:
            controls = decode_keyboard(self.view.key_row)
        elif control_device == 3:
            controls = decode_attract(self.view.key_row, self.view.lateral)
        else:
            raise RuntimeError(
                "the SkyRoads gameplay island has no authored input adapter "
                f"for control device {control_device}"
            )
        self.view.speed = controls.speed
        self.view.steer = controls.steer
        self.view.jump = controls.jump

    def _render(self) -> None:
        render_native_frame(
            self.image, self.data_segment, offscreen=1, rebuild=True,
        )
        paint_dashboard(
            self.image.data,
            SEG_DASHBRD,
            byte_count=DASHBOARD_BEZEL_OVERLAP,
        )
        update_hud(self.image, self.data_segment, self.view.ship_pos)
        update_progress_bar(self.image, self.data_segment)
        draw_grav_meter(self.image, self.data_segment)

    def advance(self) -> RegionProgress:
        if self.view.key_row[_ESCAPE_KEY_OFFSET] & _KEY_DOWN_BIT:
            return RegionProgress.exited(GAMEPLAY_ABORTED_EXIT)
        self._decode_input()
        outcome = self.driver.tick()
        _materialize_scratch(self.cpu, self.driver.scratch)
        self._render()
        if not outcome.transitioned:
            return RegionProgress.yielded(GAMEPLAY_TICK_BOUNDARY)
        try:
            exit_id = _TRANSITION_EXITS[outcome.kind]
        except KeyError as exc:
            raise RuntimeError(
                f"native gameplay produced an unclassified transition: {outcome!r}"
            ) from exc
        return RegionProgress.exited(exit_id)


class _GameplayRegistration:
    def __init__(self, runtime, binding: ResolvedExecutionRegion):
        self.runtime = runtime
        self.binding = binding
        self._collapsed_hooks = {}

    def _collapse_internal_boundaries(self) -> None:
        """Remove generated hook seams while the larger region owns them."""
        cpu = self.runtime.cpu
        hooks = getattr(cpu, "replacement_hooks", None)
        names = getattr(cpu, "hook_names", None)
        if hooks is None or names is None:
            return
        prefixes = set()
        for target in self.binding.covered_targets:
            parts = target.split(":")
            if len(parts) != 3 or parts[0] != "function":
                continue
            prefixes.add(f"lifted_{parts[1]}_{parts[2]}".lower())
        for key, name in tuple(names.items()):
            lowered = str(name).lower()
            if not any(
                lowered == prefix or lowered.startswith(prefix + "_resume_")
                for prefix in prefixes
            ):
                continue
            hook = hooks.pop(key, None)
            if hook is not None:
                self._collapsed_hooks[key] = (hook, name)
            names.pop(key, None)
        self.runtime._skyroads_collapsed_runtime_hooks = tuple(sorted(
            f"{cs:04X}:{ip:04X}" for cs, ip in self._collapsed_hooks
        ))

    def restore_internal_boundaries(self) -> None:
        cpu = self.runtime.cpu
        hooks = getattr(cpu, "replacement_hooks", None)
        names = getattr(cpu, "hook_names", None)
        if hooks is None or names is None:
            self._collapsed_hooks.clear()
            return
        for key, (hook, name) in self._collapsed_hooks.items():
            if key in hooks:
                raise RuntimeError(
                    "cannot restore collapsed gameplay boundary over an "
                    f"active hook at {key[0]:04X}:{key[1]:04X}"
                )
            hooks[key] = hook
            names[key] = name
        self._collapsed_hooks.clear()
        self.runtime._skyroads_collapsed_runtime_hooks = ()

    @staticmethod
    def _return_to_generated_caller(cpu, result: int) -> None:
        """Reproduce ``1FD9:2B0B`` and resume ``2B3D`` at ``2C61``."""
        cpu.s.ax = int(result) & 0xFFFF
        cpu.s.di = cpu.pop()
        cpu.s.si = cpu.pop()
        cpu.s.sp = cpu.s.bp
        cpu.s.bp = cpu.pop()
        cpu.s.ip = cpu.pop()
        cpu.call_depth = max(0, cpu.call_depth - 1)
        if (cpu.s.cs, cpu.s.ip) != (CODE_SEG, GAMEPLAY_CALLER_IP):
            raise RuntimeError(
                "gameplay did not return to generated caller 1010:2C61"
            )

    def maybe_handoff(self, cpu, head_cs: int, head_ip: int) -> bool:
        if (head_cs & 0xFFFF, head_ip & 0xFFFF) != (
            CODE_SEG, GAMEPLAY_ENTRY_IP,
        ):
            return False
        dispatcher = ensure_region_dispatcher(self.runtime)
        if dispatcher.active:
            return False
        cpu.s.cs, cpu.s.ip = CODE_SEG, GAMEPLAY_ENTRY_IP
        self._collapse_internal_boundaries()
        self.runtime._skyroads_gameplay_entries = (
            getattr(self.runtime, "_skyroads_gameplay_entries", 0) + 1
        )
        self.runtime._skyroads_gameplay_level = cpu.mem.rw(cpu.s.ds, 0x9332)

        def complete(exit_point) -> None:
            if exit_point.continuation not in {
                item.continuation for item in self.binding.exits
            }:
                raise RuntimeError("unplanned SkyRoads gameplay continuation")
            self.restore_internal_boundaries()
            if exit_point.exit_id == GAMEPLAY_ABORTED_EXIT:
                self._return_to_generated_caller(cpu, 7)
            elif exit_point.exit_id == LEVEL_COMPLETED_EXIT:
                # ``game_state == 2`` is the gameplay handler's internal
                # completion marker.  The enclosing 01B8 product loop uses
                # the 2B3D result as a different, caller-facing protocol:
                # result zero advances the completed-level bookkeeping and
                # returns to 5180, while result two starts 2B3D again.  The
                # old standalone native runner encoded the same distinction
                # in its menu routing.  This execution-region seam must
                # expose the product-loop result, not leak the inner flag.
                self._return_to_generated_caller(cpu, 0)
            elif exit_point.exit_id == FELL_OFF_ROAD_EXIT:
                # Original 1FD9 calls the long-running 0F05 death transition,
                # which owns its own generated parks, then resumes at 241E and
                # returns from gameplay.  Seed that exact call continuation.
                cpu.push(_FALL_HANDLER_RETURN_IP)
                cpu.call_depth += 1
                cpu.s.cs, cpu.s.ip = CODE_SEG, _FALL_HANDLER_IP
            else:
                # Death and timeout results keep their original non-zero
                # caller protocol, which tells 01B8 to retry the same level.
                self._return_to_generated_caller(
                    cpu, cpu.mem.rw(cpu.s.ds, 0x456E),
                )
            self.runtime._skyroads_last_region_exit = exit_point.exit_id

        dispatcher.handoff(
            self.binding,
            GAMEPLAY_ENTRY_ID,
            SkyroadsGameplaySession(self.runtime),
            complete=complete,
        )
        return True


def activate_gameplay_region(
    runtime, binding: ResolvedExecutionRegion,
) -> None:
    """Install the planned generated-carrier-to-native-region handoff."""
    runtime._skyroads_gameplay_services = _GeneratedGameplayServices(runtime)
    ensure_region_dispatcher(runtime)
    runtime._skyroads_gameplay_registration = _GameplayRegistration(
        runtime, binding,
    )


def reset_gameplay_region_for_restore(runtime) -> None:
    """Drop transient region objects before applying a replay continuation."""
    registration = getattr(runtime, "_skyroads_gameplay_registration", None)
    if registration is not None:
        registration.restore_internal_boundaries()
    dispatcher = getattr(runtime, "execution_regions", None)
    if dispatcher is not None:
        dispatcher.reset()


def maybe_enter_gameplay_region(runtime, cpu, head_cs: int, head_ip: int) -> bool:
    registration = getattr(runtime, "_skyroads_gameplay_registration", None)
    return bool(
        registration is not None
        and registration.maybe_handoff(cpu, head_cs, head_ip)
    )
