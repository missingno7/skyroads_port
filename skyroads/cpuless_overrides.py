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
    -- so there is no per-call resolver to hook.  The primary seam is therefore the
    MODULE OBJECT: :func:`install_overrides` shadows ``sys.modules`` ahead of the
    corpus load.

    Shadowing alone is NOT sufficient, and the gap was silent.  It only affects
    imports that have not happened yet, so any caller imported earlier keeps a
    direct reference to the original and the override does nothing for every call
    through it.  That is not hypothetical: :func:`install_shadow` itself imports
    the module (via :func:`generated`), which eagerly binds THAT module's callees,
    so installing along a call chain (4591, then 4331, then 6168) guarantees the
    later installs miss.  A counter built exactly that way reported ZERO calls for
    functions a traceback proved were executing.

    So installation also RETRO-PATCHES already-bound references (:func:`_retro_patch`)
    and clears ``_dyncall._cache`` -- the latter because DISPATCH stores module and
    function NAMES (resolved late, which is fine) but ``_dyncall`` memoises the
    resolved closure on first call, so a shadow installed after the first dynamic
    transfer would never be seen.  With both, installation order stops mattering;
    ``tests/test_cpuless_overrides.py`` pins the caller-imported-first case.

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
        fname = func_name(addr)
        original = getattr(real, fname)
        shadow = types.ModuleType(name)
        shadow.__dict__.update(real.__dict__)
        setattr(shadow, fname, impl)
        _INSTALLED[name] = real
        sys.modules[name] = shadow
        _retro_patch(fname, original, impl)
        installed.append(addr)
    return installed


def _retro_patch(fname: str, original, impl) -> None:
    """Rebind ALREADY-IMPORTED callers, and drop the dynamic-dispatch cache.

    Shadowing ``sys.modules`` only affects imports that have not happened yet,
    and a generated module binds its callees at import time
    (``from ...func_1010_XXXX import func_1010_XXXX``). So any caller imported
    before the shadow keeps a direct reference to the original and the override
    silently does nothing -- silently being the whole problem.

    That is not a theoretical edge: :func:`install_shadow` calls
    :func:`generated`, which IMPORTS the module, which eagerly binds ITS callees.
    Installing along a call chain (4591, then 4331, then 6168) therefore
    guarantees the later installs miss, because importing 4591 already bound the
    real 4331. A counter built that way read ZERO calls for functions a traceback
    proved were running.

    ``_dyncall`` needs the same treatment for a different reason: DISPATCH stores
    module/function NAMES (so it resolves late, which is fine), but ``_dyncall``
    memoises the resolved closure in ``_cache`` on first call. A shadow installed
    after the first dynamic transfer to an address would never be seen.
    """
    for mod in list(sys.modules.values()):
        if mod is None or not getattr(mod, "__name__", "").startswith(RECOVERED_PKG):
            continue
        if getattr(mod, fname, None) is original:
            setattr(mod, fname, impl)
    dyn = sys.modules.get(f"{RECOVERED_PKG}._dyncall")
    if dyn is not None and hasattr(dyn, "_cache"):
        dyn._cache.clear()


def install_shadow(addr: str, checker: Callable, snapshot: Callable = None) -> None:
    """Run an island in SHADOW against the generated body -- the rung below running.

    The generated function still drives: it computes the outputs, the flags and the
    virtual-time cost, and its result is returned unchanged, so behaviour is provably
    untouched. The island is invoked alongside it and ``checker`` asserts they agree.

    This exists because of a constraint the first absorption hit immediately. An
    island computes a VALUE; it cannot produce a cycle COST, and cost feeds the
    frame scheduler that the cold-start differential compares. So an island cannot
    simply take over a body without a virtual-time contract. But it can be PROVEN
    against every real call the game makes -- which is a far stronger claim than the
    captured-case diffing behind ``ASM_MATCHED``, and it costs nothing but runtime.

    ``checker(mem, kwargs_in, outputs, compat, pre)`` must raise on disagreement.
    An island that WRITES memory cannot be checked from the post-state alone -- the
    generated body has already overwritten its own inputs -- so ``snapshot(mem, kw)``
    runs BEFORE the call and its result is handed to the checker as ``pre``. That is
    what lets a decompressor be re-run independently and its output compared.

    Shadows are a verification mode: install them from a gate, never from the
    shipped runner.
    """
    gen = generated(addr)

    def shadowed(mem, *args, **kw):
        pre = snapshot(mem, kw) if snapshot is not None else None
        out, compat = gen(mem, *args, **kw)
        checker(mem, kw, out, compat, pre)     # raises on disagreement
        return out, compat

    shadowed.__name__ = func_name(addr)
    OVERRIDES[addr] = shadowed
    install_overrides([addr])


def uninstall_overrides() -> None:
    """Restore every shadowed module (tests that need the pristine corpus)."""
    for name, real in _INSTALLED.items():
        sys.modules[name] = real
        fname = name.rsplit(".", 1)[1]
        impl_now = None
        for mod in list(sys.modules.values()):
            if mod is None or not getattr(mod, "__name__", "").startswith(RECOVERED_PKG):
                continue
            if getattr(mod, fname, None) is not None and mod is not real:
                setattr(mod, fname, getattr(real, fname))
    dyn = sys.modules.get(f"{RECOVERED_PKG}._dyncall")
    if dyn is not None and hasattr(dyn, "_cache"):
        dyn._cache.clear()
    _INSTALLED.clear()
