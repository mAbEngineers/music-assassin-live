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

The wet path is mono end to end (the enhancers are mono speech models), so
the processed signal used to be written to both output channels — measured
in B1 as a total stereo collapse on 104/104 corpus pairs. StereoRebuild
(audio/stereo.py) reconstructs the image from the dry pair instead; the
worker keeps its own dry FIFO for it, drained in the same lockstep as the
callback's, so the pair it re-images is the instant the model actually
processed rather than whatever is live.
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
from .lag import LagEstimator
from .midside import MidSideFilter
from .stereo import StereoRebuild

SAMPLE_RATE = 48000
BLOCK = 960          # 20 ms
XFADE_BLOCKS = 1     # gain ramp spread over one block == 20 ms

LIMITER_THRESHOLD = 0.8  # below this, output passes through untouched
LIMITER_CEILING = 0.98   # peaks above threshold compress toward this, never past it

_EMPTY_STEREO = np.zeros((0, 2), dtype=np.float32)


def _soft_limit(x: np.ndarray) -> np.ndarray:
    """Memoryless soft-knee limiter: a no-op below LIMITER_THRESHOLD, and a
    smooth tanh compression from there up to LIMITER_CEILING for anything
    louder — so boosting wet gain can't send a hard-clipped (or >1.0)
    signal to the speaker, but normal-level audio is never colored."""
    mag = np.abs(x)
    over = mag > LIMITER_THRESHOLD
    if not np.any(over):
        return x
    span = LIMITER_CEILING - LIMITER_THRESHOLD
    compressed = LIMITER_THRESHOLD + span * np.tanh((mag - LIMITER_THRESHOLD) / span)
    return np.where(over, np.sign(x) * compressed, x)


class EngineStats:
    def __init__(self):
        self.blocks_in = 0
        self.fallback_blocks = 0   # wet wanted but not ready -> dry emitted
        self.overflows = 0
        self.worker_ms_avg = 0.0
        self.xruns = 0
        self.callback_errors = 0   # exception in _callback -> would silently kill the stream
        self.backlog_trimmed = 0   # samples dropped once to correct a startup stall


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
        self._out = np.zeros((0, 2), dtype=np.float32)  # processed stereo FIFO
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
        self._lag_ema = 0.0      # samples of pipeline delay, smoothed
        # How far the processor's output trails the dry it is paired with,
        # measured at stream start (audio/lag.py). Needed by the stereo
        # rebuild, which is bypassed until it is known, and by the backlog
        # correction below.
        self._lag_est = LagEstimator(SAMPLE_RATE)
        self._mask_lag: int | None = None
        # Set by the off-thread measurement; the worker picks it up and does
        # the padding itself, so _dry_work is only ever touched by one thread.
        self._pending_lag: int | None = None
        self._measuring = False
        self._midside = MidSideFilter()
        self._midside_enabled = False
        self._stereo = StereoRebuild()
        self._stereo_enabled = False  # B3 fix; off until measured — see set_stereo()
        # dry stereo held for the rebuild, worker-thread-only. Same lockstep
        # trick as _dry_out: fed every block, drained by exactly what the
        # processor produced, so its head always lines up with the model's
        # output however large the model's internal lag is.
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._bandlimit = BandlimitFilter(SAMPLE_RATE)
        # second instance for the right channel: it is a stateful IIR, so a
        # stereo processor's two channels cannot share one. Unused until a
        # wants_stereo processor exists.
        self._bandlimit_r = BandlimitFilter(SAMPLE_RATE)
        self._bandlimit_enabled = True  # safety hygiene filter — on by default
        self._atten_limit_db = 0.0  # forwarded to the processor if it supports one

        # (processor, downsampler, upsampler) swapped as one unit so the
        # worker thread never reads a processor paired with the wrong
        # resamplers mid-switch
        self._runtime = self._build_runtime(processor)

        # bound how much processed audio may pile up before we drop old
        # samples (keeps wet path from drifting seconds behind live audio)
        self._max_out = BLOCK * 8

    def _warm_up(self, seconds: float = 0.5) -> None:
        """Run silence through the processor so the first real block meets a
        hot runtime. Errors are swallowed: a processor that dislikes being
        warmed must not stop the stream from opening."""
        proc, down, up = self._runtime
        n = int(SAMPLE_RATE * seconds)
        try:
            if proc.wants_stereo:
                x = np.zeros((n, 2), dtype=np.float32)
            else:
                x = np.zeros(n, dtype=np.float32)
            if down is not None:
                x = down.resample_chunk(x)
            y = proc.feed(x)
            if up is not None and len(y):
                up.resample_chunk(y)
        except Exception:  # noqa: BLE001 — see docstring
            pass
        finally:
            proc.reset()
            if down is not None or up is not None:
                # the resamplers now hold silence; rebuild them clean
                self._runtime = self._build_runtime(proc)

    @staticmethod
    def _build_runtime(processor: StreamProcessor):
        processor.reset()
        if processor.sample_rate != SAMPLE_RATE:
            import soxr
            ch = 2 if processor.wants_stereo else 1
            down = soxr.ResampleStream(
                SAMPLE_RATE, processor.sample_rate, ch, dtype="float32")
            up = soxr.ResampleStream(
                processor.sample_rate, SAMPLE_RATE, ch, dtype="float32")
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

    @property
    def latency_ms(self) -> float:
        """Measured delay between a sample arriving and its processed
        counterpart leaving, in ms.

        Excludes the output device's own buffer, which the engine cannot
        see — so this is the latency the app *adds*, which is the number a
        user watching video wants (ROADMAP C7). Comparable to
        bench_quality.py's `latency_ms` column, which measures the same
        span by cross-correlation.
        """
        return 1000.0 * self._lag_ema / SAMPLE_RATE

    def recent_levels(self) -> list:
        """Copy of the most recent output RMS levels, oldest first — for a
        live waveform/level meter."""
        return list(self._levels)

    @property
    def stream_ok(self) -> bool:
        """False if the underlying PortAudio stream died silently — e.g. an
        uncaught callback exception or the device disappearing mid-stream
        both leave it inactive with no error surfaced, so the caller must
        poll this rather than wait for an exception."""
        return self._stream is not None and self._stream.active

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

    def set_stereo(self, enabled: bool) -> None:
        """Toggle stereo reconstruction of the wet path (audio/stereo.py).

        Off by default: it changes what every shipped pipeline sends to the
        speakers, and this project does not flip shipped defaults on an
        untested expectation (see ROADMAP §2.2 for what that cost last time).
        Sweep it first — `bench_quality.py --sweep stereo=off,on` reports the
        suppression it trades for the image — then flip the default on the
        numbers. A no-op for processors that produce stereo themselves.
        """
        self._stereo_enabled = enabled

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

        # Warm the processor BEFORE the stream exists. The first inference
        # after load is slow — measured at 26 blocks (~0.5 s) for
        # dpdfnet_hr against 1 block once hot — and anything the callback
        # sees during that time piles up in the dry FIFO, whose depth then
        # IS the session's latency. Cold start was baking in 140 ms of
        # permanent delay instead of 20 ms, varying run to run with
        # scheduling. Paying it here costs a moment on the button, which is
        # now a state the button can show.
        self._warm_up()
        self.proc.reset()
        self._midside.reset()
        self._bandlimit.reset()
        self._bandlimit_r.reset()
        self._stereo.reset()
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._lag_est = LagEstimator(SAMPLE_RATE)
        self._mask_lag = None
        self._pending_lag = None
        self._measuring = False
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
            self._out = np.zeros((0, 2), dtype=np.float32)
        self._dry_out = np.zeros((0, 2), dtype=np.float32)
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._lag_ema = 0.0
        self._mask_lag = None
        self._pending_lag = None
        self._measuring = False
        self._wet_gain = 0.0

    def retarget(self, monitor_source: str, sink_name: str) -> None:
        """Output device changed (headset plugged/unplugged).

        The heavy path: full teardown and rebuild. Try retarget_output()
        first — this one costs a multi-second dropout and a reset model.
        """
        self.stop()
        self.start(monitor_source, sink_name)

    def retarget_output(self, sink_name: str) -> bool:
        """Move just the playback endpoint, keeping the stream, the model's
        hidden state and every buffer alive (ROADMAP C1).

        An output change does not concern the capture side or the processor,
        so nothing about them needs to be disturbed. Returns False if the
        live move was not possible, meaning the caller should fall back to
        retarget(); it deliberately does not fall back on its own, because
        the caller is the one that knows whether a heavyweight rebuild is
        acceptable right now.
        """
        if self._backend is None or not self.stream_ok:
            return False
        mover = getattr(self._backend, "retarget_playback", None)
        if mover is None:
            return False           # backend predates C1; heavy path only
        try:
            return bool(mover(os.getpid(), sink_name))
        except Exception:  # noqa: BLE001 — any failure just means "fall back"
            return False

    # -- audio path ------------------------------------------------------------
    def _callback(self, indata, outdata, frames, _time, status) -> None:
        if status:
            self.stats.xruns += 1
        self.stats.blocks_in += 1
        try:
            self._callback_body(indata, outdata, frames)
        except Exception:  # noqa: BLE001 — sounddevice silently kills the
            # whole stream on any exception out of this callback (it goes
            # inactive with no error surfaced to the app — the GUI keeps
            # showing "on" with no audio, needing a manual off/on to
            # recover). Never let that happen: fall back to plain dry
            # passthrough for this block and keep the stream alive.
            self.stats.callback_errors += 1
            outdata[:] = indata

    def _callback_body(self, indata, outdata, frames) -> None:
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
            wet_st = self._out[:take]
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
        # What is left in the dry FIFO after the drain IS the pipeline's
        # delay: input that has been fed but whose processed counterpart is
        # not out yet. Measured rather than summed from nominal parts —
        # the model's own latency_samples is only one contributor, alongside
        # the resamplers, the queue hand-off and (when on) the stereo
        # rebuild, and the sum of the documented figures has never matched
        # what bench_quality measures end-to-end. Smoothed because _out is
        # filled by another thread, so any single reading jitters by up to a
        # block.
        self._lag_ema = 0.9 * self._lag_ema + 0.1 * len(self._dry_out)

        wet = np.empty_like(dry)
        wet[:take] = wet_st
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
        mixed = dry * dry_g * (1.0 - ramp) + wet * wet_g * ramp
        outdata[:] = _soft_limit(mixed) if wet_g > 1.0 else mixed
        self._levels.append(float(np.sqrt(np.mean(np.square(outdata)))))

    def _work(self) -> None:
        while self._running:
            try:
                block = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            proc, down, up = self._runtime
            t0 = time.perf_counter()
            y = (self._wet_stereo_proc(proc, down, up, block)
                 if proc.wants_stereo
                 else self._wet_mono_proc(proc, down, up, block))
            ms = (time.perf_counter() - t0) * 1000.0
            self.stats.worker_ms_avg = 0.9 * self.stats.worker_ms_avg + 0.1 * ms
            if len(y) == 0:
                continue
            with self._lock:
                self._out = np.concatenate([self._out, y.astype(np.float32)])
                if len(self._out) > self._max_out:
                    self._out = self._out[-self._max_out:]

    def _wet_mono_proc(self, proc, down, up, block) -> np.ndarray:
        """The shipped path: stereo in, mono model, stereo back out.

        Returns (n, 2) either way — with the image rebuilt from the dry pair
        when enabled, or the mono signal duplicated (the pre-B3 behaviour)
        when not, so the callback never has to care which.
        """
        # Kept fed unconditionally rather than only while the rebuild is on:
        # this array is worker-thread-only, and letting the toggle mutate it
        # from the UI thread would race the concatenate below for no gain.
        self._dry_work = np.concatenate([self._dry_work, block])
        if len(self._dry_work) > self._max_out:
            self._dry_work = self._dry_work[-self._max_out:]

        mono = (self._midside.process(block) if self._midside_enabled
                else block.mean(axis=1))
        if len(mono) == 0:
            return _EMPTY_STEREO
        x = down.resample_chunk(mono) if down is not None else mono
        y = proc.feed(x)
        if up is not None and len(y):
            y = up.resample_chunk(y)
        if len(y) and self._bandlimit_enabled:
            y = self._bandlimit.process(y)
        if len(y) == 0:
            return _EMPTY_STEREO

        n = min(len(y), len(self._dry_work))
        dry_st, self._dry_work = self._dry_work[:n], self._dry_work[n:]
        if n == 0:
            return np.repeat(y[:, None], 2, axis=1)

        # FIFO position alone does NOT align these. The processor's output
        # trails the input it is paired with — by 50 ms for dpdfnet_hr, and
        # by nothing like its nominal latency_samples (audio/lag.py). Shaping
        # the dry pair with a mask measured 50 ms away from it is what made
        # the stereo rebuild sound doubled and out of sync when it shipped.
        if self._mask_lag is None:
            self._collect_lag(dry_st, y[:n])
            # Until it is known the rebuild stays bypassed rather than
            # running misaligned — a wrong image is worse than none.
            return np.repeat(y[:, None], 2, axis=1)

        if not self._stereo_enabled:
            return np.repeat(y[:, None], 2, axis=1)
        return self._stereo.process(dry_st, y[:n])

    def _collect_lag(self, dry_st: np.ndarray, wet: np.ndarray) -> None:
        """Gather windows for the lag measurement and apply the answer.

        Correlation runs on its own thread — it costs 5-12 ms, and spending
        that here empties the wet FIFO and inflates the dry backlog, which
        is permanent latency (audio/lag.py). Only this thread ever mutates
        _dry_work, so the answer is applied here rather than by the measurer.
        """
        if self._pending_lag is not None:
            lag, self._pending_lag = self._pending_lag, None
            self._mask_lag = lag
            if lag:
                # Delaying the dry stream by the lag makes every later
                # pairing correct with no per-sample bookkeeping: the head
                # of the FIFO is simply that much further behind.
                pad = np.zeros((lag, 2), dtype=np.float32)
                self._dry_work = np.concatenate([pad, self._dry_work])
            return
        if self._measuring:
            return
        if self._lag_est.exhausted:
            self._mask_lag = 0        # assume aligned; bounded, and no worse
            return                    # than never having measured
        if not self._lag_est.push(dry_st.mean(axis=1), wet):
            return

        d, w = self._lag_est.take_window()
        self._measuring = True

        def run():
            try:
                lag = self._lag_est.measure(d, w)
            except Exception:  # noqa: BLE001 — a failed measurement just
                lag = None     # means another window, never a dead stream
            if lag is not None:
                self._pending_lag = lag
            self._measuring = False

        threading.Thread(target=run, daemon=True).start()

    def _wet_stereo_proc(self, proc, down, up, block) -> np.ndarray:
        """A processor that consumes the stereo pair itself (ROADMAP A1).

        No downmix, so no image to rebuild — and no mid/side either: that
        filter's whole job is producing a mono, center-emphasised signal for
        a mono model, which is precisely what this path does not want.
        """
        x = down.resample_chunk(block) if down is not None else block
        y = proc.feed(x)
        if up is not None and len(y):
            y = up.resample_chunk(y)
        if len(y) and self._bandlimit_enabled:
            y = np.stack([self._bandlimit.process(y[:, 0]),
                          self._bandlimit_r.process(y[:, 1])], axis=1)
        return y
