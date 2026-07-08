"""Boundary-less input-wait loop registry (see docs/demos_and_snapshots.md).

Fill with (cs, ip) canonical head addresses of SKYROADS's title/menu keyboard
polls once located — every driver (interactive, headless, frame verifier)
must agree on these or recorded demos will hang or lie.
"""
from __future__ import annotations

INPUT_WAIT_HEADS: dict[tuple[int, int], str] = {}


def is_input_wait(addr: tuple[int, int]) -> bool:
    return addr in INPUT_WAIT_HEADS
