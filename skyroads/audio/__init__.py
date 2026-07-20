"""SkyRoads semantic audio and host presentation implementations.

Boundary architecture:

  * :mod:`skyroads.audio.events` — semantic game-audio events (what the game
    wants to hear); no OPL registers, no DGROUP offsets leak across.
  * :mod:`skyroads.audio.opl_events` — the decoder from the RECOVERED music
    engine's exact OPL write stream (`skyroads.handrecovered.music.Engine`,
    verified 12,882 ticks) to those semantic events.
  * :mod:`skyroads.audio.synth` — the MODERN backend: renders the events with
    a clean float synth through pygame.mixer (frontend ring; lazy imports).
    This is deliberately NOT an OPL chip emulation — per the project decision,
    this non-authoritative presentation implementation interprets the original
    music's notes and patches through a modern mixer.
"""
