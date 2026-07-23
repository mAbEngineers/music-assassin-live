"""Audio-range band-limit — removes content outside human hearing
(~20 Hz-20 kHz) from the processed (wet) signal, guarding against DC
drift, subsonic rumble, and ultrasonic artifacts that resampling or model
inference can introduce.

Runs on the engine's worker thread, right after the model — same place
and performance budget as inference itself, not the realtime-sensitive
audio callback.

Hand-rolled RBJ Audio EQ Cookbook biquads (not scipy — this app is
deliberately dependency-light, see requirements.txt): one highpass, one
lowpass, cascaded, both at Butterworth Q (1/sqrt(2), maximally flat).
Like any IIR filter this needs no lookahead/buffering — only the tiny
group delay inherent to any filter, a fraction of a millisecond at these
frequencies — so it adds no algorithmic latency, unlike the WOLA-framed
model processors (which need n_fft-hop worth of buffering).
"""

import math

import numpy as np

LOW_HZ = 20.0
HIGH_HZ = 20000.0
_Q = 0.70710678  # 1/sqrt(2)


def _biquad_coeffs(freq_hz: float, sample_rate: float, highpass: bool):
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * _Q)
    if highpass:
        b0, b1, b2 = (1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2
    else:
        b0, b1, b2 = (1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2
    a0, a1, a2 = 1 + alpha, -2 * cos_w0, 1 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


class _Biquad:
    """Direct Form II Transposed — the standard numerically-stable form
    for streaming (state carried block to block)."""

    def __init__(self, coeffs):
        self.b0, self.b1, self.b2, self.a1, self.a2 = coeffs
        self.z1 = self.z2 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        z1, z2 = self.z1, self.z2
        xs = x.tolist()  # plain floats: avoids per-element numpy scalar overhead
        out = [0.0] * len(xs)
        for i, xi in enumerate(xs):
            y = b0 * xi + z1
            z1 = b1 * xi - a1 * y + z2
            z2 = b2 * xi - a2 * y
            out[i] = y
        self.z1, self.z2 = z1, z2
        return np.array(out, dtype=np.float32)


class BandlimitFilter:
    def __init__(self, sample_rate: int):
        nyq = sample_rate / 2.0
        high = min(HIGH_HZ, nyq * 0.99)  # stay clear of Nyquist
        self._hp = _Biquad(_biquad_coeffs(LOW_HZ, sample_rate, highpass=True))
        self._lp = _Biquad(_biquad_coeffs(high, sample_rate, highpass=False))

    def reset(self) -> None:
        self._hp.z1 = self._hp.z2 = 0.0
        self._lp.z1 = self._lp.z2 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        return self._lp.process(self._hp.process(x))
