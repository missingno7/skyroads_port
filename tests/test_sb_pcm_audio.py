"""SkyRoads Sound Blaster digital-SFX capture + audio-sink tests.

Two things are locked in here:

1. **The pure resample/mix logic** of
   :class:`skyroads.audio.sink.SkyroadsAudioSink`
   (8-bit-unsigned PCM -> mixer-rate int samples), which needs no VM or pygame.

2. **The observer guarantee**: ``capture_sb_pcm=True`` reads a single-cycle
   DMA-out block for playback without writing machine memory or advancing the
   CPU. This keeps presentation capture outside authoritative execution state
   while proving the SkyRoads runtime wires capture mode correctly.
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

from skyroads.audio.sink import SkyroadsAudioSink  # noqa: E402  (pure logic, no VM)


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
def test_capture_mode_reads_dma_without_changing_cpu_or_memory() -> None:
    from skyroads.runtime import create_game_runtime

    payload = bytes(range(32))

    def prepare(capture: bool):
        rt = create_game_runtime(_EXE, capture_sb_pcm=capture)
        sb = rt.dos.sound_blaster
        channel = sb.channels[sb.dma]
        channel.restore_state({
            "page": 0x09,
            "base_addr": 0x0100,
            "base_count": len(payload) - 1,
            "cur_addr": 0x0100,
            "cur_count": len(payload) - 1,
            "mode": 0x49,
            "masked": False,
            "flipflop_high": False,
        })
        for index, value in enumerate(payload):
            rt.cpu.mem.wb_phys(0x90100 + index, value)
        before = hashlib.sha256(bytes(rt.cpu.mem.data)).hexdigest()
        sb.detect_irq_limit = 0
        sb.port_write(sb.base + 0x0C, 0x14)
        sb.port_write(sb.base + 0x0C, len(payload) - 1)
        sb.port_write(sb.base + 0x0C, 0)
        return rt, before

    base, base_before = prepare(False)
    cap, cap_before = prepare(True)

    assert base.dos.sound_blaster.detection_only is True
    assert cap.dos.sound_blaster.detection_only is False
    assert base.dos.sound_blaster.pcm_out == b""
    assert cap.dos.sound_blaster.pcm_out == payload
    assert base.dos.sound_blaster._dma_requests == 1
    assert cap.dos.sound_blaster._dma_requests == 1

    assert base.cpu.instruction_count == cap.cpu.instruction_count
    base_after = hashlib.sha256(bytes(base.cpu.mem.data)).hexdigest()
    cap_after = hashlib.sha256(bytes(cap.cpu.mem.data)).hexdigest()
    assert base_before == base_after
    assert cap_before == cap_after
    assert base_after == cap_after
