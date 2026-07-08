"""Adapter side of the frame verifier (dos_re.frame_verify.run_frame_verifier).

To fill in once the present/timer/retrace routines are located (docs/porting_
new_game.md step 3): boundary_hooks, a sample_builder (framebuffer first),
and reference_env_hooks for the ASM-side oracle's hardware waits.
"""
from __future__ import annotations

WIDTH, HEIGHT = 320, 200

BOUNDARY_HOOKS: tuple[tuple[tuple[int, int], str], ...] = ()

REFERENCE_ENV_HOOKS: set[tuple[int, int]] = set()
