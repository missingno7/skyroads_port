"""Faithful cockpit overlay used by the authored gameplay renderer.

The gameplay region consumes a dashboard image already present in shared DOS
memory.  Keeping this operation separate from native boot reconstruction
avoids retaining EXE decoding and level-loading experiments in the runtime
dependency closure.
"""
from __future__ import annotations


SEG_DASHBRD = 0x6BEA
DASHBOARD_VGA_OFFSET = 0xA140
DASHBOARD_LEN = 22_720
DASHBOARD_BEZEL_OVERLAP = (137 - 129 + 1) * 320


def paint_dashboard(
    img_data: bytearray, dashboard_seg: int = SEG_DASHBRD, *,
    byte_count: int = DASHBOARD_LEN,
) -> None:
    """Mask non-zero dashboard pixels over the live VGA plane."""
    src_base = dashboard_seg << 4
    dst_base = 0xA0000 + DASHBOARD_VGA_OFFSET
    for index in range(min(byte_count, DASHBOARD_LEN)):
        pixel = img_data[src_base + index]
        if pixel:
            img_data[dst_base + index] = pixel
