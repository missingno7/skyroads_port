"""The generated bootstrap must not capture a development device profile."""
from __future__ import annotations

from types import SimpleNamespace

from scripts import build_boot_image


def test_build_image_runtime_is_sound_device_neutral(monkeypatch) -> None:
    calls = []
    expected = SimpleNamespace()

    def create_game_runtime(exe_path, **kwargs):
        calls.append((exe_path, kwargs))
        return expected

    monkeypatch.setattr(
        build_boot_image,
        "create_game_runtime",
        create_game_runtime,
    )

    actual = build_boot_image.create_bootstrap_runtime(
        "SKYROADS.EXE",
        game_root="assets",
    )

    assert actual is expected
    assert calls == [(
        "SKYROADS.EXE",
        {"game_root": "assets", "enable_sound": False},
    )]
