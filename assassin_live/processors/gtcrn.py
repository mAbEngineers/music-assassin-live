"""GTCRN streaming processor (16 kHz).

Model: gtcrn_simple.onnx (k2-fsa export of Xiaobin-Rong/gtcrn stream model).
One STFT frame per call (n_fft=512, hop=256, sqrt-hann window) with three
explicit recurrent caches carried between frames.
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session
from .stft import StreamingWola, sqrt_hann

N_FFT, HOP = 512, 256


class GtcrnProcessor(StreamProcessor):
    name = "gtcrn"
    sample_rate = 16000
    latency_samples = N_FFT - HOP  # 16 ms

    def __init__(self, model_path: str):
        self.session = make_session(model_path)
        self.wola = StreamingWola(N_FFT, HOP, sqrt_hann(N_FFT))
        self.reset()

    def reset(self) -> None:
        self.wola.reset()
        self.conv_cache = np.zeros((2, 1, 16, 16, 33), dtype=np.float32)
        self.tra_cache = np.zeros((2, 3, 1, 1, 16), dtype=np.float32)
        self.inter_cache = np.zeros((2, 1, 33, 16), dtype=np.float32)

    def _frame(self, spec: np.ndarray) -> np.ndarray:
        mix = np.stack([spec.real, spec.imag], axis=-1).reshape(1, 257, 1, 2)
        enh, self.conv_cache, self.tra_cache, self.inter_cache = self.session.run(
            None,
            {
                "mix": mix.astype(np.float32),
                "conv_cache": self.conv_cache,
                "tra_cache": self.tra_cache,
                "inter_cache": self.inter_cache,
            },
        )
        e = enh.reshape(257, 2)
        return (e[:, 0] + 1j * e[:, 1]).astype(np.complex64)

    def feed(self, x: np.ndarray) -> np.ndarray:
        return self.wola.push(x, self._frame)
