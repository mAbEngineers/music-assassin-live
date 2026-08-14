"""DPDFNet streaming processors — experimental.

Two exports ship, same architecture family, same spec-in/spec-out ONNX
contract with a flat float state vector — only the STFT config and state
size differ (read from each export's ONNX metadata):

  dpdfnet_baseline.onnx     16 kHz, n_fft=320  hop=160  bins=161  state=38256
  dpdfnet2_48khz_hr.onnx    48 kHz, n_fft=960  hop=480  bins=481  state=56436

The 48 kHz export matters because it's native at the engine's own sample
rate (SAMPLE_RATE in audio/engine.py) — no resample round-trip either
side, unlike the 16 kHz baseline which discards everything above 8 kHz.

Both exports' metadata carries erb_norm_init / spec_norm_init values —
sherpa-onnx's reference GetInitState() (offline-speech-denoiser-dpdfnet-
model.cc, same code path the streaming/online DPDFNet impl calls) writes
them into the state vector's leading elements: erb_norm_init at state[0:erb_size],
spec_norm_init immediately after at state[erb_size:erb_size+spec_size],
zeros for the rest (the actual RNN/conv recurrent state). Skipping that and
zero-initializing the whole vector was measured to make dpdfnet_hr's music
attenuation swing ~36 dB across three orders of magnitude of input level —
not a "brief transient", a level-dependent filter unusable for live audio
at unpredictable gain. reset() below seeds the calibrated prefix from the
session's own metadata so this doesn't depend on which export is loaded.
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session
from .stft import StreamingWola, vorbis


def _seeded_state(session, state_size: int) -> np.ndarray:
    """Build the initial state vector per sherpa-onnx's GetInitState(): the
    export's calibrated erb_norm_init / spec_norm_init prefix, zeros after.
    Falls back to all-zeros if a model lacks the metadata (or it doesn't add
    up), so a future non-DPDFNet-shaped export can't crash reset().
    """
    state = np.zeros(state_size, dtype=np.float32)
    meta = session.get_modelmeta().custom_metadata_map
    try:
        erb_size = int(meta["erb_norm_state_size"])
        spec_size = int(meta["spec_norm_state_size"])
        erb_init = np.array(meta["erb_norm_init"].split(","), dtype=np.float32)
        spec_init = np.array(meta["spec_norm_init"].split(","), dtype=np.float32)
    except (KeyError, ValueError):
        return state
    if (
        erb_init.size != erb_size
        or spec_init.size != spec_size
        or erb_size + spec_size > state_size
    ):
        return state
    state[:erb_size] = erb_init
    state[erb_size:erb_size + spec_size] = spec_init
    return state


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
        self.state = _seeded_state(self.session, self.state_size)

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
