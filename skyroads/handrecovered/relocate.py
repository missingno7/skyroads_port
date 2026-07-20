"""SkyRoads buffer relocation/patch — add a constant to every nonzero byte in
a (possibly multi-segment) memory span.

Recovered from `1010:4052-4083`, mechanically lifted and proven byte-exact
against the ASM oracle first (`dos_re.tools.liftverify`, ORACLE_PASSING, 56
verified calls / 8 of 9 blocks) before this refactor — see
`docs/history/skyroads/run_status.md`. A classic DOS relocation-fixup pattern: after a
loaded asset lands at whatever segment happened to be free, every absolute
byte reference inside it needs shifting by a constant; `0` is used as a
"leave alone" sentinel (a null pointer / unset slot), so it is skipped rather
than patched.
"""
from __future__ import annotations



def patch_nonzero_bytes(source: bytes, delta: int) -> bytes:
    """Add ``delta`` to every nonzero byte in ``source``, mod 256 (1010:4062-4069)."""
    d = delta & 0xFF
    return bytes(b if b == 0 else (b + d) & 0xFF for b in source)
