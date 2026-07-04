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

import os
import queue
import threading
import time

import numpy as np

from ..processors.base import StreamProcessor

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
        self.proc = processor
        self.stats = EngineStats()
        self._in_q: queue.Queue = queue.Queue(maxsize=8)
        self._out = np.zeros(0, dtype=np.float32)  # processed mono FIFO
        self._lock = threading.Lock()
        self._running = False
        self._wet_target = 1.0   # 1 = process, 0 = bypass (dry)
        self._wet_gain = 0.0
        self._stream = None
        self._worker = None

        if processor.sample_rate != SAMPLE_RATE:
            import soxr
            self._down = soxr.ResampleStream(
                SAMPLE_RATE, processor.sample_rate, 1, dtype="float32")
            self._up = soxr.ResampleStream(
                processor.sample_rate, SAMPLE_RATE, 1, dtype="float32")
        else:
            self._down = self._up = None

        # bound how much processed audio may pile up before we drop old
        # samples (keeps wet path from drifting seconds behind live audio)
        self._max_out = BLOCK * 8

    # -- control -------------------------------------------------------------
    def set_bypass(self, bypass: bool) -> None:
        self._wet_target = 0.0 if bypass else 1.0

    def start(self, monitor_source: str, sink_name: str) -> None:
        import sounddevice as sd

        os.environ["PULSE_SOURCE"] = monitor_source
        os.environ["PULSE_SINK"] = sink_name
        _reinit_portaudio()

        self.proc.reset()
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
        outdata[:] = dry * (1.0 - ramp) + wet * ramp

    def _work(self) -> None:
        while self._running:
            try:
                block = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            t0 = time.perf_counter()
            mono = block.mean(axis=1)
            x = self._down.resample_chunk(mono) if self._down is not None else mono
            y = self.proc.feed(x)
            if self._up is not None and len(y):
                y = self._up.resample_chunk(y)
            ms = (time.perf_counter() - t0) * 1000.0
            self.stats.worker_ms_avg = 0.9 * self.stats.worker_ms_avg + 0.1 * ms
            if len(y) == 0:
                continue
            with self._lock:
                self._out = np.concatenate([self._out, y.astype(np.float32)])
                if len(self._out) > self._max_out:
                    self._out = self._out[-self._max_out:]
