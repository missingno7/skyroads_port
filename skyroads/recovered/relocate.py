"""SkyRoads buffer relocation/patch — add a constant to every nonzero byte in
a (possibly multi-segment) memory span.

Recovered from `1010:4052-4083`, mechanically lifted and proven byte-exact
against the ASM oracle first (`dos_re.tools.liftverify`, ORACLE_PASSING, 56
verified calls / 8 of 9 blocks) before this refactor — see
`docs/skyroads/run_status.md`. A classic DOS relocation-fixup pattern: after a
loaded asset lands at whatever segment happened to be free, every absolute
byte reference inside it needs shifting by a constant; `0` is used as a
"leave alone" sentinel (a null pointer / unset slot), so it is skipped rather
than patched.
"""
from __future__ import annotations

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:4062",
    contract="patch_nonzero_bytes(source, delta): map each byte b in source -> "
             "b if b==0, else (b+delta)&0xFF. Pure byte-patch core of the "
             "1010:4062-4069 scan loop; the surrounding far-pointer/segment-wrap/"
             "multi-pass mechanics are VM-hook concerns -- see skyroads/hooks.py.",
    status="ASM_MATCHED",
    merge_target="skyroads.recovered_native.relocate (future)",
)
def patch_nonzero_bytes(source: bytes, delta: int) -> bytes:
    """Add ``delta`` to every nonzero byte in ``source``, mod 256 (1010:4062-4069)."""
    d = delta & 0xFF
    return bytes(b if b == 0 else (b + d) & 0xFF for b in source)
