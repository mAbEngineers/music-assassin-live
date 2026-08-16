#!/usr/bin/env python3
"""Pick one excerpt per source track for tests/bench_quality.py --build-refs.

Exists as a pre-processing step because `--duration` on build_refs truncates
AFTER demucs separates the whole file — expensive (a full track vs. 30s: 3.6GB
peak RSS on a 4-core i3, and the mdx_extra ensemble OOM-killed the machine
outright on the first corpus build attempt here) and it doesn't pick a good
window on its own.

WINDOW SELECTION — biased toward the first vocal entrance, not the loudest.
An earlier version of this script (and its first real corpus run) picked the
single 20-30s window with the highest centre-channel energy in the 200Hz-4kHz
vocal band. That reliably skips instrumental intros, but it also reliably
lands on the CHORUS, because choruses are both louder and, in a lot of anime
J-rock/pop, sung in a noticeably brighter register than the verse. Measured
directly on this project's own corpus: one male vocalist's auto-picked (loud)
window read a median F0 of 336 Hz -- squarely "female" range on this script's
own classifier -- while his verse, 20s earlier in the same song, read 201 Hz,
ordinary adult male tenor. Sampling only the loudest moment of every track
would silently bias EVERY category toward the easiest, most climactic content
in the mix, and away from the quieter passages that turned out to matter for
this project's own dpdfnet_hr level-sensitivity investigation (see
docs/ROADMAP.md sec 2.1/2.2). So instead: pick the FIRST window whose
vocal-band energy clears a threshold relative to the track's own energy
distribution, i.e. the first real vocal entrance, which in verse/chorus song
structure is normally the verse.

ACAPELLA GUARD -- a previous corpus build here fed 91 filenames from a folder
that turned out to be entirely vocal-only extractions to demucs, and it took
two full separations before the mistake was caught (build_refs's own manifest
looked perfectly healthy; the tell was the raw vocals/music stem RATIO, only
visible after separation). This script checks the cheap, pre-separation proxy
instead -- real mixes carry 30-150Hz (bass/kick) energy an acapella does not
-- and refuses a file outright rather than silently writing a bad excerpt.
"""
import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
WIN = 30.0
SKIP_HEAD = 10.0
ACAPELLA_LOWEND_DB = -25.0  # below this, treat as vocal-only, refuse


def load(path, secs=None):
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if secs:
        cmd += ["-t", str(secs)]
    cmd += ["-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    return a.reshape(-1, 2) if a.size >= 2 else None


def band_energy(mid, lo, hi, n=8192):
    w = np.hanning(n).astype(np.float32)
    acc, cnt = None, 0
    for i in range(0, len(mid) - n, n // 2):
        s = np.abs(np.fft.rfft(mid[i:i + n] * w)) ** 2
        acc = s if acc is None else acc + s
        cnt += 1
    if acc is None:
        return None
    acc /= cnt
    f = np.fft.rfftfreq(n, 1 / SR)
    band = (f >= lo) & (f < hi)
    tot = acc[(f >= 30) & (f < 16000)].sum() + 1e-20
    return 10 * np.log10(acc[band].sum() / tot + 1e-20)


def vocal_band_envelope(x, n=2048, hop=1024):
    mid = (x[:, 0] + x[:, 1]) / 2
    w = np.hanning(n).astype(np.float32)
    lo, hi = int(200 / SR * n), int(4000 / SR * n)
    env = np.array([
        float(np.sum(np.abs(np.fft.rfft(mid[i:i + n] * w))[lo:hi] ** 2))
        for i in range(0, len(mid) - n, hop)
    ])
    return env, hop


def pick_first_entrance(x, win=WIN, skip_head=SKIP_HEAD, thresh_frac=0.5):
    """First window whose vocal-band energy clears `thresh_frac` of the
    track's own peak windowed energy -- the first real vocal entrance rather
    than the single loudest (usually chorus) moment. Falls back to the loudest
    window if nothing clears the threshold (e.g. a track with one big swell)."""
    env, hop = vocal_band_envelope(x)
    if len(env) == 0:
        return 0.0
    wf = int(win * SR / hop)
    sf = int(skip_head * SR / hop)
    if len(env) <= wf + sf:
        return 0.0
    c = np.concatenate([[0.0], np.cumsum(env)])
    scores = c[wf:] - c[:-wf]
    scores = scores.copy()
    scores[:sf] = -np.inf
    peak = float(np.max(scores))
    thresh = peak * thresh_frac
    above = np.where(scores >= thresh)[0]
    start = int(above[0]) if len(above) else int(np.argmax(scores))
    return start * hop / SR


def write_wav(path, x):
    w = wave.open(str(path), "w")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()


def process(src: Path, outdir: Path, force: bool = False):
    x = load(src)
    if x is None or len(x) < SR * 5:
        print(f"  SKIP {src.name}: couldn't decode or too short")
        return False
    mid = (x[:, 0] + x[:, 1]) / 2
    low = band_energy(mid, 30, 150)
    if low is not None and low < ACAPELLA_LOWEND_DB and not force:
        print(f"  REFUSE {src.name}: low-end {low:.1f} dB < {ACAPELLA_LOWEND_DB} dB "
              "-- looks vocal-only, not a full mix (--force to override)")
        return False
    start = pick_first_entrance(x)
    seg = x[int(start * SR):int(start * SR) + int(WIN * SR)]
    if len(seg) < SR * 5:
        print(f"  SKIP {src.name}: excerpt too short after windowing")
        return False
    out = outdir / (src.stem + ".wav")
    write_wav(out, seg)
    tag = f"low={low:.1f}dB" if low is not None else "low=?"
    print(f"  {src.stem[:44]:<46} excerpt @ {start:6.1f}s  {tag}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--force", action="store_true",
                    help="write an excerpt even if the acapella guard fires")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ok = 0
    for src in args.sources:
        if process(src, args.out, args.force):
            ok += 1
    print(f"\n{ok}/{len(args.sources)} excerpts written to {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
