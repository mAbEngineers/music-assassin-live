"""DPDFNet streaming processor (16 kHz) — experimental.

Model: dpdfnet_baseline.onnx. One STFT frame per call (n_fft=320, hop=160,
vorbis window) with a flat 38256-float state vector.

Caveat: the export's metadata carries erb_norm_init / spec_norm_init values
that sherpa-onnx bakes into the initial state at the right offsets. We start
from zeros instead, so the built-in feature normalization adapts over the
first ~1 s of audio (brief over/under-suppression transient after reset()).
Acceptable for a live always-on filter; fix by porting sherpa-onnx's state
layout if it ever matters.
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session
from .stft import StreamingWola, vorbis

N_FFT, HOP, BINS = 320, 160, 161


class DpdfnetProcessor(StreamProcessor):
    name = "dpdfnet"
    sample_rate = 16000
    latency_samples = N_FFT - HOP  # 10 ms

    def __init__(self, model_path: str):
        self.session = make_session(model_path)
        self.wola = StreamingWola(N_FFT, HOP, vorbis(N_FFT))
        self.reset()

    def reset(self) -> None:
        self.wola.reset()
        self.state = np.zeros(38256, dtype=np.float32)

    def _frame(self, spec: np.ndarray) -> np.ndarray:
        inp = np.stack([spec.real, spec.imag], axis=-1).reshape(1, 1, BINS, 2)
        spec_e, self.state = self.session.run(
            None, {"spec": inp.astype(np.float32), "state_in": self.state}
        )
        e = spec_e.reshape(BINS, 2)
        return (e[:, 0] + 1j * e[:, 1]).astype(np.complex64)

    def feed(self, x: np.ndarray) -> np.ndarray:
        return self.wola.push(x, self._frame)
