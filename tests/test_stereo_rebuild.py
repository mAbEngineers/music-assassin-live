#!/usr/bin/env python3
"""StereoRebuild (audio/stereo.py) — the B3 fix, tested without models.

The property that matters is not "output resembles input" but "the stereo
image the mono wet path destroyed is back", so these check the same quantity
bench_quality.stereo_width_db reports: side-channel energy retained. B1
measured that at −161 dB on 104/104 corpus pairs, every model, every config;
the mono-path test below reproduces that number from first principles so the
comparison is visible in one run.

Run: .venv/bin/python tests/test_stereo_rebuild.py
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import BLOCK, AudioEngine  # noqa: E402
from assassin_live.audio.lag import LagEstimator, _xcorr_lag  # noqa: E402
from assassin_live.audio.stereo import HOP, N_FFT, StereoRebuild  # noqa: E402
from assassin_live.processors.base import StreamProcessor  # noqa: E402

SR = 48000
LAT = N_FFT - HOP

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _side(x: np.ndarray) -> float:
    return float(np.sum(np.square(x[:, 0] - x[:, 1])))


def _db(a: float, b: float) -> float:
    return 10.0 * np.log10((a + 1e-20) / (b + 1e-20))


def _band_energy(x: np.ndarray, lo: float, hi: float) -> float:
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    s = np.fft.rfft(x)
    return float(np.sum(np.abs(s[(f >= lo) & (f < hi)]) ** 2))


def wide_stereo(n: int = SR, seed: int = 0) -> np.ndarray:
    """Center voice + hard-panned instruments — a deliberately wide image.

    Wider than anything in the real corpus (median S/M −8.2 dB, see ROADMAP
    §3.1), which is the point: the corpus can show the fix works, but not
    that it survives extreme panning.

    The panned content deliberately straddles 800 Hz, the cutoff the fake
    model below suppresses at. Everything wide living in the killed band
    would make "the image survived" and "the model suppressed nothing"
    indistinguishable — the rebuild is supposed to keep width only where the
    model kept signal, so the test needs wide content on both sides of that
    line to tell the two apart.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    # A slow envelope and audible noise, because the lag estimator that now
    # gates the rebuild works by cross-correlation and stationary tones give
    # it a periodic, ambiguous peak. Real audio is never stationary; a test
    # signal that is would only prove the estimator works on nothing.
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 0.9 * t)
    center = 0.4 * np.sin(2 * np.pi * 220 * t)
    kept_l = 0.3 * np.sin(2 * np.pi * 300 * t)     # panned, below the cutoff
    kept_r = 0.3 * np.sin(2 * np.pi * 500 * t)
    cut_l = 0.3 * np.sin(2 * np.pi * 1400 * t)     # panned, above it
    cut_r = 0.3 * np.sin(2 * np.pi * 3100 * t)
    # Aperiodic content BELOW the fake model's 800 Hz cutoff. Without it the
    # only thing surviving into the wet path is steady tones, and a lag of
    # 2400 samples is ~11 periods of 220 Hz — so lag 0 and the true lag
    # correlate almost equally and the estimator picks the wrong one. Real
    # music has broadband low-frequency content and does not have this
    # problem (measured: confidence 0.81 on a real corpus clip), but a test
    # signal made only of tones would quietly test the wrong thing.
    lf = rng.standard_normal(n)
    k = 80                                     # crude low-pass, ~300 Hz
    lf = np.convolve(lf, np.ones(k) / k, mode="same")
    lf = 0.25 * lf / (np.abs(lf).max() + 1e-9)
    noise = 0.05 * rng.standard_normal(n)
    return np.stack([(center + kept_l + cut_l) * env + lf + noise,
                     (center + kept_r + cut_r) * env + lf + noise],
                    axis=1).astype(np.float32)


def stream(dry: np.ndarray, wet: np.ndarray, chunk: int = 960) -> np.ndarray:
    """Push through in blocks, the way AudioEngine._work() does."""
    reb = StereoRebuild()
    out = [reb.process(dry[i:i + chunk], wet[i:i + chunk])
           for i in range(0, len(dry), chunk)]
    out = [o for o in out if len(o)]
    return np.concatenate(out) if out else np.zeros((0, 2), dtype=np.float32)


def test_todays_mono_path_collapses_the_image():
    """The behaviour being replaced, for reference: duplicating the mono wet
    signal to both channels annihilates the side channel."""
    print("\nthe defect this replaces")
    dry = wide_stereo()
    wet = dry.mean(axis=1)
    old = np.stack([wet, wet], axis=1)
    width = _db(_side(old), _side(dry))
    check("mono duplication destroys side energy (B1's −161 dB)",
          width < -100.0, f"{width:.1f} dB retained")


def test_unity_mask_returns_the_original_image():
    """wet == dry_mono means the chain did nothing; the output must be the
    dry stereo pair back, not a dual-mono copy of it."""
    print("\nunity mask (chain did nothing)")
    dry = wide_stereo()
    out = stream(dry, dry.mean(axis=1))
    n = len(out) - LAT
    ref, got = dry[:n], out[LAT:LAT + n]
    width = _db(_side(got), _side(ref))
    check("side channel preserved", width > -1.0, f"{width:.1f} dB retained")
    err = float(np.max(np.abs(got - ref)))
    check("waveform reconstructed", err < 5e-3, f"max abs err {err:.2e}")


def test_silence_stays_silent():
    """A chain that removed everything must not get width re-injected."""
    print("\nfully-suppressing chain")
    dry = wide_stereo()
    out = stream(dry, np.zeros(len(dry), dtype=np.float32))
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    check("no output resurrected from the dry pair", peak < 1e-4,
          f"peak {peak:.2e}")


def test_suppressed_band_stays_suppressed_in_both_channels():
    """The reason for the mask formulation rather than side re-injection: a
    band the model killed stays killed even though it was hard-panned — i.e.
    it lives in the side channel, which `L = wet + k·S` would hand straight
    back (see audio/stereo.py's docstring)."""
    print("\nselectivity: killed band vs kept band")
    dry = wide_stereo()
    mono = dry.mean(axis=1)
    spec = np.fft.rfft(mono)
    freqs = np.fft.rfftfreq(len(mono), 1.0 / SR)
    spec[freqs > 800.0] = 0.0          # a "model" that deletes the panned parts
    wet = np.fft.irfft(spec, n=len(mono)).astype(np.float32)

    out = stream(dry, wet)
    n = len(out) - LAT
    got = out[LAT:LAT + n]
    for ch, side in ((0, "L"), (1, "R")):
        killed = _db(_band_energy(got[:, ch], 1000, 4000),
                     _band_energy(dry[:n, ch], 1000, 4000))
        kept = _db(_band_energy(got[:, ch], 150, 400),
                   _band_energy(dry[:n, ch], 150, 400))
        check(f"{side}: suppressed band does not leak back", killed < -30.0,
              f"{killed:.1f} dB")
        check(f"{side}: kept band undamaged", kept > -3.0, f"{kept:.1f} dB")


def test_streaming_consistency():
    """The worker hands over whatever the model produced, which is never a
    round number of hops — same guarantee test_processors_offline.py makes
    of the processors themselves."""
    print("\nstreaming consistency")
    dry = wide_stereo(n=int(SR * PUMP_S))
    wet = dry.mean(axis=1)
    even, odd = stream(dry, wet, chunk=960), stream(dry, wet, chunk=137)
    n = min(len(even), len(odd))
    err = float(np.max(np.abs(even[:n] - odd[:n]))) if n else 1.0
    check("chunk size does not change the samples", err < 1e-5,
          f"max abs err {err:.2e} over {n} frames")

    reb = StereoRebuild()
    first = reb.process(dry, wet)
    reb.reset()
    check("reset() clears all state",
          np.array_equal(first, reb.process(dry, wet)))


class _Gate(StreamProcessor):
    """Mono stand-in for a real enhancer: passes low frequencies, kills the
    rest. 48 kHz so no resampler is involved — this is testing the engine's
    plumbing, not soxr."""

    name, sample_rate, latency_samples = "gate", 48000, 0

    def reset(self) -> None:
        pass

    def feed(self, x: np.ndarray) -> np.ndarray:
        spec = np.fft.rfft(x)
        spec[np.fft.rfftfreq(len(x), 1.0 / SR) > 800.0] = 0.0
        return np.fft.irfft(spec, n=len(x)).astype(np.float32)


class _LaggedGate(_Gate):
    """Same gate, but its output trails its input — like every real model.

    THE point of this class: the shipped misalignment survived testing
    because the only fake processor had zero lag, so pairing by FIFO
    position was trivially correct. A processor that cannot exhibit the bug
    cannot catch it.
    """

    name = "lagged_gate"
    LAG = 2400          # 50 ms at 48 kHz — what dpdfnet_hr actually measures

    def __init__(self):
        self._tail = np.zeros(self.LAG, dtype=np.float32)

    def reset(self) -> None:
        self._tail = np.zeros(self.LAG, dtype=np.float32)

    def feed(self, x: np.ndarray) -> np.ndarray:
        y = _Gate.feed(self, x)
        buf = np.concatenate([self._tail, y])
        self._tail = buf[len(y):]
        return buf[:len(y)]


class _StereoProc(_Gate):
    """A wants_stereo processor — the A1 seam. Nothing shipped sets this."""

    name, wants_stereo = "stereo_gate", True

    def feed(self, x: np.ndarray) -> np.ndarray:
        return np.stack([_Gate.feed(self, x[:, 0]),
                         _Gate.feed(self, x[:, 1])], axis=1)


WARMUP_BLOCKS = 6
PUMP_S = 3.0          # must exceed the lag estimator's window


def _pump(eng: AudioEngine, dry: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drive the engine's real callback + worker paths without hardware.

    Returns (output, matching dry reference) with the opening blocks dropped:
    the wet path legitimately lags (worker hand-off + model + rebuild), and
    the engine emits dry for those blocks by design. Left in, that dry
    passthrough carries the full stereo image and swamps exactly the
    measurement these tests make.
    """
    import threading

    # numpy imports np.fft lazily on first use; paying that inside the worker
    # thread stalls it long enough that every block falls back to dry and the
    # test measures nothing. (Seconds, not milliseconds, when the checkout
    # lives on a network mount.)
    np.fft.irfft(np.fft.rfft(np.zeros(8, dtype=np.float32)), n=8)

    eng._running = True
    worker = threading.Thread(target=eng._work, daemon=True)
    worker.start()
    out = []
    try:
        for i in range(0, len(dry) - BLOCK + 1, BLOCK):
            block = np.ascontiguousarray(dry[i:i + BLOCK])
            buf = np.zeros_like(block)
            eng._callback(block, buf, BLOCK, None, None)
            out.append(buf.copy())
            # let the worker keep up; a real stream gets 20 ms of wall clock
            # per block and this one is not trying to be realtime.
            time.sleep(0.005)
    finally:
        eng._running = False
        worker.join(timeout=2)
    got = np.concatenate(out)[WARMUP_BLOCKS * BLOCK:]
    return got, dry[WARMUP_BLOCKS * BLOCK:WARMUP_BLOCKS * BLOCK + len(got)]


def test_engine_emits_a_stereo_wet_signal():
    """End to end through AudioEngine: the shipped mono processor still
    produces a stereo image when the rebuild is on, and dual-mono when it is
    off (the pre-B3 behaviour, kept as the default until swept)."""
    print("\nengine wet path")
    dry = wide_stereo(n=int(SR * PUMP_S))

    eng = AudioEngine(_Gate())
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0          # skip the 20 ms ramp; not what is under test
    off, ref = _pump(eng, dry)

    eng2 = AudioEngine(_Gate())
    eng2.set_intensity(1.0)
    eng2._wet_gain = 1.0
    eng2.set_stereo(True)
    on, ref_on = _pump(eng2, dry)

    # Measure over the final third only. The rebuild is deliberately
    # bypassed until the lag measurement completes (~1.5 s), so averaging
    # side energy across the whole pump reports roughly half of it and says
    # more about the warm-up than about the rebuild.
    def tail(a):
        return a[2 * len(a) // 3:]

    w_off, w_on = (_db(_side(tail(off)), _side(tail(ref))),
                   _db(_side(tail(on)), _side(tail(ref_on))))
    check("engine default is still the measured-mono behaviour",
          w_off < -40.0, f"{w_off:.1f} dB side retained")
    # Not 0 dB, and should not be: half this signal's width lives in the
    # band the model kills, and the rebuild is meant to drop that with it.
    check("set_stereo(True) restores the surviving image", w_on > -6.0,
          f"{w_on:.1f} dB side retained")
    check("stereo rebuild does not add gain",
          float(np.max(np.abs(on))) <= float(np.max(np.abs(ref_on))) * 1.05,
          f"peak {float(np.max(np.abs(on))):.3f} vs dry {float(np.max(np.abs(ref_on))):.3f}")


def test_wants_stereo_processor_bypasses_the_rebuild():
    """The A1 seam: a stereo-native processor is handed the pair directly and
    its output is used as-is."""
    print("\nwants_stereo seam")
    dry = wide_stereo(n=int(SR * PUMP_S))
    proc = _StereoProc()
    eng = AudioEngine(proc)
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0
    eng.set_stereo(True)         # must be ignored — nothing to rebuild
    out, ref = _pump(eng, dry)

    width = _db(_side(out), _side(ref))
    supp = _db(_band_energy(out[:, 0], 1000, 4000),
               _band_energy(ref[:, 0], 1000, 4000))
    check("stereo processor sees the pair, not a downmix", width > -6.0,
          f"{width:.1f} dB side retained")
    check("its own suppression still applies", supp < -20.0, f"{supp:.1f} dB")


def test_measured_latency_tracks_the_extra_framing():
    """AudioEngine.latency_ms (ROADMAP C4/C7) is measured from the lockstep
    FIFO rather than summed from nominal parts. The rebuild's framing delay
    is a known, exact quantity, so it makes a good ruler: turning it on must
    move the reported latency by that much and nothing else changes."""
    print("\nmeasured latency")
    dry = wide_stereo(n=int(SR * PUMP_S))

    eng = AudioEngine(_Gate())
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0
    _pump(eng, dry)
    off_ms = eng.latency_ms

    eng2 = AudioEngine(_Gate())
    eng2.set_intensity(1.0)
    eng2._wet_gain = 1.0
    eng2.set_stereo(True)
    _pump(eng2, dry)
    on_ms = eng2.latency_ms

    expect = 1000.0 * LAT / SR
    block_ms = 1000.0 * BLOCK / SR
    # NOT near zero, and it should not be: a block goes to the worker and
    # its result is only collected by a later callback, so one block of
    # hand-off is the floor this architecture can reach even with a
    # zero-latency model. Worth asserting rather than tolerating — it is
    # most of the gap between the models' nominal latency_ms and the ~50 ms
    # bench_quality measures end to end.
    check("floor is one block of queue hand-off, not zero",
          block_ms * 0.7 <= off_ms <= block_ms * 1.4,
          f"{off_ms:.1f} ms vs one block = {block_ms:.1f} ms")
    check("the stereo rebuild's framing delay shows up in the number",
          abs((on_ms - off_ms) - expect) < 3.0,
          f"{on_ms:.1f} - {off_ms:.1f} = {on_ms - off_ms:.1f} ms, expected ~{expect:.1f}")


def test_a_quiet_intro_does_not_use_up_the_measurement():
    """The estimator's failure mode, tested at the unit it lives in.

    Silence cannot correlate. Counting it as a failed attempt meant a
    session that started on a quiet intro could exhaust every window before
    the first loud bar arrived, and the engine would then "assume aligned"
    — the exact misalignment the module exists to remove, arrived at by
    giving up rather than by measuring. Silent pairs are now dropped, so
    only material that had an answer in it consumes an attempt.
    """
    print("\nquiet intro")
    est = LagEstimator(SR)
    quiet = np.zeros(BLOCK, dtype=np.float32)
    for _ in range(int(SR * 20 / BLOCK)):          # 20 s of silence
        est.push(quiet, quiet)
    check("silence consumed no attempts", est.windows_taken == 0,
          f"{est.windows_taken} taken, {est.silent_frames} frames dropped")
    check("and the estimator has not given up", not est.exhausted)

    dry = wide_stereo(n=int(SR * PUMP_S)).mean(axis=1)
    wet = np.concatenate([np.zeros(_LaggedGate.LAG, dtype=np.float32),
                          dry])[:len(dry)]
    ready = False
    for i in range(0, len(dry) - BLOCK + 1, BLOCK):
        if est.push(dry[i:i + BLOCK], wet[i:i + BLOCK]):
            ready = True
            break
    check("real audio still fills a window", ready)
    lag = est.measure(*est.take_window()) if ready else None
    check("and it measures the lag the silence would have hidden",
          lag is not None and abs(lag - _LaggedGate.LAG) < 0.003 * SR,
          f"{lag} samples, true {_LaggedGate.LAG}")


def _emitted_lag(out: np.ndarray, ref: np.ndarray) -> int:
    """How far the engine's output trails the input that produced it."""
    lag, _ = _xcorr_lag(out[:, 0].astype(np.float64),
                        ref[:, 0].astype(np.float64), int(0.25 * SR))
    return lag


def _pump_settled(eng: AudioEngine, dry: np.ndarray, tries: int = 4) -> tuple:
    """Pump until the lag correction has landed, then pump once more and
    return that.

    How long the measurement takes is not deterministic: it needs a window
    of audio, the correlation runs on its own thread, and a window whose
    peak is not convincing is discarded for another. Measuring across the
    moment it lands averages the corrected and uncorrected states together
    and reports neither — so let it land first, then measure a run that is
    entirely on one side of it. Engine state survives between pumps; only
    start()/stop() clear it.
    """
    for _ in range(tries):
        if eng._mix_pad:
            break
        _pump(eng, dry)
    return _pump(eng, dry)


def test_the_mix_is_aligned_with_a_processor_that_lags():
    """The dry the listener hears must be as late as the wet it blends with.

    B6 corrected the worker's dry copy, which feeds the stereo mask, and
    left the callback's alone — so below 100 % mix the two sides of the
    blend were the processor's lag apart, which is an echo rather than a
    mix. Measured here the way the defect is heard: run the engine fully
    dry and fully wet over the same input, and ask how late each stream
    comes out. Before the fix these differ by the processor's lag.
    """
    print("\ndry/wet alignment below 100 % mix")
    dry = wide_stereo(n=int(SR * PUMP_S))

    eng_dry = AudioEngine(_LaggedGate())
    eng_dry.set_intensity(0.0)
    eng_dry._wet_gain = 0.0
    out_dry, ref = _pump_settled(eng_dry, dry)

    eng_wet = AudioEngine(_LaggedGate())
    eng_wet.set_intensity(1.0)
    eng_wet._wet_gain = 1.0
    out_wet, ref_wet = _pump_settled(eng_wet, dry)

    # Each engine is pumped by its own worker thread, and how many blocks
    # pile up before that thread produces its first output varies run to
    # run (the B7 race, in miniature). That queue backlog delays everything
    # the engine emits, dry and wet alike — so comparing raw emitted lags
    # across two runs would be comparing two different backlogs. Subtract
    # each run's own, which is exactly what latency_ms reports now that the
    # pad is excluded from it, and what is left is the quantity under test:
    # how far behind its input each stream is *by design*.
    def own_delay(eng, out, ref):
        return _emitted_lag(out, ref) - eng.latency_ms / 1000.0 * SR

    dry_delay, wet_delay = (own_delay(eng_dry, out_dry, ref),
                            own_delay(eng_wet, out_wet, ref_wet))
    skew_ms = abs(dry_delay - wet_delay) / SR * 1000.0
    # Within the estimator's own tolerance, not exactly: the pad is
    # whatever the correlation measured, and it measures to ~0.1 ms.
    check("the lag correction reached the mix path",
          abs(eng_dry._mix_pad - _LaggedGate.LAG) < 0.003 * SR,
          f"padded {eng_dry._mix_pad} samples, processor lag {_LaggedGate.LAG}")
    check("dry and wet are the same distance behind the input", skew_ms < 5.0,
          f"dry {dry_delay:.0f} samples, wet {wet_delay:.0f}, "
          f"skew {skew_ms:.1f} ms")
    # The pad is bookkeeping in a FIFO, not delay anyone hears: it changes
    # which dry sample is paired with which wet one, not when either leaves.
    # Counting it would inflate C7's number by the model's lag while nothing
    # audible had changed (ROADMAP C4/C7).
    raw_ms = 1000.0 * eng_dry._lag_ema / SR
    pad_ms = 1000.0 * eng_dry._mix_pad / SR
    check("and the reported latency excludes the pad",
          abs(raw_ms - pad_ms - eng_dry.latency_ms) < 1.0,
          f"FIFO {raw_ms:.1f} ms - pad {pad_ms:.1f} ms, "
          f"reported {eng_dry.latency_ms:.1f} ms")


def test_an_unmeasurable_lag_is_reported_rather_than_assumed():
    """Uncorrelated input used to end in `_mask_lag = 0` — a correction of
    the wrong size, applied silently. It now ends in `unmeasured`: no
    correction anywhere, the rebuild left bypassed, and a state the UI can
    show."""
    print("\nunmeasurable lag")
    eng = AudioEngine(_Gate())
    eng._lag_est = LagEstimator(SR, max_windows=0)   # nothing will be measured
    eng.set_stereo(True)
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0
    out, ref = _pump(eng, wide_stereo(n=int(SR * PUMP_S)))
    check("state is unmeasured, not a lag of zero",
          eng.lag_state == "unmeasured" and eng._mask_lag is None,
          f"state {eng.lag_state!r}, mask lag {eng._mask_lag!r}")
    check("nothing was padded", eng._mix_pad == 0)
    width = _db(_side(out[2 * len(out) // 3:]), _side(ref[2 * len(ref) // 3:]))
    check("the rebuild stayed bypassed rather than running misaligned",
          width < -40.0, f"{width:.1f} dB side retained")


def test_the_input_is_metered_too():
    """W3: one meter cannot tell a silent session from an idle one."""
    print("\ninput metering")
    eng = AudioEngine(_Gate())
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0
    _pump(eng, wide_stereo(n=int(SR * PUMP_S)))
    lin, lout = eng.recent_input_levels(), eng.recent_levels()
    check("input levels recorded", len(lin) > 0 and max(lin) > 0.01,
          f"{len(lin)} samples, peak {max(lin or [0]):.3f}")
    check("output levels still recorded", len(lout) > 0 and max(lout) > 0.01,
          f"{len(lout)} samples, peak {max(lout or [0]):.3f}")


def test_rebuild_aligns_to_a_processor_that_lags():
    """The regression test for the doubling bug. A model whose output trails
    its input by 50 ms must still get an image rebuilt from the RIGHT audio;
    if the mask is applied 50 ms away from the samples it describes, the
    reconstruction is smeared and the width it restores is wrong."""
    print("\nprocessor with real lag (the shipped bug)")
    dry = wide_stereo(n=int(SR * PUMP_S))

    eng = AudioEngine(_LaggedGate())
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0
    eng.set_stereo(True)
    on, ref = _pump(eng, dry)

    check("the lag was measured, not assumed", eng._mask_lag is not None,
          f"measured {eng._mask_lag} samples "
          f"({(eng._mask_lag or 0) / SR * 1000:.1f} ms), true {_LaggedGate.LAG}")
    if eng._mask_lag is not None:
        err = abs(eng._mask_lag - _LaggedGate.LAG) / SR * 1000
        check("and measured close to the truth", err < 3.0, f"{err:.1f} ms off")
    w = _db(_side(on[2*len(on)//3:]), _side(ref[2*len(ref)//3:]))
    check("image restored despite the lag", w > -8.0, f"{w:.1f} dB side retained")


def main() -> int:
    print("stereo rebuild tests (no models, no audio hardware required)")
    test_todays_mono_path_collapses_the_image()
    test_unity_mask_returns_the_original_image()
    test_silence_stays_silent()
    test_suppressed_band_stays_suppressed_in_both_channels()
    test_streaming_consistency()
    test_engine_emits_a_stereo_wet_signal()
    test_wants_stereo_processor_bypasses_the_rebuild()
    test_measured_latency_tracks_the_extra_framing()
    test_rebuild_aligns_to_a_processor_that_lags()
    test_a_quiet_intro_does_not_use_up_the_measurement()
    test_the_mix_is_aligned_with_a_processor_that_lags()
    test_an_unmeasurable_lag_is_reported_rather_than_assumed()
    test_the_input_is_metered_too()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
