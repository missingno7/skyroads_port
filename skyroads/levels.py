"""Stable identities for SkyRoads' menu levels and road archive entries.

The two number spaces are intentionally different.  ``DS:[9332]`` is the
zero-based level-select identity (0..29).  The original outer loop passes
``DS:[9332] + 1`` to ``1010:5614``, because ``ROADS.LZS`` entry zero belongs
to the intro attract/demo run.  Keeping this conversion named prevents the
demo course from accidentally becoming the visual geometry for menu level 0.
"""
from __future__ import annotations


PLAYABLE_LEVEL_COUNT = 30
ATTRACT_ROAD_ARCHIVE_INDEX = 0


def validate_playable_level(level: int) -> int:
    level = int(level)
    if not 0 <= level < PLAYABLE_LEVEL_COUNT:
        raise ValueError(
            f"SkyRoads level-select identity must be 0..{PLAYABLE_LEVEL_COUNT - 1}, "
            f"got {level}"
        )
    return level


def road_archive_index(level: int) -> int:
    """Map a level-select identity to the entry passed to ``1010:5614``."""
    return validate_playable_level(level) + 1


__all__ = [
    "ATTRACT_ROAD_ARCHIVE_INDEX", "PLAYABLE_LEVEL_COUNT",
    "road_archive_index", "validate_playable_level",
]
