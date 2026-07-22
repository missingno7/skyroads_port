"""Closed-world contracts for faithful native SkyRoads audio playback."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time

import pytest

np = pytest.importorskip("numpy")

from dos_re.opl3_fast import OPL3Fast  # noqa: E402
from skyroads.audio.sink import (  # noqa: E402
    EnhancedStereoAudioSink,
    NativeFaithfulAudioSink,
)
from skyroads.native.sfx import load_original_pcm_catalog  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


class _Sound:
    def __init__(self, samples) -> None:
        self.samples = np.array(samples, copy=True)


class _Channel:
    def __init__(self) -> None:
        self.played = []
        self.queued = None

    def play(self, sound) -> None:
        self.played.append(sound)
        self.queued = None

    def queue(self, sound) -> None:
        self.queued = sound

    def get_busy(self) -> bool:
        return bool(self.played)

    def get_queue(self):
        return self.queued


class _Mixer:
    def __init__(self) -> None:
        self.channels = {}
        self.count = 2

    def get_init(self):
        return (44100, -16, 2)

    def get_num_channels(self) -> int:
        return self.count

    def set_num_channels(self, count: int) -> None:
        self.count = count

    def Channel(self, index: int):
        return self.channels.setdefault(index, _Channel())


class _Pygame:
    def __init__(self) -> None:
        self.mixer = _Mixer()
        self.sndarray = SimpleNamespace(make_sound=lambda samples: _Sound(samples))


class _SoundBlaster:
    detection_only = False

    def __init__(self) -> None:
        self.log = []
        self.pcm_out = bytearray()


class _Dos:
    def __init__(self) -> None:
        self.sound_blaster = _SoundBlaster()
        self.adlib_callback = None

    def set_adlib_callback(self, callback, emit_current=False) -> None:
        self.adlib_callback = callback

    def set_speaker_callback(self, callback, emit_current=False) -> None:
        self.speaker_callback = callback


def _runtime():
    return SimpleNamespace(dos=_Dos())


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_catalog_is_closed_over_the_shipped_pcm_sources() -> None:
    catalog = load_original_pcm_catalog(ASSETS)
    assert len(catalog.assets) == 6
    assert [asset.effect_id for asset in catalog.assets[:-1]] == list(range(5))
    assert catalog.assets[-1].source == "INTRO.SND"
    assert catalog.assets[0].roles == ("wall-crash-thud", "level-select-enter")
    assert catalog.assets[1].roles == ("bounce-landing",)
    for asset in catalog.assets:
        assert catalog.identify(asset.pcm, asset.rate) == asset

    with pytest.raises(ValueError, match="unrecovered SkyRoads PCM command"):
        catalog.identify(b"invented replacement", 8000)
    with pytest.raises(ValueError, match="unrecovered SkyRoads PCM command"):
        catalog.identify(catalog.assets[0].pcm, catalog.assets[0].rate + 1)


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_native_faithful_sink_forces_opl3_fast_and_exact_pcm() -> None:
    pygame = _Pygame()
    runtime = _runtime()
    sink = NativeFaithfulAudioSink(
        pygame, runtime, 60, game_root=ASSETS, now=lambda: 0.0,
    )

    assert sink.available
    assert isinstance(sink._opl, OPL3Fast)
    assert sink.opl_label == "opl3-fast"
    assert runtime._skyroads_audio_sink is sink

    runtime.dos.adlib_callback(0x20, 0x01)
    runtime.dos.adlib_callback(0xB0, 0x31)
    asset = sink._pcm_catalog.assets[1]
    sink._enqueue_sfx(asset.pcm, asset.rate, pan=0.9)

    diagnostic = sink.diagnostics()
    assert diagnostic["opl_writes"] == 2
    assert diagnostic["sfx_plays"] == 1
    assert diagnostic["last_sfx"]["effect_id"] == 1
    assert diagnostic["stereo"].startswith("centered dual-mono")
    played = pygame.mixer.channels[2].played[-1].samples
    assert np.array_equal(played[:, 0], played[:, 1])

    with pytest.raises(ValueError, match="unrecovered SkyRoads PCM command"):
        sink._enqueue_sfx(b"placeholder", 8000)


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_enhanced_stereo_pans_only_ship_local_original_dma() -> None:
    pygame = _Pygame()
    runtime = _runtime()
    sink = EnhancedStereoAudioSink(
        pygame, runtime, 60, game_root=ASSETS, now=lambda: 0.0,
    )

    crash = sink._pcm_catalog.assets[0]
    trigger = sink.begin_sfx_trigger(0, 123, pan=0.75)
    runtime.dos.sound_blaster.log.append((
        "dma_start", {"len": len(crash.pcm), "rate": crash.rate},
    ))
    runtime.dos.sound_blaster.pcm_out.extend(crash.pcm)
    sink.end_sfx_trigger(trigger)
    sink._drain_sb()

    played = pygame.mixer.channels[2].played[-1].samples.astype(np.int32)
    assert np.abs(played[:, 1]).sum() > np.abs(played[:, 0]).sum()
    assert sink.diagnostics()["mode"] == "native-stereo"
    assert sink.sfx_events[-1]["spatial_source"].startswith("recovered")

    # ID 3 is a non-spatial HUD warning and remains centered.
    warning = sink._pcm_catalog.assets[3]
    sink._enqueue_sfx(warning.pcm, warning.rate, pan=-1.0)
    centered = pygame.mixer.channels[2].played[-1].samples
    assert np.array_equal(centered[:, 0], centered[:, 1])

    with pytest.raises(RuntimeError, match="trigger/DMA identity mismatch"):
        sink._enqueue_sfx(
            warning.pcm, warning.rate, expected_effect_id=0, pan=0.5,
        )


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_input_dma_does_not_shift_the_next_original_effect() -> None:
    runtime = _runtime()
    sink = NativeFaithfulAudioSink(
        _Pygame(), runtime, 60, game_root=ASSETS, now=lambda: 0.0,
    )
    asset = sink._pcm_catalog.assets[2]
    runtime.dos.sound_blaster.log.extend((
        ("dma_start", {"len": 1024, "rate": 8000, "input": True}),
        ("dma_start", {"len": len(asset.pcm), "rate": asset.rate}),
    ))
    runtime.dos.sound_blaster.pcm_out.extend(asset.pcm)

    sink._drain_sb()

    assert sink.sfx_events[-1]["effect_id"] == 2
    assert sink._pcm_cursor == len(asset.pcm)


def test_native_faithful_requires_capture_mode_sound_blaster() -> None:
    runtime = _runtime()
    runtime.dos.sound_blaster.detection_only = True
    with pytest.raises(RuntimeError, match="capture-mode Sound Blaster"):
        NativeFaithfulAudioSink(
            _Pygame(), runtime, 60, game_root=ASSETS, now=lambda: 0.0,
        )


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_live_audio_output_worker_owns_synthesis_and_stops_cleanly() -> None:
    pygame = _Pygame()
    runtime = _runtime()
    sink = NativeFaithfulAudioSink(
        pygame, runtime, 120, game_root=ASSETS,
    )

    # Authoritative execution emits compact register commands only.  Starting
    # the host pump creates an independent producer which fills both SDL slots.
    runtime.dos.adlib_callback(0x20, 0x01)
    runtime.dos.adlib_callback(0xB0, 0x31)
    sink.pump()
    try:
        channel = pygame.mixer.channels[1]
        deadline = time.perf_counter() + 2.0
        while channel.get_queue() is None and time.perf_counter() < deadline:
            time.sleep(0.005)

        pacing = sink.pacing_diagnostics()
        assert pacing["worker_alive"]
        assert sink._worker_chunks >= 2
        assert channel.get_queue() is not None
        assert pacing["python_synthesis"] == "independent bounded output worker"
        assert pacing["command_callback_max_ms"] < 10.0
    finally:
        sink.close()
    assert sink._audio_thread is None


@pytest.mark.skipif(not (ASSETS / "SFX.SND").exists(), reason="needs game assets")
def test_output_worker_command_handoff_preserves_opl_pcm() -> None:
    writes = ((0x20, 0x01), (0x40, 0x10), (0xA0, 0x98), (0xB0, 0x31))
    sync_runtime = _runtime()
    sync = NativeFaithfulAudioSink(
        _Pygame(), sync_runtime, 60, game_root=ASSETS, now=lambda: 0.0,
    )
    worker_runtime = _runtime()
    worker = NativeFaithfulAudioSink(
        _Pygame(), worker_runtime, 60, game_root=ASSETS,
    )
    for register, value in writes:
        sync_runtime.dos.adlib_callback(register, value)
        worker_runtime.dos.adlib_callback(register, value)

    worker._apply_audio_commands()
    assert np.array_equal(sync._synthesize(2048), worker._synthesize(2048))
