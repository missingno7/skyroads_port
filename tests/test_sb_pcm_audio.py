"""SkyRoads Sound Blaster digital-SFX capture + audio-sink tests.

Two things are locked in here:

1. **The pure resample/mix logic** of :class:`skyroads.audio.SkyroadsAudioSink`
   (8-bit-unsigned PCM -> mixer-rate int samples), which needs no VM or pygame.

2. **The observer guarantee**: booting with ``capture_sb_pcm=True`` (which reads
   each single-cycle DMA-out block out of memory for playback) must not perturb
   the CPU timeline at all versus the detection-only stub the game normally runs
   against — same instruction count and same full memory image — while actually
   capturing the intro's INTRO.SND digital sample.  This is what lets the viewer
   play the game's PCM effects without breaking demo determinism.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# numpy is a frontend ("viewer") dependency; the core CI env does not install it.
# Skip this whole module (the SB audio-sink maths need numpy) when it is absent,
# rather than breaking collection — same policy as the other frontend-ring tests.
np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]

from skyroads.audio import SkyroadsAudioSink  # noqa: E402  (pure logic, no VM)


class _FakeSink(SkyroadsAudioSink):
    """Bypass the pygame/OPL __init__ to unit-test the SB resample/mix maths."""

    def __init__(self, rate: int) -> None:
        self._np = np
        self._rate = rate
        self._chunk = rate // 30
        self._sb = None
        self._log_cursor = 0
        self._pcm_cursor = 0
        self._sfx = np.zeros(0, dtype=np.float32)
        # SFX backlog cap (part of _enqueue_sfx's contract since the wall-clock
        # pacing fix); large enough that these small maths cases never trim.
        self._sfx_cap = int(rate * SkyroadsAudioSink.MAX_SFX_S)


def test_unsigned8_pcm_is_recentred_and_scaled() -> None:
    sink = _FakeSink(rate=8000)
    # A block already at the mixer rate is not resampled: 0x80 -> 0, full swing maps out.
    sink._enqueue_sfx(bytes([128, 128, 255, 0]), rate=8000)
    out = sink._take_sfx(4)
    assert out is not None
    assert list(out[:2]) == [0, 0]                 # 0x80 == silence
    assert out[2] == (255 - 128) * 256             # +full
    assert out[3] == (0 - 128) * 256               # -full


def test_resample_changes_length_by_rate_ratio() -> None:
    sink = _FakeSink(rate=44100)
    sink._enqueue_sfx(bytes([128] * 1000), rate=8000)   # 1000 samples @ 8 kHz
    # resampled to 44100 Hz -> ~ 1000 * 44100/8000 = 5512 samples
    assert abs(len(sink._sfx) - round(1000 * 44100 / 8000)) <= 1


def test_overlapping_effects_sum() -> None:
    sink = _FakeSink(rate=8000)
    sink._enqueue_sfx(bytes([255, 255]), rate=8000)     # +full, +full
    sink._enqueue_sfx(bytes([255, 255]), rate=8000)     # summed on top
    out = sink._take_sfx(2)
    assert out[0] == 2 * (255 - 128) * 256


def test_take_sfx_none_when_idle() -> None:
    sink = _FakeSink(rate=8000)
    assert sink._take_sfx(16) is None


# --- integration: capture is a byte-exact observer -----------------------------

_EXE = ROOT / "assets" / "SKYROADS.EXE"
_needs_game = pytest.mark.skipif(
    not _EXE.is_file(),
    reason="assets/SKYROADS.EXE not present — game files are never committed")


@_needs_game
def test_capture_mode_is_byte_identical_and_captures_intro_pcm() -> None:
    from dos_re.interrupts import deliver_interrupt
    from dos_re.cpu import HaltExecution
    from dos_re.execution import plan_execution
    from scripts.play import SkyroadsFrontend
    from skyroads.execution import catalog, configuration, coverage
    from skyroads.runtime import create_game_runtime

    faithful = plan_execution(
        configuration("development", "faithful"), coverage(), catalog())
    frontend = SkyroadsFrontend(ROOT)

    def boot_and_run(capture: bool):
        rt = create_game_runtime(_EXE, capture_sb_pcm=capture)
        frontend.bind_execution_plan(rt, faithful)
        for _ in range(140):                       # far enough to hit INTRO.SND DMA (~f121)
            try:
                for _ in range(6):
                    deliver_interrupt(rt, 0x08)
                rt.cpu.run(30_000)
            except HaltExecution:
                break
        return rt

    base = boot_and_run(False)
    cap = boot_and_run(True)

    # The detection-only stub captures nothing; capture mode pulls the intro sample.
    assert base.dos.sound_blaster.detection_only is True
    assert cap.dos.sound_blaster.detection_only is False
    assert len(cap.dos.sound_blaster.pcm_out) > 0
    assert cap.dos.sound_blaster._dma_requests >= 1

    # ...yet the CPU timeline is unaffected: same steps + same whole memory image.
    assert base.cpu.instruction_count == cap.cpu.instruction_count
    assert hashlib.sha256(bytes(base.cpu.mem.data)).hexdigest() \
        == hashlib.sha256(bytes(cap.cpu.mem.data)).hexdigest()
