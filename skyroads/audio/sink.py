"""SkyRoads live-viewer audio: AdLib/OPL music + PC speaker + Sound Blaster PCM SFX.

SkyRoads plays its music through the AdLib/OPL FM chip and its *sound effects*
as digitized 8-bit PCM streamed to the Sound Blaster over single-cycle DMA
(DSP command 0x14).  The stock :class:`dos_re.audio_sink.AdlibSpeakerSink`
renders the OPL + PC speaker but not the SB PCM, so the game's engine/jump/
crash/pickup effects (the ``*.SND`` sample banks) were silent.

This sink adds the missing digital layer as a read-only presentation observer:
it never writes game memory. The VM's emulated Sound Blaster is attached in
*capture* mode (see :func:`skyroads.runtime.create_game_runtime`
``capture_sb_pcm``), so each single-cycle DMA-out block is copied into
``sb.pcm_out`` and its programmed sample rate recorded in ``sb.log``. The sink
only drains those captured blocks, resamples them to the mixer rate, and sums
them into host output. Sound Blaster device state and IRQ behavior remain owned
by the emulator and are included in replay profile identity.

Because SkyRoads fires each effect as a one-shot ``0x14`` (never auto-init
streaming) and never waits on the completion IRQ, the presentation sink needs
no feedback path into authoritative game state.

## Wall-clock pacing (why ``pump()`` is overridden)

The base :class:`AdlibSpeakerSink.pump` generates and drains a **fixed**
``chunk = rate // present_hz`` samples per call, on the assumption that the
viewer calls ``pump()`` at a steady ``present_hz``.  Under CPython the viewer
loop cannot hold 30 Hz: ~29% of E2E-replay frames exceed the 33 ms budget (p99
230 ms, max 450 ms — measured), and ``clock.tick(present_hz)`` only pads a
frame *up* to the budget, never speeds a slow one up.  So ``pump()`` is called
well below 30 Hz on nearly a third of frames, and the fixed-chunk model then
emits fewer samples than the mixer consumes.  Two audible failures result:

* **Stutter** — the OPL music channel underruns on every slow stretch, goes
  idle, and restarts with a fresh lead (an audible gap).
* **Multi-second SFX delay** — a captured effect is resampled to real-time
  duration and dumped into ``self._sfx`` all at once, but drained at only
  ``chunk`` samples *per pump*.  Coupled to pump frequency rather than the wall
  clock, that backlog ratchets up on every slow frame and never clears, so
  effects play seconds after their visual (measured structural deficit over
  one replay: ~26 s).

The fix (this class's :meth:`pump`) sizes each pump by **real elapsed
wall-clock time** instead of a fixed chunk, so samples produced/drained always
track what the mixer consumes, and hard-caps the pre-mixer buffer so a long
stall resyncs to "now" (a brief glitch) instead of accumulating delay.  This
cannot fix *music tempo* dragging when the VM itself runs below 30 Hz — the
game emits note changes at its own tick rate, so a slow VM genuinely advances
the score slowly; only a faster VM (more hooking) addresses that.  It does fix
the stutter and the SFX delay, which are pacing artifacts, not tempo.
"""
from __future__ import annotations

import time

from dos_re.audio_sink import AdlibSpeakerSink


class SkyroadsAudioSink(AdlibSpeakerSink):
    """AdLib + PC-speaker (inherited) plus Sound Blaster digital SFX, paced to
    the wall clock so sub-``present_hz`` viewer frames don't stutter or delay."""

    #: Never let the pre-mixer buffer hold more than this many seconds of audio.
    #: Above it we drop the oldest samples and resync to "now" — trading a brief
    #: glitch for staying in sync, instead of ratcheting into seconds of lag.
    MAX_BUFFER_S = 0.20
    #: Safety cap on the pending-SFX buffer (wall-clock draining already keeps
    #: it bounded in practice; this only guards a pathological pile-up).
    MAX_SFX_S = 1.0
    #: Clamp on how many samples a single pump may synthesize, so one long stall
    #: doesn't allocate a huge array (multiple pumps catch up instead).
    MAX_PUMP_S = 0.25

    def __init__(self, pygame, rt, present_hz: int, *, now=time.perf_counter) -> None:
        super().__init__(pygame, rt, present_hz)
        if not self.available:
            return
        import numpy as np

        self._now = now
        self._last_pump = None            # perf_counter of the previous pump
        self._min_chunk = 256
        self._max_pump = max(self._min_chunk, int(self._rate * self.MAX_PUMP_S))
        self._buf_cap = int(self._rate * self.MAX_BUFFER_S)
        self._sfx_cap = int(self._rate * self.MAX_SFX_S)

        self._sb = getattr(rt.dos, "sound_blaster", None)
        self._log_cursor = 0      # next unread index in sb.log
        self._pcm_cursor = 0      # bytes of sb.pcm_out already consumed
        # Pending SFX samples, mono, already resampled to the mixer rate.
        # Overlapping effects sum into this buffer; pump() drains its front.
        self._sfx = np.zeros(0, dtype=np.float32)
        if self._sb is not None and getattr(self._sb, "detection_only", True):
            # Capture mode was not enabled (SB is a detection stub); the DMA
            # blocks are never copied out, so there is nothing to play.  Warn
            # once so a misconfigured front-end is diagnosable.
            print("[audio] Sound Blaster is in detection-only mode; digital SFX "
                  "silent (front-end must attach a capture-mode SB).")

    # ---- SB PCM capture -> resampled mono SFX buffer -------------------------
    def _drain_sb(self) -> None:
        """Pull any newly-captured single-cycle DMA blocks into the SFX buffer."""
        sb = self._sb
        if sb is None:
            return
        log = sb.log
        pcm = sb.pcm_out
        while self._log_cursor < len(log):
            tag, payload = log[self._log_cursor]
            self._log_cursor += 1
            if tag != "dma_start":
                continue
            length = int(payload.get("len", 0))
            rate = int(payload.get("rate") or 0) or 8000
            block = bytes(pcm[self._pcm_cursor:self._pcm_cursor + length])
            self._pcm_cursor += length
            self._enqueue_sfx(block, rate)

    def _enqueue_sfx(self, block: bytes, rate: int) -> None:
        """Resample one 8-bit-unsigned PCM effect to the mixer rate and mix it in."""
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
        if len(res) > len(self._sfx):
            self._sfx = np.concatenate(
                [self._sfx, np.zeros(len(res) - len(self._sfx), np.float32)])
        self._sfx[:len(res)] += res
        # Bounded backlog: if effects pile up past the cap (only under a severe
        # stall), keep the most recent audio so a new effect is never buried
        # behind seconds of stale SFX.
        if len(self._sfx) > self._sfx_cap:
            self._sfx = self._sfx[-self._sfx_cap:]

    def _take_sfx(self, n: int):
        """Pop ``n`` mono SFX samples (int32) off the front, or None if idle."""
        if len(self._sfx) == 0:
            return None
        np = self._np
        chunk = self._sfx[:n]
        self._sfx = self._sfx[n:]
        if len(chunk) < n:
            chunk = np.concatenate([chunk, np.zeros(n - len(chunk), np.float32)])
        return chunk.astype(np.int32)

    # ---- hook into the base mixer -------------------------------------------
    def _speaker_chunk(self, n: int):
        """Base pump() adds this into the (int32) output; fold the SB SFX in here
        alongside the PC-speaker square wave so no pump() logic is duplicated."""
        self._drain_sb()
        np = self._np
        spk = super()._speaker_chunk(n)      # int16 array or None
        sfx = self._take_sfx(n)              # int32 array or None
        if spk is None and sfx is None:
            return None
        out = np.zeros(n, dtype=np.int32)
        if spk is not None:
            out += spk
        if sfx is not None:
            out += sfx
        return out

    # ---- wall-clock-paced pump (replaces the fixed-chunk base) ---------------
    def _synthesize(self, n: int):
        """Render ``n`` stereo mixer samples: OPL music + speaker + SB SFX."""
        np = self._np
        if self._opl is not None:
            pcm = np.frombuffer(self._opl.generate_stereo(n), dtype="<i2").reshape(-1, 2)
            out = pcm.astype(np.int32)
        else:
            out = np.zeros((n, 2), dtype=np.int32)
        extra = self._speaker_chunk(n)       # speaker + SFX, drains SFX by n
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
        now = self._now()
        if self._last_pump is None:
            n = self._chunk                  # first pump: one nominal frame
        else:
            n = int(round((now - self._last_pump) * self._rate))
            n = max(self._min_chunk, min(n, self._max_pump))
        self._last_pump = now

        self._buf = self._np.concatenate([self._buf, self._synthesize(n)])
        # Hard latency cap: after a long stall the buffer would hold the whole
        # backlog; keep only the most recent MAX_BUFFER_S and resync to "now".
        if len(self._buf) > self._buf_cap:
            self._buf = self._buf[-self._buf_cap:]

        if not self._started:
            if len(self._buf) >= self._lead:
                self._channel.play(self._pop_sound(len(self._buf)))
                self._started = True
            return
        if not self._channel.get_busy():
            self._started = False
            return
        if self._channel.get_queue() is None and len(self._buf) >= self._min_chunk:
            self._channel.queue(self._pop_sound(len(self._buf)))
