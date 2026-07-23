"""Mid/side stereo pre-filter — runs on the raw stereo capture, before the
mono downmix everything downstream (the enhancer processors) operates on.

Center-panned content (lead vocals, dialogue — standard mix convention)
survives; wide-panned content (instrumental backing, ambience) is
attenuated. Per STFT bin: gain = M^2 / (M^2 + S^2), where M/S are the
mid/side magnitudes; gain is raised to `exponent`. Measured in the
Music-Assassin research repo (docs/REALTIME_FILTER_RESEARCH.md): g^4
gives a 16.8x voice/music ratio vs. 9.2x for plain mono downmix, with
~0.01 dB measured vocal loss on the reference clip. Causal, frame-by-
frame — no lookahead beyond the analysis window itself.

Unlike a StreamProcessor this consumes stereo (n, 2) and emits mono; it
sits in AudioEngine._work() ahead of the wet-path processor, entirely
optional (plain mono downmix — (L+R)/2 — is the behavior when disabled).
"""

import numpy as np

from ..processors.stft import sqrt_hann

N_FFT, HOP = 512, 256


class MidSideFilter:
    latency_samples = N_FFT - HOP  # ~5 ms @ 48 kHz

    def __init__(self, exponent: float = 4.0):
        self.exponent = exponent
        self._win = sqrt_hann(N_FFT)
        norm = np.zeros(HOP, dtype=np.float64)
        for k in range(N_FFT // HOP):
            norm += self._win[k * HOP:(k + 1) * HOP] ** 2
        self._norm = norm.astype(np.float32)
        self.reset()

    def reset(self) -> None:
        self._in_m = np.zeros(N_FFT, dtype=np.float32)
        self._in_s = np.zeros(N_FFT, dtype=np.float32)
        self._ola = np.zeros(N_FFT, dtype=np.float32)
        self._pending = np.zeros((0, 2), dtype=np.float32)

    def process(self, stereo: np.ndarray) -> np.ndarray:
        """stereo: (n, 2) float32 -> mono float32, center-emphasized."""
        self._pending = np.concatenate([self._pending, stereo.astype(np.float32)])
        out = []
        while len(self._pending) >= HOP:
            chunk, self._pending = self._pending[:HOP], self._pending[HOP:]
            m = (chunk[:, 0] + chunk[:, 1]) * 0.5
            s = (chunk[:, 0] - chunk[:, 1]) * 0.5

            self._in_m = np.roll(self._in_m, -HOP)
            self._in_m[-HOP:] = m
            self._in_s = np.roll(self._in_s, -HOP)
            self._in_s[-HOP:] = s

            spec_m = np.fft.rfft(self._in_m * self._win)
            spec_s = np.fft.rfft(self._in_s * self._win)
            gain = (np.abs(spec_m) ** 2) / (np.abs(spec_m) ** 2 + np.abs(spec_s) ** 2 + 1e-12)
            gain **= self.exponent

            y = np.fft.irfft(spec_m * gain, n=N_FFT).astype(np.float32) * self._win
            self._ola += y
            out.append((self._ola[:HOP] / self._norm).copy())
            self._ola = np.roll(self._ola, -HOP)
            self._ola[-HOP:] = 0.0
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)
