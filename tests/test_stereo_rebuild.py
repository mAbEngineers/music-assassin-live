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
    center = 0.4 * np.sin(2 * np.pi * 220 * t)
    kept_l = 0.3 * np.sin(2 * np.pi * 300 * t)     # panned, below the cutoff
    kept_r = 0.3 * np.sin(2 * np.pi * 500 * t)
    cut_l = 0.3 * np.sin(2 * np.pi * 1400 * t)     # panned, above it
    cut_r = 0.3 * np.sin(2 * np.pi * 3100 * t)
    noise = 0.01 * rng.standard_normal(n)
    return np.stack([center + kept_l + cut_l + noise,
                     center + kept_r + cut_r + noise],
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
    dry = wide_stereo(n=SR // 2)
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


class _StereoProc(_Gate):
    """A wants_stereo processor — the A1 seam. Nothing shipped sets this."""

    name, wants_stereo = "stereo_gate", True

    def feed(self, x: np.ndarray) -> np.ndarray:
        return np.stack([_Gate.feed(self, x[:, 0]),
                         _Gate.feed(self, x[:, 1])], axis=1)


WARMUP_BLOCKS = 6


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
    dry = wide_stereo(n=SR // 2)

    eng = AudioEngine(_Gate())
    eng.set_intensity(1.0)
    eng._wet_gain = 1.0          # skip the 20 ms ramp; not what is under test
    off, ref = _pump(eng, dry)

    eng2 = AudioEngine(_Gate())
    eng2.set_intensity(1.0)
    eng2._wet_gain = 1.0
    eng2.set_stereo(True)
    on, ref_on = _pump(eng2, dry)

    w_off, w_on = _db(_side(off), _side(ref)), _db(_side(on), _side(ref_on))
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
    dry = wide_stereo(n=SR // 2)
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
    dry = wide_stereo(n=SR)

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
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
