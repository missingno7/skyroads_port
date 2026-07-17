"""SkyRoads stencil blit — a 3-value template-to-screen byte copy.

Recovered from `1010:0F62-0F8B`. Copies a source byte run to a destination
byte run (typically screen/off-screen memory: mode 13h is one byte per
pixel), remapping each source byte through a tiny 3-entry stencil: `0`
(background/transparent) stays `0`; `1` becomes ``template_color``; any other
nonzero value becomes ``other_color``. This is the low-level primitive behind
menu text/glyph rendering — font glyphs are stored as 0/1/2 stencils and
recolored per-draw into the caller's chosen palette entries.
"""
from __future__ import annotations

from skyroads.islands import oracle_link


@oracle_link(
    boundary="1010:0F62",
    contract="stencil_blit(source, template_color, other_color): map each byte "
             "b in source -> 0 if b==0, template_color&0xFF if b==1, else "
             "other_color&0xFF. Pure byte-substitution core of the 0F62 blit "
             "(1010:0F75-0F85); the surrounding register/segment mechanics "
             "(far-pointer source, ES:DI destination, SI/DI/CX/flags at exit) "
             "are VM-hook concerns, not game logic -- see skyroads/hooks.py.",
    status="ASM_MATCHED",  # matches the ASM's per-byte write value on all sampled calls
    merge_target="skyroads.native.blit (future)",
)
def stencil_blit(source: bytes, template_color: int, other_color: int) -> bytes:
    """Remap each source byte through the 3-entry stencil (1010:0F76-0F84)."""
    tc = template_color & 0xFF
    oc = other_color & 0xFF
    return bytes(0 if b == 0 else (tc if b == 1 else oc) for b in source)
