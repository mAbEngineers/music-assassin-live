"""StreamProcessor — the contract between music-assassin-live and the
Music-Assassin research repo.

A processor consumes mono float32 audio at its native `sample_rate` and
returns processed mono float32. Because processors buffer internally
(STFT hops, model frames), the number of samples returned by one `feed()`
call may differ from the number pushed; the cumulative lag is bounded by
`latency_samples`.

The research repo benchmarks candidate models against this same interface
and promotes winners as ONNX + model_card.json release assets. The app
never needs code changes for a new model that ships a processor here.
"""

from abc import ABC, abstractmethod

import numpy as np


class StreamProcessor(ABC):
    name: str = "base"
    sample_rate: int = 16000     # rate this processor consumes/produces
    latency_samples: int = 0     # algorithmic delay at sample_rate

    @abstractmethod
    def reset(self) -> None:
        """Clear all hidden state (model caches, overlap buffers)."""

    @abstractmethod
    def feed(self, x: np.ndarray) -> np.ndarray:
        """Push mono float32 samples; return whatever output is ready."""

    @property
    def latency_ms(self) -> float:
        return 1000.0 * self.latency_samples / self.sample_rate
