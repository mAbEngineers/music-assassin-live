"""Spleeter 2-stem source separation as a StreamProcessor (ROADMAP A1).

This is the first `wants_stereo` processor: Spleeter is trained on stereo
mixtures, so it takes the pair directly and the engine skips both the mono
downmix and the stereo rebuild built around mono enhancers (audio/stereo.py).

It is NOT a drop-in for the shipped enhancers, and the reason is latency, not
throughput. Measured per-call cost is nearly FLAT in the chunk length
(130-205 ms from 0.25 s to 8 s, scripts/spike_a1_separator.py), so it is fixed
overhead rather than work proportional to the audio. RTF therefore improves
only because the denominator grows, and the binding constraint is how long a
chunk you are willing to wait for. See `latency_samples` below.

WHAT WAS MEASURED, so the next reader does not re-derive it:

  * Feed at 44100 and sherpa does no internal resampling, which makes the
    output length exactly `(n // 1024) * 1024` -- verified over ten buffer
    sizes. Feed at any other rate and it resamples to 44.1 kHz internally,
    the returned length is in the OUTPUT rate, and offsets stop lining up
    with input offsets. Hence `sample_rate = 44100`: let the engine's
    resamplers (which already build 2-channel pairs for a wants_stereo
    processor) do the conversion, and keep this class's arithmetic exact.

  * Every call puts a large impulse at BOTH ENDS of its output. On real
    corpus audio one call peaked at 11043 in its first 8 samples against an
    interior peak of 1.28, with a smaller one (863) at the tail. The A1
    spike recorded only the head one; the tail is just as real. That is why
    context is discarded on both sides rather than only the front.

  * Chunking does not reproduce one-shot separation, and MORE CONTEXT DOES
    NOT FIX IT: interior chunks land 8-11 dB below a whole-buffer run, flat
    across 0.25 s and 0.5 s of context and across 1 s / 2 s / 4 s chunks. It
    is not a seam artifact -- fitting one gain per chunk explains almost
    none of it (-7.8 -> -8.1 dB), and the residual is spread through the
    chunk rather than piled at its edges. Spleeter masks a spectrogram whose
    context is the whole buffer, so a chunked run is a different computation,
    not an approximation of the same one. Whether that costs anything
    AUDIBLE is a question for bench_quality.py against the reference stems,
    which is what the `spleeter_<n>ms` registry variants exist to sweep.
"""

import numpy as np

from .base import StreamProcessor

# Output length is quantised to this, so the post-context must be at least
# one full unit or a chunk can come back short of what we need to emit.
QUANTUM = 1024

_EMPTY = np.zeros((0, 2), dtype=np.float32)

# Loading the two 26 MB ONNX files costs ~4.2 s, and bench_quality.py builds a
# processor three times per corpus item (the run plus two streaming-consistency
# offsets) -- about half the wall clock of a sweep spent re-reading the same
# weights. The separator is stateless across process() calls (verified: three
# consecutive calls on one buffer are bit-identical) and the chunk length is a
# property of THIS wrapper, not of the sherpa config, so every spleeter_<n>ms
# variant can share one instance. Keyed by what actually distinguishes a
# session, so a different model path or thread count still gets its own.
_SEPARATORS: dict[tuple, object] = {}


def _separator(vocals_path: str, accompaniment_path: str, num_threads: int):
    key = (vocals_path, accompaniment_path, num_threads)
    sep = _SEPARATORS.get(key)
    if sep is None:
        # Imported here, not at module scope: sherpa-onnx is deliberately not
        # an app dependency (it is not in requirements.txt and the app venv
        # does not have it), so importing assassin_live.processors must not
        # drag it in. Only constructing a Spleeter needs it.
        import sherpa_onnx as so

        cfg = so.OfflineSourceSeparationConfig()
        cfg.model.spleeter.vocals = vocals_path
        cfg.model.spleeter.accompaniment = accompaniment_path
        cfg.model.num_threads = num_threads
        sep = _SEPARATORS[key] = so.OfflineSourceSeparation(cfg)
    return sep


class SpleeterProcessor(StreamProcessor):
    """Chunked Spleeter. Emits the vocals stem, music suppressed.

    `chunk_samples` is the block handed to the separator per call and is the
    dominant term in latency; `context_samples` is extra audio processed on
    each side and thrown away, present only to move the edge impulses out of
    what gets emitted (it does not improve agreement with a one-shot run).
    """

    name = "spleeter"
    sample_rate = 44100
    wants_stereo = True

    def __init__(self, vocals_path: str, accompaniment_path: str,
                 chunk_samples: int = 44032, context_samples: int = 4096,
                 num_threads: int = 1):
        if context_samples < QUANTUM:
            raise ValueError(
                f"context_samples must be >= {QUANTUM} (output length is "
                f"quantised to it, so a smaller tail cannot be discarded "
                f"safely); got {context_samples}")

        self._sep = _separator(vocals_path, accompaniment_path, num_threads)

        self.chunk = int(chunk_samples)
        self.context = int(context_samples)
        # A chunk cannot be emitted until its trailing context has arrived,
        # so the algorithmic delay is chunk + trailing context -- ~1.1 s at
        # the 1 s default. This is the number that rules the separator out of
        # the ~50 ms live path and confines it to a high-latency mode.
        self.latency_samples = self.chunk + self.context
        self.reset()

    def reset(self) -> None:
        # Leading context for the very first chunk is silence: the model puts
        # its impulse inside the padding, where it is discarded, instead of
        # into the first samples the listener hears.
        self._hist = np.zeros((self.context, 2), dtype=np.float32)
        self._buf = _EMPTY

    def _separate(self, block: np.ndarray) -> np.ndarray:
        """(n, 2) in -> (m, 2) vocals out, m = (n // QUANTUM) * QUANTUM."""
        out = self._sep.process(self.sample_rate,
                                np.ascontiguousarray(block.T.astype(np.float32)))
        # stems[0] is vocals, [1] accompaniment -- established by
        # cross-correlating each against the corpus's own stems in the A1
        # spike, not assumed from the file names.
        return np.ascontiguousarray(out.stems[0].data.T.astype(np.float32))

    def feed(self, x: np.ndarray) -> np.ndarray:
        if x.ndim != 2 or x.shape[1] != 2:
            raise ValueError(f"wants_stereo processor needs (n, 2), got {x.shape}")
        self._buf = np.concatenate([self._buf, x.astype(np.float32)])

        need = self.chunk + self.context          # chunk plus its trailing context
        ready = []
        while len(self._buf) >= need:
            block = np.concatenate([self._hist, self._buf[:need]])
            out = self._separate(block)
            lead = len(self._hist)
            # Guaranteed by context >= QUANTUM: quantisation can eat at most
            # QUANTUM - 1 samples off the tail, and the trailing context is
            # at least that long, so the emitted window is always complete.
            ready.append(out[lead:lead + self.chunk])
            consumed = self._buf[:self.chunk]
            self._hist = np.concatenate([self._hist, consumed])[-self.context:]
            self._buf = self._buf[self.chunk:]

        return np.concatenate(ready) if ready else _EMPTY
