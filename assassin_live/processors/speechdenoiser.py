"""SpeechDenoiser streaming processor (48 kHz, time-domain).

Model: speechdenoiser.onnx (yuyun2000/SpeechDenoiser 48k denoiser_model.onnx,
DeepFilterNet-style export). 480 samples in -> 480 samples out per call with
a flat state vector; ~480 samples (10 ms) algorithmic delay.

Native 48 kHz means zero resampling in the live engine — the cheapest path
end-to-end even though the network itself is heavier per frame than GTCRN.
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session

HOP = 480


class SpeechDenoiserProcessor(StreamProcessor):
    name = "speechdenoiser"
    sample_rate = 48000
    latency_samples = HOP  # FFT(960) - HOP(480) per the reference inference script

    def __init__(self, model_path: str):
        self.session = make_session(model_path)
        # 0 dB limit == unlimited attenuation (reference script does the same)
        self._atten = np.zeros(1, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros(45304, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def feed(self, x: np.ndarray) -> np.ndarray:
        self._pending = np.concatenate([self._pending, x.astype(np.float32)])
        out = []
        while len(self._pending) >= HOP:
            frame, self._pending = self._pending[:HOP], self._pending[HOP:]
            enh, self.state, _lsnr = self.session.run(
                None,
                {"input_frame": frame, "states": self.state, "atten_lim_db": self._atten},
            )
            out.append(enh)
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)
