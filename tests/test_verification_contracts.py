"""SkyRoads' declared native gameplay verification authority."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from dos_re.cpu import CPU8086
from dos_re.dos import DOSMachine
from dos_re.memory import Memory
from dos_re.replay import CanonicalState, compare_projection_contract

from skyroads.replay import _semantic_projection
from skyroads.verification_contracts import GAMEPLAY_INTERIOR_PROJECTION


class _SoundBlasterScratch:
    def __init__(self, marker: int) -> None:
        self.marker = marker

    def snapshot_state(self) -> dict[str, int]:
        return {"private-device-marker": self.marker}


def _runtime(sound_marker: int):
    cpu = CPU8086(Memory())
    cpu.s.ds = cpu.s.ss = 0x1686
    dos = DOSMachine(Path("."))
    dos.sound_blaster = _SoundBlasterScratch(sound_marker)
    return SimpleNamespace(cpu=cpu, dos=dos)


def test_native_gameplay_semantics_exclude_device_scratch_but_not_gameplay_state() -> None:
    oracle = _semantic_projection(_runtime(1), event_cursor=4)
    candidate = _semantic_projection(_runtime(999), event_cursor=4)

    assert "sound_blaster" not in oracle.fields
    assert compare_projection_contract(
        oracle, candidate, GAMEPLAY_INTERIOR_PROJECTION,
    ).equivalent

    wrong_fields = deepcopy(dict(candidate.fields))
    wrong_fields["gameplay"]["ship_pos"] += 1
    wrong = CanonicalState(
        candidate.schema_id, candidate.event_cursor,
        wrong_fields, candidate.regions,
    )
    rejected = compare_projection_contract(
        oracle, wrong, GAMEPLAY_INTERIOR_PROJECTION,
    )
    assert not rejected.equivalent
    assert "fields.gameplay.ship_pos" in rejected.differences[0]
