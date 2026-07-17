"""Verify the pure render orchestrator (`skyroads.recovered_native.render_params`,
= `1010:0C98`) against real VM invocations.

Fixture: 8 real `0C98` calls captured from the level-14 demo — the DGROUP words
the pure function actually reads, plus the 8 parameters the ASM then pushed to
`2D1F`. Live verification was 40/40 (params byte-equal + render/skip decisions
agree) — see run_status.md 2026-07-12. The dirty-cache SKIP path did not occur
in the captured window (every invocation rendered), so it is covered by the
synthetic test below, not the fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

from skyroads.recovered_native.render_params import RenderParams, compute_render_params

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "render_params_0c98.json").read_text())


def _run(case: dict):
    words = {int(k, 16): v for k, v in case["reads"].items()}
    writes: dict[int, int] = {}

    def rw(off: int) -> int:
        off &= 0xFFFF
        if off in writes:
            return writes[off]
        assert off in words, (
            f"compute_render_params read 0x{off:04X}, which the fixture never "
            "captured -- the implementation's read-set changed; regenerate the fixture")
        return words[off]

    def ww(off: int, v: int) -> None:
        writes[off & 0xFFFF] = v & 0xFFFF

    return compute_render_params(rw, ww, case["arg"]), writes


def test_render_params_match_real_0c98_calls() -> None:
    assert _CASES, "fixture empty"
    for i, case in enumerate(_CASES):
        dec, _ = _run(case)
        if case["params"] is None:
            assert dec.skipped, f"case {i}: VM skipped, native rendered"
        else:
            assert not dec.skipped, f"case {i}: VM rendered, native skipped"
            assert list(dec.params) == case["params"], f"case {i}"
            assert dec.sprite_idx == case["sprite_idx"], f"case {i}"


def test_render_params_updates_the_dirty_cache() -> None:
    """A rendered frame must write the [0E1C..0E26] cache with (lateral, af1c,
    af2c_eff, sprite idx, page) so the NEXT identical frame skips."""
    case = next(c for c in _CASES if c["params"] is not None)
    dec, writes = _run(case)
    words = {int(k, 16): v for k, v in case["reads"].items()}
    assert writes[0x0E1C] == words[0x9618] and writes[0x0E1E] == words[0x961A]
    assert writes[0x0E20] == words[0xAF1C]
    assert writes[0x0E24] == dec.sprite_idx
    assert writes[0x0E26] == words[0x9334]

    # Re-running over the updated cache (same sim state) must SKIP -- the
    # dirty-cache path the captured window never exercised.
    merged = dict(words)
    merged.update(writes)
    dec2 = compute_render_params(
        lambda o: merged[o & 0xFFFF], lambda o, v: None, case["arg"])
    # NOTE: the ASM compares RAW af2c against the cached af2c_eff; with
    # offscreen arg != 0 they differ by 0x80 and the frame re-renders. Use the
    # cache-consistent case only when arg == 0.
    if case["arg"] == 0:
        assert dec2.skipped
    else:
        assert not dec2.skipped


def test_page_flip_destination_alternates() -> None:
    """[003C]==0 -> the render dest is the page NOT being shown: A200 when
    [9334]==0, A000 when [9334]!=0 (the ASM then xors [9334])."""
    case = next(c for c in _CASES if c["params"] is not None)
    words = {int(k, 16): v for k, v in case["reads"].items()}
    if words.get(0x003C, 0) != 0:
        import pytest
        pytest.skip("captured case renders off-screen, not to the VGA pages")
    dest = case["params"][7]
    assert dest == (0xA000 if words[0x9334] else 0xA200)
