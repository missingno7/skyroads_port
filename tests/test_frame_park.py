"""Frame-park pacing (skyroads.pacing) must be byte-equivalent for the game.

The park ends a frame the instant the game enters its INT 08h tick-wait, since
ds:[1600] cannot advance again until the next frame's IRQ.  This is an
optimisation, so the bar is behavioural equivalence: running gameplay with the
park ON must produce the *same rendered frames* and the *same game state* as the
full-spin baseline, while executing strictly fewer interpreted steps.

(The full E2E-replay proof -- every one of 126 rendered frames byte-identical,
final memory differing only in 11 bytes of fade-loop scratch at DGROUP+0xB87C --
is in the commit that introduced skyroads/pacing.py.  Here we lock in the
gameplay-window guarantee as a fast, self-contained regression.)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dos_re.cpu import HaltExecution
from dos_re.interrupts import deliver_interrupt
from dos_re.framebuffer import decode_frame_default
from skyroads.pacing import FrameIdle, install_frame_park
from skyroads.runtime import load_game_snapshot

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "assets" / "SKYROADS.EXE"
SNAP = ROOT / "artifacts" / "gameplay_snap_f520"
FRAMES = 24
SPF = 30_000
IRQS = 6

# ds:DGROUP game-state fields the port has named (see skyroads/handrecovered/player.py)
_STATE_FIELDS = {
    "ship_pos_lo": 0x54AC, "ship_pos_hi": 0x54AE, "speed": 0x9330,
    "bounce": 0x9336, "view_y_base": 0xAF2C, "game_state": 0x456E,
    "lateral_lo": 0x9618, "lateral_hi": 0x961A, "tick": 0x1600,
}

pytestmark = pytest.mark.skipif(
    not (EXE.exists() and SNAP.exists()),
    reason="needs SKYROADS.EXE + the gameplay snapshot",
)


def _run(park: bool):
    rt = load_game_snapshot(str(EXE), str(SNAP))
    if park:
        install_frame_park(rt)
    cpu = rt.cpu
    start = cpu.instruction_count
    frames = []
    for _ in range(FRAMES):
        for _ in range(IRQS):
            deliver_interrupt(rt, 0x08)
        try:
            cpu.run(SPF)
        except FrameIdle:
            pass
        except HaltExecution:
            break
        frames.append(decode_frame_default(rt).tobytes())
    ds = cpu.s.ds
    state = {name: cpu.mem.rw(ds, off) for name, off in _STATE_FIELDS.items()}
    return frames, state, cpu.instruction_count - start


def test_frame_park_is_byte_equivalent_and_cheaper() -> None:
    base_frames, base_state, base_steps = _run(park=False)
    park_frames, park_state, park_steps = _run(park=True)

    assert park_frames == base_frames, "a rendered frame diverged under frame-park"
    assert park_state == base_state, f"game state diverged: {park_state} != {base_state}"
    # the whole point: the park must skip the idle spin, not run it
    assert park_steps < base_steps // 2, (park_steps, base_steps)


def test_frame_park_parks_every_gameplay_frame() -> None:
    """Each gameplay frame should reach the tick-wait and park (FrameIdle),
    rather than exhausting the step budget."""
    rt = load_game_snapshot(str(EXE), str(SNAP))
    install_frame_park(rt)
    cpu = rt.cpu
    parked = 0
    for _ in range(FRAMES):
        for _ in range(IRQS):
            deliver_interrupt(rt, 0x08)
        try:
            cpu.run(SPF)
        except FrameIdle:
            parked += 1
        except HaltExecution:
            break
    assert parked == FRAMES, f"only {parked}/{FRAMES} frames parked at the tick-wait"


# --- menu/animation tick-wait (1010:47CD) --------------------------------------
# Runtime-loaded code (invisible in the static EXE), found profiling the E2E
# replay's menu screens (2026-07-11 perf diagnosis): several consecutive frames
# were burning the entire step budget on this spin. Uses a captured snapshot
# mid-spin (not the gameplay snapshot above, which never reaches menu code).

MENU_SNAP = ROOT / "artifacts" / "page4700_snap"

menu_pytestmark = pytest.mark.skipif(
    not (EXE.exists() and MENU_SNAP.exists()),
    reason="needs SKYROADS.EXE + the menu-animation snapshot",
)


@menu_pytestmark
def test_menu_anim_wait_is_byte_equivalent_and_cheaper() -> None:
    def run(park: bool):
        rt = load_game_snapshot(str(EXE), str(MENU_SNAP))
        if park:
            install_frame_park(rt)
        cpu = rt.cpu
        start = cpu.instruction_count
        frames = []
        for _ in range(FRAMES):
            for _ in range(IRQS):
                deliver_interrupt(rt, 0x08)
            try:
                cpu.run(SPF)
            except FrameIdle:
                pass
            except HaltExecution:
                break
            frames.append(decode_frame_default(rt).tobytes())
        return frames, cpu.instruction_count - start

    base_frames, base_steps = run(park=False)
    park_frames, park_steps = run(park=True)
    assert park_frames == base_frames, "a rendered frame diverged under the menu-anim park"
    assert park_steps < base_steps, (park_steps, base_steps)
