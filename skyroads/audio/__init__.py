"""SkyRoads native audio — the modern sound/music layer (pre2_port's model).

Boundary architecture (mirrors `pre2_port/pre2/audio/`):

  * :mod:`skyroads.audio.events` — semantic game-audio events (what the game
    wants to hear); no OPL registers, no DGROUP offsets leak across.
  * :mod:`skyroads.audio.opl_events` — the decoder from the RECOVERED music
    engine's exact OPL write stream (`skyroads.recovered.music.Engine`,
    verified 12,882 ticks) to those semantic events.
  * :mod:`skyroads.audio.synth` — the MODERN backend: renders the events with
    a clean float synth through pygame.mixer (frontend ring; lazy imports).
    This is deliberately NOT an OPL chip emulation — per the project decision,
    native SkyRoads sound is a modern layer that interprets the original
    music's notes/patches, like pre2's enhanced backend plays the original
    tracker data through a modern mixer.
"""


#: The VM live-viewer's AdLib/OPL + Sound Blaster PCM sink (formerly the
#: ``skyroads/audio.py`` MODULE this package superseded) now lives in
#: :mod:`skyroads.audio.sink`; re-exported lazily so ``from skyroads.audio
#: import SkyroadsAudioSink`` keeps working without dragging pygame into the
#: pure events/decoder consumers.
def __getattr__(name):
    if name == "SkyroadsAudioSink":
        from skyroads.audio.sink import SkyroadsAudioSink
        return SkyroadsAudioSink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
