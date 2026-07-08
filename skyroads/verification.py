"""Per-hook continuation metadata for the differential verifier.

One entry per registered hook address in hooks.py; empty until the first hook
is added. See dos_re.verification.HookVerifierConfig.strict() for auto-
continuation while investigating a single routine.
"""
from __future__ import annotations

from dos_re.verification import GenericHookStop

DEFAULT_STOPS: dict[tuple[int, int], GenericHookStop] = {}
