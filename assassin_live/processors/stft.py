"""Streaming WOLA (weighted overlap-add) STFT wrapper.

Turns a frame-in/frame-out spectral model into a sample-streaming processor:
push any number of samples, complex STFT frames are produced every `hop`
samples, handed to `frame_fn`, and the returned frames are synthesized back
with overlap-add using the same window (WOLA).

Windows here satisfy the Princen-Bradley condition at 50% overlap
(sum of squared, hop-shifted windows == 1), so no OLA renormalization is
needed; we still compute the normalizer numerically for safety.
"""

import numpy as np


def sqrt_hann(n_fft: int) -> np.ndarray:
    # sqrt of *periodic* hann == sin ramp; matches torch.hann_window(n)**0.5
    return np.sin(np.pi * np.arange(n_fft) / n_fft).astype(np.float32)


def vorbis(n_fft: int) -> np.ndarray:
    inner = np.sin(np.pi * (np.arange(n_fft) + 0.5) / n_fft)
    return np.sin(0.5 * np.pi * inner**2).astype(np.float32)


WINDOWS = {"hann_sqrt": sqrt_hann, "vorbis": vorbis}


class StreamingWola:
    def __init__(self, n_fft: int, hop: int, window: np.ndarray):
        assert len(window) == n_fft and n_fft % hop == 0
        self.n_fft, self.hop, self.win = n_fft, hop, window
        # OLA normalizer: sum of w^2 across all hop shifts (constant for
        # Princen-Bradley windows; assert instead of trusting).
        norm = np.zeros(hop, dtype=np.float64)
        for k in range(n_fft // hop):
            norm += (window[k * hop:(k + 1) * hop] ** 2)
        assert np.allclose(norm, norm[0], rtol=1e-3), "window violates COLA"
        self._norm = norm.astype(np.float32)
        self.reset()

    def reset(self) -> None:
        self._in = np.zeros(self.n_fft, dtype=np.float32)   # analysis buffer
        self._ola = np.zeros(self.n_fft, dtype=np.float32)  # synthesis buffer
        self._pending = np.zeros(0, dtype=np.float32)

    @property
    def latency_samples(self) -> int:
        return self.n_fft - self.hop

    def push(self, x: np.ndarray, frame_fn) -> np.ndarray:
        """frame_fn: complex64[n_fft//2+1] -> complex64[n_fft//2+1]"""
        self._pending = np.concatenate([self._pending, x.astype(np.float32)])
        out = []
        while len(self._pending) >= self.hop:
            chunk, self._pending = self._pending[:self.hop], self._pending[self.hop:]
            self._in = np.roll(self._in, -self.hop)
            self._in[-self.hop:] = chunk

            spec = np.fft.rfft(self._in * self.win)
            spec = frame_fn(spec.astype(np.complex64))
            y = np.fft.irfft(spec, n=self.n_fft).astype(np.float32) * self.win

            self._ola += y
            out.append(self._ola[:self.hop] / self._norm)
            self._ola = np.roll(self._ola, -self.hop)
            self._ola[-self.hop:] = 0.0
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)
