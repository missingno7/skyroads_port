"""Verify the recovered keyboard control decode (skyroads.handrecovered.controls)
against real ASM I/O captured over the level replay.

Fixture: unique (key-state row -> speed/steer/jump) samples recorded at every
``074C`` keyboard-case call (``95F6==0``) over the replay. The full run matched
1466/1466 calls byte-exact (497 with keys held); the fixture keeps the distinct
key combinations that actually occurred.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.handrecovered.controls import Controls, decode_keyboard

_FIXTURE = Path(__file__).parent / "fixtures" / "controls_trace.json"
_CASES = json.loads(_FIXTURE.read_text())["decode_keyboard"]


def _row(case: dict) -> dict[int, int]:
    return {int(off, 16): val for off, val in case["row"].items()}


def test_decode_keyboard_matches_asm() -> None:
    assert _CASES, "fixture empty"
    for case in _CASES:
        speed, steer, jump = case["out"]
        got = decode_keyboard(_row(case))
        assert got == Controls(speed, steer, jump), (case["row"], case["out"], got)

    # the fixture must exercise real key presses, not just the idle all-zero row
    held = [c for c in _CASES if any(c["out"])]
    assert held, "fixture should include samples with keys held"


def test_decode_keyboard_axes_and_diagonals() -> None:
    from skyroads.handrecovered.controls import (
        K_UP, K_DOWN, K_LEFT, K_RIGHT, K_UPRIGHT, K_DOWNLEFT, K_JUMP, KEY_DOWN_BIT,
    )
    down = KEY_DOWN_BIT

    def row(*held: int) -> dict[int, int]:
        return {off: (down if off in held else 0) for off in range(0x0BD2, 0x0BDC)}

    assert decode_keyboard(row(K_UP)) == Controls(1, 0, 0)
    assert decode_keyboard(row(K_DOWN)) == Controls(-1, 0, 0)
    assert decode_keyboard(row(K_LEFT)) == Controls(0, -1, 0)
    assert decode_keyboard(row(K_RIGHT)) == Controls(0, 1, 0)
    # a diagonal drives both axes at once
    assert decode_keyboard(row(K_UPRIGHT)) == Controls(1, 1, 0)
    assert decode_keyboard(row(K_DOWNLEFT)) == Controls(-1, -1, 0)
    # opposing keys cancel; jump is independent
    assert decode_keyboard(row(K_UP, K_DOWN)) == Controls(0, 0, 0)
    assert decode_keyboard(row(K_JUMP)) == Controls(0, 0, 1)
