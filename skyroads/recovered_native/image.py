"""``NativeGameImage`` -- the recovered game's FULL real-mode address space,
owned without a VM.

Mirrors pre2_port's ``pre2/native/state.py::NativeGameState`` (a 1 MB
bytearray at REAL physical addresses) more closely than
``skyroads.recovered_native.state.NativeGameState`` does -- that class deliberately
stays a 64 KB DGROUP-only image (see its own docstring) because every
gameplay island recovered so far only ever touches DGROUP. The renderer is
different: routines like ``road_column_strip`` (`1010:38BF`) read SEGMENT
VALUES out of DGROUP fields (``ds:[0E60]``/``[0E62]``/``[0E66]``/``[0E68]``)
that are real DOS segment numbers pointing at OTHER parts of the address
space (display lists, source bitmaps, the screen buffer) -- a "virtual",
reindexed image would need those segment values remapped too, so this class
holds the real thing instead, at real physical addresses, exactly like a VM's
``mem.data``.

A SEPARATE class from ``NativeGameState`` (not a replacement) so every
existing gameplay consumer -- which only ever needs DGROUP and constructs
``NativeGameState()`` expecting DGROUP aliased at physical offset 0 -- is
completely unaffected. Use ``dos_re.state_view.SegmentBackend`` (already
promoted to ``skyroads.state_view``) to build typed views over any segment of
this image, DGROUP included, at its REAL physical base.
"""
from __future__ import annotations

#: A full real-mode address space: 1 MB, matching a VM's ``mem.data``.
ADDR_SPACE = 0x100000


class NativeGameImage:
    """The recovered game's FULL real-mode memory image. Exposes ``.data`` so
    ``dos_re.state_view.SegmentBackend`` (and any VM-facing helper expecting a
    ``mem``-like object with ``.data``) reads and writes it unchanged."""

    __slots__ = ("data",)

    def __init__(self, data: bytearray | bytes | None = None):
        if data is None:
            data = bytearray(ADDR_SPACE)
        elif not isinstance(data, bytearray):
            data = bytearray(data)
        if len(data) < ADDR_SPACE:
            data = data + bytearray(ADDR_SPACE - len(data))
        elif len(data) > ADDR_SPACE:
            data = data[:ADDR_SPACE]
        self.data = data

    @classmethod
    def from_vm(cls, rt) -> "NativeGameImage":
        """Seed from a loaded VM runtime's full memory image."""
        return cls(bytearray(rt.cpu.mem.data))

    def rb(self, seg: int, off: int) -> int:
        """Read a byte at ``seg:off`` (real segment:offset addressing)."""
        return self.data[((seg & 0xFFFF) << 4) + (off & 0xFFFF)]

    def wb(self, seg: int, off: int, v: int) -> None:
        self.data[((seg & 0xFFFF) << 4) + (off & 0xFFFF)] = v & 0xFF

    def rw(self, seg: int, off: int) -> int:
        a = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        return self.data[a] | (self.data[a + 1] << 8)

    def ww(self, seg: int, off: int, v: int) -> None:
        a = ((seg & 0xFFFF) << 4) + (off & 0xFFFF)
        self.data[a] = v & 0xFF
        self.data[a + 1] = (v >> 8) & 0xFF
