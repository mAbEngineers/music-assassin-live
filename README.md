# Music Assassin Live

[![Latest release](https://img.shields.io/github/v/release/mAbEngineers/music-assassin-live)](https://github.com/mAbEngineers/music-assassin-live/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/mAbEngineers/music-assassin-live/total)](https://github.com/mAbEngineers/music-assassin-live/releases)
[![License: MIT](https://img.shields.io/github/license/mAbEngineers/music-assassin-live)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%28PipeWire%29-blue)](#requirements)

System-wide realtime music removal for Linux — works like a volume booster:
one toggle, and everything the device plays gets background music stripped
before it reaches the speaker. No per-app setup, no manual audio routing.

Sibling repo of [Music-Assassin](https://github.com/mAbEngineers/Music-Assassin)
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

# quantitative de-musicing quality: builds ground-truth vocal/music stems
# with an offline separator (once), then scores any model/filter/mix
# combination against them — no audio hardware needed, fully repeatable,
# meant for comparing configs, not just pass/fail. See "Measuring quality"
# below for what each reported quantity means.
.venv/bin/python tests/bench_quality.py --build-refs your_clip.wav
.venv/bin/python tests/bench_quality.py --sweep model=dpdfnet_hr,gtcrn,dtln \
                                        --sweep midside=off,on

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

## Measuring quality

Every claim in the table above has numbers behind it.
`tests/bench_quality.py` scores any combination of model, filter and mix
setting against known-correct audio — no sound hardware, same answer every
run — so "did that change help?" doesn't depend on remembering what last week
sounded like.

**How it knows the right answer.** An offline separator splits your own music
into a *vocals* track and a *music* track, then the harness recombines them at
a chosen ratio. Because it built that mixture itself, it knows exactly what
perfect output would be (the vocals track) and exactly what should have
disappeared (the music track). Nothing is guessed at.

**Four things every number is compared against:**

| reference point | what it is |
|---|---|
| **do nothing** | the untouched mix — every "improvement" is measured from here |
| **this app** | the real chain in 20 ms blocks, exactly as it runs live |
| **offline separator** | the same separator with unlimited time and no realtime constraints |
| **theoretical best** | perfect knowledge of both tracks — cheating by construction, and unreachable |

Where a config falls on that ladder is what makes a bad score actionable. Well
short of the offline separator means **the realtime path** is the limit — the
one thing this repo can fix directly. Offline separator well short of the
theoretical best means **the model** is the limit. And when even the
theoretical best does badly, that song is simply hard: voice and instruments
occupy the same frequencies at the same moments, and no amount of engineering
wins there.

### What gets measured

Deliberately many numbers rather than one score, because they pull against
each other — suppression is trivially bought by wrecking the vocal, and any
"quality" number is winnable by adding delay.

| reported as | what it tells you | better is |
|---|---|---|
| **music** | how much backing music is left, measured only in the moments the singer is silent — the cleanest possible reading, because nothing but music is there to measure | more negative |
| **vocal** | how much of the voice survived, measured on the voice alone | closer to 0 |
| **per-band vocal damage** | *where* the voice got damaged: sub, low, mid, high and air bands. Damage is rarely spread evenly | closer to 0 |
| **dSI-SDR** | overall improvement over doing nothing, immune to plain volume changes | higher — but see the caveat below |
| **SIR** | how much of the music the output rejected | higher |
| **SAR** | freedom from *invented* sound — musical noise, warbling, the "watery" complaint | higher |
| **musNoise** | added sparkly/robotic artifacts, which no energy measurement can see and the ear catches instantly | lower — but see the caveat below |
| **pumping** | whether the attenuation holds steady or breathes in and out | lower |
| **leak bursts** | worst-case and longest run of music breaking through. A config that is consistently mediocre beats one that is excellent except for a second of music blasting out | lower / shorter |
| **clicks** | digital glitches at block edges | lower |
| **stereo** | how much of the stereo image survived | 0 = intact, though see the note below |
| **lat ms** | delay you actually hear, including any buffering the model does internally | under ~120 ms, or lip-sync breaks |
| **RTF** | share of the 20 ms per-block CPU budget used | under ~0.8, with room for spikes |
| **alignment sensitivity** | whether the streaming path quietly loses state at block edges — a fault that *only* exists in realtime and that offline testing cannot see at all | more negative; judge against the other models, not against zero |

Three of those come with a catch worth knowing:

- **dSI-SDR weights music removal above keeping the voice**, which is the
  opposite of this project's priority. Use it to rank candidates, not to
  decide between them.
- **A very low musNoise is not automatically good.** A spectrum hollowed out
  by high-frequency loss scores low too — which is exactly why `gtcrn` posts
  the best number in the table below while cutting everything above 8 kHz.
- **0 stereo width is not the target.** Stereo spread that lived in the bands
  the model removed is supposed to leave with them.

### How the shipped models score

Whole corpus, 104 song/ratio pairs, references built with htdemucs. `music`
and `vocal` in dB; latency is what you hear, not what the model claims.

| model | music removed | vocal kept | dSI-SDR | SAR | musNoise | lat | RTF |
|---|---|---|---|---|---|---|---|
| **`dpdfnet_hr`** (default) | **−46.1** | −5.0 | **+4.19** | **4.40** | **1.02** | 50 ms | 0.32 |
| `dtln` | −20.6 | **−0.9** | +3.59 | 4.24 | 3.11 | 24 ms | **0.07** |
| `gtcrn` | −28.1 | −2.3 | +2.64 | 3.59 | −20.0 | **16 ms** | 0.09 |
| `dpdfnet` (16 kHz baseline) | −52.9 | −3.1 | **−0.28** | 0.27 | 7.15 | 50 ms | 0.15 |
| *do nothing* | 0.0 | — | 0.0 | 76.4 | 0.0 | 0 ms | — |
| *offline separator* | −42.5 | — | +24.3 | 22.9 | 25.1 | — | — |
| *theoretical best* | −55.0 | — | +18.1 | 16.9 | −3.0 | — | — |

Read across rather than down. **`dpdfnet_hr` wins overall and is the default**,
but it is also the *most* damaging to the voice of the four, and its win comes
from removing so much music that the scoring forgives that. **`dtln` barely
touches the voice at all** (−0.9 dB, the next best is more than twice as
damaging) and runs ~4.5× lighter, but leaves more than 25 dB more music
behind — it is the strongest candidate for an A/B if vocals sounding cut is
what bothers you.
**`dpdfnet` (the 16 kHz baseline) is actively harmful** — a negative dSI-SDR
means worse than not processing at all — despite posting the deepest raw
suppression number of anything here. Depth of suppression alone is not
quality.

Where the voice damage lands, same run:

| model | sub | low | mid | high | air |
|---|---|---|---|---|---|
| `dpdfnet_hr` | −4.8 | −4.4 | −7.0 | −8.6 | −9.1 |
| `dtln` | −1.1 | −0.6 | −1.1 | −1.5 | −59.7 |
| `gtcrn` | −1.8 | −1.8 | −3.0 | −2.9 | −63.4 |
| `dpdfnet` | −2.3 | −2.6 | −4.5 | −3.8 | −70.4 |

The air column is the giveaway: the three 16 kHz models delete everything
above 8 kHz *by construction*, so they trade breath and presence for their
lighter CPU cost. `dpdfnet_hr` keeps the top octave, but damages the voice
steadily more the higher you go — nearly double at the top what it is at the
bottom.

`speechdenoiser` is not in this comparison; it has only ever been judged by
ear.

### Named failures, counted per song

Averages hide the worst case — a defect that ruins one song in ten is exactly
what a mean is designed to bury. So each song is also tagged, and the tags are
counted rather than averaged, which turns a sweep result into "gains 1.4 dB of
suppression, but every regression is high-frequency vocal damage" instead of
just "worse".

| tag | what it means | fires above |
|---|---|---|
| `music-leak` / `leak-burst` / `long-burst` | music passing through — on average, in bursts, or in one long stretch | −6 dB / −3 dB / 400 ms |
| `vocal-loss` | the voice got pulled down | −4 dB |
| `hf-loss` / `hf-loss-4k` | 8–20 kHz or 4–8 kHz destroyed | −12 dB |
| `artifacts` | output contains sound from neither the voice nor the music | SAR under 3 dB |
| `musical-noise` | sparkly, robotic artifacts added | kurtosis rise over 15 |
| `pumping` | attenuation breathing in and out | 9 dB of unsteadiness |
| `clicks` | sample-level glitches | 200 000 |
| `stereo-collapse` | image flattened | −20 dB |
| `boundary-sensitive` | unusually sensitive to where block edges fall | −12 dB (compare peers, not zero) |
| `latency` / `cpu-risk` | beyond lip-sync tolerance / little realtime headroom | 120 ms / 0.8 RTF |

How often each fired on the run above:

| model | fires most often |
|---|---|
| `dpdfnet_hr` | clicks 57, long-burst 49, pumping 38, artifacts 38, **vocal-loss 36**, hf-loss-4k 30, hf-loss 28, musical-noise 26 |
| `dtln` | **hf-loss 104**, boundary-sensitive 44, artifacts 41, pumping 39, musical-noise 26, long-burst 19, vocal-loss 2 |
| `gtcrn` | **hf-loss 104**, pumping 49, artifacts 47, vocal-loss 18, long-burst 13, musical-noise 2 |
| `dpdfnet` | **hf-loss 104**, **clicks 79**, artifacts 73, musical-noise 40, pumping 28, vocal-loss 22 |

`stereo-collapse` fires 104/104 on all four and is not a model fault — the
chain emits mono unless the stereo rebuild is on.

Note how the tag counts and the averages disagree: `dpdfnet_hr` posts the best
musNoise average of the four (1.02) while still tripping the musical-noise tag
on a quarter of the songs. That is the whole reason both are reported.

### Trusting the numbers

Configs that no other config beats on *every* axis at once are marked `*` in
the report — nothing is collapsed into a ranking, because there is no
defensible exchange rate between "1 dB more music removed" and "40 ms more
delay". "% of offline separator" is shown for the same reason it isn't a
target: that separator isn't perfect either, so 95% of it can still sound
clearly worse.

Corpus choice matters as much as the metrics. A corpus of one kind of material
quietly turns this into "how well does it handle *this* mixture", so vary
genre, arrangement density, vocal loudness, dry versus reverberant, speech
versus singing, and stereo placement — and note that a **mono** source makes
every mid/side result meaningless, since there is no stereo information to
exploit.

Most importantly: every quality finding in this project so far has needed
confirmation by ear, and two of them contradicted a perfectly plausible
number. `--dump-audio` writes the processed audio out for exactly that reason.
**The numbers rank candidates and show direction; they do not settle
quality.**

## Packaging

```bash
scripts/build_deb.sh    # -> dist/music-assassin-live_<version>_<arch>.deb
sudo apt install dist/music-assassin-live_*.deb
```

Builds a onefile PyInstaller binary (bundles onnxruntime + tkinter) into a
minimal `.deb` with a desktop entry. Depends on `libportaudio2` (apt pulls
it in automatically — sounddevice looks it up via the system library
cache at runtime, so it must be a real installed package, not just
embedded in the binary). The
openly-licensed models (gtcrn, dpdfnet, dpdfnet_hr, dtln) are bundled into
`/usr/share/music-assassin-live/models` at build time and picked up
automatically — `apt install` and go, no manual step. `speechdenoiser` is
excluded (its upstream license is unresolved, see `models/README.md`); add
it yourself in `~/.local/share/music-assassin/models/` if you want it.
Windows and mobile packaging are not yet possible — see
`docs/ARCHITECTURE.md` §6 (Platform Reality Check).

## Status

- [x] Layer 1 — PipeWire trap-sink routing, crash recovery, hotplug watch
- [x] Layer 2 — streaming engine (dry-fallback, click-free toggle)
- [x] Layer 3 — GTCRN / DPDFNet / SpeechDenoiser processors (offline-tested)
- [x] Layer 4 — minimal toggle GUI + headless mode
- [x] Live end-to-end validation on real playback (BT sink; injected noise
      removed from output, speech passes, clean sink restore on exit)
- [ ] Tray icon, autostart, first-run model download
- [x] `.deb` packaging (`scripts/build_deb.sh`) — openly-licensed models bundled, ready to run after install
- [ ] PyInstaller → AppImage packaging
- [ ] Native LADSPA port (see docs/ARCHITECTURE.md Phase 3)

License: MIT
