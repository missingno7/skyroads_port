"""``NativeGameState`` -- the recovered game's DGROUP, owned without a VM.

Mirrors pre2_port's ``pre2/native/state.py`` (see docs/state_mirrors.md /
dos_re/docs/state_mirrors.md for the general pattern this instantiates).
Unlike pre2 -- whose recovered logic already crosses several segments (level
map, asset bank) and so owns a full 1 MB image -- every SkyRoads island
recovered so far (skyroads/recovered/*) reads/writes only the game's ONE data
segment (DGROUP, ``ds == 0x1686`` in every captured runtime; see
skyroads/recovered/player.py's field map). So ``.data`` here is just that
64 KB segment, not the full real-mode address space: the smallest byte-backed
image that makes ``skyroads/bridge/dgroup_view.py``'s ``ByteBackend`` (base 0)
work unchanged over either this or a VM's ``mem``.

If/when a recovered routine needs another segment (e.g. the intro-animation
table's per-entry segments, or a level asset bank), extend ``.data`` to the
full 1 MB image and switch the affected views to
``dos_re.state_view.SegmentBackend`` -- the pattern already supports it
without touching this class's public shape.
"""
from __future__ import annotations

#: The game's data segment (DGROUP) paragraph, as seen in every captured
#: runtime this session's recovery work has used. Documented here (the layout
#: layer), never hard-coded inside skyroads/recovered/* (pitfall #17).
DATA_SEG = 0x1686

#: One DOS segment: 64 KB, matching every 16-bit offset the recovered islands
#: address DGROUP with.
SEGMENT_SIZE = 0x10000


class NativeGameState:
    """The recovered game's DGROUP image. Exposes ``.data`` so
    ``dos_re.state_view.ByteBackend`` (and any VM-facing helper that expects a
    ``mem``-like object with ``.data``) reads and writes it unchanged."""

    __slots__ = ("data",)

    def __init__(self, data: bytearray | bytes | None = None):
        if data is None:
            data = bytearray(SEGMENT_SIZE)
        elif not isinstance(data, bytearray):
            data = bytearray(data)
        if len(data) < SEGMENT_SIZE:
            data = data + bytearray(SEGMENT_SIZE - len(data))
        elif len(data) > SEGMENT_SIZE:
            data = data[:SEGMENT_SIZE]
        self.data = data

    @classmethod
    def from_vm(cls, rt, ds: int = DATA_SEG) -> "NativeGameState":
        """Seed from a loaded VM runtime's DGROUP -- the bootstrap into native
        state ownership. ``ds`` defaults to the documented DGROUP segment but
        can be overridden (e.g. from ``rt.cpu.s.ds`` at a captured DGROUP-live
        frame) if a future runtime ever relocates it."""
        base = (ds & 0xFFFF) << 4
        return cls(bytearray(rt.cpu.mem.data[base:base + SEGMENT_SIZE]))

    def rb(self, off: int) -> int:
        """Read a DGROUP byte (DS-relative) -- the recovered islands' ``rb`` accessor."""
        return self.data[off & 0xFFFF]

    def wb(self, off: int, v: int) -> None:
        self.data[off & 0xFFFF] = v & 0xFF

    def rw(self, off: int) -> int:
        """Read a DGROUP word (DS-relative) -- the recovered islands' ``rw`` accessor."""
        o = off & 0xFFFF
        return self.data[o] | (self.data[(o + 1) & 0xFFFF] << 8)

    def ww(self, off: int, v: int) -> None:
        o = off & 0xFFFF
        self.data[o] = v & 0xFF
        self.data[(o + 1) & 0xFFFF] = (v >> 8) & 0xFF
