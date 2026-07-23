"""DTLN streaming processor (16 kHz) — dual-stage LSTM enhancer.

breizhn/DTLN (MIT): stage 1 predicts a magnitude mask from the STFT
magnitude (carrying its own LSTM state), stage 2 refines the masked,
inverse-FFT'd time-domain frame directly (a separate LSTM state).

Unlike GTCRN/DPDFNet's WOLA framing (stft.py), DTLN's reference
implementation uses NO analysis/synthesis window — rectangular framing
plus a straight overlap-add at 75% overlap (block=512, shift=128 samples
@ 16 kHz). The model was trained end-to-end against that exact framing,
so imposing a COLA window here would only degrade it; ported directly
from breizhn's real-time reference (real_time_processing_onnx.py).
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session

BLOCK_LEN, BLOCK_SHIFT = 512, 128
STATE_SHAPE = (1, 2, 128, 2)


class DtlnProcessor(StreamProcessor):
    name = "dtln"
    sample_rate = 16000
    latency_samples = BLOCK_LEN - BLOCK_SHIFT  # 24 ms

    def __init__(self, model1_path: str, model2_path: str):
        self.sess1 = make_session(model1_path)
        self.sess2 = make_session(model2_path)
        self.reset()

    def reset(self) -> None:
        self._in_buf = np.zeros(BLOCK_LEN, dtype=np.float32)
        self._out_buf = np.zeros(BLOCK_LEN, dtype=np.float32)
        self._state1 = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._state2 = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def _frame(self, chunk: np.ndarray) -> np.ndarray:
        self._in_buf[:-BLOCK_SHIFT] = self._in_buf[BLOCK_SHIFT:]
        self._in_buf[-BLOCK_SHIFT:] = chunk

        spec = np.fft.rfft(self._in_buf)
        mag = np.abs(spec).astype(np.float32)
        phase = np.angle(spec)

        mask, self._state1 = self.sess1.run(
            None, {"input_2": mag[None, None, :], "input_3": self._state1})
        estimated = mag * mask[0, 0] * np.exp(1j * phase)
        time_frame = np.fft.irfft(estimated, n=BLOCK_LEN).astype(np.float32)

        refined, self._state2 = self.sess2.run(
            None, {"input_4": time_frame[None, None, :], "input_5": self._state2})

        self._out_buf[:-BLOCK_SHIFT] = self._out_buf[BLOCK_SHIFT:]
        self._out_buf[-BLOCK_SHIFT:] = 0.0
        self._out_buf += refined[0, 0]
        return self._out_buf[:BLOCK_SHIFT].copy()

    def feed(self, x: np.ndarray) -> np.ndarray:
        self._pending = np.concatenate([self._pending, x.astype(np.float32)])
        out = []
        while len(self._pending) >= BLOCK_SHIFT:
            chunk, self._pending = self._pending[:BLOCK_SHIFT], self._pending[BLOCK_SHIFT:]
            out.append(self._frame(chunk))
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)
