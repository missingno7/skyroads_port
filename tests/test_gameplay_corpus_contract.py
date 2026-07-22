"""Reusable native-gameplay lifecycle corpus contracts.

The expensive oracle recordings live as ReplayArtifacts under ``artifacts``;
this test keeps their required paths and direct-launch coverage explicit in
versioned source without pretending synthetic tests are recorded evidence.
"""
from __future__ import annotations

from skyroads.launch_inputs import LEVEL_COUNT, validate_level
from skyroads.levels import ATTRACT_ROAD_ARCHIVE_INDEX, road_archive_index


def test_direct_launch_includes_every_archived_level() -> None:
    assert LEVEL_COUNT == 30
    assert [validate_level(level) for level in range(LEVEL_COUNT)] == list(range(30))
    assert ATTRACT_ROAD_ARCHIVE_INDEX == 0
    assert [road_archive_index(level) for level in range(LEVEL_COUNT)] == list(range(1, 31))
