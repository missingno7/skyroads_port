"""Faithful playback of SkyRoads OPL, speaker, and Sound Blaster commands.

Music consumes the exact OPL register stream emitted by the selected program
implementation. Digital audio consumes the original unsigned-8 PCM blocks the
game submits with Sound Blaster DSP command ``0x14``. The original owns one
PCM voice: a new transfer interrupts the current effect instead of mixing it.

Both host sinks are read-only observers. Device state, command timing, and
replay evidence remain authoritative in the emulator. ``SkyroadsAudioSink``
provides device-reference playback. ``NativeFaithfulAudioSink`` additionally
forces :class:`dos_re.opl3_fast.OPL3Fast` and rejects PCM that does not match
the recovered ``SFX.SND``/``INTRO.SND`` catalog byte-for-byte.

The pump follows elapsed wall time because presentation frames are not an
audio clock. A bounded OPL buffer prevents a slow host frame from becoming
seconds of playback latency; it does not alter the game's command timeline.
"""
from __future__ import annotations

from hashlib import sha256
import math
import time

from dos_re.audio_sink import AdlibSpeakerSink


class SkyroadsAudioSink(AdlibSpeakerSink):
    """AdLib + PC-speaker (inherited) plus Sound Blaster digital SFX, paced to
    the wall clock so sub-``present_hz`` viewer frames don't stutter or delay."""

    #: Never let the pre-mixer buffer hold more than this many seconds of audio.
    #: Above it we drop the oldest samples and resync to "now" — trading a brief
    #: glitch for staying in sync, instead of ratcheting into seconds of lag.
    MAX_BUFFER_S = 0.20
    #: Clamp on how many samples a single pump may synthesize, so one long stall
    #: doesn't allocate a huge array (multiple pumps catch up instead).
    MAX_PUMP_S = 0.25
    # The source replay exposed repeatable 90-105ms transition frames before
    # the renderer/palette fixes; its post-fix non-cold maximum is about 44ms.
    # SDL's mixer thread consumes queued Sound objects independently of the
    # Python render/simulation thread, but pygame exposes only one queue slot.
    # Two 80ms chunks provide measured headroom without adding a second Python
    # thread that could race OPL writes or replay state.
    QUEUE_CHUNK_S = 0.080
    START_LEAD_S = 0.160

    def __init__(self, pygame, rt, present_hz: int, *, now=time.perf_counter) -> None:
        super().__init__(pygame, rt, present_hz)
        if not self.available:
            return
        import numpy as np

        self._now = now
        self._last_pump = None            # perf_counter of the previous pump
        self._max_pump_gap = 0.0
        self._underruns = 0
        self._min_chunk = 256
        self._max_pump = max(self._min_chunk, int(self._rate * self.MAX_PUMP_S))
        self._buf_cap = int(self._rate * self.MAX_BUFFER_S)
        self._sb = getattr(rt.dos, "sound_blaster", None)
        self._log_cursor = 0      # next unread index in sb.log
        self._pcm_cursor = 0      # bytes of sb.pcm_out already consumed
        # The original uses one Sound Blaster voice.  Every effect issues DSP
        # D0 (pause) before programming a new single-cycle 0x14 transfer, so a
        # new effect INTERRUPTS the old one; effects never mix with each other.
        if pygame.mixer.get_num_channels() < 3:
            pygame.mixer.set_num_channels(3)
        self._sfx_channel = pygame.mixer.Channel(2)
        self._last_sfx_pcm = None
        self._last_sfx_rate = None
        if self._sb is not None and getattr(self._sb, "detection_only", True):
            # Capture mode was not enabled (SB is a detection stub); the DMA
            # blocks are never copied out, so there is nothing to play.  Warn
            # once so a misconfigured front-end is diagnosable.
            print("[audio] Sound Blaster is in detection-only mode; digital SFX "
                  "silent (front-end must attach a capture-mode SB).")

    # ---- SB PCM capture -> original one-voice PCM playback ------------------
    def _drain_sb(self) -> None:
        """Pull newly captured single-cycle DMA blocks into host playback."""
        sb = self._sb
        if sb is None:
            return
        log = sb.log
        pcm = sb.pcm_out
        while self._log_cursor < len(log):
            log_index = self._log_cursor
            tag, payload = log[log_index]
            self._log_cursor += 1
            if tag != "dma_start":
                continue
            length = int(payload.get("len", 0))
            rate = int(payload.get("rate") or 0) or 8000
            # Input probes and DSP silence commands append no bytes to
            # ``pcm_out``.  Treating them as output consumed the next real
            # effect and shifted every subsequent DMA block.
            if payload.get("input") or payload.get("silence"):
                continue
            block = bytes(pcm[self._pcm_cursor:self._pcm_cursor + length])
            self._pcm_cursor += length
            if len(block) != length:
                raise RuntimeError(
                    "Sound Blaster PCM observer lost block alignment: "
                    f"wanted {length} bytes, found {len(block)}"
                )
            trigger = getattr(self, "_sfx_pan_by_log_index", {}).pop(
                log_index, None,
            )
            self._enqueue_sfx(
                block, rate,
                pan=(None if trigger is None else trigger["pan"]),
                expected_effect_id=(
                    None if trigger is None else trigger["effect_id"]
                ),
            )

    def _enqueue_sfx(self, block: bytes, rate: int, *, pan=None,
                     expected_effect_id=None) -> None:
        """Resample and start one original mono effect.

        ``Channel.play`` intentionally replaces any sound already on this
        channel, reproducing the original D0 + new 0x14 transfer instead of
        the previous (invented) overlap mixer.
        """
        if not block:
            return
        np = self._np
        u = np.frombuffer(block, dtype=np.uint8).astype(np.float32)
        sig = (u - 128.0) * 256.0                      # unsigned8 -> ~int16 swing
        n_out = max(1, int(round(len(sig) * self._rate / max(1, rate))))
        if n_out == len(sig):
            res = sig
        else:                                          # linear resample
            xp = np.arange(len(sig), dtype=np.float32)
            x = np.linspace(0.0, len(sig) - 1, num=n_out, dtype=np.float32)
            res = np.interp(x, xp, sig).astype(np.float32)
        mono = np.clip(res, -32768, 32767).astype(np.int16)
        if pan is None:
            # SkyRoads' SB 1.x PCM path is mono. Faithful output is centered
            # dual-mono; positional stereo is an explicit enhancement.
            stereo = np.repeat(mono[:, None], 2, axis=1)
        else:
            position = max(-1.0, min(1.0, float(pan)))
            angle = (position + 1.0) * math.pi / 4.0
            gains = np.asarray(
                (math.cos(angle), math.sin(angle)), dtype=np.float32,
            )
            stereo = np.clip(
                mono.astype(np.float32)[:, None] * gains[None, :],
                -32768, 32767,
            ).astype(np.int16)
        sound = self._pygame.sndarray.make_sound(np.ascontiguousarray(stereo))
        self._last_sfx_pcm = bytes(block)
        self._last_sfx_rate = int(rate)
        self._sfx_channel.play(sound)

    # ---- wall-clock-paced pump (replaces the fixed-chunk base) ---------------
    def _synthesize(self, n: int):
        """Render ``n`` stereo mixer samples: OPL music + speaker + SB SFX."""
        np = self._np
        if self._opl is not None:
            pcm = np.frombuffer(self._opl.generate_stereo(n), dtype="<i2").reshape(-1, 2)
            out = pcm.astype(np.int32)
        else:
            out = np.zeros((n, 2), dtype=np.int32)
        extra = super()._speaker_chunk(n)
        if extra is not None:
            out += extra[:, None]
        out = np.clip(out, -32768, 32767).astype(np.int16)
        if self._channels == 1:
            out = out[:, :1]
        return out

    def _pop_sound(self, k: int):
        """Make a pygame Sound from the front ``k`` buffered samples and drop them."""
        chunk, self._buf = self._buf[:k], self._buf[k:]
        arr = chunk if self._channels > 1 else chunk.reshape(-1)
        return self._pygame.sndarray.make_sound(self._np.ascontiguousarray(arr))

    def pump(self) -> None:
        """Feed audio sized by REAL elapsed time, not a fixed per-frame chunk, so
        a viewer frame that runs slower than ``present_hz`` neither underruns the
        mixer (stutter) nor lets the SFX backlog grow (delay). Call once per
        presented frame, exactly like the base sink."""
        if not self.available:
            return
        self._drain_sb()
        now = self._now()
        if self._last_pump is None:
            n = self._chunk                  # first pump: one nominal frame
        else:
            gap = max(0.0, now - self._last_pump)
            self._max_pump_gap = max(self._max_pump_gap, gap)
            n = int(round(gap * self._rate))
            n = max(self._min_chunk, min(n, self._max_pump))
        self._last_pump = now

        self._buf = self._np.concatenate([self._buf, self._synthesize(n)])
        # Hard latency cap: after a long stall the buffer would hold the whole
        # backlog; keep only the most recent MAX_BUFFER_S and resync to "now".
        if len(self._buf) > self._buf_cap:
            self._buf = self._buf[-self._buf_cap:]

        if not self._started:
            if len(self._buf) >= self._lead:
                self._channel.play(self._pop_sound(self._chunk))
                self._started = True
            return
        if not self._channel.get_busy():
            self._underruns += 1
            self._started = False
            return
        if self._channel.get_queue() is None and len(self._buf) >= self._chunk:
            self._channel.queue(self._pop_sound(self._chunk))

    def pacing_diagnostics(self) -> dict:
        return {
            "mixer_thread": "SDL playback (presentation-only)",
            "python_synthesis": "main thread before rendering",
            "queue_chunk_ms": round(self._chunk * 1000 / self._rate),
            "jitter_reservoir_ms": round(2 * self._chunk * 1000 / self._rate),
            "max_pump_gap_ms": round(self._max_pump_gap * 1000, 3),
            "underruns": self._underruns,
        }


class NativeFaithfulAudioSink(SkyroadsAudioSink):
    """Closed-world native playback of the recovered original audio commands.

    Music consumes the exact OPL register writes emitted by the selected
    original/generated/recovered sequencer, rendered only by ``OPL3Fast``.
    Digital effects consume the exact DMA bytes and must match ``SFX.SND`` or
    ``INTRO.SND`` byte-for-byte before playback.  There are no inferred event
    names, substitute oscillators, noise drums, panning rules, or fallbacks.
    """

    def __init__(self, pygame, rt, present_hz: int, *, game_root,
                 now=time.perf_counter) -> None:
        self._trace_enabled = False
        self.opl_write_count = 0
        self._opl_digest = sha256()
        self.sfx_events = []
        self.sfx_triggers = []
        self._sfx_pan_by_log_index = {}
        super().__init__(pygame, rt, present_hz, now=now)
        if not self.available:
            return

        from dos_re.opl3_fast import OPL3Fast
        from skyroads.native.sfx import load_original_pcm_catalog

        # ``SkyroadsAudioSink`` permits the optional Nuked backend for the
        # device-reference mode.  Native-faithful is deliberately fixed to
        # the requested dos_re implementation and cannot be changed by an
        # environment variable.
        self._opl = OPL3Fast(sample_rate=self._rate)
        self.opl_label = "opl3-fast"
        self._pcm_catalog = load_original_pcm_catalog(game_root)
        if self._sb is None or getattr(self._sb, "detection_only", True):
            raise RuntimeError(
                "native-faithful audio requires capture-mode Sound Blaster "
                "observation; launch interactively with sound enabled"
            )
        self._trace_enabled = True
        rt.dos.set_adlib_callback(self._on_adlib, emit_current=True)
        rt._skyroads_audio_sink = self

    def _on_adlib(self, reg: int, value: int) -> None:
        register = int(reg) & 0x1FF
        byte = int(value) & 0xFF
        if self._trace_enabled:
            self.opl_write_count += 1
            self._opl_digest.update(register.to_bytes(2, "little"))
            self._opl_digest.update(bytes((byte,)))
        super()._on_adlib(register, byte)

    def _identify_sfx(self, block: bytes, rate: int):
        asset = self._pcm_catalog.identify(bytes(block), int(rate))
        self.sfx_events.append({
            "source": asset.source,
            "effect_id": asset.effect_id,
            "roles": asset.roles,
            "rate": asset.rate,
            "length": len(asset.pcm),
            "sha256": asset.digest,
        })
        return asset

    def _enqueue_sfx(self, block: bytes, rate: int, *, pan=None,
                     expected_effect_id=None) -> None:
        asset = self._identify_sfx(block, rate)
        if (expected_effect_id is not None
                and asset.effect_id != int(expected_effect_id)):
            self.sfx_events.pop()
            raise RuntimeError(
                "recovered 03C2 trigger/DMA identity mismatch: "
                f"requested effect {expected_effect_id}, captured "
                f"{asset.effect_id} from {asset.source}"
            )
        # Faithful mode deliberately ignores the enhancement coordinate.
        super()._enqueue_sfx(asset.pcm, asset.rate, pan=None)

    def begin_sfx_trigger(self, effect_id: int, tick: int, *, pan=0.0):
        """Mark one recovered ``03C2`` call before its generated carrier runs."""
        trigger = {
            "tick": int(tick) & 0xFFFF,
            "effect_id": int(effect_id),
            "pan": max(-1.0, min(1.0, float(pan))),
            "log_start": len(self._sb.log),
        }
        self.sfx_triggers.append(trigger)
        return trigger

    def end_sfx_trigger(self, trigger) -> None:
        """Bind the call to the exact DMA transfer it synchronously emitted."""
        for index in range(int(trigger["log_start"]), len(self._sb.log)):
            tag, payload = self._sb.log[index]
            if (tag == "dma_start" and not payload.get("input")
                    and not payload.get("silence")):
                self._sfx_pan_by_log_index[index] = trigger
                return

    def diagnostics(self) -> dict:
        return {
            "mode": "native-faithful",
            "music_renderer": self.opl_label,
            "music_claim": "exact original OPL register stream",
            "opl_writes": self.opl_write_count,
            "opl_digest": self._opl_digest.hexdigest(),
            "sfx_claim": "byte-exact original PCM assets",
            "sfx_plays": len(self.sfx_events),
            "sfx_triggers": len(self.sfx_triggers),
            "last_sfx": (None if not self.sfx_events else self.sfx_events[-1]),
            "stereo": "centered dual-mono (original OPL2 and SB PCM)",
            "enhancement": "none",
            "pacing": self.pacing_diagnostics(),
        }


class EnhancedStereoAudioSink(NativeFaithfulAudioSink):
    """Optional stereo presentation over the closed faithful audio model.

    Music, asset identity, trigger timing and one-voice interruption remain
    unchanged.  Only ship-local effects 0/1/2 are equal-power panned from the
    exact recovered ship screen position at their original ``03C2`` call.
    HUD/menu effects and INTRO.SND remain centred because they have no ship
    source in the recovered call graph.
    """

    _SHIP_LOCAL_EFFECTS = frozenset((0, 1, 2))

    def _enqueue_sfx(self, block: bytes, rate: int, *, pan=None,
                     expected_effect_id=None) -> None:
        asset = self._identify_sfx(block, rate)
        if (expected_effect_id is not None
                and asset.effect_id != int(expected_effect_id)):
            self.sfx_events.pop()
            raise RuntimeError(
                "recovered 03C2 trigger/DMA identity mismatch: "
                f"requested effect {expected_effect_id}, captured "
                f"{asset.effect_id} from {asset.source}"
            )
        spatial = pan if asset.effect_id in self._SHIP_LOCAL_EFFECTS else None
        self.sfx_events[-1].update({
            "pan": spatial,
            "spatial_source": (
                None if spatial is None
                else "recovered 0C98/325B ship screen coordinate"
            ),
        })
        SkyroadsAudioSink._enqueue_sfx(
            self, asset.pcm, asset.rate, pan=spatial,
            expected_effect_id=expected_effect_id,
        )

    def diagnostics(self) -> dict:
        result = super().diagnostics()
        result.update({
            "mode": "native-stereo",
            "stereo": "equal-power ship-local PCM; music/UI remain centred",
            "enhancement": (
                "pan from recovered 0C98/325B ship screen coordinate at 03C2"
            ),
        })
        return result
