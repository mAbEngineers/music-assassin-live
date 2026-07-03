import numpy as np

from .base import StreamProcessor


class Passthrough(StreamProcessor):
    """Identity processor — routing/engine testing without a model."""

    name = "passthrough"
    sample_rate = 48000
    latency_samples = 0

    def reset(self) -> None:
        pass

    def feed(self, x: np.ndarray) -> np.ndarray:
        return x.astype(np.float32)
