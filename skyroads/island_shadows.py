"""Island-driven bodies checked against the generated bodies they claim to reproduce.

An island's ``status`` is a claim about evidence. ``ASM_MATCHED`` means "diffed on
captured cases" -- weaker than the standard the generated corpus already meets
(672 frames byte-exact, VGA plane and DAC palette, from cold start). Promoting an
island on that basis would lower the proof standard, so the ladder needs a rung
between "diffed once" and "drives the program".

Shadow mode is that rung, and it now lives in :mod:`dos_re.lift.shadow` because
Overkill needs the same thing. The generated body drives -- outputs, flags and
cost are its own, so behaviour is provably unchanged -- and the candidate from
:mod:`skyroads.island_bodies` runs beside it on every call the real game makes,
with every observable compared.

WHAT CHANGED, and why it matters more than it looks: this module used to pass a
``checker`` callback, which left it free to compare whatever it liked. It
compared **AX and nothing else** -- not the other six outputs, not flags, not
fmask, not the 25 words the body leaves on the stack -- while tallying cost into
a counter that read like an assertion and asserted nothing. The candidate is now
a drop-in with the generated signature, so the comparison is total by
construction and the thing being proven is the thing that would ship.

The shadow checks run directly in the test suite.
         python scripts/check_all.py            (gated there too)

NOT SHADOWED: ``1010:3A96`` unpack_animation_segment
    It has no drop-in body, only a pure island, and the checker that used to
    stand in for one compared exactly ONE 64 KB segment per call -- the one named
    by the post-state ``es`` -- and neither AX, BX, CX, DS, DX, flags, fmask nor
    cost. Its recorded "full 64 KB segment byte-exact" evidence is therefore
    materially narrower than its VERIFIED status suggests. An inherited survey
    also reports the pre-state snapshot overlooking ~39 KB of overlap accumulated
    by preceding passes; that specific figure is NOT re-measured here and is
    recorded as a caution, not a result. Either way it is further out than 04C0
    and is deliberately left alone.

NOT SHADOWABLE AT ALL: ``1010:1B49`` and ``1010:3B17``
    Their bodies reach ``1010:03C2``, which does ``inp(0x61)``/``outp(0x61, 3)``
    on the PC speaker. :class:`dos_re.lift.shadow._NoEffectPlat` refuses every
    platform attribute precisely so this surfaces as an error on the first call
    rather than as a run perturbed by a doubled device write that every compared
    observable agrees about. An address whose body performs platform I/O is not
    shadowable by this instrument, and that is a recorded limit, not a to-do.

NEXT UP, and what it will cost -- ``1010:41A0`` masked_blit (read, not attempted)
    Eight blocks, no callees, and an island already exists, so it looks like the
    natural successor to 3A22. Three things make it more than that, all read off
    the generated body rather than guessed:

      * it is SELF-MODIFYING. 41A0 writes the low threshold byte into its OWN
        code at ``cs:41E9`` and the middle loop re-reads it from there on every
        pixel. The write is just another entry in the log, but a candidate that
        uses the threshold as a value instead of re-reading it is making an
        assumption the ASM does not (it holds only while ES != 0x1010).
      * the middle band is a ``loop``, so a computed count of 0 means 65,536
        pixels; the top and bottom bands are ``rep movsb``, where 0 means none.
        Two opposite readings of a zero count in one function.
      * ``masked_blit`` takes its thresholds eagerly and its bands as counts, so
        both would have to become lazy -- and ``present_rect`` (1010:4201) and
        ``skyroads/native/frame.py`` call it as it stands.

    Also worth pinning when it is attempted: the middle-band pixel count is read
    from ``ss:[0x9612]``, not ds -- the two are equal in this program, so a body
    that used ds would agree on every real call and still be wrong.
"""
from __future__ import annotations

from dos_re.lift.shadow import Verdict, install_shadows, records, report, reset, verdict

from skyroads.cpuless_overrides import RECOVERED_PKG
from skyroads.island_bodies import BODIES

__all__ = ["Verdict", "install_all", "records", "report", "reset", "verdict"]

#: No exemptions. Every output, flags, fmask, cost and the ordered byte-write log
#: are compared for every shadowed address. If that ever has to change, the
#: exemption goes here with a written reason -- see :class:`dos_re.lift.shadow.Exemption`.
EXEMPTIONS: dict = {}


def install_all() -> list:
    """Install every shadow. Call BEFORE the corpus is imported."""
    reset()
    return install_shadows(RECOVERED_PKG, BODIES, exemptions=EXEMPTIONS)
