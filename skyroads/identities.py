"""Stable SkyRoads original-program identities shared by all evidence layers."""
from __future__ import annotations

from dos_re.identity import (
    ExecutionPointIdentity,
    FunctionIdentity,
    ImageIdentity,
    ProgramIdentity,
    RegionIdentity,
    real_mode_address,
)

PROGRAM = ProgramIdentity("skyroads:1.0")
PROGRAM_ID = str(PROGRAM)
IMAGE = ImageIdentity(
    PROGRAM,
    "skyroads-exe",
    "sha256",
    "ce1b1ec1688eb853b6fbb11614793d8c4bef563b0d31e5cbd76cd1801832ec0f",
)
CODE_SEG = 0x1010
PROGRAM_ROOT = str(RegionIdentity(PROGRAM, "program"))
GAMEPLAY_REGION = str(RegionIdentity(PROGRAM, "gameplay"))
# 1010:0000 is the packed MZ entry. The generated program begins at the
# observed LZEXE hand-off after unpacking, so product reachability roots there.
RECOVERY_ENTRY_FUNCTION = str(FunctionIdentity(
    IMAGE, "real-mode", real_mode_address(CODE_SEG, 0x61F3)))


def function_identity(offset: int) -> str:
    return str(FunctionIdentity(
        IMAGE, "real-mode", real_mode_address(CODE_SEG, int(offset))))


def execution_point_identity(offset: int) -> str:
    return str(ExecutionPointIdentity(
        IMAGE, "real-mode", real_mode_address(CODE_SEG, int(offset))))


GAMEPLAY_ENTRY_POINT = execution_point_identity(0x2317)
GAMEPLAY_RETURN_POINT = execution_point_identity(0x20AD)
