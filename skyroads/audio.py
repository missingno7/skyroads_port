"""SkyRoads live-viewer audio: AdLib/OPL music + PC speaker + Sound Blaster PCM SFX.

SkyRoads plays its music through the AdLib/OPL FM chip and its *sound effects*
as digitized 8-bit PCM streamed to the Sound Blaster over single-cycle DMA
(DSP command 0x14).  The stock :class:`dos_re.audio_sink.AdlibSpeakerSink`
renders the OPL + PC speaker but not the SB PCM, so the game's engine/jump/
crash/pickup effects (the ``*.SND`` sample banks) were silent.

This sink adds the missing digital layer as a **pure observer**, exactly like
the AdLib sink: it never writes game state, so demos replay identically with
audio on or off.  The VM's emulated Sound Blaster is attached in *capture*
mode (see :func:`skyroads.runtime.create_game_runtime` ``capture_sb_pcm``):
every single-cycle DMA-out block is copied out of memory into ``sb.pcm_out``
and its programmed sample rate recorded in ``sb.log`` — but no block-complete
IRQ is delivered, so the CPU timeline is byte-identical to the detection-only
stub the game already runs against (proven differentially over the full E2E
demo).  We just drain those captured blocks, resample each from its DSP rate to
the mixer rate, and sum them into the output.

Because SkyRoads fires each effect as a one-shot ``0x14`` (never auto-init
streaming) and never waits on the completion IRQ, no timing/feedback wiring is
needed — capturing and playing the bytes is enough.
"""
from __future__ import annotations

from dos_re.audio_sink import AdlibSpeakerSink


class SkyroadsAudioSink(AdlibSpeakerSink):
    """AdLib + PC-speaker (inherited) plus Sound Blaster digital SFX."""

    def __init__(self, pygame, rt, present_hz: int) -> None:
        super().__init__(pygame, rt, present_hz)
        if not self.available:
            return
        import numpy as np

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
