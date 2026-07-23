"""DPDFNet streaming processors — experimental.

Two exports ship, same architecture family, same spec-in/spec-out ONNX
contract with a flat float state vector — only the STFT config and state
size differ (read from each export's ONNX metadata):

  dpdfnet_baseline.onnx     16 kHz, n_fft=320  hop=160  bins=161  state=38256
  dpdfnet2_48khz_hr.onnx    48 kHz, n_fft=960  hop=480  bins=481  state=56436

The 48 kHz export matters because it's native at the engine's own sample
rate (SAMPLE_RATE in audio/engine.py) — no resample round-trip either
side, unlike the 16 kHz baseline which discards everything above 8 kHz.

Caveat (both): the export's metadata carries erb_norm_init / spec_norm_init
values that sherpa-onnx bakes into the initial state at the right offsets.
We start from zeros instead, so the built-in feature normalization adapts
over the first ~1 s of audio (brief over/under-suppression transient after
reset()). Acceptable for a live always-on filter; fix by porting
sherpa-onnx's state layout if it ever matters.
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session
from .stft import StreamingWola, vorbis


class DpdfnetProcessor(StreamProcessor):
    """16 kHz baseline (dpdfnet_baseline.onnx)."""
    name = "dpdfnet"
    sample_rate = 16000
    n_fft, hop, bins, state_size = 320, 160, 161, 38256
    latency_samples = n_fft - hop  # 10 ms

    def __init__(self, model_path: str):
        self.session = make_session(model_path)
        self.wola = StreamingWola(self.n_fft, self.hop, vorbis(self.n_fft))
        self.reset()

    def reset(self) -> None:
        self.wola.reset()
        self.state = np.zeros(self.state_size, dtype=np.float32)

    def _frame(self, spec: np.ndarray) -> np.ndarray:
        inp = np.stack([spec.real, spec.imag], axis=-1).reshape(1, 1, self.bins, 2)
        spec_e, self.state = self.session.run(
            None, {"spec": inp.astype(np.float32), "state_in": self.state}
        )
        e = spec_e.reshape(self.bins, 2)
        return (e[:, 0] + 1j * e[:, 1]).astype(np.complex64)

    def feed(self, x: np.ndarray) -> np.ndarray:
        return self.wola.push(x, self._frame)


class Dpdfnet48kProcessor(DpdfnetProcessor):
    """48 kHz high-resolution export (dpdfnet2_48khz_hr.onnx) — native at
    the engine's sample rate, so no resampling either side."""
    name = "dpdfnet_hr"
    sample_rate = 48000
    n_fft, hop, bins, state_size = 960, 480, 481, 56436
    latency_samples = n_fft - hop  # 10 ms
