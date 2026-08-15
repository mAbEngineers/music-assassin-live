#!/usr/bin/env python3
"""Quantitative de-musicing quality harness.

Answers "did that change make it better?" with numbers instead of a
re-listen, across arbitrary combinations of model / pre-filter / post-filter
/ mix parameters.

WHY IT'S BUILT THIS WAY
-----------------------
The obvious design — run the offline separator on a clip, run the live app on
the same clip, compare the two outputs — measures *agreement with the offline
separator*, artifacts included, and cannot tell "music leaked through" apart
from "vocals got damaged". Those two failure modes need opposite fixes, so a
metric that blends them is worse than useless for tuning.

So instead the offline separator is used to MANUFACTURE GROUND TRUTH:

  1. `--build-refs` runs demucs (mdx_extra by default) with --two-stems on
     your real source audio, giving a `vocals` stem and a `music` stem.
  2. Those stems are remixed at a known ratio:  mix = vocals + alpha*music.
     Because we built the mixture, the ground truth is exact — we know
     precisely what "perfect" output looks like (`vocals`) and precisely what
     should be removed (`alpha*music`).
  3. The same separator is then run ON THE REMIX, and its output is stored as
     the CEILING — the best this class of model achieves on this material with
     no realtime constraint at all.

Every metric is then reported as a three-way comparison:

    input mix (do nothing)  ->  live pipeline  ->  offline ceiling

which is the actual question: not "is the live output good" in the abstract,
but "how much of the offline quality does the realtime path retain, and where
exactly does it lose it".

WHAT RUNS AS "LIVE"
-------------------
The live processing chain is driven directly at block granularity, mirroring
`AudioEngine._work()` and `._callback_body()` exactly (same 20 ms blocks, same
MidSideFilter/BandlimitFilter instances, same soxr resamplers, same soft
limiter). No audio hardware, no PipeWire, fully deterministic and repeatable —
which is what makes parameter sweeps meaningful. Hardware-path validation is a
different question and stays in `test_live_e2e.py`.

NO SINGLE SCORE
---------------
The metrics are deliberately not collapsed into one number, because they
correspond to *different failure modes* that trade against each other:

    did music get removed?      music_supp_db (vocal-gap only), SIR
    did the vocals survive?     vocal_ret_db, per-band damage, dSI-SDR
    did it add artifacts?       SAR, musical_noise (spectral kurtosis)
    did it glitch?              clicks
    did it flatten the image?   stereo_width_db
    can it run realtime?        latency_ms, RTF
    what does it sound like?    --dump-audio

Optimising any one of these alone has an obvious degenerate solution — a
config can post superb suppression by destroying the vocal, or win on every
quality axis at 400 ms of latency. So live configs are marked `*` when they
sit on the PARETO FRONTIER (nothing tested beats them on every objective at
once) and the choice between frontier configs is left to a human who knows
which constraint currently binds.

"% of offline ceiling" is reported for the same reason it is NOT a target:
the ceiling is not perceptually ideal either, so 95% of it can still sound
clearly worse.

USAGE
-----
    # once per corpus (slow — this is demucs on CPU). Categories let
    # adversarial sets be built up incrementally and scored separately.
    python tests/bench_quality.py --build-refs ~/clips/*.wav \
        --demucs-python /home/yoake/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python
    python tests/bench_quality.py --build-refs ~/wide_mixes/*.wav \
        --category stereo_torture
    python tests/bench_quality.py --build-refs ~/final/*.wav --holdout

    # fast, repeatable, sweepable
    python tests/bench_quality.py --sweep model=dpdfnet_hr,gtcrn,dtln \
                                  --sweep midside=off,on
    python tests/bench_quality.py --sweep midside_exp=1,2,4,6 --sort music_supp_db
    python tests/bench_quality.py --only-category stereo_torture
    python tests/bench_quality.py --dump-audio /tmp/ab   # for the by-ear check

    # regression gate — exits non-zero if anything moved the wrong way
    python tests/bench_quality.py --gate
    python tests/bench_quality.py --gate --gate-file limits.json

Reports land in ~/.local/state/music-assassin/bench/quality/ and each run
diffs against the previous one, same convention as test_live_e2e.py.

CORPUS DESIGN MATTERS AS MUCH AS THE METRICS
--------------------------------------------
A corpus of one kind of material silently turns this into "how well does the
model separate THIS mixture at THIS ratio". Vary aggressively: vocal/music
ratios (--ratios), genre, dense vs. sparse arrangement, dry vs. reverberant
vocals, sustained pads vs. transient percussion, speech vs. singing, and —
because mid/side processing is a shipped option — centred/wide/hard-panned
and decorrelated stereo cases. Note that a MONO source clip makes every
mid/side result meaningless by construction (no side channel exists to
exploit), so stereo material is required to evaluate that filter at all.

Reserve some clips with --holdout and never tune against them. After enough
rounds the harness becomes something to overfit to, and a set that has only
ever been used for final validation is the only defence.

A NOTE ON TRUSTING THESE NUMBERS
--------------------------------
Every quality finding in this project so far has needed a by-ear confirmation,
and two of them contradicted a plausible-looking metric (synthetic tones read
as "noise" and scored -47 dB where real music scores -0.4 dB; aggressive
mid/side exponents introduce musical noise that an energy ratio cannot see).
`--dump-audio` exists for exactly that reason. The numbers rank candidates and
show direction; they do not settle quality.
"""

import argparse
import itertools
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.bandlimit import BandlimitFilter  # noqa: E402
from assassin_live.audio.engine import BLOCK, SAMPLE_RATE, _soft_limit  # noqa: E402
from assassin_live.audio.midside import MidSideFilter  # noqa: E402
from assassin_live.paths import models_dir, state_dir  # noqa: E402
from assassin_live import processors  # noqa: E402

SR = SAMPLE_RATE
REPORT_DIR = state_dir() / "bench" / "quality"
DEFAULT_CORPUS = state_dir() / "bench" / "corpus"

# Analysis framing for all frame-wise metrics (energy gates, roughness,
# spectral distance). 21 ms / 50% overlap — same order as the engine's own
# 20 ms block, so a per-frame number is roughly "per engine block".
FRAME = 1024
HOP = 512

# Vocal-activity gate: frames more than this far below the loudest vocal
# frame count as vocal-silent. In those frames the ONLY thing present is
# music, so output energy there is pure leakage — the cleanest possible
# music-suppression measurement, with no vocal energy to confound it.
GAP_FLOOR_DB = 40.0

# Taps in the least-squares projection filters used for the SDR/SIR/SAR
# decomposition. 256 @ 48 kHz = 5.3 ms of allowed linear distortion, which
# covers filter group delay and small misalignment without letting the
# projection "explain away" real damage.
PROJ_TAPS = 256

EPS = 1e-12

# Bands for per-band vocal-damage reporting. The 4-8k and 8-20k rows are the
# ones that expose 16 kHz-native models discarding everything above 8 kHz —
# the high-frequency content of high-pitched voices this project fights for.
BANDS = [("sub", 0, 200), ("low", 200, 1000), ("mid", 1000, 4000),
         ("high", 4000, 8000), ("air", 8000, 20000)]

# Objectives for Pareto-dominance marking, as metric -> "lower"/"higher" is
# better. Deliberately a VECTOR, not a weighted scalar: these trade against
# each other (suppression is trivially bought with artifacts or latency), and
# there is no defensible exchange rate between "1 dB more music removed" and
# "40 ms more delay". Marking the non-dominated set says "nothing here beats
# this config on every axis at once" and leaves the actual choice to a human
# who knows which constraint currently binds.
PARETO_OBJECTIVES = {
    "music_supp_db": "lower",      # more music removed
    "vocal_ret_db": "higher",      # closer to 0 = less vocal damage
    "sar_db": "higher",            # fewer artifacts
    "musical_noise": "lower",      # less musical noise
    "latency_ms": "lower",         # realtime product constraint
}

# Regression-gate metrics: direction plus how far a metric may move the wrong
# way before it counts as a regression. Tolerances are in each metric's own
# units and are set above run-to-run noise, not at zero — content and model
# nondeterminism move these slightly even with no code change.
GATE_METRICS = {
    "music_supp_db": ("lower", 1.5),
    "vocal_ret_db": ("higher", 1.0),
    "delta_si_sdr_db": ("higher", 1.0),
    "sar_db": ("higher", 1.5),
    "musical_noise": ("lower", 5.0),
    "rtf": ("lower", 0.15),
}


# ---------------------------------------------------------------- wav io ----

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """-> (n, ch) float32 in [-1, 1], native rate. Keeps channels: the
    mid/side pre-filter is meaningless on a mono downmix."""
    with wave.open(str(path)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"{path}: unsupported sample width {sw}")
    return a.reshape(-1, ch), sr


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    if x.ndim == 1:
        x = x[:, None]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def to_sr(x: np.ndarray, src_sr: int, dst_sr: int = SR) -> np.ndarray:
    if src_sr == dst_sr:
        return x.astype(np.float32)
    return np.stack([soxr.resample(x[:, c], src_sr, dst_sr)
                     for c in range(x.shape[1])], axis=1).astype(np.float32)


def as_stereo(x: np.ndarray) -> np.ndarray:
    return np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x[:, :2]


def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


# ------------------------------------------------------- corpus building ----

def build_refs(sources, corpus: Path, demucs_python: str, model: str,
               ratios, duration_s: float | None,
               category: str = "general", holdout: bool = False,
               rebuild: bool = False) -> dict:
    """Separate each source into vocals/music stems, remix at each ratio, and
    separate the remix again to establish the offline ceiling.

    `category` labels what kind of material this is (e.g. "stereo_torture",
    "transient_music", "sustained_pad", "vocal_only") so adversarial sets can
    be built incrementally and reported separately — a config that wins on
    average can still fail badly on one category, and an average hides that.

    `holdout` marks clips reserved for final validation and excluded from
    normal runs. After enough tuning rounds the harness itself becomes
    something to overfit to; a set never used while tuning is the only
    defence against that, and it is worth nothing if it silently leaks into
    the sweep you are optimizing against.
    """
    corpus.mkdir(parents=True, exist_ok=True)
    existing = {}
    manifest_path = corpus / "manifest.json"
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text())
        existing = {e["name"]: e for e in prior.get("items", [])}
    manifest = {"model": model, "ratios": list(ratios), "items": []}

    def _flush():
        # Merge rather than overwrite: building a corpus is slow and
        # incremental (a category at a time), so a second --build-refs run
        # must not silently discard everything the first one produced.
        manifest["items"] = list(existing.values())
        (corpus / "manifest.json").write_text(json.dumps(manifest, indent=2))

    for i, src in enumerate(sources, 1):
        src = Path(src).resolve()
        item = corpus / src.stem
        item.mkdir(parents=True, exist_ok=True)
        print(f"\n=== [{i}/{len(sources)}] {src.name}")

        if src.stem in existing and not rebuild:
            print("  already in manifest — skipping (pass --rebuild to redo)")
            continue

        stems = _demucs(src, item / "_sep", demucs_python, model)
        if stems is None:
            print("  SKIP — separation failed")
            continue
        voc, sr_v = read_wav(stems["vocals"])
        mus, sr_m = read_wav(stems["no_vocals"])
        voc, mus = to_sr(voc, sr_v), to_sr(mus, sr_m)
        n = min(len(voc), len(mus))
        if duration_s:
            n = min(n, int(duration_s * SR))
        voc, mus = as_stereo(voc[:n]), as_stereo(mus[:n])

        # Normalising the stems independently is what makes `alpha` mean
        # something comparable across clips: alpha is then the music-to-vocal
        # amplitude ratio, not an artifact of how the source was mastered.
        voc = _norm(voc)
        mus = _norm(mus)
        write_wav(item / "vocals.wav", voc)
        write_wav(item / "music.wav", mus)

        entry = {"name": src.stem, "source": str(src), "samples": int(n),
                 "category": category, "holdout": bool(holdout), "mixes": {}}
        for a in ratios:
            tag = f"a{int(round(a * 100)):03d}"
            mix = _headroom(voc + a * mus)
            write_wav(item / f"mix_{tag}.wav", mix)
            print(f"  ceiling pass: alpha={a}")
            ceil = _demucs(item / f"mix_{tag}.wav", item / f"_ceil_{tag}",
                           demucs_python, model)
            if ceil is None:
                continue
            c, sr_c = read_wav(ceil["vocals"])
            write_wav(item / f"ceiling_{tag}.wav", as_stereo(to_sr(c, sr_c))[:n])
            entry["mixes"][tag] = {"alpha": a}
        existing[entry["name"]] = entry

        # Checkpoint after every source, not once at the end. A full corpus is
        # hours of demucs on CPU; writing the manifest only after the last
        # source means any interruption -- a crashed editor, a closed laptop --
        # discards every completed clip along with the incomplete one. Learned
        # the hard way. Combined with the skip above, an interrupted build now
        # resumes by rerunning the same command.
        _flush()

    _flush()
    kept = len(manifest["items"])
    print(f"\ncorpus written: {corpus}  ({kept} items total)")
    return manifest


def _norm(x: np.ndarray, target_rms: float = 0.1) -> np.ndarray:
    r = float(np.sqrt(np.mean(np.square(x))))
    return x if r < EPS else (x * (target_rms / r)).astype(np.float32)


def _headroom(x: np.ndarray, ceiling: float = 0.95) -> np.ndarray:
    peak = float(np.abs(x).max())
    return x if peak <= ceiling else (x * (ceiling / peak)).astype(np.float32)


def _demucs(src: Path, out: Path, python: str, model: str) -> dict | None:
    """Shell out to the demucs CLI rather than importing the research repo's
    GUI module: the GUI's separate_with_demucs() returns the vocals stem only
    and downmixes to mono on the way in, which would destroy the very stereo
    information the mid/side pre-filter exists to exploit."""
    voc = out / model / src.stem / "vocals.wav"
    nov = out / model / src.stem / "no_vocals.wav"
    if voc.is_file() and nov.is_file():
        print(f"  cached: {voc.parent}")
        return {"vocals": voc, "no_vocals": nov}
    cmd = [python, "-m", "demucs", "-n", model, "--two-stems", "vocals",
           "-o", str(out), str(src)]
    print(f"  {' '.join(cmd[:6])} ... ({src.name})")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not voc.is_file():
        print(f"  demucs failed ({r.returncode}): {r.stderr.strip()[-400:]}")
        return None
    print(f"  separated in {time.time() - t0:.1f}s")
    return {"vocals": voc, "no_vocals": nov}


# --------------------------------------------------------- the live chain ----

def run_chain(cfg: dict, x: np.ndarray, mdir: Path) -> tuple[np.ndarray, dict]:
    """Push stereo audio through the live processing chain, block by block.

    Mirrors AudioEngine._work(): mid/side (or plain mono downmix) -> optional
    downsample -> processor.feed() -> optional upsample -> band-limit. The
    wet/dry mix and soft limiter from _callback_body() are applied afterwards
    by the caller, once latency alignment is known.
    """
    proc = processors.create(cfg["model"], mdir)
    proc.reset()
    setter = getattr(proc, "set_atten_limit", None)
    if setter:
        setter(cfg["atten_db"])

    if proc.sample_rate != SR:
        down = soxr.ResampleStream(SR, proc.sample_rate, 1, dtype="float32")
        up = soxr.ResampleStream(proc.sample_rate, SR, 1, dtype="float32")
    else:
        down = up = None

    ms = MidSideFilter(exponent=cfg["midside_exp"]) if cfg["midside"] else None
    bl = BandlimitFilter(SR) if cfg["bandlimit"] else None

    out, times = [], []
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        blk = x[i:i + BLOCK]
        t0 = time.perf_counter()
        m = ms.process(blk) if ms is not None else blk.mean(axis=1)
        if len(m):
            y = down.resample_chunk(m) if down is not None else m
            y = proc.feed(y)
            if up is not None and len(y):
                y = up.resample_chunk(y)
            if len(y) and bl is not None:
                y = bl.process(y)
            if len(y):
                out.append(y.astype(np.float32))
        times.append((time.perf_counter() - t0) * 1000.0)

    wet = np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
    t = np.array(times) if times else np.zeros(1)
    budget_ms = 1000.0 * BLOCK / SR
    perf = {"ms_per_block_mean": round(float(t.mean()), 2),
            "ms_per_block_p95": round(float(np.percentile(t, 95)), 2),
            "ms_per_block_max": round(float(t.max()), 2),
            # Jitter, not just mean: a model averaging 0.7x realtime but
            # occasionally spiking to 3x will underrun audibly, while a
            # steady 0.9x never will. The mean alone rates the first higher.
            "ms_per_block_jitter": round(float(t.std()), 2),
            "rtf": round(float(t.mean()) / budget_ms, 3),
            "rtf_p95": round(float(np.percentile(t, 95)) / budget_ms, 3),
            "budget_overruns": int((t > budget_ms).sum())}
    return wet, perf


def _mix_gains(mix_pct: float) -> tuple[float, float]:
    ramp = min(mix_pct, 100.0) / 100.0
    wet_g = 1.0 + max(0.0, mix_pct - 100.0) / 100.0
    return ramp, wet_g


def streaming_consistency(cfg: dict, x: np.ndarray, mdir: Path,
                          offsets=(37, 480)) -> dict:
    """How much does the output depend on WHERE frame boundaries fall?

    Feeds the same audio again with the input shifted by a few samples, which
    moves every analysis frame to a different point in the signal, then undoes
    the shift and compares.

    IMPORTANT — read this as COMPARATIVE, not pass/fail. Some sensitivity is
    inherent to any framed processing: shifting the input also shifts the
    model's internal STFT framing, so the same sample is analysed under a
    different window and legitimately gets a slightly different gain. A
    perfectly-implemented streaming pipeline does NOT score 0 here. What the
    number is good for is ranking: a model with more overlap and a smaller hop
    is intrinsically less alignment-sensitive, and a config that is far worse
    than its peers is worth investigating for genuinely dropped state
    (resampler or filter state reset at a block edge) on top of the inherent
    part. That failure mode exists ONLY in the realtime path and a
    conventional offline evaluation cannot see it at all — offline there is
    one big buffer and no boundaries to get wrong.

    Offsets are deliberately not multiples of BLOCK: 480 is half a block, 37
    is an odd nudge that lands mid-frame everywhere.

    Reported as dB of difference energy relative to the output itself, so more
    negative = less alignment-sensitive.
    """
    base, _ = run_chain(cfg, x, mdir)
    if not len(base):
        return {}
    worst = None
    for k in offsets:
        pad = np.zeros((k, x.shape[1]), dtype=np.float32)
        shifted, _ = run_chain(cfg, np.concatenate([pad, x]), mdir)
        if len(shifted) <= k:
            continue
        a, b = base, shifted[k:]
        n = min(len(a), len(b))
        if n < SR // 10:
            continue
        dev = _db(_energy(a[:n] - b[:n]), _energy(a[:n]))
        worst = dev if worst is None else max(worst, dev)
    return {} if worst is None else {"stream_dev_db": round(worst, 1)}


def apply_mix(dry: np.ndarray, wet: np.ndarray, mix_pct: float) -> np.ndarray:
    """Reproduce _callback_body()'s wet/dry blend and App._wet_boost(): past
    100% the crossfade is already fully wet, so extra travel becomes wet gain
    (up to 3x at 300%), with the soft limiter guarding the boost."""
    n = min(len(dry), len(wet))
    d, w = dry[:n], wet[:n]
    ramp, wet_g = _mix_gains(mix_pct)
    mixed = d * (1.0 - ramp) + w * wet_g * ramp
    return _soft_limit(mixed) if wet_g > 1.0 else mixed


def apply_mix_stereo(dry_st: np.ndarray, wet: np.ndarray,
                     mix_pct: float) -> np.ndarray:
    """The same blend, but preserving what actually reaches the speakers.

    The engine writes the MONO wet signal to both output channels while the
    dry path stays stereo, so the stereo image survives only in proportion to
    how much dry is mixed in — at 100% wet the output is dual-mono and the
    image collapses entirely. The separation metrics all run on the mono
    signal (correct — they compare against mono stems), which would hide that
    cost completely, so this reconstruction exists purely to measure it.
    """
    n = min(len(dry_st), len(wet))
    d, w = dry_st[:n], wet[:n, None]
    ramp, wet_g = _mix_gains(mix_pct)
    mixed = d * (1.0 - ramp) + np.repeat(w, 2, axis=1) * wet_g * ramp
    return _soft_limit(mixed) if wet_g > 1.0 else mixed


# -------------------------------------------------------------- alignment ----

CONF_THRESHOLD = 0.02  # normalized-correlation peak margin below which the
# raw-waveform estimate is untrustworthy (near-silent or fully decorrelated
# output) and the envelope fallback takes over instead.


def estimate_lag(y: np.ndarray, x: np.ndarray, max_ms: float = 400.0) -> int:
    """Samples y lags x by, at single-sample accuracy.

    Correlates raw waveforms, not envelopes — reversing an earlier version
    of this function, which correlated envelopes on the theory that a model
    reshapes the spectrum enough to break waveform correlation. Measured
    against real output that theory was wrong for the shipped enhancers:
    they're magnitude-mask models (a real-valued gain applied per STFT bin,
    phase untouched), so broadband correlation stays sharp, and it resolves
    to the sample, whereas envelope correlation is blurry by construction
    (a HOP-wide smoothing window) and on continuous material its peak is
    often broad and ambiguous rather than a clean maximum — measured on a
    real 15s action clip, raw correlation gave a peak 30% sharper than its
    nearest neighbor at single-sample resolution, while the envelope's
    'peak' was indistinguishable from points 40+ ms away. Sample-accurate
    alignment matters here because SI-SDR/SDR/SIR/SAR are sample-domain
    metrics: a residual HOP-scale (~10 ms) misalignment reads as pure noise
    to them and corrupts every one of those numbers while looking like a
    real quality problem.

    Falls back to the coarser envelope estimate when the raw peak isn't
    confidently resolved (near-silent or fully decorrelated output, e.g. a
    future non-causal separator that scrambles phase) — a case a pure
    waveform correlation would otherwise answer with a plausible-looking
    but meaningless exact sample offset. (The hardware tier of
    test_live_e2e.py aligns on a first-sample-over-threshold onset instead,
    which is what makes its 48 kHz-native numbers unreliable — this avoids
    that failure mode by using the whole clip, not one onset.)
    """
    lag, confidence = _xcorr_lag(y, x, max_ms)
    if confidence >= CONF_THRESHOLD:
        return lag
    return _envelope_lag(y, x, max_ms)


def _xcorr_lag(y: np.ndarray, x: np.ndarray, max_ms: float) -> tuple[int, float]:
    n = min(len(y), len(x))
    if n < 8:
        return 0, 0.0
    ys = y[:n].astype(np.float64) - y[:n].astype(np.float64).mean()
    xs = x[:n].astype(np.float64) - x[:n].astype(np.float64).mean()
    ny, nx = np.linalg.norm(ys), np.linalg.norm(xs)
    if ny < EPS or nx < EPS:
        return 0, 0.0
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(ys, nfft) * np.conj(np.fft.rfft(xs, nfft)), nfft)
    max_samp = max(1, int(max_ms / 1000.0 * SR))
    cand = np.concatenate([cc[:max_samp + 1], cc[-max_samp:]]) / (ny * nx)
    lags = np.concatenate([np.arange(max_samp + 1), np.arange(-max_samp, 0)])
    i = int(np.argmax(cand))
    confidence = float(cand[i] - np.median(np.abs(cand)))
    return int(lags[i]), confidence


def _envelope_lag(y: np.ndarray, x: np.ndarray, max_ms: float) -> int:
    ey, ex = _envelope(y), _envelope(x)
    n = min(len(ey), len(ex))
    if n < 8:
        return 0
    ey, ex = ey[:n] - ey[:n].mean(), ex[:n] - ex[:n].mean()
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(ey, nfft) * np.conj(np.fft.rfft(ex, nfft)), nfft)
    max_frames = max(1, int(max_ms / 1000.0 * SR / HOP))
    cand = np.concatenate([cc[:max_frames + 1], cc[-max_frames:]])
    lags = np.concatenate([np.arange(max_frames + 1), np.arange(-max_frames, 0)])
    return int(lags[int(np.argmax(cand))] * HOP)


def _envelope(x: np.ndarray) -> np.ndarray:
    n = (len(x) - FRAME) // HOP + 1
    if n < 1:
        return np.zeros(0, dtype=np.float32)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return np.sqrt(np.mean(np.square(x[idx]), axis=1) + EPS)


def align(y: np.ndarray, x: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Trim y and x to a common timeline given y's lag.

    Returns (y_aligned, x_aligned, x_offset): x_offset is how many samples
    were cut from the front of x. Callers need it to slice any OTHER array
    that shares x's original timeline (reference stems) — the common
    timeline starts at original index 0 only when lag >= 0; when the
    estimator finds y leading x, it's x's front that gets trimmed instead,
    and every reference array has to be offset the same way or every metric
    downstream is silently comparing the wrong samples to each other while
    still producing plausible-looking numbers.
    """
    x_off = 0
    if lag > 0:
        y = y[lag:]
    elif lag < 0:
        x_off = -lag
        x = x[x_off:]
    n = min(len(y), len(x))
    return y[:n], x[:n], x_off


# ---------------------------------------------------------------- metrics ----

def _db(a: float, b: float) -> float:
    return 10.0 * np.log10((a + EPS) / (b + EPS))


def _energy(x: np.ndarray) -> float:
    return float(np.sum(np.square(x)))


def si_sdr(est: np.ndarray, ref: np.ndarray) -> float:
    """Scale-invariant SDR. Immune to any overall gain difference, so it is
    not fooled by a pipeline that merely turns everything down."""
    n = min(len(est), len(ref))
    est, ref = est[:n] - est[:n].mean(), ref[:n] - ref[:n].mean()
    a = float(np.dot(est, ref)) / (_energy(ref) + EPS)
    target = a * ref
    return _db(_energy(target), _energy(est - target))


def _proj(y: np.ndarray, refs: list[np.ndarray], taps: int) -> np.ndarray:
    """Least-squares projection of y onto the span of `taps`-delayed copies of
    every ref (the bss_eval construction, solved through correlations rather
    than an explicit design matrix, which would be gigabytes here)."""
    k, n = len(refs), len(y)
    nfft = 1 << int(np.ceil(np.log2(n + taps + 1)))
    R = [np.fft.rfft(r, nfft) for r in refs]
    Y = np.fft.rfft(y, nfft)
    p = np.arange(taps)
    lags = (p[:, None] - p[None, :]) % nfft

    G = np.empty((k * taps, k * taps))
    for i in range(k):
        for j in range(k):
            cc = np.fft.irfft(np.conj(R[i]) * R[j], nfft)
            G[i * taps:(i + 1) * taps, j * taps:(j + 1) * taps] = cc[lags]
    d = np.concatenate([np.fft.irfft(np.conj(R[i]) * Y, nfft)[:taps]
                        for i in range(k)])

    reg = 1e-8 * (np.trace(G) / (k * taps) + EPS)
    try:
        c = np.linalg.solve(G + reg * np.eye(k * taps), d)
    except np.linalg.LinAlgError:
        c = np.linalg.lstsq(G, d, rcond=None)[0]

    out = np.zeros(n, dtype=np.float64)
    for i in range(k):
        out += np.convolve(refs[i], c[i * taps:(i + 1) * taps])[:n]
    return out


def bss_metrics(y: np.ndarray, target: np.ndarray, interf: np.ndarray,
                taps: int = PROJ_TAPS) -> dict:
    """SDR / SIR / SAR.

    The reason to pay for this on top of the simpler energy ratios: it splits
    what's wrong with the output into interference (music that survived) and
    artifacts (content in neither source — musical noise, warbling, the
    'watery' complaint). Those have different fixes, and a single suppression
    number hides the trade: a config can post excellent suppression while
    destroying SAR.
    """
    n = min(len(y), len(target), len(interf))
    y, target, interf = (np.asarray(v[:n], dtype=np.float64)
                         for v in (y, target, interf))
    s_target = _proj(y, [target], taps)
    s_all = _proj(y, [target, interf], taps)
    e_interf = s_all - s_target
    e_artif = y - s_all
    return {
        "sdr_db": round(_db(_energy(s_target), _energy(e_interf + e_artif)), 2),
        "sir_db": round(_db(_energy(s_target), _energy(e_interf)), 2),
        "sar_db": round(_db(_energy(s_target + e_interf), _energy(e_artif)), 2),
    }


def gap_metrics(y: np.ndarray, x: np.ndarray, voc: np.ndarray) -> dict:
    """Music suppression measured only in vocal-silent frames.

    In those frames the reference contains nothing but music, so any output
    energy is leakage — no vocal energy to confound the ratio. This is the
    single most trustworthy 'how much music was removed' number available, and
    it is the automated form of the manual gap-energy comparison the research
    repo used (music-only gap 4.48-7.81 s vs vocal segment 0.42-2.85 s).
    """
    n = min(len(y), len(x), len(voc))
    ey, ex, ev = _envelope(y[:n]), _envelope(x[:n]), _envelope(voc[:n])
    f = min(len(ey), len(ex), len(ev))
    if f < 4:
        return {}
    ey, ex, ev = ey[:f], ex[:f], ev[:f]
    gap = ev < (ev.max() * 10 ** (-GAP_FLOOR_DB / 20.0))
    active = ~gap
    out = {}
    if gap.sum() >= 3:
        out["music_supp_db"] = round(_db(float(np.sum(ey[gap] ** 2)),
                                         float(np.sum(ex[gap] ** 2))), 2)
        # Pumping/warbling: how unsteady the residual is across gap frames.
        # A clean constant-attenuation result is smooth; musical noise is not.
        g = ey[gap] / (ex[gap] + EPS)
        per_frame = 20 * np.log10(g + EPS)
        out["gap_frames"] = int(gap.sum())

        # Roughness only over frames whose OUTPUT still has real energy.
        # Taking the spread of a dB ratio across a near-silent residual
        # measures the logarithm's behaviour near zero, not audible pumping —
        # it made an ideal-mask oracle (which correctly drives gaps to near
        # silence) score rougher than every model being tested.
        lively = ey[gap] > (ey.max() * 10 ** (-60.0 / 20.0))
        if lively.sum() >= 3:
            out["gap_roughness_db"] = round(float(np.std(per_frame[lively])), 2)

        # Time-localized leakage. An average hides bursts, and for realtime
        # audio a config that is consistently mediocre is usually preferable
        # to one that is excellent except for occasional seconds of music
        # blasting through — the average cannot tell those apart, these can.
        out["music_supp_p90_db"] = round(float(np.percentile(per_frame, 90)), 2)
        out["music_supp_worst_db"] = round(float(np.percentile(per_frame, 99)), 2)
        # Longest contiguous stretch of clearly-worse-than-typical leakage,
        # in ms — "how long does the worst moment last", which is what makes
        # a burst audible as an event rather than as texture.
        thresh = float(np.median(per_frame)) + 6.0
        out["worst_burst_ms"] = round(
            _longest_run(per_frame > thresh) * HOP / SR * 1000.0, 1)
    if active.sum() >= 3:
        out["active_db"] = round(_db(float(np.sum(ey[active] ** 2)),
                                     float(np.sum(ex[active] ** 2))), 2)
    return out


def _longest_run(flags: np.ndarray) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def band_damage(y: np.ndarray, ref: np.ndarray) -> dict:
    """Per-band energy deviation of y from ref, in dB. Run on the vocals-only
    probe, where any deviation from 0 dB is damage to speech the pipeline was
    supposed to keep. The `air` and `high` rows are where 16 kHz-native models
    show their 8 kHz ceiling."""
    n = min(len(y), len(ref))
    if n < FRAME:
        return {}
    fy = np.abs(np.fft.rfft(y[:n])) ** 2
    fr = np.abs(np.fft.rfft(ref[:n])) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    out = {}
    for label, lo, hi in BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        if sel.sum() and fr[sel].sum() > EPS:
            out[f"band_{label}"] = round(_db(float(fy[sel].sum()),
                                             float(fr[sel].sum())), 1)
    return out


def musical_noise(y: np.ndarray, x: np.ndarray) -> float:
    """Change in spectral kurtosis, in dB-free units.

    Musical noise is isolated, randomly-placed time-frequency peaks, which
    raises the kurtosis of the spectrum well above that of the input. A large
    positive number here means the config bought its suppression with
    artifacts — the thing an energy ratio cannot see and the ear immediately
    can.
    """
    def kurt(sig):
        n = (len(sig) - FRAME) // HOP + 1
        if n < 2:
            return float("nan")
        idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
        spec = np.abs(np.fft.rfft(sig[idx] * np.hanning(FRAME), axis=1)) ** 2
        m = spec.mean(axis=1, keepdims=True)
        s = spec.std(axis=1, keepdims=True) + EPS
        return float(np.nanmean(np.mean(((spec - m) / s) ** 4, axis=1)))

    ky, kx = kurt(y), kurt(x)
    return round(ky - kx, 1) if np.isfinite(ky) and np.isfinite(kx) else float("nan")


def click_count(y: np.ndarray, sigma: float = 12.0) -> int:
    """Sample-level discontinuities — block-boundary clicks from bad overlap
    handling or resampler state loss."""
    if len(y) < 3:
        return 0
    d = np.abs(np.diff(y))
    thr = sigma * (np.median(d) + EPS)
    return int((d > thr).sum())


def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    win = np.sqrt(np.hanning(n_fft + 1)[:-1])
    n = max(0, (len(x) - n_fft) // hop + 1)
    if n < 1:
        return np.zeros((0, n_fft // 2 + 1), dtype=complex)
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    return np.fft.rfft(x[idx] * win, axis=1)


def _istft(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    win = np.sqrt(np.hanning(n_fft + 1)[:-1])
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * win
    out = np.zeros(length + n_fft, dtype=np.float64)
    norm = np.zeros(length + n_fft, dtype=np.float64)
    for i in range(len(frames)):
        out[i * hop:i * hop + n_fft] += frames[i]
        norm[i * hop:i * hop + n_fft] += win ** 2
    return (out[:length] / np.maximum(norm[:length], EPS)).astype(np.float32)


def oracle_irm(voc: np.ndarray, mus: np.ndarray, mix: np.ndarray,
               n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """Ideal-ratio-mask separation of the mixture, using the known stems.

    This is the SEPARATOR-INDEPENDENT upper bound: the best any method that
    works by applying a real-valued gain per STFT bin can possibly do on this
    mixture, given perfect knowledge of both sources. It is not achievable by
    any real system — it cheats by construction — and that is the point.

    It turns the report into a four-level ladder:

        input  ->  live  ->  offline ceiling  ->  oracle

    which separates three things the three-level version confounds:
      * oracle far below 0 dB damage    -> the MIXTURE is intrinsically hard
                                           (sources overlap in time-frequency;
                                           no masking method can win here)
      * offline well short of oracle    -> the OFFLINE MODEL is the limit
      * live well short of offline      -> the REALTIME IMPLEMENTATION is the
                                           limit, which is the only one of the
                                           three this repo can fix directly

    Without it, a live config scoring poorly is ambiguous between "our
    realtime path is bad" and "this clip is unseparable", and those lead to
    completely different next actions.
    """
    n = min(len(voc), len(mus), len(mix))
    V, M = _stft(voc[:n], n_fft, hop), _stft(mus[:n], n_fft, hop)
    X = _stft(mix[:n], n_fft, hop)
    f = min(len(V), len(M), len(X))
    if f < 1:
        return np.zeros(0, dtype=np.float32)
    pv, pm = np.abs(V[:f]) ** 2, np.abs(M[:f]) ** 2
    mask = pv / (pv + pm + EPS)
    # Trim to the span the frames actually cover: samples past the last frame
    # get no overlap-add contribution at all and would come back as digital
    # silence, which reads to every downstream metric as catastrophic error
    # in the final milliseconds rather than as the missing data it is.
    covered = (f - 1) * hop + n_fft
    return _istft(X[:f] * mask, n_fft, hop, n)[:min(n, covered)]


def stereo_width_db(y: np.ndarray, x: np.ndarray) -> float:
    """Side-channel energy retention. The engine currently emits the mono wet
    signal to both channels, so a fully-wet config collapses the stereo image
    entirely — this quantifies that cost."""
    n = min(len(y), len(x))
    sy = _energy(y[:n, 0] - y[:n, 1]) if y.ndim == 2 else 0.0
    sx = _energy(x[:n, 0] - x[:n, 1]) if x.ndim == 2 else 0.0
    return round(_db(sy, sx), 1)


# Failure taxonomy: metric -> (comparison, threshold, tag, what it means.)
# Turns a row of numbers into named defects, so a sweep can report "config B
# gains 1.4 dB of suppression but its regressions are all HF vocal damage"
# instead of a score that says only "worse". Thresholds are deliberately
# loose — these flag "look here", they are not pass/fail.
TAXONOMY = [
    ("music_supp_db", "gt", -6.0, "music-leak", "music passes through"),
    ("music_supp_worst_db", "gt", -3.0, "leak-burst", "bursts of music leak"),
    ("worst_burst_ms", "gt", 400.0, "long-burst", "a long contiguous leak"),
    ("vocal_ret_db", "lt", -4.0, "vocal-loss", "vocal level pulled down"),
    ("band_air", "lt", -12.0, "hf-loss", "8-20 kHz destroyed"),
    ("band_high", "lt", -12.0, "hf-loss-4k", "4-8 kHz destroyed"),
    ("sar_db", "lt", 3.0, "artifacts", "output has content in neither source"),
    ("musical_noise", "gt", 15.0, "musical-noise", "isolated T-F peaks added"),
    ("gap_roughness_db", "gt", 9.0, "pumping", "attenuation is unsteady"),
    ("clicks", "gt", 200000.0, "clicks", "sample-level discontinuities"),
    ("stereo_width_db", "lt", -20.0, "stereo-collapse", "image flattened"),
    # -12 dB, not -25: some frame-alignment sensitivity is inherent to framed
    # processing (see streaming_consistency), so this fires only when a config
    # is well beyond what its framing alone explains.
    ("stream_dev_db", "gt", -12.0, "boundary-sensitive",
     "unusually sensitive to frame alignment (compare peers, not zero)"),
    ("latency_ms", "gt", 120.0, "latency", "beyond lip-sync tolerance"),
    ("rtf", "gt", 0.8, "cpu-risk", "little realtime headroom"),
]


def classify_failures(row: dict) -> list[str]:
    tags = []
    for key, op, thr, tag, _ in TAXONOMY:
        v = row.get(key)
        if not isinstance(v, (int, float)) or not np.isfinite(v):
            continue
        if (v > thr if op == "gt" else v < thr) and tag not in tags:
            tags.append(tag)
    return tags


# ------------------------------------------------------------- evaluation ----

def evaluate(cfg: dict, item: Path, tag: str, mdir: Path,
             dump: Path | None) -> dict:
    """Score one config against one corpus item at one mix ratio."""
    voc = as_stereo(read_wav(item / "vocals.wav")[0])
    mus = as_stereo(read_wav(item / "music.wav")[0])
    mix = as_stereo(read_wav(item / f"mix_{tag}.wav")[0])
    n = min(len(voc), len(mus), len(mix))
    voc, mus, mix = voc[:n], mus[:n], mix[:n]

    wet, perf = run_chain(cfg, mix, mdir)
    if not len(wet):
        return {"error": "chain produced no output"}
    lag = estimate_lag(wet, mono(mix))
    wet_a, dry_a, off = align(wet, mono(mix), lag)
    y = apply_mix(dry_a, wet_a, cfg["mix_pct"])

    m = min(len(y), n - off)
    y = y[:m]
    v_ref = mono(voc)[off:off + m]
    m_ref = mono(mus)[off:off + m] * _alpha(tag)
    x_ref = mono(mix)[off:off + m]

    res = {"latency_ms": round(lag / SR * 1000.0, 1), **perf}
    res.update(gap_metrics(y, x_ref, v_ref))
    res.update(bss_metrics(y, v_ref, m_ref))
    res["si_sdr_db"] = round(si_sdr(y, v_ref), 2)
    res["delta_si_sdr_db"] = round(res["si_sdr_db"] - si_sdr(x_ref, v_ref), 2)
    res["musical_noise"] = musical_noise(y, x_ref)
    res["clicks"] = click_count(y)
    res["stereo_width_db"] = stereo_width_db(
        apply_mix_stereo(mix[off:off + m], wet_a, cfg["mix_pct"]),
        mix[off:off + m])

    # Isolated probes. The chain is nonlinear so these differ from in-mixture
    # behaviour, but they are unambiguous and they are what tells you WHICH
    # WAY to move a parameter: vocal_ret_db falling as midside_exp rises is a
    # direct readout of the trade being made.
    wv, _ = run_chain(cfg, voc, mdir)
    if len(wv):
        lv = estimate_lag(wv, mono(voc))
        a, b, _ = align(wv, mono(voc), lv)
        res["vocal_ret_db"] = round(_db(_energy(a), _energy(b)), 2)
        res.update(band_damage(a, b))
    wm, _ = run_chain(cfg, mus, mdir)
    if len(wm):
        lm = estimate_lag(wm, mono(mus))
        a, b, _ = align(wm, mono(mus), lm)
        res["music_supp_solo_db"] = round(_db(_energy(a), _energy(b)), 2)

    res.update(streaming_consistency(cfg, mix, mdir))
    res["failures"] = classify_failures(res)

    if dump is not None:
        write_wav(dump / f"{item.name}_{tag}_{config_id(cfg)}.wav", y)
    return res


def _alpha(tag: str) -> float:
    return int(tag.lstrip("a")) / 100.0


def evaluate_reference(item: Path, tag: str, kind: str,
                       dump: Path | None) -> dict:
    """Score the two fixed comparison points: `input` (do nothing at all) and
    `ceiling` (the offline separator on the same remix). Same metrics, same
    code path — that is what makes the three-way comparison meaningful."""
    voc = mono(as_stereo(read_wav(item / "vocals.wav")[0]))
    mus = mono(as_stereo(read_wav(item / "music.wav")[0]))
    mix = mono(as_stereo(read_wav(item / f"mix_{tag}.wav")[0]))
    off = 0
    if kind == "input":
        y = mix.copy()
    elif kind == "oracle":
        y = oracle_irm(voc, mus * _alpha(tag), mix)
        if not len(y):
            return {"error": "oracle mask produced no output"}
    else:
        p = item / f"ceiling_{tag}.wav"
        if not p.is_file():
            return {"error": "no ceiling rendered"}
        y = mono(as_stereo(read_wav(p)[0]))
        y, mix, off = align(y, mix, estimate_lag(y, mix))
    n = min(len(y), len(voc) - off, len(mus) - off, len(mix))
    y, v_ref, m_ref, x_ref = (y[:n], voc[off:off + n],
                              mus[off:off + n] * _alpha(tag), mix[:n])

    res = {"latency_ms": 0.0 if kind == "input" else float("nan")}
    res.update(gap_metrics(y, x_ref, v_ref))
    res["failures"] = []
    res.update(bss_metrics(y, v_ref, m_ref))
    res["si_sdr_db"] = round(si_sdr(y, v_ref), 2)
    res["delta_si_sdr_db"] = round(res["si_sdr_db"] - si_sdr(x_ref, v_ref), 2)
    res["musical_noise"] = musical_noise(y, x_ref)
    res["clicks"] = click_count(y)
    if dump is not None:
        write_wav(dump / f"{item.name}_{tag}_{kind}.wav", y)
    return res


# ----------------------------------------------------------------- sweeps ----

BOOLS = {"on": True, "off": False, "true": True, "false": False,
         "1": True, "0": False, "yes": True, "no": False}

DEFAULT_CFG = {"model": "dpdfnet_hr", "midside": False, "midside_exp": 4.0,
               "bandlimit": True, "mix_pct": 100.0, "atten_db": 0.0}


def parse_sweep(specs: list[str]) -> list[dict]:
    axes = {}
    for spec in specs or []:
        if "=" not in spec:
            raise SystemExit(f"--sweep needs key=v1,v2 (got {spec!r})")
        key, values = spec.split("=", 1)
        key = key.strip()
        if key not in DEFAULT_CFG:
            raise SystemExit(f"unknown sweep key {key!r}; "
                             f"valid: {', '.join(DEFAULT_CFG)}")
        axes[key] = [_coerce(key, v.strip()) for v in values.split(",") if v.strip()]
    if not axes:
        return [dict(DEFAULT_CFG)]
    keys = list(axes)
    return [dict(DEFAULT_CFG, **dict(zip(keys, combo)))
            for combo in itertools.product(*(axes[k] for k in keys))]


def _coerce(key: str, value: str):
    proto = DEFAULT_CFG[key]
    if isinstance(proto, bool):
        if value.lower() not in BOOLS:
            raise SystemExit(f"{key}={value!r} must be on/off")
        return BOOLS[value.lower()]
    return float(value) if isinstance(proto, float) else value


def config_id(cfg: dict) -> str:
    parts = [cfg["model"]]
    if cfg["midside"]:
        parts.append(f"ms{cfg['midside_exp']:g}")
    if not cfg["bandlimit"]:
        parts.append("nobl")
    if cfg["mix_pct"] != 100.0:
        parts.append(f"mix{cfg['mix_pct']:g}")
    if cfg["atten_db"]:
        parts.append(f"att{cfg['atten_db']:g}")
    return "+".join(parts)


# -------------------------------------------------------------- reporting ----

COLUMNS = [
    ("music_supp_db", "music", "dB in vocal gaps; lower = more music removed"),
    ("vocal_ret_db", "vocal", "dB on vocals-only probe; 0 = undamaged"),
    ("delta_si_sdr_db", "dSI-SDR", "dB improvement over doing nothing; higher better"),
    ("sir_db", "SIR", "dB interference rejection; higher better"),
    ("sar_db", "SAR", "dB artifact-freeness; higher better"),
    ("musical_noise", "musNoise", "kurtosis rise; lower better"),
    ("stereo_width_db", "stereo", "dB side-channel retained; 0 = image intact"),
    ("latency_ms", "lat ms", "measured algorithmic delay"),
    ("rtf", "RTF", "fraction of the 20 ms block budget used"),
]


def _mean(rows: list[dict], key: str):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))
            and np.isfinite(r[key])]
    return round(float(np.mean(vals)), 2) if vals else None


def aggregate(per_item: list[dict]) -> dict:
    keys = {k for r in per_item for k in r if k not in ("error", "failures")}
    out = {k: v for k in sorted(keys) if (v := _mean(per_item, k)) is not None}
    # Failure tags are per-clip and must not be averaged away: a defect that
    # appears on one clip out of ten is exactly the worst-case behaviour the
    # mean is designed to hide, so carry the union plus how often each fired.
    counts = {}
    for r in per_item:
        for tag in r.get("failures", []):
            counts[tag] = counts.get(tag, 0) + 1
    if counts:
        out["failures"] = counts
        out["n_clips"] = len(per_item)
    return out


def print_table(rows: list[dict], previous: dict | None, sort_key: str) -> None:
    prev = {r["config"]: r for r in (previous or {}).get("rows", [])}

    width = 30 + 11 * len(COLUMNS)
    print("\n" + "=" * width)
    print("de-musicing quality  —  input (do nothing) / live configs / ceiling (offline separator)")
    print("=" * width)
    head = f"  {'config':<26}"
    for key, label, _ in COLUMNS:
        head += f"{label:>11}"
    print(head)
    print("  " + "-" * (width - 4))

    fixed = [r for r in rows if r.get("kind") != "live"]
    live = [r for r in rows if r.get("kind") == "live"]
    live.sort(key=lambda r: _sort_val(r, sort_key))
    front = set(pareto_front(live))

    ladder = ([rows_by_kind(fixed, "input")] + live
              + [rows_by_kind(fixed, "ceiling"), rows_by_kind(fixed, "oracle")])
    for r in ladder:
        if r is None:
            continue
        mark = "*" if r["config"] in front else " "
        line = f" {mark}{r['config']:<26}"
        for key, _, _ in COLUMNS:
            v = r.get(key)
            if not isinstance(v, (int, float)) or not np.isfinite(v):
                line += f"{'--':>11}"
                continue
            cell = f"{v:.2f}" if abs(v) < 1000 else f"{v:.0f}"
            p = prev.get(r["config"], {}).get(key)
            if isinstance(p, (int, float)) and abs(v - p) >= 0.05:
                cell += "^" if v > p else "v"
            line += f"{cell:>11}"
        print(line)

    print("  " + "-" * (width - 4))
    for _, label, meaning in COLUMNS:
        print(f"    {label:<10} {meaning}")
    print(f"    {'*':<10} on the Pareto frontier — nothing tested beats it on "
          f"all of: {', '.join(PARETO_OBJECTIVES)}")

    print_band_table(rows)
    print_failure_table(live)

    ceiling = rows_by_kind(fixed, "ceiling")
    if ceiling and live:
        print("\n  fraction of the offline ceiling reached (100% = matches the "
              "offline separator):")
        for r in live:
            print(f"    {r['config']:<26}"
                  f"  music {_pct(r.get('music_supp_db'), ceiling.get('music_supp_db')):>6}"
                  f"   dSI-SDR {_pct(r.get('delta_si_sdr_db'), ceiling.get('delta_si_sdr_db')):>6}")
        print("    (reporting metric, NOT an optimization target: the ceiling "
              "is not perceptually ideal either, so a config can sit at 95% of\n"
              "     it and still sound clearly worse. Read it alongside SAR and "
              "the band table, and confirm with --dump-audio.)")


def rows_by_kind(rows: list[dict], kind: str) -> dict | None:
    return next((r for r in rows if r.get("kind") == kind), None)


def pareto_front(rows: list[dict],
                 objectives: dict | None = None) -> list[str]:
    """Config names not strictly dominated on PARETO_OBJECTIVES.

    A config is dominated when some other config is at least as good on every
    objective and strictly better on at least one — i.e. there is no reason to
    ever choose it. Everything else is on the frontier and represents a real
    trade someone has to decide about (more suppression for more latency, less
    vocal damage for more music leakage).

    This replaces the temptation to rank by a single weighted score. Only
    configs scored on every objective can be compared; one missing metric
    makes domination undecidable, so those are left off the frontier rather
    than being assumed good or bad.
    """
    objectives = objectives or PARETO_OBJECTIVES
    usable = [r for r in rows
              if all(isinstance(r.get(k), (int, float)) and np.isfinite(r[k])
                     for k in objectives)]

    def better_eq(a, b, key, direction):
        return a[key] <= b[key] if direction == "lower" else a[key] >= b[key]

    front = []
    for cand in usable:
        dominated = any(
            other is not cand
            and all(better_eq(other, cand, k, d) for k, d in objectives.items())
            and any(other[k] != cand[k] for k in objectives)
            for other in usable)
        if not dominated:
            front.append(cand["config"])
    return front


def print_band_table(rows: list[dict]) -> None:
    """Per-band vocal damage, promoted to the default report.

    A single SI-SDR number can look healthy while everything above 8 kHz is
    gone — which is precisely what a 16 kHz-native model does, and it costs
    consonants, breath and intelligibility that the aggregate score never
    shows. Measured on the vocals-only probe, so any deviation from 0 dB is
    damage to content the pipeline was supposed to leave alone.
    """
    live = [r for r in rows if r.get("kind") == "live"]
    labels = [f"band_{b[0]}" for b in BANDS]
    if not any(any(k in r for k in labels) for r in live):
        return
    print("\n  per-band vocal damage, dB (vocals-only probe; 0 = undamaged, "
          "very negative = band destroyed)")
    head = f"    {'config':<26}"
    for label, lo, hi in BANDS:
        head += f"{label + ' ' + _band_range(lo, hi):>16}"
    print(head)
    for r in live:
        line = f"    {r['config']:<26}"
        for label, _, _ in BANDS:
            v = r.get(f"band_{label}")
            cell = f"{v:.1f}" if isinstance(v, (int, float)) and np.isfinite(v) else "--"
            line += f"{cell:>16}"
        print(line)


def print_failure_table(live: list[dict]) -> None:
    """Named defects per config, with how many clips each fired on.

    The point is actionability: "3 dB worse" says something is wrong,
    "hf-loss on 8/12 clips" says what to go fix.
    """
    if not any(r.get("failures") for r in live):
        return
    meanings = {tag: why for _, _, _, tag, why in TAXONOMY}
    print("\n  failure taxonomy (tag x/n = fired on x of n clips):")
    for r in live:
        fails = r.get("failures") or {}
        if not fails:
            print(f"    {r['config']:<26}  (none flagged)")
            continue
        n = r.get("n_clips", 1)
        tags = ", ".join(f"{t} {c}/{n}" for t, c in sorted(
            fails.items(), key=lambda kv: -kv[1]))
        print(f"    {r['config']:<26}  {tags}")
    seen = {t for r in live for t in (r.get("failures") or {})}
    if seen:
        print("      " + "; ".join(f"{t}: {meanings[t]}"
                                   for t in sorted(seen) if t in meanings))


def _band_range(lo: int, hi: int) -> str:
    fmt = lambda h: f"{h // 1000}k" if h >= 1000 else str(h)  # noqa: E731
    return f"({fmt(lo)}-{fmt(hi)})"


def run_gate(rows: list[dict], baseline: dict | None,
             floors: dict | None) -> list[str]:
    """Regression gate. Returns a list of violation strings (empty = pass).

    Two independent checks, because they catch different things:

    * relative — every live config is compared against the same config in the
      baseline run, and may not move the wrong way by more than its tolerance.
      This is what stops the project gradually trading vocal quality for
      better-looking suppression numbers, one small change at a time.
    * absolute — optional hard floors/ceilings from --gate-file, for limits
      that are product constraints rather than regressions (e.g. RTF must
      stay under 1.0 or it cannot run realtime at all, regardless of what
      last week's number was).
    """
    violations = []
    base = {r["config"]: r for r in (baseline or {}).get("rows", [])}

    for r in [x for x in rows if x.get("kind") == "live"]:
        prior = base.get(r["config"])
        if prior:
            for key, (direction, tol) in GATE_METRICS.items():
                now, was = r.get(key), prior.get(key)
                if not all(isinstance(v, (int, float)) and np.isfinite(v)
                           for v in (now, was)):
                    continue
                drift = (now - was) if direction == "lower" else (was - now)
                if drift > tol:
                    violations.append(
                        f"{r['config']}: {key} regressed {was:+.2f} -> "
                        f"{now:+.2f} (tolerance {tol})")
        for key, limit in (floors or {}).items():
            v = r.get(key)
            if not isinstance(v, (int, float)) or not np.isfinite(v):
                continue
            if "min" in limit and v < limit["min"]:
                violations.append(
                    f"{r['config']}: {key} {v:.2f} below floor {limit['min']}")
            if "max" in limit and v > limit["max"]:
                violations.append(
                    f"{r['config']}: {key} {v:.2f} above ceiling {limit['max']}")
    return violations


def _sort_val(row: dict, key: str) -> float:
    v = row.get(key)
    if not isinstance(v, (int, float)) or not np.isfinite(v):
        return float("inf")
    # for these, "better" is more negative / smaller
    return v if key in ("music_supp_db", "musical_noise", "latency_ms", "rtf") else -v


def _pct(live, ceiling) -> str:
    if not all(isinstance(v, (int, float)) and np.isfinite(v)
               for v in (live, ceiling)) or abs(ceiling) < 0.05:
        return "--"
    return f"{100.0 * live / ceiling:.0f}%"


def write_csv(path: Path, rows: list[dict]) -> None:
    # config_params is a raw dict (for the JSON report / programmatic use);
    # its str() contains unescaped commas and corrupts CSV columns, and it's
    # redundant here anyway — config_id() already encodes it in the name.
    keys = sorted({k for r in rows for k in r
                   if k not in ("config", "kind", "config_params", "errors",
                                "failures")})
    lines = ["config,kind," + ",".join(keys)]
    for r in rows:
        lines.append(f"{r['config']},{r.get('kind', '')}," +
                     ",".join(str(r.get(k, "")) for k in keys))
    path.write_text("\n".join(lines) + "\n")
    print(f"csv: {path}")


# ------------------------------------------------------------------- main ----

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-refs", nargs="+", metavar="AUDIO",
                    help="build the ground-truth corpus from these source files")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--demucs-python", default=str(
        Path.home() / "Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python"),
        help="interpreter that has demucs installed (the live app deliberately "
             "has no torch, so separation runs out-of-process)")
    ap.add_argument("--demucs-model", default="mdx_extra",
                    help="mdx_extra (best) | htdemucs_ft | htdemucs (fast)")
    ap.add_argument("--ratios", default="1.0",
                    help="music:vocal amplitude ratios to remix at, e.g. 0.5,1.0,2.0")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="seconds of each clip to use (0 = whole file)")
    ap.add_argument("--sweep", action="append", metavar="KEY=V1,V2",
                    help=f"sweep axis; keys: {', '.join(DEFAULT_CFG)}")
    ap.add_argument("--sort", default="delta_si_sdr_db",
                    help="metric to rank live configs by")
    ap.add_argument("--dump-audio", type=Path, default=None,
                    help="write every output wav here for the by-ear check")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--category", default="general",
                    help="label for clips being built with --build-refs, e.g. "
                         "stereo_torture / transient_music / sustained_pad")
    ap.add_argument("--holdout", action="store_true",
                    help="with --build-refs: mark these clips as the reserved "
                         "validation set, excluded from normal runs")
    ap.add_argument("--rebuild", action="store_true",
                    help="with --build-refs: re-separate sources already in "
                         "the manifest (default is to skip them, so an "
                         "interrupted build resumes by rerunning the command)")
    ap.add_argument("--only-category", default=None,
                    help="evaluate only clips with this category label")
    ap.add_argument("--include-holdout", action="store_true",
                    help="also evaluate holdout clips — use for final "
                         "validation, never while tuning")
    ap.add_argument("--gate", action="store_true",
                    help="regression gate: exit non-zero if any metric moved "
                         "the wrong way beyond tolerance vs. the baseline")
    ap.add_argument("--gate-baseline", type=Path, default=None,
                    help="baseline report for --gate (default: the previous run)")
    ap.add_argument("--gate-file", type=Path, default=None,
                    help='JSON of absolute limits, e.g. {"rtf": {"max": 0.8}}')
    args = ap.parse_args()

    ratios = [float(r) for r in args.ratios.split(",") if r.strip()]
    duration = args.duration or None

    if args.build_refs:
        build_refs(args.build_refs, args.corpus, args.demucs_python,
                   args.demucs_model, ratios, duration,
                   category=args.category, holdout=args.holdout,
                   rebuild=args.rebuild)
        return 0

    manifest_path = args.corpus / "manifest.json"
    if not manifest_path.is_file():
        print(f"no corpus at {args.corpus}\n"
              f"build one first:\n"
              f"  python {Path(__file__).name} --build-refs your_clip.wav")
        return 1
    manifest = json.loads(manifest_path.read_text())

    entries = manifest["items"]
    total = len(entries)
    if not args.include_holdout:
        entries = [e for e in entries if not e.get("holdout")]
    held = total - len(entries)
    if args.only_category:
        entries = [e for e in entries
                   if e.get("category", "general") == args.only_category]
    items = [(args.corpus / e["name"], tag)
             for e in entries for tag in e["mixes"]]
    if not items:
        print("no corpus items match — "
              f"{total} in corpus, {held} held out"
              + (f", none in category {args.only_category!r}"
                 if args.only_category else ""))
        return 1

    mdir = models_dir()
    configs = parse_sweep(args.sweep)
    have = processors.available(mdir)
    missing = {c["model"] for c in configs} - set(have)
    if missing:
        print(f"model(s) not installed: {', '.join(sorted(missing))}\n"
              f"available: {', '.join(have)}")
        return 1

    cats = sorted({e.get("category", "general") for e in entries})
    print(f"corpus:  {args.corpus}  ({len(items)} item-ratio pairs, "
          f"separator={manifest['model']}, categories: {', '.join(cats)})")
    if held:
        print(f"         {held} holdout item(s) excluded"
              if not args.include_holdout else "")
    if args.include_holdout:
        print("         INCLUDING holdout items — final validation only, "
              "these stop being a clean check once tuned against")
    print(f"models:  {mdir}")
    print(f"configs: {len(configs)}")
    if args.dump_audio:
        args.dump_audio.mkdir(parents=True, exist_ok=True)

    rows = []
    for kind in ("input", "ceiling", "oracle"):
        per = [evaluate_reference(it, tag, kind, args.dump_audio)
               for it, tag in items]
        good = [p for p in per if "error" not in p]
        if not good:
            continue
        rows.append({"config": f"[{kind}]", "kind": kind, **aggregate(good)})

    for cfg in configs:
        cid = config_id(cfg)
        print(f"  running {cid} ...", flush=True)
        per = []
        for it, tag in items:
            try:
                per.append(evaluate(cfg, it, tag, mdir, args.dump_audio))
            except Exception as e:  # noqa: BLE001 — one bad config must not
                # abandon a long sweep; record it and keep going
                per.append({"error": f"{type(e).__name__}: {e}"})
        good = [p for p in per if "error" not in p]
        row = {"config": cid, "kind": "live", "config_params": cfg,
               **aggregate(good)}
        errs = [p["error"] for p in per if "error" in p]
        if errs:
            row["errors"] = errs[:3]
            print(f"    {len(errs)}/{len(per)} failed: {errs[0]}")
        rows.append(row)

    previous = None
    latest = REPORT_DIR / "latest.json"
    if latest.is_file():
        previous = json.loads(latest.read_text())

    print_table(rows, previous, args.sort)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"timestamp": time.time(), "corpus": str(args.corpus),
              "separator": manifest["model"], "rows": rows}
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (REPORT_DIR / f"{stamp}.json").write_text(json.dumps(report, indent=2))
    latest.write_text(json.dumps(report, indent=2))
    print(f"\nreport: {latest}   (^ / v mark movement vs. the previous run)")
    if args.csv:
        write_csv(args.csv, rows)
    if args.dump_audio:
        print(f"audio:  {args.dump_audio}  — listen before trusting any of this")

    if args.gate:
        baseline = previous
        if args.gate_baseline:
            if not args.gate_baseline.is_file():
                print(f"\ngate: baseline {args.gate_baseline} not found")
                return 1
            baseline = json.loads(args.gate_baseline.read_text())
        floors = (json.loads(args.gate_file.read_text())
                  if args.gate_file else None)
        if baseline is None and not floors:
            print("\ngate: no baseline to compare against and no --gate-file; "
                  "this run becomes the baseline for the next one")
            return 0
        violations = run_gate(rows, baseline, floors)
        if violations:
            print(f"\ngate: FAIL ({len(violations)} violation(s))")
            for v in violations:
                print(f"  {v}")
            return 1
        print("\ngate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
