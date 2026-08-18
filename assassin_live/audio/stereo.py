"""Rebuild a stereo image from the mono wet signal (ROADMAP B3).

The shipped enhancers consume mono and emit mono, so the engine has always
written the same processed samples to both output channels. B1 measured what
that costs: `stereo_width_db = -161 dB` on every one of 104 corpus pairs,
every model, every config -- total image collapse whenever the filter is on.

This module recovers the image WITHOUT asking the processors to change, by
re-applying the chain's *implied* spectral gain to the original stereo pair:

    mask[k] = |Wet[k]| / |Dry_mono[k]|        (per STFT bin, clamped)
    L_out   = istft(mask * stft(L_dry))
    R_out   = istft(mask * stft(R_dry))

The same mask hits both channels, so every bin's L:R ratio survives exactly
-- panning is untouched, the image is the original one -- while the model's
per-band suppression is still applied. Bins the model zeroed (music) go to
zero in both channels, so suppression is not traded away wholesale; bins it
kept (voice) keep whatever width they had.

WHY NOT the cheaper option B3 listed first, re-injecting the side channel
(`L = wet + k*S`, `R = wet - k*S`): the side channel is overwhelmingly the
content the model just removed. Standard mixes put lead vocals center and
instruments wide -- that is the entire premise of the mid/side prefilter in
midside.py, which deletes the side channel *on purpose*. Adding it back at
any gain k returns the music, and the two features would fight each other by
construction. This mask formulation instead keeps side content only in the
bands the model judged worth keeping.

WHY NOT a per-sample broadband gain (`g = wet/dry_mono`): it preserves the
image just as well but throws away the model's spectral selectivity -- the
whole point of a masking enhancer is removing music in one band while keeping
voice in another *within the same instant*, which a single time-domain gain
cannot express.

WHY reusing the DRY phase is sound here: the shipped enhancers are all
magnitude-mask models -- a real-valued gain per STFT bin, phase untouched
(this is also what makes bench_quality.estimate_lag's raw waveform
correlation resolve to the sample; see its docstring). Their output phase IS
the input phase, so taking it from the dry pair introduces no error. A future
non-causal separator that scrambles phase would need `wants_stereo` on the
processor instead (processors/base.py) -- which is why that seam exists.

Costs 4 FFTs + 2 IFFTs of length 512 per hop: measured 0.13 ms per 20 ms
block, 0.0065 of the block budget (2026-08-18, same machine as the B1 sweep)
-- negligible next to dpdfnet_hr's 0.38. The real cost is the framing delay,
+5.3 ms (one N_FFT-HOP window, the same framing midside.py uses) on top of
the model's own, which matters because latency here is a product constraint
(ROADMAP Q3), not a tuning detail.
"""

import numpy as np

from ..processors.stft import sqrt_hann

N_FFT, HOP = 512, 256

# Runaway guard only. A magnitude-mask model cannot exceed 1.0 by design, but
# the wet path also carries the band-limit filter and any future make-up gain,
# and an unclamped |Wet|/|Dry| explodes wherever the dry bin is near-silent.
MAX_GAIN = 8.0
EPS = 1e-9


class StereoRebuild:
    """Consumes aligned (dry_stereo, wet_mono); emits stereo float32.

    Both inputs must refer to the same instant -- in AudioEngine._work() the
    dry FIFO is drained by exactly as many frames as the processor produced,
    the same lockstep invariant _dry_out/_out already use in the callback.
    """

    latency_samples = N_FFT - HOP  # ~5.3 ms @ 48 kHz

    def __init__(self):
        self._win = sqrt_hann(N_FFT)
        norm = np.zeros(HOP, dtype=np.float64)
        for k in range(N_FFT // HOP):
            norm += self._win[k * HOP:(k + 1) * HOP] ** 2
        self._norm = norm.astype(np.float32)
        self.reset()

    def reset(self) -> None:
        self._in_l = np.zeros(N_FFT, dtype=np.float32)
        self._in_r = np.zeros(N_FFT, dtype=np.float32)
        self._in_d = np.zeros(N_FFT, dtype=np.float32)   # dry mono (mask denominator)
        self._in_w = np.zeros(N_FFT, dtype=np.float32)   # wet mono (mask numerator)
        self._ola_l = np.zeros(N_FFT, dtype=np.float32)
        self._ola_r = np.zeros(N_FFT, dtype=np.float32)
        self._pend_st = np.zeros((0, 2), dtype=np.float32)
        self._pend_w = np.zeros(0, dtype=np.float32)

    def process(self, dry_stereo: np.ndarray, wet_mono: np.ndarray) -> np.ndarray:
        """dry_stereo: (n, 2), wet_mono: (n,) -> (m, 2) float32."""
        n = min(len(dry_stereo), len(wet_mono))
        self._pend_st = np.concatenate([self._pend_st, dry_stereo[:n].astype(np.float32)])
        self._pend_w = np.concatenate([self._pend_w, wet_mono[:n].astype(np.float32)])

        out = []
        while len(self._pend_w) >= HOP:
            st, self._pend_st = self._pend_st[:HOP], self._pend_st[HOP:]
            wm, self._pend_w = self._pend_w[:HOP], self._pend_w[HOP:]
            dm = (st[:, 0] + st[:, 1]) * 0.5

            for buf, chunk in ((self._in_l, st[:, 0]), (self._in_r, st[:, 1]),
                               (self._in_d, dm), (self._in_w, wm)):
                buf[:-HOP] = buf[HOP:]
                buf[-HOP:] = chunk

            spec_d = np.fft.rfft(self._in_d * self._win)
            spec_w = np.fft.rfft(self._in_w * self._win)
            mask = np.abs(spec_w) / (np.abs(spec_d) + EPS)
            np.clip(mask, 0.0, MAX_GAIN, out=mask)

            for in_buf, ola in ((self._in_l, self._ola_l), (self._in_r, self._ola_r)):
                spec = np.fft.rfft(in_buf * self._win) * mask
                ola += np.fft.irfft(spec, n=N_FFT).astype(np.float32) * self._win

            frame = np.empty((HOP, 2), dtype=np.float32)
            frame[:, 0] = self._ola_l[:HOP] / self._norm
            frame[:, 1] = self._ola_r[:HOP] / self._norm
            out.append(frame)

            for ola in (self._ola_l, self._ola_r):
                ola[:-HOP] = ola[HOP:]
                ola[-HOP:] = 0.0

        if not out:
            return np.zeros((0, 2), dtype=np.float32)
        return np.concatenate(out)
