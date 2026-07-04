#!/usr/bin/env python3
"""Repeatable live regression check.

Rerun this after `scripts/import_models.py` pulls updated weights from the
Music-Assassin research repo — it re-benchmarks whatever landed and diffs
the numbers against the previous run, so a model change shows up as a
number moving instead of a manual re-listen.

Two tiers:

  1. offline quality bench — every installed processor vs. three phases:
     noise-only (synthetic white noise — always available, no cross-repo
     dependency, and exactly what these models are actually trained to
     remove), plus music-only / music+speech built from real reference
     clips when available (default: looks for two files under the
     sibling Music-Assassin repo's test_outputs/, override with
     --music-wav/--speech-wav). Real clips matter here: an earlier version
     of this fixture used synthetic tone chords as a music stand-in and
     they got suppressed just as hard as noise (-47 dB) — completely
     contradicting the real measured behavior (real music: -0.4 to
     -1.7 dB, see README). Stationary synthetic tones read as "noise" to
     these models in a way real music does not, so faking the music
     phase silently produces a wrong quality signal. If no real clips are
     found, those two phases are skipped rather than reported from a
     misleading proxy.
  2. live hardware tier — inserts the real trap-sink routing + streaming
     engine, replays the fixture through it, and records from the real
     output sink *by name* (a past bug here silently recorded the wrong
     node when targeted by a stale id — see routing.pin_process_streams).
     This works against ANY PipeWire sink, including the "auto_null"
     dummy sink most distros load by default, so it needs no headphones
     plugged in to produce numbers — only to confirm by ear.

Each run's numbers are saved under the XDG state dir (~/.local/state/
music-assassin/bench/) so `latest.json` always holds the last run to diff
against — rerun this after `scripts/import_models.py` and the printed
table shows exactly what moved.
"""

import argparse
import hashlib
import json
import signal
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import models_dir, state_dir  # noqa: E402
from assassin_live import processors  # noqa: E402

FIXTURE_SR = 48000
REPORT_DIR = state_dir() / "bench"

# Sibling research repo, same convention as scripts/import_models.py's
# default --source: hand-off by directory layout until release assets
# exist. Override with --music-wav/--speech-wav if these move.
_DEFAULT_MA_DIR = Path(__file__).resolve().parents[2] / "Music-Assassin"
DEFAULT_MUSIC_WAV = _DEFAULT_MA_DIR / "test_outputs" / "bench_op10s_mono_ref.wav"
DEFAULT_SPEECH_WAV = (_DEFAULT_MA_DIR / "test_outputs" /
                      "bench_dialogue07m00_audio_separator_UVR-MDX-NET-Voc_FT.wav")


# -- fixture --------------------------------------------------------------

def _load_wav_resampled(path: Path, sr: int) -> np.ndarray:
    w = wave.open(str(path))
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    src_sr, ch = w.getframerate(), w.getnchannels()
    w.close()
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if src_sr != sr:
        a = soxr.resample(a, src_sr, sr).astype(np.float32)
    return a


def _build_real_segment(music: np.ndarray, speech: np.ndarray, sr: int,
                        music_only_s: float = 4.0, total_s: float = 17.0):
    """music-only intro, then music+speech — speech is silenced for the
    intro regardless of what the source clip contains there, so the phase
    boundary is exact."""
    n = min(len(music), len(speech), int(total_s * sr))
    music, speech = music[:n].copy(), speech[:n].copy()
    cut = int(music_only_s * sr)
    speech[:cut] = 0.0
    mix = 0.45 * music + 0.9 * speech
    peak = float(np.abs(mix).max())
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix.astype(np.float32), {"music_only": (0, cut), "music_speech": (cut, n)}


def make_fixture(sr: int = FIXTURE_SR, seed: int = 0,
                 music_wav: Path | None = None, speech_wav: Path | None = None):
    """noise-only synthetic block, followed by a real-clip music/speech
    segment if both wavs are supplied and exist — see module docstring
    for why the music phase needs real material, not a synthetic proxy.
    """
    rng = np.random.default_rng(seed)
    noise = (0.12 * rng.standard_normal(int(3.0 * sr))).astype(np.float32)
    phases = {"noise_only": (0, len(noise))}
    segments = [noise]

    if music_wav and Path(music_wav).is_file() and speech_wav and Path(speech_wav).is_file():
        music = _load_wav_resampled(Path(music_wav), sr)
        speech = _load_wav_resampled(Path(speech_wav), sr)
        seg, seg_phases = _build_real_segment(music, speech, sr)
        offset = len(noise)
        phases.update({label: (a + offset, b + offset) for label, (a, b) in seg_phases.items()})
        segments.append(seg)

    full = np.concatenate(segments).astype(np.float32)
    return full, phases


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))) + 1e-9)


def _db(out: np.ndarray, ref: np.ndarray) -> float:
    return 20.0 * np.log10(_rms(out) / _rms(ref))


def _phase_db(y: np.ndarray, x: np.ndarray, phases: dict, scale: float = 1.0) -> dict:
    out = {}
    for label, (a, b) in phases.items():
        ia, ib = int(a * scale), int(min(b * scale, len(y), len(x)))
        if ib <= ia:
            continue
        out[label] = round(_db(y[ia:ib], x[ia:ib]), 1)
    return out


# -- tier 1: offline --------------------------------------------------------

def bench_offline(name, mdir, fixture, phases):
    proc = processors.create(name, mdir)
    x = fixture if proc.sample_rate == FIXTURE_SR else \
        soxr.resample(fixture, FIXTURE_SR, proc.sample_rate).astype(np.float32)
    scale = proc.sample_rate / FIXTURE_SR

    proc.reset()
    block = int(proc.sample_rate * 0.020)
    chunks = []
    for i in range(0, len(x) - block, block):
        y = proc.feed(x[i:i + block])
        if len(y):
            chunks.append(y)
    y = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    result = {"name": name, "sr": proc.sample_rate, **_phase_db(y, x, phases, scale)}
    onnx_name = processors.model_file(name)
    onnx = mdir / onnx_name if onnx_name else None
    if onnx and onnx.is_file():
        result["weights_sha256"] = hashlib.sha256(onnx.read_bytes()).hexdigest()[:12]
    return result


# -- tier 2: live hardware --------------------------------------------------

def _write_wav(path: Path, x: np.ndarray, sr: int):
    w = wave.open(str(path), "w")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()


def _read_wav(path: Path):
    w = wave.open(str(path))
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    sr = w.getframerate()
    w.close()
    return a, sr


def _is_transient_dummy(sink) -> bool:
    """PipeWire's pipewire-pulse compat layer keeps a placeholder sink
    ("auto_null" / "Dummy Output") alive only while NO other sink exists.
    The instant our trap sink is created and set default, that placeholder
    is destroyed outright — not renumbered, gone — so pin_process_streams
    can never find it again until the trap is torn down. Discovered by
    this test on a machine with a crashed audio codec and no Bluetooth
    connected: routing.enable()/disable() still worked (the placeholder
    reappears once the trap is destroyed), but nothing could be routed to
    it in between, so validating the audio path against it is meaningless.
    """
    return sink.name == "auto_null" or "dummy" in sink.description.lower()


def _wait_default_sink(name: str, timeout_s: float = 1.5) -> bool:
    from assassin_live.audio.routing import get_default_sink
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        d = get_default_sink()
        if d and d.name == name:
            return True
        time.sleep(0.1)
    return False


def bench_hardware(name, mdir, fixture, phases, work_dir: Path):
    from assassin_live.audio.routing import RoutingSession
    from assassin_live.audio.engine import AudioEngine

    fixture_path = work_dir / "fixture.wav"
    if not fixture_path.is_file():
        _write_wav(fixture_path, fixture, FIXTURE_SR)
    rec_path = work_dir / f"rec_{name}.wav"

    routing = RoutingSession()
    routing.recover_stale()
    proc = processors.create(name, mdir)
    real = routing.enable()
    if real is None:
        routing.disable()
        return {"name": name, "skipped": "no sink available"}
    if _is_transient_dummy(real):
        routing.disable()
        return {"name": name, "skipped":
                f"only a transient dummy sink ({real.name!r}) is available — "
                "it gets removed by PipeWire the instant the trap sink "
                "becomes default, so pinned routing can't be verified here. "
                "Connect real audio hardware or a Bluetooth sink to run "
                "this tier."}

    engine = AudioEngine(proc)
    engine.set_bypass(False)
    rec = None
    try:
        engine.start(routing.monitor_source, real.name)
        # Target by NAME, not node id: ids get recycled across runs, and
        # pw-record silently falls back to the default sink on a stale
        # one — that once looked exactly like a "wet path does nothing"
        # bug when it was actually just recording the wrong node.
        rec = subprocess.Popen([
            "pw-record", "-P", "{ stream.capture.sink = true }",
            "--target", real.name, "--format", "s16",
            "--rate", str(FIXTURE_SR), "--channels", "1", str(rec_path),
        ])
        time.sleep(0.4)
        subprocess.run(["pw-play", str(fixture_path)], check=True, timeout=60)
        time.sleep(0.4)
    finally:
        if rec is not None:
            rec.send_signal(signal.SIGINT)
            try:
                rec.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rec.kill()
        engine.stop()
        routing.disable()

    result = {"name": name, "sink": real.name,
              "sink_restored": _wait_default_sink(real.name)}
    if not rec_path.is_file() or rec_path.stat().st_size < 1000:
        result["error"] = "no audio captured"
        return result

    rec_audio, _ = _read_wav(rec_path)
    onset = np.where(np.abs(rec_audio) > 0.005)[0]
    if not len(onset):
        result["error"] = "recording is silent"
        return result
    start = int(onset[0])
    sig = rec_audio[start:start + len(fixture)]
    result.update(_phase_db(sig, fixture[:len(sig)], phases))
    return result


# -- reporting --------------------------------------------------------------

def _load_latest():
    p = REPORT_DIR / "latest.json"
    return json.loads(p.read_text()) if p.is_file() else None


def _save_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    (REPORT_DIR / f"{ts}.json").write_text(json.dumps(report, indent=2))
    (REPORT_DIR / "latest.json").write_text(json.dumps(report, indent=2))


def _print_offline_table(results, previous):
    prev_by_name = {r["name"]: r for r in (previous or {}).get("offline", [])}
    print("\noffline quality bench (dB attenuation; more negative = more removed)")
    print(f"  {'model':<16}{'music_only':>14}{'noise_only':>14}{'music_speech':>16}  weights")
    for r in results:
        if "error" in r:
            print(f"  FAIL {r['name']}: {r['error']}")
            continue
        prev = prev_by_name.get(r["name"])

        def cell(label, prev=prev, r=r):
            v = r.get(label)
            if v is None:
                return f"{'--':>10}"
            delta = ""
            if prev and prev.get(label) is not None:
                d = round(v - prev[label], 1)
                if abs(d) >= 0.1:
                    delta = f" ({'+' if d > 0 else ''}{d})"
            return f"{v:>6.1f} dB{delta}"

        print(f"  {r['name']:<16}{cell('music_only'):>14}{cell('noise_only'):>14}"
              f"{cell('music_speech'):>16}  {r.get('weights_sha256', '?')}")


def _print_hardware_table(results):
    print("\nlive hardware tier (routed through real trap-sink + streaming engine)")
    for r in results:
        if r.get("skipped"):
            print(f"  SKIP {r['name']}: {r['skipped']}")
            continue
        if r.get("error"):
            print(f"  FAIL {r['name']} ({r.get('sink', '?')}): {r['error']}")
            continue
        restored = "restored" if r["sink_restored"] else "NOT RESTORED"
        print(f"  {r['name']:<16} sink={r['sink']:<28} default-sink {restored}")
        for label in ("music_only", "noise_only", "music_speech"):
            if label in r:
                print(f"      {label:<14} {r[label]:>6.1f} dB")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=None,
                    help="model for the hardware tier (default: gtcrn, or first available)")
    ap.add_argument("--hardware-all", action="store_true",
                    help="run the hardware tier for every available model")
    ap.add_argument("--offline-only", action="store_true",
                    help="skip the hardware tier (e.g. no PipeWire on this machine)")
    ap.add_argument("--music-wav", type=Path, default=None,
                    help=f"real music clip (default: {DEFAULT_MUSIC_WAV} if present)")
    ap.add_argument("--speech-wav", type=Path, default=None,
                    help=f"real speech clip (default: {DEFAULT_SPEECH_WAV} if present)")
    args = ap.parse_args()

    mdir = models_dir()
    print(f"models dir: {mdir}")

    names = [n for n in processors.available(mdir) if n != "passthrough"]
    if not names:
        print("no models installed — run scripts/import_models.py first")
        return 1
    if args.model and args.model not in names:
        print(f"--model {args.model!r} not available (have: {', '.join(names)})")
        return 1

    music_wav = args.music_wav or DEFAULT_MUSIC_WAV
    speech_wav = args.speech_wav or DEFAULT_SPEECH_WAV
    if music_wav.is_file() and speech_wav.is_file():
        print(f"real-audio fixture: music={music_wav}  speech={speech_wav}")
    else:
        print(f"no real music/speech reference found at {music_wav} — "
              "music_only/music_speech phases will be skipped.\n"
              "  pass --music-wav/--speech-wav to supply your own clips.")
        music_wav = speech_wav = None

    fixture, phases = make_fixture(music_wav=music_wav, speech_wav=speech_wav)
    previous = _load_latest()

    ok = True
    offline_results = []
    for n in names:
        try:
            offline_results.append(bench_offline(n, mdir, fixture, phases))
        except Exception as e:  # noqa: BLE001
            offline_results.append({"name": n, "error": str(e)})
            ok = False
    _print_offline_table(offline_results, previous)

    hardware_results = []
    if not args.offline_only:
        default_model = "gtcrn" if "gtcrn" in names else names[0]
        targets = names if args.hardware_all else [args.model or default_model]
        with tempfile.TemporaryDirectory(prefix="assassin-e2e-") as tmp:
            for n in targets:
                r = bench_hardware(n, mdir, fixture, phases, Path(tmp))
                hardware_results.append(r)
                if r.get("error") or (not r.get("skipped") and not r.get("sink_restored", True)):
                    ok = False
        _print_hardware_table(hardware_results)

    report = {"timestamp": time.time(), "offline": offline_results, "hardware": hardware_results}
    _save_report(report)
    print(f"\nreport saved: {REPORT_DIR / 'latest.json'}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
