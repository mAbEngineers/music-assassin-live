"""StreamProcessor — the contract between music-assassin-live and the
Music-Assassin research repo.

A processor consumes mono float32 audio at its native `sample_rate` and
returns processed mono float32. Because processors buffer internally
(STFT hops, model frames), the number of samples returned by one `feed()`
call may differ from the number pushed; the cumulative lag is bounded by
`latency_samples`.

A processor that sets `wants_stereo = True` is handed (n, 2) instead and
must return (n, 2) — the engine skips its mono downmix and its stereo
rebuild entirely for those. Every shipped enhancer is a mono speech model,
so this exists for the separator work (ROADMAP A1): Spleeter and friends
are trained on stereo mixtures and crash or degrade on a downmix. Until
one lands, stereo output is reconstructed around mono processors instead
(audio/stereo.py); the two paths are mutually exclusive by construction.

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
    wants_stereo: bool = False   # True -> feed()/return (n, 2), not (n,)

    @abstractmethod
    def reset(self) -> None:
        """Clear all hidden state (model caches, overlap buffers)."""

    @abstractmethod
    def feed(self, x: np.ndarray) -> np.ndarray:
        """Push float32 samples; return whatever output is ready.

        Mono (n,) unless `wants_stereo`, in which case (n, 2) both ways.
        """

    @property
    def latency_ms(self) -> float:
        return 1000.0 * self.latency_samples / self.sample_rate
