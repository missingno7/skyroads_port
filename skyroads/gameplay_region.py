"""Canonical-player adapter for the authored SkyRoads gameplay island.

The generated provider reaches the original gameplay loop at ``1010:2317``.
At that stable point this adapter transfers ownership to the recovered native
gameplay driver for successive semantic ticks.  State remains authoritative in
the same DOS memory image, so the generated frontend can resume at
``1010:20AD`` without importing or reconciling a second game state.

This first executable slice is deliberately strict: native Sound Blaster
effects are not yet wired back into the shared device model, so activation
currently requires ``--no-sound`` rather than silently dropping device state.
"""
from __future__ import annotations

from dos_re.execution import ResolvedExecutionRegion
from dos_re.regions import RegionProgress, ensure_region_dispatcher

from skyroads.bridge.dgroup_view import GameView
from skyroads.handrecovered.controls import decode_attract, decode_keyboard
from skyroads.identities import CODE_SEG
from skyroads.native.boot import DASHBOARD_BEZEL_OVERLAP, SEG_DASHBRD, paint_dashboard
from skyroads.native.frame import render_native_frame
from skyroads.native.hud import draw_grav_meter, update_hud, update_progress_bar
from skyroads.native.image import NativeGameImage
from skyroads.native.loop import GameplayScratch, NativeGameplayDriver
from skyroads.handrecovered.dynamics import JumpScratch


GAMEPLAY_ENTRY_IP = 0x2317
GAMEPLAY_RETURN_IP = 0x20AD
GAMEPLAY_TICK_BOUNDARY = "skyroads:main-loop-or-input-boundary:v1"
GAMEPLAY_ENTRY_ID = "start-level"
LEVEL_COMPLETED_EXIT = "level-completed"
PLAYER_DIED_EXIT = "player-died"


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
        self._decode_input()
        outcome = self.driver.tick()
        self._render()
        if not outcome.transitioned:
            return RegionProgress.yielded(GAMEPLAY_TICK_BOUNDARY)
        if outcome.kind == "finish":
            return RegionProgress.exited(LEVEL_COMPLETED_EXIT)
        return RegionProgress.exited(PLAYER_DIED_EXIT)


class _GameplayRegistration:
    def __init__(self, runtime, binding: ResolvedExecutionRegion):
        self.runtime = runtime
        self.binding = binding

    def maybe_handoff(self, cpu, head_cs: int, head_ip: int) -> bool:
        if (head_cs & 0xFFFF, head_ip & 0xFFFF) != (
            CODE_SEG, GAMEPLAY_ENTRY_IP,
        ):
            return False
        dispatcher = ensure_region_dispatcher(self.runtime)
        if dispatcher.active:
            return False
        cpu.s.cs, cpu.s.ip = CODE_SEG, GAMEPLAY_ENTRY_IP

        def complete(exit_point) -> None:
            if exit_point.continuation not in {
                item.continuation for item in self.binding.exits
            }:
                raise RuntimeError("unplanned SkyRoads gameplay continuation")
            # The generated 1FD9 provider exposes 20AD as a resumable block.
            # Synchronize its per-frame local to the shared virtual tick before
            # returning so the surrounding timing gate observes no stale debt.
            tick = cpu.mem.rw(cpu.s.ds, 0x1600)
            cpu.mem.ww(
                cpu.s.ss,
                (cpu.s.bp - 2) & 0xFFFF,
                tick,
            )
            # 20AD is entered immediately after 20AA loaded that same local
            # into AX. Reconstruct the complete block-entry contract, not just
            # its backing stack word.
            cpu.s.ax = tick
            cpu.s.cs, cpu.s.ip = CODE_SEG, GAMEPLAY_RETURN_IP
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
    if not getattr(runtime, "_skyroads_no_sound", False):
        raise RuntimeError(
            "the authored gameplay execution island does not yet bridge its "
            "native SFX events into the shared Sound Blaster device; launch "
            "this pilot with --no-sound"
        )
    ensure_region_dispatcher(runtime)
    runtime._skyroads_gameplay_registration = _GameplayRegistration(
        runtime, binding,
    )


def maybe_enter_gameplay_region(runtime, cpu, head_cs: int, head_ip: int) -> bool:
    registration = getattr(runtime, "_skyroads_gameplay_registration", None)
    return bool(
        registration is not None
        and registration.maybe_handoff(cpu, head_cs, head_ip)
    )
