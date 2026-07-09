"""Back-compat shim: the Display presenter lives at ``dos_re.display``.

This file used to be a full pre-unification copy; it stays a shim so it can
never drift from the canonical implementation again.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dos_re"))

from dos_re.display import Display  # noqa: E402,F401
