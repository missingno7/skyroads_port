# SkyRoads audio recovery

SkyRoads audio has one authoritative command timeline and two faithful host
presentation choices. No runtime path infers a sound from a gameplay event or
synthesizes a substitute effect.

## Original music path

The runtime-loaded driver at `1010:5892-5A96` is a compact song-bytecode
interpreter. The timer ISR calls `1010:5A55`; it walks the current song stream
at `DGROUP:[3196]`, reads instrument and pitch tables from game-loaded data,
updates its delay/loop/status state, and emits ordered YM3812 register writes.
The primitive at `1010:5892` writes the register selector to port `0x388` and
the value to `0x389`, including the original bus-delay reads.

The eight recovered song operations are delay, patch load, note-on pitch,
key-off, volume, loop, set-loop-point, and status flag. The one-time
`1010:58A5-5913` path silences operators, keys channels off, enables waveform
and rhythm modes, loads percussion patches, and fixes percussion pitches.
`skyroads.handrecovered.music.Engine` reproduces this logic without executing
individual CPU instructions. Its OPL output was checked byte-for-byte over the
12,882 timer ticks of the cold-sound replay, and its 63-write reset occurrence
has a separate exact fixture.

Track selection, song loading, starting, stopping, and replacement remain in
the selected faithful program implementation. The native host sink does not
choose tracks: it receives the actual ordered register stream that control flow
produces. `--audio native-faithful` renders that stream only with
`dos_re.opl3_fast.OPL3Fast`.

## Original digital effects

The shipped `SFX.SND` is one bank with six little-endian offsets delimiting
five effects. Each effect begins with a Sound Blaster DSP time constant; every
following byte is unsigned-8 mono PCM. `1010:03C2(id)` reads exactly that
directory, pauses the current DSP voice with `D0`, programs the rate, and calls
`1010:5B76` to submit one single-cycle `0x14` DMA transfer. It does not wait for
completion. A later effect therefore interrupts the current effect; effects do
not overlap or mix with each other.

`INTRO.SND` is the other recovered PCM source. It is a headerless 32,100-byte
unsigned-8 mono block, played with time constant 90 (6,024 Hz). The native
catalog admits only those six exact payloads. Identity includes source file,
effect ID, sample rate, byte length, SHA-256, and exact byte equality. An
unknown transfer fails with those diagnostics instead of falling back to an
invented sound.

Recovered `03C2` roles are:

| ID | Original call-site roles |
|---:|---|
| 0 | flagged wall-crash thud; level-selector enter |
| 1 | bounce-decay landing |
| 2 | wall bump; distance-gated blocked-repeat thump |
| 3 | low fuel/oxygen HUD warning |
| 4 | conditional menu action 9 |

The names describe observed original call sites; the PCM itself is always
selected by the original numeric ID. `03C2` stamps `[AF38]` from timer
`[1600]` even when muted. `1010:0476` supplies an eight-tick busy/debounce gate
only to the call sites that consult it; unconditional crash/bump sites remain
unconditional. The original PC-speaker fallback points at its period table
when no Sound Blaster was detected. Native-faithful playback requires the
recovered Sound Blaster PCM path and fails clearly if capture observation is
not available.

## Playback modes and stereo claim

- `--audio adlib` is the emulated-device reference host sink.
- `--audio native-faithful` uses `opl3_fast` for the exact OPL command stream
  and the closed original PCM catalog for digital effects.
- `--audio native-stereo` keeps that same faithful stream and asset catalog,
  then applies an explicit presentation-only pan to ship-local effects 0, 1,
  and 2. The pan source is not inferred from a sound name: it is the exact
  29-pixel ship sprite centre produced by recovered `0C98`/`325B` projection
  at the original `03C2` call. Equal-power gains map that coordinate across
  the physical 320-pixel aperture. Music, INTRO audio, HUD warning 3, and menu
  action 4 remain centred because the recovered call graph gives them no ship
  source.
- `--audio off` disables host playback.

The original OPL2 and Sound Blaster sources are mono. Faithful host playback is
centred dual-mono. Positional panning is not claimed by `native-faithful` or
silently attributed to the original hardware. `native-stereo` is separately
declared as a non-authoritative enhancement over the verified command/effect
model; it cannot change selection, timing, interruption, PCM bytes, or game
state.

## Verification authority

Host sample generation is non-authoritative and never writes gameplay state.
During replay verification, dos_re records every logical OPL register/value as
an `AUDIO_COMMAND`. Sound Blaster, DMA, and PIC writes are recorded as ordered
observable port effects. The rolling interval digest therefore detects a
command that is missing, additional, reordered, differently parameterized, or
emitted at a different replay interval even if endpoint OPL registers later
become equal. Semantic checkpoints also retain the complete OPL register file;
machine-state seams retain Sound Blaster/DMA continuation state.

The host sink additionally reports its OPL command count/digest and each
byte-identified PCM play for live diagnosis. These diagnostics do not replace
the replay oracle. The strict replay verifier remains the authority for command
position and continuation equivalence.

## Host pacing and stall isolation

Game simulation remains the sole producer of deterministic OPL writes and PCM
transfers. Native synthesis runs on the player thread before rendering, while
SDL's mixer thread consumes immutable queued sample buffers independently. No
second Python owner is introduced for DOS, gameplay, OPL, or replay state.

The native sink queues fixed 80 ms chunks with a two-slot 160 ms reservoir.
Those values come from point-by-point profiling of
`replay_skyroads_20260722_173742`: after removing duplicate original-projection
work and collapsing the recovered palette byte loop, its worst non-cold
transition point is about 44 ms. A slow render or generated-shell transition
therefore cannot starve playback. Runtime diagnostics report the chunk,
reservoir, largest observed pump gap, and underrun count. The buffer affects
host presentation latency only; it never changes command timing or replay
state.

The profiler also reports a roughly 0.4 s cold first native frame while Python
imports NumPy and decodes immutable presentation assets. The canonical player
renders that initial frame before it creates the audio sink, so this startup
cost cannot drain a live mixer queue and is kept separate from steady-state
stall measurements.
