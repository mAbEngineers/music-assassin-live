#!/usr/bin/env python3
"""Spleeter wrapper checks (ROADMAP A1) — no audio devices needed.

The registry-name checks are pure and always run. The rest need the two
spleeter ONNX files AND sherpa-onnx, which is deliberately not an app
dependency, so they report NOTHING TESTED rather than passing vacuously —
same rule as test_processors_offline.py, and for the same reason (C1 and C8
both shipped checks that could not see anything and did not say so).

What these pin down, all of it measured rather than assumed:

  * output length is quantised to 1024 samples when fed at 44100. If a
    sherpa-onnx upgrade changes that, the wrapper's arithmetic is wrong and
    the emitted window silently shifts — this catches it.

  * the emitted audio carries NO edge impulse. Raw calls put one at both
    ends: on real corpus audio, 11043 in the first 8 samples against an
    interior peak of 1.28, plus a smaller one at the tail. That is what the
    discarded context is for, and it is the difference between a usable
    stream and a click at every chunk boundary.

  * output lag stays inside latency_samples, so the engine's lockstep FIFO
    accounting holds.
"""

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import models_dir  # noqa: E402
from assassin_live import processors  # noqa: E402

SR = 44100


def synth(seconds: float = 6.0) -> np.ndarray:
    """(n, 2) music + voice. Deliberately free of the hard gating
    test_processors_offline.synth uses: a gated sine is a click train, and
    the clicks would show up as exactly the impulses this file asserts are
    absent — the probe would be testing itself."""
    t = np.arange(int(SR * seconds)) / SR
    voice = 0.3 * np.sin(2 * np.pi * (180 + 40 * np.sin(2 * np.pi * 2.5 * t)) * t)
    voice *= 0.5 * (1 + np.sin(2 * np.pi * 3.0 * t))     # smooth syllables
    music = 0.2 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t))
    noise = 0.01 * np.random.default_rng(0).standard_normal(len(t))
    left = (voice + music + noise).astype(np.float32)
    right = (voice + 0.8 * music + noise).astype(np.float32)   # some width
    return np.stack([left, right], axis=1)


def test_registry_names() -> None:
    print("\nregistry name parsing")
    from assassin_live.processors import _spleeter_chunk_ms as parse
    assert parse("spleeter") == 1000, parse("spleeter")
    assert parse("spleeter_250ms") == 250
    assert parse("spleeter_4000ms") == 4000
    for bad in ("spleeter_ms", "spleeter_0ms", "spleeter_-5ms", "spleeter_1000",
                "spleeterms", "dpdfnet_hr", "spleeter_abcms"):
        assert parse(bad) is None, f"{bad!r} should not parse as a spleeter"
    # every advertised chunk must actually parse back to itself
    for ms in processors.SPLEETER_CHUNKS_MS:
        assert parse(f"spleeter_{ms}ms") == ms
    print(f"  OK  default 1000 ms, variants {processors.SPLEETER_CHUNKS_MS}, "
          f"7 malformed names rejected")


def test_context_floor() -> None:
    """context below the output quantum cannot be discarded safely."""
    print("\ncontext floor")
    from assassin_live.processors.spleeter import SpleeterProcessor, QUANTUM
    try:
        SpleeterProcessor("x.onnx", "y.onnx", context_samples=QUANTUM - 1)
    except ValueError as e:
        assert "context_samples" in str(e)
        print(f"  OK  rejected context {QUANTUM - 1} < quantum {QUANTUM}")
    except ImportError:
        print("  SKIP no sherpa-onnx (the check runs before the model loads,"
              " but the import does not)")
    else:
        raise AssertionError("context below the quantum was accepted")


def test_quantum(proc) -> None:
    print("\noutput length quantum (fed at 44100, no internal resample)")
    from assassin_live.processors.spleeter import QUANTUM
    for n in (44100, 44032, 43008, 22050, 12288):
        out = proc._separate(synth(2.0)[:n])
        assert len(out) == (n // QUANTUM) * QUANTUM, \
            f"{n} in -> {len(out)} out, expected {(n // QUANTUM) * QUANTUM}"
    print(f"  OK  out == (in // {QUANTUM}) * {QUANTUM} over 5 buffer sizes")


def test_no_edge_impulses(proc) -> None:
    print("\nedge impulses removed from emitted audio")
    x = synth(6.0)
    proc.reset()
    out = [y for y in (proc.feed(x[i:i + 1024])
                       for i in range(0, len(x) - 1023, 1024)) if len(y)]
    assert out, "nothing emitted — chunk longer than the test signal?"
    y = np.concatenate(out)
    assert np.isfinite(y).all(), "non-finite output"

    in_peak = float(np.abs(x).max())
    out_peak = float(np.abs(y).max())
    # Raw calls peak ~16000x the input at the edges; clean ones sit below it.
    # 10x is a wide margin that still catches the regression by orders of
    # magnitude rather than by a hair.
    assert out_peak < 10 * in_peak, \
        f"edge impulse leaked: peak {out_peak:.1f} vs input peak {in_peak:.3f}"
    print(f"  OK  emitted peak {out_peak:.3f} vs input peak {in_peak:.3f} "
          f"({out_peak / in_peak:.2f}x, limit 10x)")


def test_lag_bounded(proc) -> None:
    print("\nlag stays inside latency_samples")
    x = synth(8.0)
    proc.reset()
    fed = emitted = 0
    for i in range(0, len(x) - 1023, 1024):
        fed += 1024
        emitted += len(proc.feed(x[i:i + 1024]))
    lag = fed - emitted
    assert 0 <= lag <= proc.latency_samples, \
        f"lag {lag} outside [0, {proc.latency_samples}]"
    print(f"  OK  fed {fed}, emitted {emitted}, lag {lag} "
          f"<= latency_samples {proc.latency_samples} "
          f"({proc.latency_ms:.0f} ms)")


def test_streaming_consistency(proc) -> None:
    """Output must not depend on how the caller happens to slice its pushes —
    the wrapper buffers to a fixed chunk, so this is exact, not approximate."""
    print("\nstreaming consistency (push size must not change the output)")
    x = synth(6.0)
    proc.reset()
    one = proc.feed(x)
    proc.reset()
    rng = np.random.default_rng(1)
    pos, parts = 0, []
    while pos < len(x):
        n = int(rng.integers(100, 5000))
        parts.append(proc.feed(x[pos:pos + n]))
        pos += n
    many = np.concatenate([p for p in parts if len(p)])
    n = min(len(one), len(many))
    assert n > 0, "nothing emitted"
    assert np.array_equal(one[:n], many[:n]), \
        f"push size changed the output (max diff " \
        f"{np.abs(one[:n] - many[:n]).max():.2e})"
    print(f"  OK  {len(one)} vs {len(many)} samples, first {n} bit-identical")


def main() -> int:
    test_registry_names()
    test_context_floor()

    mdir = models_dir()
    name = "spleeter_1000ms"
    if not processors.is_available(name, mdir):
        print(f"\nNOTHING TESTED — {name} unavailable from {mdir}")
        print("  Needs spleeter_vocals.int8.onnx + spleeter_accompaniment"
              ".int8.onnx and sherpa-onnx (not an app dependency — use the")
        print("  research venv). Set ALLOW_NO_MODELS=1 to accept that.")
        return 0 if os.environ.get("ALLOW_NO_MODELS") == "1" else 1

    proc = processors.create(name, mdir)
    print(f"\n{proc.name}: sr={proc.sample_rate} wants_stereo={proc.wants_stereo} "
          f"chunk={proc.chunk} context={proc.context} "
          f"latency={proc.latency_ms:.0f} ms")
    test_quantum(proc)
    test_no_edge_impulses(proc)
    test_lag_bounded(proc)
    test_streaming_consistency(proc)
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
