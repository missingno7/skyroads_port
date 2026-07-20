"""SkyRoads oracle transfer observer feeding ReplayArtifact evidence."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from dos_re.identity import ExecutionPointIdentity, real_mode_address
from dos_re.lift.ir import load_recovery_ir
from dos_re.replay import ReplayEvidenceRecorder, ReplayPoint

from skyroads.identities import IMAGE, function_identity


class OracleAtlasObserver:
    """Observe real CPU transfers against retained IR ownership metadata."""

    def __init__(
        self, ir_path: str | Path, *, timeline_id: str,
        ordinal: Callable[[], int],
    ):
        document = load_recovery_ir(ir_path)
        records = document["functions"]
        if not isinstance(records, dict):
            records = {record["entry"]: record for record in records}
        self.timeline_id = timeline_id
        self.ordinal = ordinal
        self.recorder = ReplayEvidenceRecorder()
        self._owners: dict[tuple[int, int], str] = {}
        self._entries: dict[tuple[int, int], str] = {}
        self._kinds: dict[tuple[int, int], str] = {}
        for record in records.values():
            cs, ip = (int(part, 16) for part in record["entry"].split(":"))
            identity = function_identity(ip) if cs == 0x1010 else self._point_id(cs, ip)
            self._entries[(cs, ip)] = identity
            for block in record.get("blocks", ()):
                for instruction in block.get("instructions", ()):
                    address = (cs, int(instruction["ip"], 16))
                    self._owners.setdefault(address, identity)
                    self._kinds[address] = str(instruction.get("kind", ""))
        self._active: list[str] = []

    def _point(self, *, after: bool = False) -> ReplayPoint:
        ordinal = int(self.ordinal()) + (1 if after else 0)
        return ReplayPoint(ordinal, self.timeline_id)

    @staticmethod
    def _point_id(cs: int, ip: int) -> str:
        return str(ExecutionPointIdentity(
            IMAGE, "real-mode", real_mode_address(cs, ip)))

    def _identity(self, address: tuple[int, int]) -> str:
        return self._entries.get(
            address, self._owners.get(address, self._point_id(*address)))

    def _enter(self, identity: str) -> None:
        if ":function:" in identity:
            self.recorder.enter(identity, self._point())
            self._active.append(identity)

    def _exit(self) -> str | None:
        if not self._active:
            return None
        identity = self._active.pop()
        self.recorder.exit(identity, self._point(after=True))
        return identity

    @contextmanager
    def observe(self, cpu):
        original = cpu.step

        def step():
            before = (cpu.s.cs, cpu.s.ip)
            source = self._identity(before)
            kind = self._kinds.get(before, "")
            depth = cpu.call_depth
            if not self._active and source in self._entries.values():
                self._enter(source)
            result = original()
            after = (cpu.s.cs, cpu.s.ip)
            target = self._identity(after)
            if cpu.call_depth > depth:
                transfer = "call-indirect" if kind == "call_ind" else (
                    "interrupt" if kind == "int" else "call")
                self.recorder.observe_transfer(source, target, transfer, self._point())
                self._enter(target)
            elif cpu.call_depth < depth:
                completed = self._exit()
                self.recorder.observe_transfer(
                    completed or source, target, "return", self._point(after=True))
            elif source != target and kind in {"jmp", "jmp_ind"}:
                transfer = "jump-indirect" if kind == "jmp_ind" else "tail-transfer"
                self.recorder.observe_transfer(source, target, transfer, self._point())
                if self._active and self._active[-1] == source:
                    self._exit()
                self._enter(target)
            return result

        cpu.step = step
        try:
            yield self.recorder
        finally:
            cpu.step = original
