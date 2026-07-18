"""THE STITCH: hand-recovered implementations patched over the autolifted CPUless corpus.

The generated corpus **is** the game's real control flow -- by construction, because it
was lifted from the original's own code, and proven byte-exact from cold start over a
full playthrough (672 frames, intro -> menu -> level select -> play -> death -> back).
So it drives.  Hand-recovered code does not re-implement the flow; it PATCHES individual
addresses inside it, and every address with no manual implementation is served by the
generated function automatically.  The whole thing therefore holds together without
anyone hand-wiring screen-to-screen transitions -- which is exactly where the previous
hand-written port went wrong: its menu and level-select modules carried ZERO
recovered-address anchors between them, because their flow was inferred from the screen
rather than derived from the program (see ``tools/absorption_ledger.py --native``).

THE MECHANISM NOW LIVES IN ``dos_re.lift.standalone``.  This module is the port's
registry and nothing else.  The seam was developed here and in the Overkill port
simultaneously, which is what argued for promoting it; keeping a second copy after that
is how the two drift apart, and a stitch that behaves differently in two ports is worse
than one that behaves wrongly in both.  The shared version is also strictly better than
this copy was: it normalises addresses through ``int(x, 16)``, so ``'1010:4C0'`` and
``'1010:04C0'`` name the same module (the local string-lowercase silently did not), and
it restores overridden bindings BY NAME rather than by attribute presence.

Why the mechanism is not merely ``sys.modules`` shadowing, in one line each -- the full
argument is in ``dos_re.lift.standalone`` and pinned by its tests:

* a caller that already imported the callee holds a DIRECT reference, so shadowing does
  nothing for every call through it, silently;
* ``_dyncall`` memoises the resolved closure on first call, so a late install is never
  seen by the dynamic-dispatch path.

Both ports' suites passed before this was fixed, because each only exercised the
favourable import order.

ABSORPTION IS GATED ON EVIDENCE, NOT ENTHUSIASM.  An address may appear in
:data:`OVERRIDES` only after :mod:`dos_re.lift.shadow` has VERIFIED its body against the
generated one on real calls, comparing the WHOLE contract -- every output register,
flags, fmask, virtual-time cost, and the ordered memory-write log -- with no exemptions.
Most of this port's 42 islands are ``ASM_MATCHED`` ("diffed on captured cases"), which
is WEAKER than the byte-exact standard the generated corpus already meets, so stitching
one on its recorded status would LOWER the proof standard.  Shadow mode is the rung
between, and ``tests/test_cpuless_overrides.py`` pins the gate.

Usage (see scripts/play_cpuless.py):

    from skyroads.cpuless_overrides import install_overrides
    install_overrides()              # BEFORE the first corpus import
"""
from __future__ import annotations

from typing import Callable

from dos_re.lift import standalone as _s

from skyroads.island_bodies import BODIES

#: The package holding the autolifted corpus these overrides patch.
RECOVERED_PKG = "skyroads.recovered"

#: address -> replacement callable.  Absorption is one evidence-gated address at a time.
#:
#: ``1010:04C0`` perspective_row_offset, admitted on:
#:   6,000 seeded random states, both paths forced (3,228 in-range / 2,772 out-of-range);
#:   demo_cold_20260718_003412    -- 14,802 calls  {104: 12822, 19: 1980};
#:   demo_colde2e_full_20260713   -- 125,728 calls {104: 125604, 19: 124};
#:   E2E cold differential vs the pure ASM oracle with it DRIVING -- 261 and 672 frames.
#: Two demos deliberately: the spine demo NEVER takes the short path, so evidence
#: gathered there alone yields the constant cost 104 and is silently wrong.
#:
#: Each later admission carries its own evidence on its island's ``oracle_link``
#: -- the call population AND the arm/block coverage it was proven over, stated
#: narrowly -- rather than being restated here where it would drift:
#:   ``1010:1631`` road_segment_clip     -- skyroads/handrecovered/renderer.py
#:   ``1010:0533`` ship_fell_off         -- .../collision_response.py
#:   ``1010:1732`` road_object_visible   -- skyroads/handrecovered/renderer.py
#:   ``1010:0F62`` stencil_blit          -- skyroads/handrecovered/blit.py
OVERRIDES: "dict[str, Callable]" = dict(BODIES)


def module_name(addr: str) -> str:
    """``'1010:04C0'`` -> ``'skyroads.recovered.func_1010_04c0'``."""
    return f"{RECOVERED_PKG}.{_s.module_name(addr)}"


def func_name(addr: str) -> str:
    """``'1010:04C0'`` -> ``'func_1010_04c0'``."""
    return _s.module_name(addr)


def generated(addr: str) -> Callable:
    """The GENERATED body for ``addr`` -- the differential reference.

    Reachable from the real module even while an override shadows it, so a delegating
    override can call the mechanical implementation it wraps.  Reach the original THIS
    WAY and never through a cached direct reference: a rebind keyed on "holds the
    original function" would repoint that cache too, and the differential would then
    compare the override against ITSELF and still pass.
    """
    return _s.generated(RECOVERED_PKG, addr)


def install_overrides(addrs=None) -> "list[str]":
    """Shadow the corpus modules for every override.  Returns the addresses installed.

    MUST run before anything imports the corpus.  An address absent from the corpus
    raises: a stale entry is a bug in this registry, not something to skip -- an absorbed
    address that disappears from the census must break the build, not quietly stop being
    applied.
    """
    selected = OVERRIDES if addrs is None else {a: OVERRIDES[a] for a in addrs}
    return _s.install_overrides(RECOVERED_PKG, selected)


def uninstall_overrides() -> None:
    """Restore every shadowed module (tests that need the pristine corpus)."""
    _s.uninstall_overrides(RECOVERED_PKG)
