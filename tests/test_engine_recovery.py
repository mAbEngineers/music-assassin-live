#!/usr/bin/env python3
"""Crash-survival tests for the streaming engine. No audio hardware needed.

Guards the failure mode that has bitten this project twice in live use:
PortAudio silently kills the whole stream when the audio callback raises, and
sounddevice surfaces NO error when it does — the stream just goes inactive,
the GUI keeps showing "ON", and audio stops until the user manually toggles
off and on. The engine's defences against that are pure logic and cheap to
test directly, so there is no reason for them to stay unverified just because
the full recover-on-real-hardware cycle is awkward to reproduce.

What this covers:
  * _callback() never propagates an exception, and falls back to dry
    passthrough for the offending block instead of killing the stream.
  * the failure is counted (stats.callback_errors) rather than swallowed
    silently, since an invisible fallback is its own bug.
  * stream_ok correctly reports a dead/absent stream — it is what both the
    GUI tick and the headless loop poll to decide to attempt recovery.
  * the soft limiter's guarantees, which is what makes the mix slider's
    300% wet boost safe to expose at all.

Run: .venv/bin/python tests/test_engine_recovery.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import (  # noqa: E402
    BLOCK, LIMITER_CEILING, LIMITER_THRESHOLD, AudioEngine, _soft_limit)
from assassin_live.processors.passthrough import Passthrough  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def test_callback_survives_exception():
    """The core guarantee: a raising callback body must not escape."""
    print("\ncallback crash survival")
    eng = AudioEngine(Passthrough())
    rng = np.random.default_rng(0)
    indata = (0.1 * rng.standard_normal((BLOCK, 2))).astype(np.float32)
    outdata = np.zeros_like(indata)

    def boom(*_a, **_k):
        raise RuntimeError("simulated inference blowup")

    eng._callback_body = boom

    raised = None
    try:
        eng._callback(indata, outdata, BLOCK, None, None)
    except Exception as e:  # noqa: BLE001 — that this cannot happen IS the test
        raised = e

    check("exception does not propagate out of _callback", raised is None,
          f"got {raised!r}" if raised else "")
    check("output falls back to dry passthrough",
          np.allclose(outdata, indata),
          f"max diff {float(np.abs(outdata - indata).max()):.2e}")
    check("failure is counted, not silent", eng.stats.callback_errors == 1,
          f"callback_errors={eng.stats.callback_errors}")

    # Repeated failures keep the stream alive and keep counting: a transient
    # fault must not become permanent silence.
    for _ in range(4):
        eng._callback(indata, outdata, BLOCK, None, None)
    check("repeated failures stay contained", eng.stats.callback_errors == 5,
          f"callback_errors={eng.stats.callback_errors}")


def test_stream_ok_reports_dead_stream():
    """stream_ok is what the GUI/headless supervisors poll to trigger
    recovery — if it lies, the auto-recovery never fires."""
    print("\nstream_ok reporting")
    eng = AudioEngine(Passthrough())
    check("no stream -> not ok", eng.stream_ok is False)

    class FakeStream:
        def __init__(self, active):
            self.active = active

    eng._stream = FakeStream(active=False)
    check("inactive stream -> not ok", eng.stream_ok is False)
    eng._stream = FakeStream(active=True)
    check("active stream -> ok", eng.stream_ok is True)


def test_soft_limiter():
    """The limiter is what makes boosting wet gain to 3x safe."""
    print("\nsoft limiter")
    quiet = np.linspace(-LIMITER_THRESHOLD, LIMITER_THRESHOLD, 2048).astype(np.float32)
    check("below threshold is bit-exact passthrough",
          np.array_equal(_soft_limit(quiet), quiet))

    loud = np.linspace(-8.0, 8.0, 20001).astype(np.float32)
    out = _soft_limit(loud)
    peak = float(np.abs(out).max())
    check("never exceeds ceiling even at 8x overdrive",
          peak <= LIMITER_CEILING + 1e-6, f"peak {peak:.6f}")
    check("sign is preserved", np.all(np.sign(out) == np.sign(loud)))
    check("monotonic (no fold-back distortion)",
          np.all(np.diff(out) >= -1e-7))

    # A limiter that mangles ordinary audio would be worse than the clipping
    # it prevents. The property that matters is SELECTIVITY: it must touch
    # only what is actually over threshold and leave everything else bit-
    # exact. (Testing "a normal-level signal is untouched" wholesale is wrong
    # — a Gaussian at 0.3 RMS still puts ~0.8% of its samples past 0.8, and
    # those SHOULD be limited.)
    rng = np.random.default_rng(1)
    normal = (0.3 * rng.standard_normal(4096)).astype(np.float32)
    limited = _soft_limit(normal)
    under = np.abs(normal) <= LIMITER_THRESHOLD
    check("samples under threshold are bit-exact",
          np.array_equal(limited[under], normal[under]),
          f"{int(under.sum())}/{under.size} samples under threshold")
    over = ~under
    check("samples over threshold are attenuated toward the ceiling",
          bool(np.all(np.abs(limited[over]) < np.abs(normal[over]))) if over.any() else True,
          f"{int(over.sum())} samples over threshold")


def main() -> int:
    print("engine crash-survival tests (no audio hardware required)")
    test_callback_survives_exception()
    test_stream_ok_reports_dead_stream()
    test_soft_limiter()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
