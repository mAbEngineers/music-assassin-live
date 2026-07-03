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

# run
.venv/bin/python -m assassin_live               # GUI toggle window
.venv/bin/python -m assassin_live --headless    # terminal mode, Ctrl-C stops
.venv/bin/python -m assassin_live --recover     # cleanup after a crash
```

## Models

| name | file | rate | notes |
|---|---|---|---|
| gtcrn | gtcrn_simple.onnx (2 MB) | 16 kHz | default; lightest |
| speechdenoiser | speechdenoiser.onnx | 48 kHz | no resampling path |
| dpdfnet | dpdfnet_baseline.onnx | 16 kHz | experimental (see module docstring) |

All are speech-enhancement models: output is voice-only (music removed, but
non-speech SFX are attenuated too). A true music-vs-SFX realtime model drops
in with zero app changes once one exists — that's the point of the
processor interface.

## Status

- [x] Layer 1 — PipeWire trap-sink routing, crash recovery, hotplug watch
- [x] Layer 2 — streaming engine (dry-fallback, click-free toggle)
- [x] Layer 3 — GTCRN / DPDFNet / SpeechDenoiser processors (offline-tested)
- [x] Layer 4 — minimal toggle GUI + headless mode
- [ ] Live end-to-end validation on real playback
- [ ] Tray icon, autostart, first-run model download
- [ ] PyInstaller → AppImage packaging
- [ ] Native LADSPA port (see docs/ARCHITECTURE.md Phase 3)

License: MIT
