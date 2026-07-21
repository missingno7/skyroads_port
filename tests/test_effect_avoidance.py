"""Exact differential for authored 1010:1DFA obstacle avoidance semantics."""
from __future__ import annotations

import random

from dos_re.memory import Memory

from skyroads.handrecovered.effect_avoidance import (
    _effect_cell_blocked,
    _project_arc,
    _projected_arc_is_clear,
    select_avoidance_adjustment,
)
from skyroads.recovered.func_1010_1ccd import func_1010_1ccd
from skyroads.recovered.func_1010_1c62 import func_1010_1c62
from skyroads.handrecovered.renderer import perspective_row_offset
from skyroads.recovered.func_1010_1dfa import func_1010_1dfa


def test_authored_avoidance_matches_generated_function() -> None:
    rng = random.Random(0x1DFA)
    ds, ss = 0x1400, 0x2A00
    for _ in range(40):
        mem = Memory()
        # 04C0 reads words from this perspective-table neighborhood. Exercise
        # clear, blocked and selector-shaped cells deterministically.
        table_words = (0x0000, 0x0001, 0x000C, 0x0100, 0x0200, 0x02C0)
        for off in range(0x162C, 0x1700, 2):
            mem.ww(ds, off, rng.choice(table_words))

        lateral = rng.randrange(0x00018000, 0x00090000)
        af1c = rng.randrange(0x3000, 0xD001)
        af2c = rng.randrange(0x3700, 0x4400)
        ship_pos = rng.randrange(0, 0x2AAB)
        accel = rng.choice((0, 29, 0xFFFF - 28, 58, 0xFFFF - 57))
        bounce = rng.randrange(0x0100, 0x0380)
        gravity = rng.choice((0xFF8D, 0xFF93, 0xFF9C))
        speed = rng.choice((0, 1, 0xFFFF))
        nudge = rng.choice((0, 17, 0xFFEF))
        old_delta = rng.randrange(0x100000000)
        old_mark = rng.randrange(2)

        _write_dword(mem, ds, 0x9618, lateral)
        mem.ww(ds, 0xAF1C, af1c)
        mem.ww(ds, 0xAF2C, af2c)
        _write_dword(mem, ds, 0x54AC, ship_pos)
        mem.ww(ds, 0x4568, accel)
        mem.ww(ds, 0x9336, bounce)
        mem.ww(ds, 0x54AA, gravity)
        mem.ww(ds, 0x9330, speed)
        mem.ww(ds, 0x5496, nudge)
        _write_dword(mem, ds, 0xAF2E, old_delta)
        mem.ww(ds, 0x455A, old_mark)

        adjustment = select_avoidance_adjustment(
            lambda off: mem.rw(ds, off),
            lateral=lateral,
            af1c=af1c,
            af2c=af2c,
            ship_pos=ship_pos,
            lateral_accel=accel,
            bounce=bounce,
            gravity=gravity,
            speed=speed,
            center_nudge=nudge,
        )
        assert _effect_cell_blocked(
            lambda off: mem.rw(ds, off), lateral, af1c,
        ) == _generated_cell_blocked(mem, ds, ss, lateral, af1c)
        authored_clear = _projected_arc_is_clear(
            lambda off: mem.rw(ds, off),
            lateral=lateral, af1c=af1c, af2c=af2c,
            ship_pos=ship_pos, lateral_accel=accel, bounce=bounce,
            gravity=gravity, speed=speed, center_nudge=nudge,
        )
        generated_clear = _generated_arc_is_clear(
            mem, ds, ss, lateral, af1c, af2c, ship_pos, accel, bounce,
        )
        generated_points = _generated_arc_points(
            mem, ds, ss, lateral, af1c, af2c, ship_pos, accel, bounce,
        )
        authored_points = _project_arc(
            lateral=lateral, af1c=af1c, af2c=af2c, ship_pos=ship_pos,
            lateral_accel=accel, bounce=bounce, gravity=gravity,
            speed=speed, center_nudge=nudge,
        )
        if generated_points:
            assert authored_points is not None
            expected_points = (
                (authored_points[0], authored_points[1]),
                (authored_points[2], authored_points[3]),
            )
            assert generated_points == expected_points[:len(generated_points)], (
                f"inputs={(lateral, af1c, af2c, ship_pos, accel, bounce)!r}"
            )
            for point_lateral, point_af1c in generated_points:
                assert _effect_cell_blocked(
                    lambda off: mem.rw(ds, off), point_lateral, point_af1c,
                ) == _generated_cell_blocked(
                    mem, ds, ss, point_lateral, point_af1c,
                ), _cell_debug(mem, ds, point_lateral, point_af1c)
        assert authored_clear == generated_clear, (
            f"predictor inputs={(lateral, af1c, af2c, ship_pos, accel, bounce, gravity, speed, nudge)!r}"
        )
        func_1010_1dfa(mem, ds=ds, ss=ss, sp=0xFF00)

        if adjustment is None:
            expected = (accel, ship_pos, old_delta, old_mark)
        else:
            expected = (
                adjustment.lateral_accel,
                adjustment.ship_pos,
                adjustment.position_delta,
                1 if adjustment.mark_effect else old_mark,
            )
        actual = (
            mem.rw(ds, 0x4568),
            _read_dword(mem, ds, 0x54AC),
            _read_dword(mem, ds, 0xAF2E),
            mem.rw(ds, 0x455A),
        )
        inputs = (lateral, af1c, af2c, ship_pos, accel, bounce,
                  gravity, speed, nudge)
        assert actual == expected, f"inputs={inputs!r} adjustment={adjustment!r}"


def _write_dword(mem: Memory, seg: int, off: int, value: int) -> None:
    mem.ww(seg, off, value & 0xFFFF)
    mem.ww(seg, off + 2, value >> 16)


def _read_dword(mem: Memory, seg: int, off: int) -> int:
    return mem.rw(seg, off) | (mem.rw(seg, off + 2) << 16)


def _generated_arc_is_clear(
    mem: Memory, ds: int, ss: int, lateral: int, af1c: int, af2c: int,
    ship_pos: int, accel: int, bounce: int,
) -> bool:
    sp = 0xFF00
    for value in (
        bounce, accel, ship_pos >> 16, ship_pos, af2c, af1c,
        lateral >> 16, lateral, 0xBEEF,
    ):
        sp = (sp - 2) & 0xFFFF
        mem.ww(ss, sp, value)
    outputs, _ = func_1010_1ccd(mem, ds=ds, ss=ss, sp=sp)
    return outputs["ax"] != 0


def _generated_cell_blocked(
    mem: Memory, ds: int, ss: int, lateral: int, af1c: int,
) -> bool:
    sp = 0xF000
    for value in (af1c, lateral >> 16, lateral, 0xBEEF):
        sp -= 2
        mem.ww(ss, sp, value)
    outputs, _ = func_1010_1c62(mem, ds=ds, ss=ss, sp=sp)
    return outputs["ax"] != 0


def _generated_arc_points(
    mem: Memory, ds: int, ss: int, lateral: int, af1c: int, af2c: int,
    ship_pos: int, accel: int, bounce: int,
) -> tuple[tuple[int, int], ...]:
    import skyroads.recovered.func_1010_1ccd as module

    calls: list[tuple[int, int]] = []
    original = module.func_1010_1c62

    def capture(memory, *, sp=0, ss=0, **kwargs):
        lat = memory.rw(ss, sp + 2) | (memory.rw(ss, sp + 4) << 16)
        depth = memory.rw(ss, sp + 6)
        calls.append((lat, depth))
        return original(memory, sp=sp, ss=ss, **kwargs)

    module.func_1010_1c62 = capture
    try:
        _generated_arc_is_clear(
            mem, ds, ss, lateral, af1c, af2c, ship_pos, accel, bounce,
        )
    finally:
        module.func_1010_1c62 = original
    return tuple(calls)


def _cell_debug(mem: Memory, ds: int, lateral: int, af1c: int) -> str:
    p = perspective_row_offset(lateral & 0xFFFF, lateral >> 16, af1c)
    word = mem.rw(ds, p.offset) if p.in_range else 0
    return f"point={(lateral, af1c)!r} perspective={p!r} word={word:#06x}"
