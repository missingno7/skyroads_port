# SkyRoads sound/music engine (2026-07-10)

The AdLib/OPL music driver, fully reverse-engineered. It is a compact **music
bytecode interpreter**: a per-timer-tick engine walks a song event stream and
programs the OPL2 FM chip. Self-contained and byte-exact verifiable (via the OPL
register-write stream) — the target for a complete "sound/music island".

## Where it lives (and the snapshot gotcha)

The driver code sits at `1010:5892–5A96`, but it is **runtime-loaded**: it is
zero in the static `SKYROADS.EXE` image and only present in memory after the
intro loads it. Disassemble it from a *post-intro* snapshot (e.g. capture one
from `demo_cold_sound_20260710_212256` after the first OPL port write), not the
EXE. `tools/lindis.py` reads snapshot memory, so it works once the snapshot is
taken late enough. (Note: lindis's *text* column mis-renders some `[disp]`
values — read the offset from the **byte** column, little-endian.)

Data segment for all of the below is DGROUP `0x1686` (the ISR sets `ds` from
`cs:[3ACA]`).

## The OPL register-write primitive — `1010:5892`

`opl_write(reg = AL, val = AH)`:

```
5892  out 0x388, AL          ; select register
5893  in  0x388 ×6           ; ~3.3us settle delay (6 dummy reads)
      mov AL, AH; out 0x389, AL   ; write data
      mov cx, 0x23; in 0x388 ×0x23 ; ~23us delay
      ret
```

## The per-tick engine — `1010:5A55` (called from the timer ISR each tick)

```
if [0C83] != 0: [0C83] -= 1; return        ; delay counter — wait between events
loop:
    word = *[3196]++; ( [3196] is the song cursor )
    op  = word & 0x0007                      ; 3-bit opcode
    al  = (word >> 4) & 0x0F                  ; 4-bit arg (channel, usually)
    ah  = (word >> 8) & 0xFF                  ; 8-bit arg (note / value / delay)
    call dispatch[op]                         ; table at DG:0x0C5B (8 near ptrs)
    goto loop                                 ; process events until op0 sets a delay
```

So a "tick" processes a run of events and ends when **op0** loads a nonzero
delay; the next tick(s) count it down.

## Opcode set (dispatch table `DG:0x0C5B`, 8 entries)

| op | handler | meaning | OPL effect |
|---|---|---|---|
| 0 | `5914` | **delay** | `[0C83] = ah` (wait `ah` ticks) |
| 1 | `5919` | **note + instrument** | key-off, then load an 11-register FM patch |
| 2 | `5971` | **note-on (pitch)** | F-number+octave → `A0`/`B0`, key-on; rhythm path |
| 3 | `59CF` | **key-off** | `B0+ch := 0`; rhythm: mask `BD` via `[319A]` |
| 4 | `5A0D` | **volume** | operator total-level (`0x40`-group) registers |
| 5 | `5A42` | **loop** | `[3196] = [3198]` (jump to loop point) |
| 6 | `5A49` | **set loop point** | `[3198] = [3196]` |
| 7 | `5A50` | **flag** | `[31A6] = ah` (song status/end) |

op1 and op2 both call op3 (`59CF`) first to key the channel off.

## Engine state (DGROUP)

| addr | name |
|---|---|
| `0C83` | delay counter (ticks until next event batch) |
| `3196` | song cursor (into the current song event stream) |
| `3198` | loop point |
| `3194` | instrument-table base pointer (op1 reads patches at `ah*16 + [3194]`) |
| `319A` | rhythm-mode `BD` register shadow |
| `319B..` | per-channel current-note store (`[di+0x319B]`) |
| `31A6` | song status flag |

## Data tables (DGROUP, read by the handlers)

| addr | used by | contents |
|---|---|---|
| `0C10` | op1/op4 | 11 FM operator register offsets (per patch slot) |
| `0C1B`,`0C26`,`0C31` | op1 | per-channel operator base registers (op-1, op-2, connection) |
| `0C3C` | op4 (`59EF`) | per-operator KSL/TL bias for volume |
| `0C6B` | op2 | note → F-number low table |
| `0C77` | op2 | note → F-number high / octave table |

## The one-time OPL reset / percussion init — `1010:58A5-5913`

Run once at driver start (called via `1010:58CD`), before any song plays:

1. `58A5-58BF` — **silence**: write all 22 operator registers `0x40-0x55` to
   `0x3F` (max attenuation).
2. `58C1-58CA` — **key-off** channels 7..0 (melodic channels 0-5 via `B0+ch:=0`;
   channels 6/7 via the rhythm mask in `op3`).
3. `58D0-58DC` — enable waveform select (`0x01:=0x20`), disable CSM/keysplit
   (`0x08:=0x00`), enable rhythm mode (`0xBD:=0xE0`).
4. `58E2-58F9` — load 4 fixed **percussion instrument patches** (channel slots
   7..10) from a fixed table at `DG:0x0C84` (+`0x0B`/slot), via the *same*
   `op1` patch-load path `run_tick` uses for ordinary notes.
5. `58FB-5913` — fix the two percussion channels' pitch directly (`A7`/`B7`,
   `A8`/`B8`) — rhythm-mode voices use fixed frequencies, not per-note ones.

Note: `1010:58A5` (the silence+key-off subroutine, ending in its own `ret` at
`58CC`) is also called **standalone** elsewhere (just "silence the chip"), not
only as step 1 of this sequence — when tracing occurrences of `58A5`, gate on
the call site `58CD` (or the eventual `5913` return) to isolate the full
init, not just any entry to `58A5`.

## Status: COMPLETE ✅

Both pieces recovered as `skyroads/recovered/music.py::Engine` — clean VM-free
Python operating on (song stream, tables, state) via two DGROUP readers:

- **`run_tick()`** — verified byte-exact over all **12,882 cold-sound-demo
  ticks, zero divergences**.
- **`reset_opl()`** — verified byte-exact against its one occurrence in the
  cold-sound demo (63 writes), confirmed the *only* occurrence over the full
  2157-frame replay.

Both guarded by `tests/test_music.py`.

The song data and the tables above are *data the port loads*, not code to
rewrite — a native port supplies `rb`/`rw` over its own copy and gets the exact
OPL programming for free. This retires the whole music subsystem — sequencer
and init — for the VM-less port.

**SFX needs no recovery island.** Sound effects are digital PCM streamed to the
Sound Blaster over DMA; `skyroads/audio.py`'s `SkyroadsAudioSink` already plays
them correctly as a *pure observer* that captures the raw DMA bytes the game
writes (same pattern as the render hooks watching OPL writes) — there is no
"trigger condition" logic to reimplement, since the port never needs to decide
*when* to play an effect, only to relay the bytes the original driver already
produced.
