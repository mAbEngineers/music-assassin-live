# Music Assassin Live

System-wide realtime music removal for Linux — works like a volume booster:
one toggle, and everything the device plays gets background music stripped
before it reaches the speaker. No per-app setup, no manual audio routing.

Sibling repo of [Music-Assassin](https://github.com/A-Ahmad-02/Music-Assassin)
(the research lab where filtering methods are tested and benchmarked). This
repo is the shippable app: routing, streaming engine, UI, packaging. Models
arrive here as ONNX release assets — see `docs/ARCHITECTURE.md` §3 for the
contract.

## How it works

```
apps (browser, VLC, Spotify…)
   │   WirePlumber migrates all default-following streams
   ▼
"Music Assassin" trap sink  (null sink, set as default while ON)
   │   monitor capture
   ▼
streaming engine: 20 ms blocks → ONNX speech-enhancement model → speaker
```

Turning OFF (or quitting, or crashing — recovery runs at startup) restores
the previous default sink. Everything uses PipeWire-native tools
(`pw-cli` / `wpctl` / `pw-dump`); no pactl needed.

## Requirements

- Linux with PipeWire + WirePlumber (tested: PipeWire 1.0.5, Ubuntu 24.04)
- Python 3.10+
- `pip install -r requirements.txt` (numpy, sounddevice, soxr, onnxruntime —
  **no torch**, whole stack ≈ 100 MB)

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# one-time: copy models from the research repo (until release assets exist)
.venv/bin/python scripts/import_models.py --source ../Music-Assassin/models

# sanity check without touching audio devices
.venv/bin/python tests/test_processors_offline.py
.venv/bin/python tests/test_routing_dry.py

# repeatable quality + plumbing regression check — rerun this any time
# import_models.py pulls updated weights from the research repo; it diffs
# the numbers against the previous run so a model change shows up as a
# number moving. Works with no audio hardware connected (falls back to
# PipeWire's dummy sink for the plumbing tier, clearly marked as skipped);
# add real headphones/speakers to also validate the live audio path.
.venv/bin/python tests/test_live_e2e.py

# run
.venv/bin/python -m assassin_live               # GUI toggle window
.venv/bin/python -m assassin_live --headless    # terminal mode, Ctrl-C stops
.venv/bin/python -m assassin_live --recover     # cleanup after a crash
```

## Models

| name | file | rate | notes |
|---|---|---|---|
| dpdfnet_hr | dpdfnet2_48khz_hr.onnx | 48 kHz | **default** — best vocal retention of these by ear so far, though still imperfect |
| gtcrn | gtcrn_simple.onnx (2 MB) | 16 kHz | lightest; cuts vocals too aggressively (by-ear finding) |
| dpdfnet | dpdfnet_baseline.onnx | 16 kHz | experimental (see module docstring); cuts vocals too aggressively (by-ear finding) |
| dtln | dtln_model_1.onnx + dtln_model_2.onnx | 16 kHz | dual-LSTM architecture (breizhn/DTLN), a genuinely different design from the others; added to compare, not yet judged by ear |
| speechdenoiser | speechdenoiser.onnx | 48 kHz | no resampling path; keeps clear spoken dialogue well but still cuts singing |

All are speech-enhancement models: they classify content by how
speech-like it sounds, which is why every one of them struggles with
singing to some degree — a sung vocal reads spectrally closer to "music"
than spoken dialogue does. Noise and noise-like backgrounds are strongly
removed (−29 to −63 dB on white noise); prominent or vocal-heavy music
largely passes through (−0.4 to −1.7 dB on a music+dialogue mix). The
research repo's own DeepFilterNet diagnostics show the same pattern
(−0.6 to −8 dB per segment). True music removal needs a realtime
source-separation model — an open research-repo problem; it drops in
here with zero app changes via the processor interface.

**Mid/side stereo pre-filter** (`assassin_live/audio/midside.py`, toggle
in the GUI or `--midside` headless): a complementary, non-model lever for
the same problem. Runs on the raw stereo capture before any model sees
it, using stereo panning rather than spectral guessing — lead vocals are
almost always mixed dead-center, instrumental backing is mixed wide, so
attenuating by `M²/(M²+S²)` per STFT bin (mid/side energy ratio) directly
targets exactly the content models misclassify. Off by default; stacks
with whichever pipeline model is selected, so it's meant to be compared
on/off rather than treated as a fixed answer.

## Status

- [x] Layer 1 — PipeWire trap-sink routing, crash recovery, hotplug watch
- [x] Layer 2 — streaming engine (dry-fallback, click-free toggle)
- [x] Layer 3 — GTCRN / DPDFNet / SpeechDenoiser processors (offline-tested)
- [x] Layer 4 — minimal toggle GUI + headless mode
- [x] Live end-to-end validation on real playback (BT sink; injected noise
      removed from output, speech passes, clean sink restore on exit)
- [ ] Tray icon, autostart, first-run model download
- [ ] PyInstaller → AppImage packaging
- [ ] Native LADSPA port (see docs/ARCHITECTURE.md Phase 3)

License: MIT
