# Music Assassin Live — Roadmap

Written 2026-08-13. Compiles four separate handover sessions (README
professionalization, Windows packaging investigation, release/publishing
status, settings persistence, top-level repo handover) into one plan.

**Supersedes** `HANDOVER.md` (repo root) and folds in
`packaging/windows/HANDOVER.md` — see §7 for what happens to those files.
For the original design rationale see `docs/ARCHITECTURE.md`; this document
is what's next, not what was intended.

---

## 1. State of play

### 1.1 The three repos

| Repo | Role | State |
|---|---|---|
| `music-assassin-live` (this) | Shippable realtime app: routing, engine, UI, packaging | v0.1.3 released; Linux works end-to-end |
| `Music-Assassin` | Research lab: offline separation, benchmarks, model sourcing | Active — latest `e9327e3`, v0.4.4 bundle |
| `music-assassin-mobile` | Android counterpart (Kotlin/Gradle, Shizuku) | Early scaffold; audio sink + inference not implemented |

**Correction to earlier handovers:** two of them state mobile is "OS-blocked,
not pursued." That was true when written, but `music-assassin-mobile` exists
and has a Shizuku-based path (Android 13+) that routes around the
`AudioPlaybackCapture` opt-in problem. Mobile is *unstaffed*, not *impossible*.
Don't re-run that research.

### 1.2 Shipped and released

- **v0.1.3** on GitHub (tags `v0.1.2`, `v0.1.3`). Linux/PipeWire only.
- Core pipeline live-validated on real Bluetooth hardware: trap-sink routing →
  20 ms duplex stream → ONNX model → real sink, with crash recovery and
  hotplug handling.
- **Five processors** + passthrough: `dpdfnet_hr` (default, 48 kHz),
  `gtcrn`, `dpdfnet`, `dtln`, `speechdenoiser`.
- **Two filters**: mid/side stereo pre-filter (off by default),
  ~20 Hz–20 kHz band-limit (on by default). Both live-toggleable.
- **Settings persistence** — `~/.config/music-assassin/settings.json`, restores
  pipeline, output device, mix, both mutes, both filter toggles, suppression
  limit. Deliberately does *not* restore the ON state.
- **`.deb` packaging** — `scripts/build_deb.sh`, bundles the four
  redistributable models, zero post-install steps.
- **Regression harness** — `tests/test_live_e2e.py` (two tiers, diffs against
  previous run), plus `test_processors_offline.py`, `test_routing_dry.py`.
  All manual; no CI.
- **Robustness fixes already landed**: stream-targeting bug, dry/wet echo
  (`a33f1e4`), ONNX thread over-subscription burning 100 W+ idle (`ae88ab3`),
  retarget debounce, stale-queue drain.

### 1.3 Merged to `main` 2026-08-15 — not pushed, not tagged

The working tree that had blocked three sessions is committed *and merged*.
`main` is at 0.1.4 and green on the full offline suite. Nothing has been
pushed; the 0.1.4 tag is deliberately held (§9, Phase 1).

| Branch | What it is | State |
|---|---|---|
| `fix/stream-recovery` | 0.1.4: `stream_ok`, callback try/except, soft limiter, 300 % wet boost, `tests/test_engine_recovery.py` | merged `42fe75d` |
| `feature/quality-harness` | `tests/bench_quality.py` + README quickstart | merged `42fe75d` |
| `fix/e2e-alignment` | `test_live_e2e.py` cross-correlation alignment + engine health counters | merged `42fe75d` |
| `refactor/routing-backend` | `RoutingBackend` protocol + `PipeWireBackend`; engine decoupled from PipeWire | merged `92aeb0a` |
| `fix/dpdfnet-norm-init` | seed DPDFNet state from ONNX metadata (§2.2) | merged `9877b0c` — read §2.2, it changes the model's behaviour |
| `docs/roadmap` | this document | merged `01f6acb` |
| `feature/windows-packaging` | installer scaffolding, `.ico`, `build_windows.bat`, `.gitignore` rule | **not merged** — see D3 |

Pre-merge state is tagged `pre-merge-backup-20260815` (`06f0ac8`).

**On the "merge order" this section used to prescribe:** the first three
branches were a *stack*, not siblings — `fix/e2e-alignment` already contained
`feature/quality-harness`, which already contained `fix/stream-recovery`. One
merge took all three, in order, with no manual sequencing. The underlying
dependency is real (`bench_quality.py` imports `_soft_limit` from the engine,
`test_live_e2e.py` imports `estimate_lag` from the harness) but it was already
expressed in the commit graph. Earlier revisions of this section and of
`HANDOVER.md` also stated the sequence in **opposite directions**, so anyone
following one of them literally would have merged backwards. Fixed here for the
record. `refactor/routing-backend` was independent of that stack but touches
`engine.py`, `ui/app.py` and `__main__.py`; it merged clean.

All four code branches had been **test-merged and verified green** before the
real merge (engine recovery, all five processors offline, routing dry,
imports), and the real merge reproduced that result. Two defects were found
only by doing that and are already fixed:

- `AudioEngine(processor, backend)` was made a required argument by the
  routing refactor, which broke `test_engine_recovery.py`. Git merged it with
  **zero textual conflicts** — a semantic break git cannot see. `backend` is
  now optional, enforced in `start()` where it actually applies.
- The 0.1.4 work was verified for the first time: the callback-crash
  containment, `stream_ok` reporting and limiter guarantees all now have a
  permanent test that needs no audio hardware.

`dist/music-assassin-live_0.1.4_amd64.deb` still predates all of this and has
never been re-verified — rebuild before trusting it.

### 1.4 Windows packaging — committed to its own branch

On `feature/windows-packaging`. Deliberately not on `main`: **the blocker is
not the installer** — `routing.py` spoke only PipeWire, so there was no WASAPI
capture path and no way to set VB-CABLE as default output; a packaged app
would not have run. `refactor/routing-backend` now provides the seam
(D2 below), so the remaining work is a real Windows backend (D3). Nothing in
this branch has been run or compiled. See §7.

---

## 2. The finding that reframes everything

From the research repo (`docs/REALTIME_FILTER_RESEARCH.md`, confirmed against
this app's own live measurements):

> **Speech-enhancement models do not remove music.** Every enhancer shipped
> here — DPDFNet (all variants), GTCRN, DTLN, SpeechDenoiser, and
> DeepFilterNet upstream — attenuates *noise* by −29 to −63 dB but passes
> prominent or vocal-heavy *music* at −0.4 to −1.7 dB. They classify music as
> speech.

All five shipped pipelines are the wrong class of model for the headline
feature. This has three consequences that drive the whole roadmap:

1. **Swapping in another enhancer will not help.** Every remaining candidate
   has been measured and is dominated (see §11). Stop looking there.
2. **The mid/side pre-filter is the only non-model lever with measured music
   suppression** — and it is ~1.8 dB at `g⁴`. Real, free, worth shipping on
   by default if it survives a by-ear check. Not "music removed."
3. **A realtime source separator is not one option among three. It is the
   only path to the product promise.** Everything else on this roadmap makes
   the app good; this is what makes it *work*.

### 2.1 One important qualification — `dpdfnet_hr` is level-dependent

Measured 2026-08-14. The default model does **not** have a single
characteristic suppression figure, because its behaviour changes with input
level far more than any other shipped model:

| input RMS | `dpdfnet_hr` music | `gtcrn` music | `speechdenoiser` music |
|---|---|---|---|
| 0.01 | **−29.9 dB** | −1.0 | −1.4 |
| 0.05 | **−4.6 dB** | −0.6 | −1.2 |
| 0.1 | **−1.3 dB** | −0.7 | −1.3 |
| 0.5 | **−1.2 dB** | −2.6 | −1.7 |

~36 dB of swing, against ~2 dB for the others across the same three orders of
magnitude. A single gain multiplier on the unmodified regression fixture
reproduces the whole thing (×1 → −30.2 dB, ×8 → −1.3 dB).

**Root cause**, verified in the model files: both DPDFNet exports carry
`erb_norm_init` + `spec_norm_init` feature-normalization calibration in ONNX
custom metadata (hr: 481 + 96 floats). `processors/dpdfnet.py`'s `reset()`
does `np.zeros(state_size)` and discards it. Its own docstring acknowledged
this but judged it "a brief over/under-suppression transient… acceptable…
fix if it ever matters" — that assessment was **wrong**: it produces level-
and history-dependence, not a transient. `gtcrn`/`speechdenoiser`/`dtln` carry
no such metadata, which is exactly why only DPDFNet misbehaves. Fix in
progress on `fix/dpdfnet-norm-init` (A5).

Separately quantified: **19.3 dB** of extra music suppression comes purely
from state carryover (3 s of loud noise preceding the music phase). Real, but
it applies to every measurement path equally, so it is a confound that makes
`bench_offline`'s figures hard to interpret — not an explanation for any
path-to-path difference.

### 2.2 A5 landed — and it overturns what we believed about the default model

`fix/dpdfnet-norm-init` seeds the state correctly (layout taken from
sherpa-onnx's `GetInitState()`: `erb_norm_init` at offset 0, `spec_norm_init`
at `erb_norm_state_size`, zeros elsewhere). It works — the level swing
collapses from ~36 dB to **≤0.17 dB**:

| input RMS | before | after |
|---|---|---|
| 0.001 | −37.0 dB | −0.72 dB |
| 0.01 | −29.9 dB | −0.70 dB |
| 0.1 | −1.3 dB | −0.71 dB |
| 0.5 | −1.2 dB | −0.87 dB |

**The uncomfortable part: the −30 dB was never music removal.** It was
indiscriminate over-suppression caused by the mis-seeded normalizer. Measured
after the fix, vocals-alone vs music-alone at matched level:

| model | vocals | music | separation |
|---|---|---|---|
| `dpdfnet_hr` | −0.28 dB | −1.11 dB | **0.83 dB** |
| `gtcrn` | −0.21 dB | −1.03 dB | **0.83 dB** |
| `speechdenoiser` | −0.66 dB | −1.61 dB | 0.95 dB |
| `dtln` | −0.10 dB | −1.30 dB | 1.20 dB |

`dpdfnet_hr` discriminates vocals from music exactly as well as `gtcrn` —
i.e. barely, and slightly worse than `dtln`. **This disproves the standing
belief** (recorded in project memory since 2026-07-24) that `dpdfnet_hr` "may
actually be doing real music-vs-voice separation rather than generic noise
suppression, which would partially resolve the open research problem." It was
not. §2's finding therefore stands *unqualified*: no shipped model removes
music, all four are ~1 dB separators, and A1 is the only path.

**Practical impact on the *music* path is smaller than the table implies.** The
dramatic change is at low input levels; at realistic listening levels
(RMS 0.05–0.1) the model was already only giving −1.3 to −4.6 dB.

### 2.3 …but the fix is a much bigger win than §2.2 first concluded

Measured 2026-08-15 on merged `main`, offline tier, against the stored
2026-08-14 pre-fix report. The models that carry no ONNX norm metadata are the
control, and they are **unchanged to the decimal** — which is what establishes
that the fix touched DPDFNet and nothing else:

| model | phase | before | after | delta |
|---|---|---|---|---|
| `dpdfnet_hr` | noise_only | −10.8 dB | **−59.5 dB** | −48.7 |
| `dpdfnet` | noise_only | −44.1 dB | **−71.7 dB** | −27.6 |
| `dpdfnet_hr` | music_only | −30.2 dB | −0.3 dB | +29.9 |
| `gtcrn` / `dtln` / `speechdenoiser` | all three | — | — | **±0.0** |

§2.2 characterised the fix's user-visible effect as "quiet passages stop being
crushed." That understated it. The same defect that manufactured the fake music
suppression was also **crippling the model at its actual job**: `dpdfnet_hr`
was the *worst* noise suppressor of the five (−10.8 dB, worse than every other
model, which is why its own noise score looked anomalous back in July), and is
now second only to `dpdfnet`. Both DPDFNet variants got substantially better at
denoising.

So the fix trades away a suppression figure that was never real for a large
gain in the thing the model is genuinely for. That is a clear net win — it just
isn't a win on the music axis, which remains A1's problem alone.

**Consequences.**
- Keep `dpdfnet_hr` as default, and it is now a stronger default than §2.2
  implied: 48 kHz-native, so it avoids the resample round-trip and preserves
  high frequencies (measured: −4.0 dB in the 8–20 kHz band vs `gtcrn`'s
  −49.0 dB), **and** it is now a top-tier denoiser. Still not because it removes
  music better — it does not.
- **The by-ear model comparison must be redone.** The 2026-07-24 listening
  judgement that picked this default was made against the broken model.
- Any future model comparison must control for input level, or it measures the
  fixture's gain staging rather than the model.

---

## 3. Workstream A0 — Quality harness ✅ built 2026-08-13

`tests/bench_quality.py`. Answers "did that change make it better?" with
numbers, for any combination of model / mid-side / band-limit / mix / atten
params — the tool every item in Workstreams A and B below now runs through
instead of a re-listen per change.

**Design**: comparing live output to an offline separator's output directly
only measures agreement with that separator, artifacts included, and can't
tell "music leaked through" apart from "vocals got damaged" — those need
opposite fixes. Instead the offline separator (mdx_extra via demucs, run
out-of-process since the app is deliberately torch-free) is used to
*manufacture ground truth*: split a real clip into vocals/music stems, remix
at a known ratio, so the target and the interference are both known exactly.
The same separator run again on the remix gives a **ceiling** — the best this
class of model does with no realtime constraint. Every metric is then a
three-way read: input (do nothing) → live pipeline → offline ceiling, so a
number like "gtcrn+ms4 reaches 91% of ceiling on music suppression" is a
believable claim, not an abstract score.

Reports SI-SDR/SDR/SIR/SAR (interference vs. artifact damage, separated —
the actual point, since a config can buy suppression by trashing SAR), gap-only
music suppression (measured only in vocal-silent frames, so no vocal energy
confounds it), per-band vocal damage (exposes 16 kHz-native models' 8 kHz
ceiling), a spectral-kurtosis musical-noise indicator, click detection, and
measured latency/RTF. `--dump-audio` writes every rendered wav for the by-ear
check the harness's own docstring insists still has the final word — two
prior findings in this project (synthetic tones scoring like noise at −47 dB;
aggressive mid/side exponents sounding worse than their energy ratio suggests)
are exactly the kind of thing a metric alone would have gotten wrong.

Live processing is driven block-by-block through the same code the engine
uses (`MidSideFilter`, `BandlimitFilter`, the soxr resamplers, `_soft_limit`),
not the actual PortAudio/PipeWire path — deterministic and hardware-free,
which is what makes a parameter sweep practical. Hardware-path validation
stays `test_live_e2e.py`'s job.

**First measured result (1 clip, htdemucs ceiling — indicative, not
conclusive).** The ladder reads: input 0 dB → live configs 4.3–5.0 dB →
offline ceiling 16.3 dB → **oracle 16.5 dB** (dSI-SDR). The offline separator
sits at ~98% of the theoretical mask-based bound, which says the offline model
is *not* the limiting factor on this material and the mixture is *not*
intrinsically unseparable — essentially the entire 11 dB shortfall is the
realtime path. That is the one term of the three this repo controls directly,
and it is what A1 is chasing. Two further findings, both invisible to any
aggregate score: `gtcrn` loses **−49 dB above 8 kHz** (its 16 kHz ceiling,
vs −4 dB for 48 kHz-native `dpdfnet_hr`), and every config reads
**−122 dB stereo width** — the mono collapse of B3, confirmed as total at
100% wet. Widen the corpus before treating any of this as settled.

Building the reference corpus needs `demucs` (torch), which the app itself
never depends on — point `--demucs-python` at
`~/Documents/venvs/assassin_venv_v0.4.4_cpu` (or any venv from the research
repo's `setup_v044.sh`) rather than adding torch to this repo's own venv.

### 3.1 The stereo corpus ✅ built 2026-08-15

Every measurement before this date used **one 15-second mono clip**, which made
every mid/side and stereo-width number meaningless by construction. Three
handovers recorded this as blocked on the user for source material. It was not:
`~/Music/Acapella/` holds 91 stereo files, 61 of them full mixes (the other 30
are vocal-only extractions by filename, and would give demucs a degenerate
music stem — excluded).

**Check the side channel before trusting "stereo".** All 61 mixes are 2-channel
at the container level, but measured side/mid RMS across the first 30 s spans
−0.8 dB to −110.8 dB, and **7 are effectively dual-mono** (−49 dB or below —
Naruto ED 12 is −110.8 dB, i.e. bit-identical channels). Those 7 behave exactly
like the old mono clip. Including them silently would have diluted the very
result the corpus was built to produce. Median across the rest is −15.1 dB;
50 of 61 sit above −20 dB.

The corpus as built (`~/.local/state/music-assassin/bench/corpus`), 30 s per
clip, remixed at ratios 1.0 and 2.0:

| Category | Clips | Purpose |
|---|---|---|
| `anime_op_stereo` | 18 | tuning set, side/mid spread −0.8 … −16.7 dB |
| `anime_op_stereo` (holdout) | 5 | reserved validation, never tune against it |
| `dual_mono_control` | 3 | **expected-negative** — mid/side must do nothing here |

The `dual_mono_control` set earns its place: it is the only thing that
distinguishes "mid/side helped" from "the harness reports a number regardless
of whether a side channel exists."

**Caveat on generalisation.** This is one genre family (anime OP/ED — dense,
loud, wide, largely female vocals). By `bench_quality.py`'s own docstring, a
single-category corpus silently answers "how good is this on *that*". It is
however the user's actual listening material, which makes it the right corpus
for choosing shipped *defaults* and the wrong one for claiming general
performance. Add categories (sparse/acoustic, spoken dialogue over score,
hard-panned) before quoting any of it as a general result.

---

## 4. Workstream A — De-musicing quality: models

### A1. Realtime separator spike — sherpa-onnx Spleeter 2-stem int8 ⭐ highest leverage

The only separator measured on this machine that is genuinely realtime-capable:
**RTF 0.067** at 1 thread (~15× headroom), ~600 MB peak RSS. It is the single
biggest quality lever available. Once it exists as a `StreamProcessor`, the
latency-vs-quality curve below is exactly what `tests/bench_quality.py` (§3)
is built to produce — sweep `--sweep model=spleeter_XXXms` variants against
the same ground-truth corpus already used for the enhancers, no new tooling
needed.

Three real obstacles, all of which need to be answered by the spike, not
assumed away:

- **It is an *offline* chunked API** (`OfflineSourceSeparation`), not a
  streaming one. RTF headroom is not latency: a 1 s chunk costs 67 ms of
  compute but ~1 s of buffering. **Measure quality as a function of chunk
  size** — Spleeter is non-causal, so short chunks degrade at boundaries.
  The output of this spike should be a latency-vs-quality curve, not a
  yes/no.
- **It requires stereo `(2, N)` input** and crashes on mono. The current
  `StreamProcessor` contract is mono-in/mono-out, and `AudioEngine._work()`
  downmixes to mono before the processor ever sees the block. This needs a
  contract extension (a `wants_stereo` flag on the processor, engine passes
  the raw stereo block through). Not a large change, but it is an API change
  — do it deliberately, not as a hack inside one processor.
- **~600 MB RAM vs ~40 MB today.** Fine on a desktop, and worth it, but it
  changes the app's resource story and should be surfaced in the UI.

Do the spike on a branch (`feat/separator-spike`). Ship it as an *additional*
pipeline entry, not a replacement for `dpdfnet_hr`, so the enhancers stay
available for the dialogue-cleanup case they're actually good at.

### A2. HS-TasNet weights (research repo, background)

The architecturally correct answer: 23 ms latency by design, genuinely
streaming. No public weights exist. lucidrains' MIT implementation is mature
(training code + realtime sounddevice example); training on MusDB via a free
Colab/Kaggle GPU is the realistic route. This is a **research-repo project**,
runs in parallel, and drops into this app through the same processor interface
if it produces weights. RT-STT still has no code release — check quarterly, do
not wait on it.

### A3. Make the model registry data-driven

`docs/ARCHITECTURE.md` §3 promises "a new model needs no app code changes."
That is not true today: `assassin_live/processors/__init__.py` hardcodes
`_MODEL_FILES` and an if-chain in `create()`, so every new model needs a new
processor class *and* two registry edits. `model_card.json` files ship next to
the weights and the app never reads them.

Fix: have `available()`/`create()` read the model cards in `models_dir()`
(name, file(s), sample rate, and which processor class implements it), so
dropping a card + weights into the models dir is enough. This directly serves
"adding/editing models" and is a prerequisite for a first-run model downloader
(C6) and for release-asset model distribution. Half a day.

### A4. Music-subtraction architecture — parked, revisit on a fast model

Predicting the *music* stem and computing `vocals = mix − music` preserves SFX
(instrumental models don't classify explosions as music). Best candidate found
is `UVR_MDXNET_KARA_2` — RTF ~0.84, 2.8 GB. Not realtime, not shippable.
Keep on the research side; revisit only if a fast instrumental-prediction model
appears.

### A5. Seed DPDFNet state from its ONNX normalization metadata ✅ done

The defect behind §2.1. `processors/dpdfnet.py` starts the model state from
zeros, discarding the `erb_norm_init`/`spec_norm_init` calibration both DPDFNet
exports ship in their ONNX metadata. Fix: read the metadata and seed the flat
state vector at the correct offsets (sherpa-onnx does exactly this — port its
layout), falling back to zeros for models without it.

Done on `fix/dpdfnet-norm-init` (1 commit, not merged). Layout taken from
sherpa-onnx's own `GetInitState()` rather than inferred. Falls back to zeros
for models without the metadata, so `gtcrn`/`speechdenoiser`/`dtln` are
provably unchanged (verified byte-identical output before/after). All five
processors still pass `test_processors_offline.py`.

Results and their consequences are in §2.2 — read that before acting on any
older suppression number for this model, because the fix invalidates them all.

### A6. Report input level in `bench_offline()` ✅ done 2026-08-15

`tests/test_live_e2e.py`'s offline tier prints a dB attenuation per phase with
no indication of the input level that produced it. For `dpdfnet_hr` the level
matters more than anything else being measured (§2.1), and printing the per-
phase input RMS alongside the figure would have made a multi-hour
investigation obvious at a glance. Cheap, and it stops the same confusion
recurring.

Both halves are implemented. The table now prints a `fresh state` row per model
(each phase re-run from a reset state) and a single `input RMS` row under the
table — the level is a property of the fixture, not of any model, so printing it
five times would only pad the output. The fresh-state variant was worth having:
the live app genuinely does process continuously, so carry-over is realistic,
but 19.3 dB of it makes a per-phase number mean something other than "how the
model treats this content", and the two rows now separate those.

It paid for itself on its first run — the pre/post comparison in §2.3 came
straight out of it.

---

## 5. Workstream B — De-musicing quality: pre/post filters

### B1. Quantitative + by-ear A/B: mid/side prefilter on vs. off ⭐ do first

Measured in research: `g⁴` gives a 16.8× voice/music ratio vs 9.2× for plain
mono downmix, at −0.01 dB vocal loss. It is shipped, working, off by default,
and **has never been evaluated against real content**. It is the most likely
partial answer to the recurring "vocals get cut" complaint, and — now that
`tests/bench_quality.py` exists (§3 above) — it costs one sweep to check:
`--sweep midside=off,on --sweep midside_exp=1,2,4,6`, then confirm the winner
by ear with `--dump-audio`. If it holds up, flip the default to ON.

Caveat the research itself flags, and the harness's own `musical_noise` /
`gap_roughness_db` columns exist to catch: aggressive exponents can introduce
"musical noise" artifacts that a plain suppression-energy number won't show.
The by-ear pass over `--dump-audio` output is still the real test — the
numbers rank candidates, they don't settle quality (see the harness's own
docstring on this).

### B2. Expose the mid/side exponent as a control

`MidSideFilter(exponent=4.0)` is hardcoded. Given B1's artifact caveat, the
useful shape is a slider (1–6, default 4) rather than a fixed value — the
right exponent is content-dependent, and users can hear what a metric can't.
Small change; only worth doing after B1 says the filter is worth keeping.

### B3. Stop collapsing output to mono ⭐ unlisted quality regression

`AudioEngine._callback_body()` writes the mono wet signal to both output
channels (`wet[:,0] = wet[:,1] = wet_mono`). **At 100 % mix the entire system
output is mono.** For a system-wide filter that users leave on while watching
video, losing the stereo image is a large, constant perceptual cost that no
handover has recorded.

Options, cheapest first:
- Apply the model's per-band gain to the original stereo pair instead of
  emitting mono (requires the processor to expose a mask, not just audio —
  not all do).
- Re-inject the original side channel at reduced gain after processing:
  `L = wet + k·S`, `R = wet − k·S`. Cheap, approximate, restores width.
  Conflicts conceptually with mid/side prefiltering, which is *removing* the
  side channel on purpose — so these two need to be designed together.
- Accept mono, but say so in the UI.

### B4. Lookahead + crossfade smoothing — gated on a question

The interrupted "sliding window continuous filtering" thread. The models
already do overlapping WOLA framing with recurrent state, so processing is not
literally per-block-independent today; the analysis window is just tiny (20 ms)
and baked into the checkpoint. The only lever on the app side is a
lookahead + crossfade stage after the model (~50–150 ms buffer, trades latency
for smoothness).

**Do not build this yet.** First confirm the symptom still reproduces — the
dry/wet echo bug fixed in `a33f1e4` may have been the actual cause of the
"choppy/watery" complaint that prompted the question. See §10, Q1.

### B5. Post-separator cleanup chain (only relevant after A1)

Once a separator is in the path, the enhancers stop being the primary filter
and become what the research already validated them as: *post-separation
cleanup for quiet residuals*. The pipeline picker should then offer
combinations (separator → enhancer), not just single models. Design this when
A1 produces a working separator, not before.

---

## 6. Workstream C — Seamless integration & UX

This is where the app currently feels like a prototype rather than a product.

### C1. Gapless output-device switching ⭐ the named pain point

Today, changing output device — from the app's dropdown *or* from the system
audio picker — calls `AudioEngine.retarget()`, which is `stop()` + `start()`:
full PortAudio teardown, `sd._terminate()/_initialize()`, processor `reset()`,
all buffers cleared, then `pin_process_streams()` polls the PipeWire graph for
up to 3 s. The result is a multi-second dropout and a reset model state on
every device change.

The real fix: **the capture side and the processor are unaffected by an output
change — only the playback endpoint moves.** PipeWire can relink a *running*
stream. Spike whether setting `target.object` metadata on the live playback
node (the same `pw-metadata` call `pin_process_streams()` already makes) is
enough to move output without closing the stream at all. If it is, the entire
teardown path disappears for this case.

Fallbacks if that doesn't work, in order:
- Keep processor state and buffers alive across retarget (don't `reset()`).
- Ramp wet gain to 0 → switch → ramp back in, so the transition is a fade
  rather than a glitch.
- Pre-warm the new stream before tearing down the old one.

Note the `PULSE_SINK`/`_reinit_portaudio()` machinery exists *only* because
pipewire-alsa ignores those env vars; if metadata retargeting works on a live
stream, that whole path becomes dead weight.

### C2. Forward volume keys to the real sink

With the trap sink as system default, the volume keys and system slider scale
the *captured* signal, not the output — so turning the volume down changes what
the model sees, and the mix slider's boost then amplifies a quieter input.
Already flagged in `ARCHITECTURE.md` §4 as "acceptable v1," and it is now the
second-most visible integration wart after C1.

Fix: pin the trap sink at 100 % and mirror volume/mute changes onto the real
hardware sink.

**Promoted 2026-08-14 — this is no longer only a UX wart, it is a suspected
measurement confound.** Given §2.1 (the default model's output depends heavily
on input level), an unpinned trap-sink volume means the system volume slider
silently changes what the model is fed, and therefore what every hardware-tier
measurement reports. That independently explains why `test_live_e2e.py`'s
hardware tier recorded `noise_only` swinging **−55.7 → −21.7 dB across two
identical runs** — an uncontrolled variable, not model nondeterminism. Until
this is fixed the hardware tier has a free variable in it and its absolute
numbers should not be compared across runs. Caveat: reproducing the full
offline-vs-hardware gap needed ~+18–30 dB, more than typical OS volume range,
so this is likely one contributor among several rather than the whole story.

### C3. Stop fighting the system output picker

`RoutingSession.check()` re-asserts the trap sink as default within 1 s of
anything stealing it, then waits out a 1.5 s debounce before treating the
change as a real device switch. Net effect when a user picks a device in GNOME
quick settings: the selection visibly flickers back, then 1.5–2.5 s later the
app retargets — with no feedback that it understood. Meanwhile
`_refresh_outputs()` runs every second and can stomp the dropdown selection.

Treat "default sink changed to X" as an explicit user intent: update the
dropdown to X immediately, show "switching output → X", then retarget. Same
mechanism, coherent story.

### C4. Human-readable status

The status line is a debug dump (`blocks: 48213  fallbacks: 0  xruns: 2`).
Users need state and health: "Filtering → Soundcore Q20i · 12 ms latency ·
healthy". Keep the counters behind a details toggle.

### C5. Tray icon, autostart, and the ON-state question

Still open from the original Phase 2. Settings persistence deliberately does
not restore the ON state — a defensible default for something that takes over
system audio, but it means every launch needs a manual toggle. The right
answer is probably an explicit opt-in ("start filtering on launch") plus a tray
icon so the app isn't a window you have to keep around. See §10, Q2.

### C6. First-run experience outside the `.deb`

`apt install` bundles models and works immediately. A `pip install` / source
checkout with an empty models dir silently shows a pipeline dropdown containing
only `passthrough`, with no explanation. Add first-run detection + a model
download (depends on A3's data-driven registry and on models being published as
release assets).

### C7. Surface latency

Nothing in the UI states the added latency (~45–70 ms by design). For anyone
watching video this is the first thing they'll want to know, and it becomes
critical if A1 lands with a chunk-size-driven latency budget.

---

## 7. Workstream D — Platform, branching, packaging

### D1. Branch strategy ✅ done 2026-08-13

One branch per change, `fix/` or `feature/` prefixed, rather than one big
platform split. The tree that had blocked three sessions is now committed and
`main` is clean.

| Branch | Contents | Merges when |
|---|---|---|
| `main` | Linux app, releases, tags | — |
| `fix/stream-recovery` | 0.1.4: `stream_ok`, callback try/except, soft limiter, 300% wet boost, `tests/test_engine_recovery.py` | Ready now — tested, suite green |
| `feature/quality-harness` | `tests/bench_quality.py` + README quickstart | Ready now |
| `fix/e2e-alignment` | `test_live_e2e.py` cross-correlation alignment + engine counters | With/after the harness |
| `feature/windows-packaging` | `packaging/windows/`, `.ico`, `build_windows.bat`, `.gitignore` rule | Windows app actually runs (D3) |
| `docs/roadmap` | this document | Anytime |

**These are not all independent — the first three are a chain**, and it is a
real semantic dependency, not an accident of ordering:
`fix/e2e-alignment` → `feature/quality-harness` → `fix/stream-recovery`.
The harness imports `_soft_limit` from the engine because it must mirror the
engine's mix path *exactly* to be meaningful, and that limiter only exists on
the stream-recovery branch; `test_live_e2e.py` in turn imports `estimate_lag`
from the harness rather than carrying a second copy of alignment logic, since
two copies is precisely how the original alignment bug survived unnoticed.
Merge them in that order, or squash the chain.

Worth noting for future splits: `python -m py_compile` does **not** catch this
class of breakage — it checks syntax only, and the missing-import failure
appeared solely under a real `import`. Verify split branches by importing or
running them, not by compiling them.

Mobile is already a separate *repo*, which is the right call for a different
language and build system. Windows shares this repo's Python codebase, so a
branch is the correct granularity, not a fourth repo.

### D2. Extract a routing backend interface — before writing Windows code

`routing.py` is Linux-only with no abstraction seam, and `engine.py` reaches
into PipeWire directly (`PULSE_SOURCE`/`PULSE_SINK`, `pin_process_streams`).
If the Windows branch starts by writing WASAPI code alongside this, the two
platforms will diverge messily and the branch will never merge cleanly.

Do this small refactor on `main` first: a `RoutingBackend` protocol
(`enable() → real sink`, `disable()`, `check() → event`, `monitor_source`),
with `PipeWireBackend` as the only implementation. Then the Windows branch adds
one file instead of forking three.

### D3. Windows routing backend — the actual unblock

WASAPI loopback capture (`sounddevice` already supports it) + default-device
switching via `pycaw` (MIT). Days, not hours. Scope it as its own task.
Everything already written in `packaging/windows/` is downstream of this.

### D4. Windows licensing decision — close it out

VB-Audio's terms require a distribution agreement above personal-use volume
(>10 units), and silently automating the install doesn't change that. Either
send the drafted `vb-audio-permission-email.md`, or decide to ship the
"prompt the user to install VB-CABLE themselves" fallback permanently and stop
treating it as open. Also: pin Inno Setup to **6.4.3** (6.5.0+ added a paid
commercial tier). Synchronous Audio Router was investigated and rejected — see
the Windows handover, don't redo it.

### D5. Remaining Linux packaging

AppImage (PyInstaller → AppImage) and the native LADSPA port
(`ARCHITECTURE.md` Phase 3) are both still open. LADSPA is the long-term
"real product" path — 10–20 ms latency, no Python in the audio path, and the
entry ticket to a Windows APO later. Not near-term.

### D6. Handover file disposition

- `HANDOVER.md` (root) → delete; superseded by this document.
- `packaging/windows/HANDOVER.md` → move to the `platform/windows` branch as
  is. It's accurate and still the best record of that investigation.

---

## 8. Workstream E — Infrastructure & hygiene

- **E1. CI.** GitHub Actions running `test_processors_offline.py` +
  `test_routing_dry.py` on push. Enables a real build-status badge (the README
  badge row deliberately omits one today because nothing backs it). The
  research repo already added a Windows CI workflow in `230a75f` — copy the
  pattern.
- **E2. Hardware-tier alignment — FIXED 2026-08-13; a second defect found
  underneath it, still open.**
  The onset-threshold alignment is replaced by whole-clip cross-correlation
  (`estimate_lag`, shared with `bench_quality.py` rather than duplicated —
  two copies of this logic is how the original bug survived). The old
  algorithm was demonstrably wrong in *both* directions on a reconstruction
  of the failure: **+500 ms** when the fixture's quiet lead-in pushed the
  first above-threshold sample late, and **−400 ms** when the recorder's own
  noise floor tripped the threshold immediately. The replacement is
  sample-exact on both. The tier now also reports engine counters
  (`fallback_pct`, xruns, callback_errors, worker ms) so a weak attenuation
  reading can be told apart from the engine having silently emitted dry
  audio — previously indistinguishable.

  **Still open — offline and hardware disagree about `dpdfnet_hr`, the
  default model.** Offline reports music_only ≈ **−30 dB** (continuous
  state) / −10.9 dB (fresh state); through the engine the same model and
  fixture give ≈ **−0.4 dB**. Ruled out: alignment (aligned vs unaligned
  agree within 0.1 dB), and dry fallback (0.1%, 1/1050 blocks, no xruns, no
  callback errors — the engine is healthy). Also observed: `noise_only`
  swung **−55.7 → −21.7 dB across two identical hardware runs**, so that
  tier has real run-to-run variance on top of the disagreement.
  Leading hypothesis, untested: `dpdfnet_hr` is level-sensitive (enhancers
  are commonly trained at particular input levels) and the two paths present
  it with different levels. Next step is a level sweep through both paths
  before trusting either number. **Resolved 2026-08-14 — see §2.1/§2.2:** the
  cause was a real defect (discarded ONNX normalization metadata), now fixed
  on `fix/dpdfnet-norm-init`. The hardware tier still has one uncontrolled
  variable left in it, though — the unpinned trap-sink volume (C2) — so its
  absolute numbers should not be compared across runs until that is fixed.
- **E3. Resolve `speechdenoiser`'s license** (upstream has no license file) or
  re-export from dual-licensed DeepFilterNet3. Until then it must not ship as
  a release asset — local dev only.
- **E4. Re-verify `dist/music-assassin-live_0.1.4_amd64.deb`** — built from an
  uncommitted tree, never independently checked, and an earlier `.deb` in that
  same directory was found corrupted mid-session. Extract and run it before
  trusting it.
- **E5. Publish models as release assets** so `import_models.py`'s cross-repo
  dependency isn't the only distribution path (prerequisite for C6).

---

## 9. Sequenced plan

### Phase 1 — Clear the tree ✅ mostly done

1. ~~Test 0.1.4 stream recovery; run the regression suite; commit.~~ Done —
   `fix/stream-recovery`, with a permanent hardware-free test.
2. ~~Get the Windows scaffolding out of the shared tree.~~ Done —
   `feature/windows-packaging` (one branch per change, see D1).
3. ~~Fix the hardware tier's alignment.~~ Done — `fix/e2e-alignment` (E2).
4. ~~Extract the routing backend seam.~~ Done — `refactor/routing-backend` (D2).
5. ~~Root-cause the `dpdfnet_hr` measurement contradiction.~~ Done — it was a
   real defect, fixed on `fix/dpdfnet-norm-init` (§2.1, §2.2, A5).

6. ~~**Merge the branches to `main`.**~~ Done 2026-08-15 (§1.3). Offline suite
   green on the merged tree. **The 0.1.4 tag is still held** until item 7, so
   release notes don't describe the default model using numbers §2.2
   invalidated.
7. ~~**Build a varied stereo corpus** for `bench_quality.py`.~~ Done
   2026-08-15 — see §3.1. This was recorded as blocked on the user for source
   material; it was not. `~/Music/Acapella/` had 61 usable full mixes.

**Remaining in this phase:**

8. **Redo the by-ear model comparison (B1).** The last thing gating the 0.1.4
   tag, and the only item here that needs the user rather than the machine: the
   existing default was chosen by ear against a model that was misbehaving, and
   §2.2 shows all four enhancers are within ~0.4 dB of each other on
   vocal/music separation. The corpus (item 7) now also lets the same pass
   settle mid/side.
9. ~~**Rebuild the `.deb`** from merged `main` (E4).~~ Done 2026-08-15 —
   0.1.4, binary smoke-tested, four redistributable models bundled
   (`speechdenoiser` correctly excluded, license still unresolved). Ships as
   soon as item 8 clears the tag.

### Phase 2 — Make it feel like a product (1–2 weeks)

9. **C1 — gapless output switching.** The named pain point. Start with the
   live-`pw-metadata`-retarget spike.
10. **C2 — volume-key forwarding.** Promoted: it is now a suspected
    measurement confound, not only a UX wart (see C2).
11. **A6 — report input RMS in `bench_offline()`.** Small, and it prevents a
    repeat of the §2.1 investigation.
12. **C3 — coherent system-picker behavior**, **C4 — human status line.**
13. **E1 — CI** (scoped smaller than it looks; see E1).

### Phase 3 — Make it actually remove music (weeks)

10. **A1 — separator spike** on `feat/separator-spike`: stereo processor
    contract, chunked Spleeter wrapper, latency-vs-quality curve.
11. **A3 — data-driven model registry** (also unblocks C6, E5).
12. **B3 — stereo output**, designed alongside whatever A1 produces.
13. **A2 — HS-TasNet training** running in the research repo in parallel
    throughout.

### Phase 4 — Second platform (parallel, own branch)

14. **D4 — close the VB-Audio licensing question.**
15. **D3 — Windows routing backend**, then a real Windows build environment,
    then validate the `.iss` script, then merge.

### Deferred

C5 (tray/autostart), C6 (first-run download), D5 (AppImage, LADSPA), A4
(music-subtraction), B5 (post-separator chain).

---

## 10. Open decisions — need an answer before the dependent work starts

**Q1 (blocks B4).** What symptom prompted the "sliding window / continuous
filtering" question — choppy/warbling artifacts, "not aggressive enough," or
general exploration? These need different, non-overlapping fixes. And: does
the original symptom still reproduce after the echo fix in `a33f1e4`?

**Q2 (blocks C5).** Should the app remember that filtering was ON and resume
on launch? Current behavior (never resume) is the safe default for something
that takes over system audio; the alternative is an explicit opt-in checkbox.

**Q3 (blocks A1 scope).** What is the acceptable latency ceiling? The separator's
chunking makes this a hard product constraint, not a tuning detail. ~50–70 ms
(today's budget, lip-sync-safe) and ~500 ms+ (comfortable for a non-causal
chunked separator) lead to different designs.

**Q4 (blocks D4).** Bundled VB-CABLE (needs the permission email answered) or
manual-install prompt as the permanent shipped Windows experience?

**Q5 (blocks B3 design).** Is mono output while filtering acceptable for now,
or is stereo preservation a requirement? It interacts with the mid/side
prefilter, which removes the side channel by design.

---

## 11. Dead ends — measured, rejected, do not re-explore

- **Any additional speech enhancer.** `dpdfnet8`: 2× slower than DeepFilterNet
  *and* bandwidth-capped at 8 kHz. `MetricGAN+`: fast but 16 kHz ceiling.
  SepFormer family: non-causal, heavy, 8/16 kHz. All dominated by
  `dpdfnet2_48khz_hr`, which is already the default. The category is exhausted.
- **Self-quantizing UVR models to int8.** 3.7× *slower* (Conv2D/STFT-bound, not
  MatMul-bound), no RAM reduction, 12 % RMS quality difference.
- **Pure-DSP SFX add-back masks.** Plateau at ~8 % music leakage; drums are
  transient and noisy exactly like SFX. A semantic classifier (YAMNet: 0.5 %
  leakage) is the only thing that works — and that's a research-repo item,
  currently TF-based while this app is onnxruntime-only.
- **Synchronous Audio Router** as a VB-CABLE replacement: needs an ASIO host
  component, worse unsigned-driver UX, adds Steinberg SDK license surface.
- **OpenVINO execution provider** for the sherpa path: ~16 % faster on raw
  ONNX, but sherpa-onnx statically links its own onnxruntime and would need a
  source rebuild to benefit.
- **`UVR-MDX-NET_Crowd_HQ_1`** for subtraction: over-subtracts (trained on
  crowd noise, mistakes dialogue for it).
- **Synthetic tones as a music test fixture.** They get suppressed like noise
  (−47 dB), contradicting real music's −0.4 to −1.7 dB. `test_live_e2e.py`
  already guards against this; don't reintroduce it.

### Eliminated while chasing the `dpdfnet_hr` contradiction (§2.1)

All measured, all negative — do not re-investigate:

- **Alignment** as the cause of the offline/hardware gap: aligned and
  unaligned per-phase figures agree within 0.1 dB.
- **Dry fallback**: 0.1 % (1/1050 blocks), 0 xruns, 0 callback errors. A
  re-read of `_callback_body()` confirms the only path putting dry samples
  into `wet[]` is the one that already increments `fallback_blocks`, so there
  is no uncounted path and the counter can be trusted.
- **Mono downmix** (`block.mean(axis=1)`): an exact no-op on a
  duplicated-channel signal, bit-for-bit.
- **Band-limit filter**: a real effect, but ~5 dB and in the *wrong
  direction* (more suppression, not less) — far too small and backwards to
  explain the gap.
- **State carryover** as the explanation for the gap: real and large
  (19.3 dB), but it applies identically to both measurement paths, so it
  cannot account for a difference between them. It remains a genuine confound
  for interpreting any single `bench_offline` number (see A6).
- **Phase/drift swap**: output length deficits are <1000 samples for every
  processor.

### Claims that did not survive re-measurement

- **"dpdfnet_hr does real music-vs-voice separation."** Believed since
  2026-07-24 on the strength of its −30 dB music figure. Disproven in §2.2:
  that figure was an artifact of the mis-seeded normalizer, and once fixed the
  model separates vocals from music by 0.83 dB — the same as `gtcrn`.
- **"A direct-engine run at 20 ms pacing gives music_only −26.8 dB."**
  Recorded in project memory; could not be reproduced. A faithful
  re-measurement of the engine chain including the band-limit gave ≈ −36 dB.
  Treat −26.8 as unverified.
- **"The hardware tier specifically can't be trusted for dpdfnet_hr."** Half
  right for the wrong reason. That tier did have a real alignment bug (E2, now
  fixed), but the offline/hardware divergence was input level, not the tier.
