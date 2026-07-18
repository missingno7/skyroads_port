"""THE STITCH: hand-recovered implementations patched over the autolifted CPUless corpus.

The generated corpus **is** the game's real control flow -- by construction, because it
was lifted from the original's own code, and proven byte-exact from cold start over a
full playthrough (672 frames, intro -> menu -> level select -> play -> death -> back).
So it drives.  Hand-recovered code does not re-implement the flow; it PATCHES individual
addresses inside it, and every address with no manual implementation is served by the
generated function automatically.  The whole thing therefore holds together without
anyone hand-wiring screen-to-screen transitions -- which is exactly where the previous
hand-written port went wrong: ``skyroads/native/menus.py`` and ``level_select.py`` carry
ZERO recovered-address anchors between them, because their flow was inferred from the
screen rather than derived from the program (see ``tools/absorption_ledger.py --native``).

Adopted from the Overkill port's ``overkill/cpuless_overrides.py``, whose shape and
invariants this mirrors deliberately: two ports agreeing is the argument for promoting
the seam into ``dos_re`` as the general DOS_RE 2.0 mechanism.

WHY THIS SHAPE (the constraint that picks it):

    A generated module binds its callees AT IMPORT TIME with direct imports --
        from skyroads.recovered.func_1010_2e6c import func_1010_2e6c
    -- so there is no per-call resolver to hook.  The only seam is the MODULE OBJECT,
    and it only works if the override is installed BEFORE anything imports it.  Hence
    :func:`install_overrides` shadows ``sys.modules`` ahead of the corpus load.  Dynamic
    transfers route through ``_dyncall`` -> ``DISPATCH``, which imports BY MODULE NAME,
    so the same shadow serves those too.

INVARIANTS:

* An override MUST match the generated contract exactly:
  ``(mem, plat, *, _base, _df, _flags_in, **regs) -> (outputs_dict, _compat)``.
  Generated callers unpack ``_o, _c`` positionally.
* An override for an address the corpus does not contain is FAIL-LOUD, never a silent
  no-op.  That is the typo guard, and it is what keeps this registry honest as the
  corpus is regenerated -- an absorbed address that disappears from the census must
  break the build, not quietly stop being applied.
* THE GENERATED BODY STAYS AVAILABLE as the differential reference (:func:`generated`).
  An override that only needs to ADD an effect should DELEGATE to it rather than
  reimplement, so the returned outputs and flags remain the generated ones and cannot
  drift.  Delegation is the preferred absorption shape; wholesale replacement is the
  exception and needs its own evidence.
* Absorption is gated on EVIDENCE, not enthusiasm: an island may be stitched only at
  ``VERIFIED``/``CANONICAL`` on the ``dos_re.islands`` ladder.  Most of this port's
  islands are ``ASM_MATCHED`` -- weaker than the byte-exact standard the generated
  corpus already meets -- so stitching them would LOWER the proof standard.

Usage (see scripts/play_cpuless.py):

    from skyroads.cpuless_overrides import install_overrides
    install_overrides()              # BEFORE the first corpus import
"""
from __future__ import annotations

import importlib
import sys
from typing import Callable

#: The package holding the autolifted corpus these overrides patch.
RECOVERED_PKG = "skyroads.recovered"
#: The game's CS for every recovered code address.
_CS = 0x1010

#: Modules this process shadowed, so :func:`uninstall_overrides` can undo it.
_INSTALLED: "dict[str, object]" = {}


def module_name(addr: str) -> str:
    """``'1010:04C0'`` -> ``'skyroads.recovered.func_1010_04c0'``."""
    seg, off = addr.split(":")
    return f"{RECOVERED_PKG}.func_{seg.lower()}_{off.lower()}"


def func_name(addr: str) -> str:
    """``'1010:04C0'`` -> ``'func_1010_04c0'``."""
    seg, off = addr.split(":")
    return f"func_{seg.lower()}_{off.lower()}"


def generated(addr: str) -> Callable:
    """The GENERATED body for ``addr`` -- the differential reference.

    Imported from the real module even while an override shadows it, so a delegating
    override can call the mechanical implementation it is wrapping.
    """
    name = module_name(addr)
    real = _INSTALLED.get(name)
    if real is not None:                      # shadowed: hand back the original module
        return getattr(real, func_name(addr))
    return getattr(importlib.import_module(name), func_name(addr))


#: address -> replacement callable.  EMPTY BY DESIGN: the seam lands first, absorption
#: follows one evidence-gated address at a time.  With no entries the composite is
#: bit-for-bit the generated program, which is what makes adopting the seam a no-op
#: that the existing cold-start differential already proves.
OVERRIDES: "dict[str, Callable]" = {}


def install_overrides(addrs=None) -> "list[str]":
    """Shadow the corpus modules for every override.  Returns the addresses installed.

    MUST run before anything imports the corpus.  An address absent from the corpus
    raises: a stale entry is a bug in this registry, not something to skip.
    """
    import types

    selected = OVERRIDES if addrs is None else {a: OVERRIDES[a] for a in addrs}
    installed = []
    for addr, impl in selected.items():
        name = module_name(addr)
        try:
            real = importlib.import_module(name)
        except ModuleNotFoundError as e:       # noqa: PERF203 -- fail loud, per-address
            raise RuntimeError(
                f"cpuless override for {addr} has no generated counterpart "
                f"({name}). The corpus was regenerated and this address is no longer "
                f"in it -- fix or drop the override; it must not silently stop "
                f"applying.") from e
        shadow = types.ModuleType(name)
        shadow.__dict__.update(real.__dict__)
        setattr(shadow, func_name(addr), impl)
        _INSTALLED[name] = real
        sys.modules[name] = shadow
        installed.append(addr)
    return installed


def uninstall_overrides() -> None:
    """Restore every shadowed module (tests that need the pristine corpus)."""
    for name, real in _INSTALLED.items():
        sys.modules[name] = real
    _INSTALLED.clear()
