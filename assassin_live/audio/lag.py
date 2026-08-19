"""Measure how far a processor's output trails the input it is paired with.

The engine hands the worker a block, gets samples back, and pairs them by
FIFO position — which assumes `output[k]` is the processed `input[k]`. That
assumption is wrong, and not by a little. Measured 2026-08-19 against the
shipped models on real corpus audio:

    model         nominal latency_ms    measured lag
    dpdfnet_hr           10.0 ms          50.0 ms
    dtln                 24.0 ms           8.0 ms
    gtcrn                16.0 ms           5.3 ms

`latency_samples` is wrong in both directions, so it cannot be used as a
correction. It is a property of the processor's own framing; the lag that
matters here is a property of the whole chain — resamplers, internal
buffering, and whatever the model does with its first frames.

Two things depend on getting this right:

  * **The stereo rebuild** (audio/stereo.py) derives a mask from wet-vs-dry
    and applies it to the dry pair. Off by 50 ms, the mask describes one
    moment and shapes another — heard as smeared, doubled audio. This is
    the bug that made "Preserve Stereo Image" unusable when it shipped.
  * **Startup sync**: the dry FIFO's backlog is the pipeline's latency, and
    whatever accumulates before the first wet sample is baked in for the
    session. Knowing the true lag is what makes that correctable rather
    than a race (measured 140 ms cold vs 20 ms warm, permanent either way).

So it is measured, once, at stream start, by cross-correlation — the same
approach and for the same reason as bench_quality.estimate_lag, whose
docstring is worth reading before changing anything here.
"""

import numpy as np

# Correlation peak must stand this far above the median of the candidates to
# be believed. Startup audio is often near-silent or uncorrelated (the model
# priming, a fade-in), and a confident-looking peak from noise would lock in
# a permanent misalignment — worse than not correcting at all.
CONFIDENCE = 0.05

# Known limit, found while testing: on strictly periodic content the peak at
# the true lag and the one at an integer number of periods are nearly equal,
# and confidence does not distinguish them — a synthetic 220 Hz tone with a
# 2400-sample lag reported 0 at confidence 0.46. Real audio has broadband
# aperiodic content and does not do this (measured 0.81 on a corpus clip),
# but do not "verify" this module with tones alone.


class LagEstimator:
    """Collects paired (dry, wet) as the engine pairs them.

    Deliberately does NOT correlate on the caller's thread. The FFT costs
    5-12 ms for a 1.5 s window, and the caller here is the inference worker:
    stalling it that long empties the wet FIFO, the callback falls back to
    dry, and the dry backlog grows — which is permanent latency, i.e. exactly
    the defect this measurement exists to remove. Measured while getting
    that wrong: the pipeline's floor went from 20 ms to 60 ms.

    So `push` only accumulates and reports when a window is ready; the
    caller hands that window to `measure()` on some other thread.
    """

    def __init__(self, sample_rate: int, window_s: float = 1.5,
                 max_lag_ms: float = 250.0, max_windows: int = 6):
        self.sample_rate = sample_rate
        self.window = int(window_s * sample_rate)
        self.max_lag = int(max_lag_ms / 1000.0 * sample_rate)
        self.max_windows = max_windows
        self.windows_taken = 0
        self._dry: list = []
        self._wet: list = []
        self._n = 0

    @property
    def exhausted(self) -> bool:
        """Give up rather than buffer for ever on silent or uncorrelated
        input. Callers treat this as "assume aligned"."""
        return self.windows_taken >= self.max_windows

    def push(self, dry, wet) -> bool:
        """Returns True when a full window is ready to be measured."""
        n = min(len(dry), len(wet))
        if n:
            self._dry.append(np.asarray(dry[:n], dtype=np.float32))
            self._wet.append(np.asarray(wet[:n], dtype=np.float32))
            self._n += n
        return self._n >= self.window

    def take_window(self) -> tuple:
        """Hand the collected pair over and start a fresh window."""
        d = np.concatenate(self._dry) if self._dry else np.zeros(0, dtype=np.float32)
        w = np.concatenate(self._wet) if self._wet else np.zeros(0, dtype=np.float32)
        self._dry, self._wet, self._n = [], [], 0
        self.windows_taken += 1
        return d, w

    def measure(self, dry, wet) -> "int | None":
        """Correlate one window. Returns the lag, or None if the peak is not
        worth believing — in which case the caller collects another window.

        Run this OFF the audio path.
        """
        lag, confidence = _xcorr_lag(wet, dry, self.max_lag)
        if confidence >= CONFIDENCE and lag >= 0:
            return lag
        return None


def _xcorr_lag(y: np.ndarray, x: np.ndarray, max_lag: int) -> tuple:
    """Samples y trails x by, plus how far the peak stands above the noise."""
    n = min(len(y), len(x))
    if n < 8:
        return 0, 0.0
    ys = y[:n].astype(np.float64) - y[:n].astype(np.float64).mean()
    xs = x[:n].astype(np.float64) - x[:n].astype(np.float64).mean()
    ny, nx = np.linalg.norm(ys), np.linalg.norm(xs)
    if ny < 1e-9 or nx < 1e-9:
        return 0, 0.0
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(ys, nfft) * np.conj(np.fft.rfft(xs, nfft)), nfft)
    m = max(1, min(max_lag, n - 1))
    cand = cc[:m + 1] / (ny * nx)
    i = int(np.argmax(cand))
    confidence = float(cand[i] - np.median(np.abs(cand)))
    return i, confidence
