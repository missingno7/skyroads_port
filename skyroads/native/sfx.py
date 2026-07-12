"""Native gameplay SFX — the `1010:03C2` trigger + the SFX.SND sample bank.

`03C2(id)` decoded from a live disassembly (2026-07-13, see run_status.md):

* stamps `[AF38] = [1600]` (the tick counter) unconditionally, then bails if
  `[451A] != 0` (muted);
* Sound Blaster path (`[0CB6] != 0`): the SFX bank was loaded whole at segment
  `[4560]`; the file starts with 6 little-endian u16 offsets bounding 5
  effects. For effect ``id``: ``start = offsets[id]``,
  ``length = offsets[id+1] - offsets[id]``. The FIRST byte at ``start`` is the
  SB DSP TIME CONSTANT (rate = 1,000,000 / (256 - tc)); the remaining
  ``length - 1`` bytes are unsigned-8 PCM, submitted as one single-cycle DMA
  block (`5B76`). Fire-and-forget — no completion IRQ is awaited.
* PC-speaker fallback (`[0CB6] == 0`): points `[0BD0]` at a per-id period
  table entry `[0x162 + id*2]` (not modelled natively — we always have PCM).

The one caller-side gate: `0476` ("channel busy") returns 1 while
`[1600] < [AF38] + 8` — an 8-tick debounce since ANY trigger. Only the
landing SFX (`03C2(1)`, from the bounce-decay branch `2470-249E`) and some
menu sounds consult it; bump/crash (`03C2(2)`) fire unconditionally.

Gameplay id map — VM-VERIFIED by capturing every `03C2` call over the
collision demo (demo_skyroads_20260710_213019: 5 calls -- 2x id 1 ret `249E`,
2x id 0 ret `27EA`, 1x id 2 ret `2763`):

====  =========================================================
 id   trigger (return-ip of the captured call site)
====  =========================================================
 0    wall CRASH thud (`27E7`, on flagging `[456A]`/`[456E]`); also the
      level-select "enter" (menu action 0xC)
 1    bounce landing (`249B`, decay branch, gated by the 8-tick debounce);
      the recurring effect in the SB-DMA capture (tc=131, 8000 Hz, 5153 B)
 2    wall bump slip (`2763`, inside `26EC`); also the blocked-repeat
      thump (`2828`, distance-gated, when a block does NOT flag a crash)
 3    HUD low-fuel/oxygen warning (the `12F8` updater; not in the sim loop)
 4    menu action 9 (conditional)
====  =========================================================
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import List, NamedTuple

#: number of effects in SFX.SND (6 header offsets bound 5 effects).
EFFECT_COUNT = 5
HEADER_LEN = 12

#: gameplay SFX ids (see the module docstring's map).
SFX_TOUCHDOWN = 0
SFX_LANDING = 1
SFX_BUMP = 2
SFX_WARNING = 3
SFX_MENU = 4

#: the `0476` busy window: ticks of `[1600]` since the last trigger.
BUSY_TICKS = 8


class SfxEffect(NamedTuple):
    """One decoded SFX.SND effect: 8-bit-unsigned PCM at ``rate`` Hz."""
    tc: int        # the raw SB DSP time constant (first byte of the effect)
    rate: int      # 1_000_000 // (256 - tc)
    pcm: bytes     # unsigned-8 PCM samples


def load_sfx_bank(path: "str | Path") -> List[SfxEffect]:
    """Parse ``SFX.SND`` into its 5 effects, exactly as `03C2` addresses them:
    u16 offset directory, first byte of each effect = DSP time constant, rest
    is the PCM block (`5B76` gets ``length - 1`` bytes from ``start + 1``)."""
    data = Path(path).read_bytes()
    offsets = struct.unpack_from("<6H", data, 0)
    if offsets[0] != HEADER_LEN or offsets[-1] != len(data):
        raise ValueError(
            f"not an SFX.SND bank: directory {offsets} vs file len {len(data)}")
    effects: List[SfxEffect] = []
    for i in range(EFFECT_COUNT):
        start, end = offsets[i], offsets[i + 1]
        tc = data[start]
        effects.append(SfxEffect(tc, 1_000_000 // (256 - tc),
                                 data[start + 1:end]))
    return effects
