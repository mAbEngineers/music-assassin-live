"""Layer 2 — streaming engine.

One duplex PortAudio stream: capture = trap sink monitor, playback = real
hardware sink. Targeting is two-layered: PULSE_SOURCE / PULSE_SINK env vars
cover the case where PortAudio's "pulse" device reaches pipewire-pulse, but
on Ubuntu 24.04 that device resolves to the PipeWire ALSA plugin which
ignores them — so after the stream starts, routing.pin_process_streams()
moves our stream nodes to the right targets with PipeWire metadata.

The audio callback only moves blocks between ring buffers; inference runs on
a worker thread. If the worker falls behind, the callback emits the dry
signal instead of glitching, and the wet/dry mix is always ramped (20 ms) so
toggling never clicks.
"""

import collections
import os
import queue
import threading
import time

import numpy as np

from ..processors.base import StreamProcessor
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


def _resolve_pulse_device():
    import sounddevice as sd
    for idx, dev in enumerate(sd.query_devices()):
        if dev["name"] == "pulse":
            return idx
    return "default"


def _reinit_portaudio():
    """PortAudio reads PULSE_SOURCE/PULSE_SINK when it connects; force a
    fresh connection after retargeting."""
    import sounddevice as sd
    sd._terminate()
    sd._initialize()


class AudioEngine:
    def __init__(self, processor: StreamProcessor):
        self.stats = EngineStats()
        self._in_q: queue.Queue = queue.Queue(maxsize=8)
        self._out = np.zeros(0, dtype=np.float32)  # processed mono FIFO
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

        os.environ["PULSE_SOURCE"] = monitor_source
        os.environ["PULSE_SINK"] = sink_name
        _reinit_portaudio()

        self.proc.reset()
        self._midside.reset()
        self._levels.clear()
        self._running = True
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

        dev = _resolve_pulse_device()
        self._stream = sd.Stream(
            device=(dev, dev), samplerate=SAMPLE_RATE, blocksize=BLOCK,
            channels=2, dtype="float32", callback=self._callback)
        self._stream.start()

        from .routing import pin_process_streams
        capture_sink = monitor_source.removesuffix(".monitor")
        if not pin_process_streams(os.getpid(), capture_sink, sink_name):
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
        except queue.Full:
            self.stats.overflows += 1

        with self._lock:
            take = min(frames, len(self._out))
            wet_mono = self._out[:take]
            self._out = self._out[take:]

        dry = indata
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
            ms = (time.perf_counter() - t0) * 1000.0
            self.stats.worker_ms_avg = 0.9 * self.stats.worker_ms_avg + 0.1 * ms
            if len(y) == 0:
                continue
            with self._lock:
                self._out = np.concatenate([self._out, y.astype(np.float32)])
                if len(self._out) > self._max_out:
                    self._out = self._out[-self._max_out:]
