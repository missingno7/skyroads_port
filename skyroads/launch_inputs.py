"""Canonical launch-input adapters for the one SkyRoads runtime."""
from __future__ import annotations

from skyroads.identities import CODE_SEG


LEVEL_SELECTION_IP = 0x5180
LEVEL_COUNT = 30
SELECTED_LEVEL_OFFSET = 0x9332
DIRECT_LEVEL_ADAPTER_ID = "skyroads:direct-level/1010:5180-selection:v1"


def validate_level(level: int) -> int:
    level = int(level)
    if not 0 <= level < LEVEL_COUNT:
        raise ValueError(
            f"SkyRoads level must be 0..{LEVEL_COUNT - 1}, got {level}"
        )
    return level


def install_direct_level_launch(runtime, level: int | None) -> None:
    """Resolve the first generated/original level-selection interaction once.

    ``1010:5180`` is the original level-selection function: it owns the menu,
    writes the selected level to ``DS:[9332]``, and returns zero when a level
    was confirmed.  ``--level`` supplies that one interaction directly.  The
    generated loader, gameplay caller, gameplay provider, and post-gameplay
    routing remain selected by the normal execution plan.  The original menu
    implementation is restored before gameplay starts, so every later finish,
    death, or abort returns to the normal generated level-selection flow.
    """
    if level is None:
        return
    level = validate_level(level)
    previous = getattr(runtime, "_skyroads_direct_level_installed", None)
    if previous is not None:
        if previous != level:
            raise RuntimeError(
                f"runtime already requests direct level {previous}, not {level}"
            )
        return

    cpu = runtime.cpu
    hooks = cpu.replacement_hooks
    names = cpu.hook_names
    key = (CODE_SEG, LEVEL_SELECTION_IP)
    selected = hooks.get(key)
    selected_name = names.get(key)

    def apply_once(current_cpu) -> None:
        # Supply exactly the authoritative result of a confirmed selection.
        # This is a near-function adapter, so return through the caller's real
        # stack frame after restoring the selected menu implementation.
        current_cpu.mem.ww(current_cpu.s.ds, SELECTED_LEVEL_OFFSET, level)
        if selected is None:
            hooks.pop(key, None)
            names.pop(key, None)
        else:
            hooks[key] = selected
            if selected_name is None:
                names.pop(key, None)
            else:
                names[key] = selected_name
        current_cpu.s.ax = 0
        current_cpu.s.ip = current_cpu.pop()
        runtime._skyroads_direct_level_applied = level

    # This pre-adapter consumes no guest virtual time: the next CPU step runs
    # the selected generated hook or the untouched original entry.
    apply_once.owns_time = True
    hooks[key] = apply_once
    names[key] = DIRECT_LEVEL_ADAPTER_ID
    runtime._skyroads_direct_level_installed = level
