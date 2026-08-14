"""Layer 2 — streaming engine.

One duplex PortAudio stream: capture = trap monitor, playback = real output
device. Device targeting is delegated to a RoutingBackend (backends/base.py)
in two steps — resolve_stream_devices() right before the stream opens,
pin_stream() right after — because "point PortAudio at the right device"
needs two passes on PipeWire (an env var a stream *usually* honors, then an
explicit fix-up for when it doesn't; see backends/pipewire.py for why) and
will need a wholly different mechanism again on Windows. AudioEngine only
knows the two-step shape, not why either platform needs it — that keeps
this module the "portable" layer the architecture doc promises.

The audio callback only moves blocks between ring buffers; inference runs on
a worker thread. If the worker falls behind, the callback emits the dry
signal instead of glitching, and the wet/dry mix is always ramped (20 ms) so
toggling never clicks. Dry is delayed through its own FIFO in lockstep with
the wet one (see _dry_out) so both always refer to the same original
instant — mixing live dry against lagged wet sounded like an echo.
"""

import collections
import os
import queue
import threading
import time

import numpy as np

from ..processors.base import StreamProcessor
from .backends.base import RoutingBackend
from .bandlimit import BandlimitFilter
from .midside import MidSideFilter

SAMPLE_RATE = 48000
BLOCK = 960          # 20 ms
XFADE_BLOCKS = 1     # gain ramp spread over one block == 20 ms


class EngineStats:
    def __init__(self):
        self.blocks_in = 0
        self.fallback_blocks = 0   # wet wanted but not ready -> dry emitted
        self.overflows = 0
        self.worker_ms_avg = 0.0
        self.xruns = 0


class AudioEngine:
    def __init__(self, processor: StreamProcessor,
                 backend: RoutingBackend | None = None):
        # Optional because the backend is only ever consulted by start() --
        # the audio path (callback, wet/dry mix, limiter) is pure signal
        # processing that has nothing to say about device targeting. Making
        # it mandatory would mean no engine can be constructed without a
        # working platform routing backend, which needlessly couples the two
        # and makes the processing path untestable on its own.
        self._backend = backend
        self.stats = EngineStats()
        self._in_q: queue.Queue = queue.Queue(maxsize=8)
        self._out = np.zeros(0, dtype=np.float32)  # processed mono FIFO
        # dry audio delayed through the same FIFO pattern as _out (fed and
        # drained in lockstep, callback-thread-only so no lock needed) —
        # mixing live indata against wet (which lags by the model's
        # algorithmic latency + queue/worker hand-off) produced an audible
        # echo/doubling instead of a blend; see stop()/_callback below.
        self._dry_out = np.zeros((0, 2), dtype=np.float32)
        self._lock = threading.Lock()
        self._running = False
        self._wet_target = 1.0   # mix intensity: 1 = fully processed, 0 = fully original
        self._wet_gain = 0.0     # current interpolated mix (ramped, click-free)
        self._dry_volume = 1.0   # original-signal gain
        self._wet_volume = 1.0   # processed-signal gain
        self._mute_dry = False
        self._mute_wet = False
        self._stream = None
        self._worker = None
        self._levels: collections.deque = collections.deque(maxlen=64)
        self._midside = MidSideFilter()
        self._midside_enabled = False
        self._bandlimit = BandlimitFilter(SAMPLE_RATE)
        self._bandlimit_enabled = True  # safety hygiene filter — on by default
        self._atten_limit_db = 0.0  # forwarded to the processor if it supports one

        # (processor, downsampler, upsampler) swapped as one unit so the
        # worker thread never reads a processor paired with the wrong
        # resamplers mid-switch
        self._runtime = self._build_runtime(processor)

        # bound how much processed audio may pile up before we drop old
        # samples (keeps wet path from drifting seconds behind live audio)
        self._max_out = BLOCK * 8

    @staticmethod
    def _build_runtime(processor: StreamProcessor):
        processor.reset()
        if processor.sample_rate != SAMPLE_RATE:
            import soxr
            down = soxr.ResampleStream(
                SAMPLE_RATE, processor.sample_rate, 1, dtype="float32")
            up = soxr.ResampleStream(
                processor.sample_rate, SAMPLE_RATE, 1, dtype="float32")
        else:
            down = up = None
        return processor, down, up

    @property
    def proc(self) -> StreamProcessor:
        return self._runtime[0]

    # -- control -------------------------------------------------------------
    def set_processor(self, processor: StreamProcessor) -> None:
        """Hot-swap the model/pipeline while the stream keeps running."""
        self._runtime = self._build_runtime(processor)
        self._apply_atten_limit()

    def set_atten_limit(self, db: float) -> None:
        """Suppression-strength ceiling, for processors that expose one
        (currently only speechdenoiser); a no-op on ones that don't."""
        self._atten_limit_db = db
        self._apply_atten_limit()

    def _apply_atten_limit(self) -> None:
        setter = getattr(self.proc, "set_atten_limit", None)
        if setter:
            setter(self._atten_limit_db)

    def recent_levels(self) -> list:
        """Copy of the most recent output RMS levels, oldest first — for a
        live waveform/level meter."""
        return list(self._levels)

    def set_intensity(self, intensity: float) -> None:
        """Music-removal intensity: 0.0 = fully original, 1.0 = fully processed."""
        self._wet_target = float(np.clip(intensity, 0.0, 1.0))

    def set_bypass(self, bypass: bool) -> None:
        self.set_intensity(0.0 if bypass else 1.0)

    def set_midside(self, enabled: bool) -> None:
        """Toggle the mid/side stereo pre-filter (see audio/midside.py):
        suppresses wide-panned content ahead of the enhancer, using stereo
        panning instead of spectral guessing. Off by default — stacks
        with whichever pipeline model is selected."""
        self._midside_enabled = enabled

    def set_bandlimit(self, enabled: bool) -> None:
        """Toggle the ~20 Hz-20 kHz band-limit (see audio/bandlimit.py) on
        the processed output — removes content outside human hearing.
        On by default; adds no algorithmic latency (a cheap IIR filter,
        not the buffered WOLA framing the models use)."""
        self._bandlimit_enabled = enabled

    def set_volumes(self, dry: float | None = None, wet: float | None = None,
                     mute_dry: bool | None = None, mute_wet: bool | None = None) -> None:
        """Independent gain for the original (dry) and de-musiced (wet) paths."""
        if dry is not None:
            self._dry_volume = float(dry)
        if wet is not None:
            self._wet_volume = float(wet)
        if mute_dry is not None:
            self._mute_dry = mute_dry
        if mute_wet is not None:
            self._mute_wet = mute_wet

    def start(self, monitor_source: str, sink_name: str) -> None:
        import sounddevice as sd

        if self._backend is None:
            raise RuntimeError(
                "AudioEngine.start() needs a RoutingBackend; construct it as "
                "AudioEngine(processor, backend). It is optional only for "
                "callers that never stream (offline processing, tests).")

        # first targeting pass — see backend.resolve_stream_devices() for
        # why this is platform-specific and why it isn't sufficient alone.
        capture_dev, playback_dev = self._backend.resolve_stream_devices(
            monitor_source, sink_name)

        self.proc.reset()
        self._midside.reset()
        self._bandlimit.reset()
        self._levels.clear()
        self._running = True
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

        self._stream = sd.Stream(
            device=(capture_dev, playback_dev), samplerate=SAMPLE_RATE,
            blocksize=BLOCK, channels=2, dtype="float32",
            callback=self._callback)
        self._stream.start()

        # second targeting pass, once the stream (and its stream nodes)
        # actually exist — see backend.pin_stream().
        if not self._backend.pin_stream(os.getpid(), monitor_source, sink_name):
            print("warning: could not pin audio streams to their targets; "
                  "routing may be wrong (check pw-link -l)")

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker:
            self._worker.join(timeout=2)
            self._worker = None
        # drop any blocks the worker didn't get to — without this, a
        # restart replays up to maxsize blocks (160 ms) of stale pre-stop
        # audio through the freshly-reset processor before catching up to
        # live audio, an audible glitch right after every retarget.
        with self._in_q.mutex:
            self._in_q.queue.clear()
        with self._lock:
            self._out = np.zeros(0, dtype=np.float32)
        self._dry_out = np.zeros((0, 2), dtype=np.float32)
        self._wet_gain = 0.0

    def retarget(self, monitor_source: str, sink_name: str) -> None:
        """Output device changed (headset plugged/unplugged)."""
        self.stop()
        self.start(monitor_source, sink_name)

    # -- audio path ------------------------------------------------------------
    def _callback(self, indata, outdata, frames, _time, status) -> None:
        if status:
            self.stats.xruns += 1
        self.stats.blocks_in += 1
        try:
            self._in_q.put_nowait(indata.copy())
            self._dry_out = np.concatenate([self._dry_out, indata])
            if len(self._dry_out) > self._max_out:
                self._dry_out = self._dry_out[-self._max_out:]
        except queue.Full:
            self.stats.overflows += 1
            # dropped this block from processing — skip it here too, so the
            # dry FIFO never gets ahead of what wet will actually produce

        with self._lock:
            take = min(frames, len(self._out))
            wet_mono = self._out[:take]
            self._out = self._out[take:]

        # dry pulled from the same lockstep FIFO as wet (both fed once per
        # callback, both drained by the same amount here) so they always
        # refer to the same original instant, however large the model's
        # actual latency turns out to be — using live indata instead made
        # the dry/wet mix sound like an echo rather than a blend.
        take_dry = min(take, len(self._dry_out))
        dry = np.empty_like(indata)
        dry[:take_dry] = self._dry_out[:take_dry]
        if take_dry < frames:
            dry[take_dry:] = indata[take_dry:]  # startup-only fallback
        self._dry_out = self._dry_out[take_dry:]

        wet = np.empty_like(dry)
        wet[:take, 0] = wet_mono
        wet[:take, 1] = wet_mono
        if take < frames:
            wet[take:] = dry[take:]  # underrun tail: fall back to dry
            if self._wet_gain > 0.01:
                self.stats.fallback_blocks += 1

        # per-block linear ramp toward target — click-free toggle
        g0, g1 = self._wet_gain, self._wet_target
        if g0 != g1:
            step = 1.0 / (XFADE_BLOCKS * frames)
            g1 = g0 + np.clip(g1 - g0, -step * frames, step * frames)
            ramp = np.linspace(g0, g1, frames, dtype=np.float32)[:, None]
            self._wet_gain = float(g1)
        else:
            ramp = g0
        dry_g = 0.0 if self._mute_dry else self._dry_volume
        wet_g = 0.0 if self._mute_wet else self._wet_volume
        outdata[:] = dry * dry_g * (1.0 - ramp) + wet * wet_g * ramp
        self._levels.append(float(np.sqrt(np.mean(np.square(outdata)))))

    def _work(self) -> None:
        while self._running:
            try:
                block = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            proc, down, up = self._runtime
            t0 = time.perf_counter()
            mono = (self._midside.process(block) if self._midside_enabled
                   else block.mean(axis=1))
            if len(mono) == 0:
                continue
            x = down.resample_chunk(mono) if down is not None else mono
            y = proc.feed(x)
            if up is not None and len(y):
                y = up.resample_chunk(y)
            if len(y) and self._bandlimit_enabled:
                y = self._bandlimit.process(y)
            ms = (time.perf_counter() - t0) * 1000.0
            self.stats.worker_ms_avg = 0.9 * self.stats.worker_ms_avg + 0.1 * ms
            if len(y) == 0:
                continue
            with self._lock:
                self._out = np.concatenate([self._out, y.astype(np.float32)])
                if len(self._out) > self._max_out:
                    self._out = self._out[-self._max_out:]
