"""Recovered SkyRoads audio playback and diagnostic analysis.

``sink`` plays the original device command streams. Native-faithful music is
the exact OPL stream rendered by ``dos_re.opl3_fast``; digital effects are the
exact shipped mono PCM payloads and original one-voice interruption behavior.
``native-stereo`` is an explicit enhancement over that faithful island: only
ship-local PCM effects are panned from the recovered original screen-space
ship coordinate. ``events`` and ``opl_events`` are offline score-analysis helpers only and are
never used to synthesize faithful runtime audio.
"""
