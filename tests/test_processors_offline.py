#!/usr/bin/env python3
"""Offline processor checks — no audio devices needed.

For each available processor:
  1. run 5 s of synthetic audio through feed(), check output is sane
  2. streaming consistency: chunked push == one-shot push (same samples out)
  3. benchmark ms per 20 ms block -> real-time headroom
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import models_dir  # noqa: E402
from assassin_live import processors  # noqa: E402


def synth(sr: int, seconds: float = 5.0) -> np.ndarray:
    """Speech-ish chirps + music-ish steady tones + noise."""
    t = np.arange(int(sr * seconds)) / sr
    voice = 0.3 * np.sin(2 * np.pi * (180 + 40 * np.sin(2 * np.pi * 2.5 * t)) * t)
    voice *= (np.sin(2 * np.pi * 3.0 * t) > 0)          # syllable-like gating
    music = 0.2 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 330 * t)
                   + np.sin(2 * np.pi * 440 * t))
    noise = 0.02 * np.random.default_rng(0).standard_normal(len(t))
    return (voice + music + noise).astype(np.float32)


def run_one(name: str, mdir: Path) -> dict:
    proc = processors.create(name, mdir)
    sr = proc.sample_rate
    x = synth(sr)

    # 1) one-shot
    proc.reset()
    y_full = proc.feed(x)
    assert np.isfinite(y_full).all(), f"{name}: non-finite output"
    lag = len(x) - len(y_full)
    assert 0 <= lag <= proc.latency_samples + sr, \
        f"{name}: unexpected lag {lag} samples"

    # 2) chunked (random push sizes) must match one-shot
    proc.reset()
    rng = np.random.default_rng(1)
    pos, chunks = 0, []
    while pos < len(x):
        n = int(rng.integers(100, 2000))
        chunks.append(proc.feed(x[pos:pos + n]))
        pos += n
    y_chunked = np.concatenate([c for c in chunks if len(c)])
    n = min(len(y_full), len(y_chunked))
    assert np.allclose(y_full[:n], y_chunked[:n], atol=1e-4), \
        f"{name}: streaming inconsistency (max diff " \
        f"{np.abs(y_full[:n]-y_chunked[:n]).max():.2e})"

    # 3) benchmark: 20 ms blocks, steady state
    block = int(sr * 0.020)
    proc.reset()
    proc.feed(x[:sr])  # warm up
    times = []
    for i in range(100):
        b = x[(i * block) % (len(x) - block):][:block]
        t0 = time.perf_counter()
        proc.feed(b)
        times.append((time.perf_counter() - t0) * 1000)
    ms = float(np.median(times))
    return {"name": name, "sr": sr, "ms_per_20ms_block": round(ms, 2),
            "rtf": round(ms / 20.0, 3),
            "latency_ms": round(proc.latency_ms, 1)}


def main():
    mdir = models_dir()
    print(f"models dir: {mdir}\n")
    results = []
    for name in processors.available(mdir):
        if name == "passthrough":
            continue
        try:
            r = run_one(name, mdir)
            results.append(r)
            print(f"  PASS {r['name']:<16} {r['ms_per_20ms_block']:>6.2f} ms/block"
                  f"  rtf={r['rtf']:<6} latency={r['latency_ms']} ms")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {name}: {e}")
            results.append({"name": name, "error": str(e)})
    ok = all("error" not in r for r in results)
    print("\nall passed" if ok else "\nFAILURES above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
