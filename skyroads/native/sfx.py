"""Recovered gameplay SFX trigger ``1010:03C2`` and ``SFX.SND`` bank.

``03C2(id)``:

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

Observed gameplay id map:

====  =========================================================
 id   trigger (return-ip of the captured call site)
====  =========================================================
 0    wall CRASH thud (`27E7`, on flagging `[456A]`) -- fires the instant
      `resolve_lateral_crash` sets `[456A]` 0 -> nonzero (ship past
      `CRASH_MILESTONE_POS`, not already flagged), regardless of
      `[456E]`/game_state. A
      pre-milestone or already-flagged lateral block does NOT fire this --
      see `native_gameplay_substep`'s collision-response comment; also the
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
from hashlib import sha256
from pathlib import Path
from typing import List, NamedTuple

#: number of effects in SFX.SND (6 header offsets bound 5 effects).
EFFECT_COUNT = 5
HEADER_LEN = 12

#: gameplay SFX ids (see the module docstring's map).
SFX_CRASH = 0
SFX_LANDING = 1
SFX_BUMP = 2
SFX_WARNING = 3
SFX_MENU = 4

#: the `0476` busy window: ticks of `[1600]` since the last trigger.
BUSY_TICKS = 8

SFX_ROLES = (
    ("wall-crash-thud", "level-select-enter"),
    ("bounce-landing",),
    ("wall-bump", "blocked-repeat-thump"),
    ("hud-low-resource-warning",),
    ("menu-action-9",),
)

# INTRO.SND is the one headerless digital sample.  The original intro path
# programs DSP time constant 90 before submitting the complete file.
INTRO_TIME_CONSTANT = 90
INTRO_RATE = 1_000_000 // (256 - INTRO_TIME_CONSTANT)


class SfxEffect(NamedTuple):
    """One decoded SFX.SND effect: 8-bit-unsigned PCM at ``rate`` Hz."""
    tc: int        # the raw SB DSP time constant (first byte of the effect)
    rate: int      # 1_000_000 // (256 - tc)
    pcm: bytes     # unsigned-8 PCM samples


class OriginalPcmAsset(NamedTuple):
    """One byte-exact PCM payload accepted by the faithful native sink."""

    source: str
    effect_id: int | None
    roles: tuple[str, ...]
    rate: int
    pcm: bytes
    digest: str


class OriginalPcmCatalog:
    """The closed set of digital samples recovered from the shipped game."""

    def __init__(self, assets: tuple[OriginalPcmAsset, ...]) -> None:
        self.assets = assets

    def identify(self, pcm: bytes, rate: int) -> OriginalPcmAsset:
        digest = sha256(pcm).hexdigest()
        for asset in self.assets:
            if (asset.rate == int(rate) and asset.digest == digest
                    and asset.pcm == pcm):
                return asset
        raise ValueError(
            "unrecovered SkyRoads PCM command: "
            f"rate={int(rate)} length={len(pcm)} sha256={digest}"
        )


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


def load_original_pcm_catalog(game_root: "str | Path") -> OriginalPcmCatalog:
    """Load every digital sound the recovered original audio path can emit.

    Unknown DMA payloads are deliberately not given a guessed fallback.  A
    native-faithful run fails with the payload identity so the missing source
    can be recovered and added explicitly.
    """
    root = Path(game_root)
    assets = [
        OriginalPcmAsset(
            "SFX.SND",
            effect_id,
            SFX_ROLES[effect_id],
            effect.rate,
            effect.pcm,
            sha256(effect.pcm).hexdigest(),
        )
        for effect_id, effect in enumerate(load_sfx_bank(root / "SFX.SND"))
    ]
    intro = (root / "INTRO.SND").read_bytes()
    assets.append(OriginalPcmAsset(
        "INTRO.SND", None, ("intro-digital-playback",), INTRO_RATE, intro,
        sha256(intro).hexdigest(),
    ))
    return OriginalPcmCatalog(tuple(assets))
