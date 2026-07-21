"""Replayable SkyRoads product features over authoritative game state."""
from __future__ import annotations

from dos_re.features import FeatureController

from skyroads.bridge.dgroup_view import GameView
from skyroads.handrecovered.player import LEVEL_END


PRACTICE_LEVEL_FEATURE_ID = "skyroads:practice-level-position"
PRACTICE_FEATURE_CHANNEL = "skyroads:feature/v1"
FEATURE_SAFE_BOUNDARY = "skyroads:main-loop-boundary"


class SkyroadsFeatureState:
    """Applies planned feature changes through the same live/replay path."""

    def __init__(self, descriptors) -> None:
        self.controller = FeatureController(descriptors)

    def request_level_position(
        self, position: int, *, ordinal: int, record_event,
    ) -> None:
        position = int(position)
        if not 0 <= position <= LEVEL_END:
            raise ValueError(
                f"practice level position must be 0..{LEVEL_END}, got {position}"
            )
        self.controller.request(
            PRACTICE_LEVEL_FEATURE_ID,
            position,
            ordinal=ordinal,
            record_event=record_event,
        )

    def accept_replay_event(self, event) -> None:
        self.controller.accept_replay_event(event.channel, event.payload)

    def apply_main_loop_boundary(self, runtime) -> None:
        def apply(feature_id, value) -> None:
            if feature_id != PRACTICE_LEVEL_FEATURE_ID:
                raise ValueError(f"unsupported SkyRoads feature {feature_id!r}")
            position = int(value)
            if not 0 <= position <= LEVEL_END:
                raise ValueError(
                    f"replayed level position lies outside 0..{LEVEL_END}"
                )
            view = GameView(
                runtime.cpu.mem,
                base=(int(runtime.cpu.s.ds) & 0xFFFF) << 4,
            )
            # Enter the original level-select state and move its authoritative
            # scroll/selection field. The original game consumes the result.
            view.game_state = 2
            view.entered = 1
            view.ship_pos = position

        self.controller.apply_pending(FEATURE_SAFE_BOUNDARY, apply)
