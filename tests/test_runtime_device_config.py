"""Every carrier derives deterministic SkyRoads devices from one policy."""
from types import SimpleNamespace

from skyroads.device_config import capture_sound_blaster_pcm


def _args(*, audio="off", no_sound=False, headless=False):
    return SimpleNamespace(
        audio=audio,
        no_sound=no_sound,
        headless=headless,
    )


def test_pcm_capture_requires_interactive_adlib_presentation() -> None:
    assert capture_sound_blaster_pcm(_args(audio="adlib"))
    assert not capture_sound_blaster_pcm(_args(audio="off"))
    assert not capture_sound_blaster_pcm(_args(audio="adlib", headless=True))
    assert not capture_sound_blaster_pcm(_args(audio="adlib", no_sound=True))
