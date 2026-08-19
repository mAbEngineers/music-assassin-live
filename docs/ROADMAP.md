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

(This table is a single-clip, isolated-signal probe — vocals fed alone, then
music fed alone. §5 B1's 104-pair, in-mixture, whole-corpus sweep is a
different and more complete measurement and ranks the same four models
differently by net dSI-SDR — not a contradiction, a different question. Read
both; B1 is the one that should drive the default-model decision.)

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

### 3.1 The stereo corpus — built and used, 2026-08-16/17 ✅

Every measurement before this date used **one 15-second mono clip**, which
made every mid/side and stereo-width number meaningless by construction. Three
handovers recorded this as blocked on the user for source material, and a
2026-08-15 attempt to resolve it from `~/Music/Acapella/` failed outright — see
the dead-ends note below, kept so nobody repeats it. The user then supplied 57
real files at `~/Music/MusicAssassin/Corpus/`: 39 anime OP/ED full mixes plus
18 tracks added specifically to cover gaps the first batch left (below). All 57
passed structural verification before a single `demucs` call — real mixes
(30–150 Hz energy −1.2 to −11.0 dB, nowhere near the −25 dB acapella
threshold), 44.1/48 kHz stereo, S/M spread wide enough that mid/side is finally
testable (median −8.2 dB, vs. −15.1 dB and much narrower in the first batch).

**A second methodology bug, found before it could bias the ground truth.**
`scripts/corpus_excerpt.py` (committed 2026-08-16 — the make-excerpts logic
from the abandoned attempt, now a real tool with an acapella guard built in)
originally picked the single loudest 20–30 s window per track. That reliably
skips instrumental intros, but it also reliably lands on the chorus — and
anime OP choruses are frequently sung brighter than the verse even by male
vocalists. Measured directly on this corpus: one singer's auto-picked window
read median F0 336 Hz (female range on a simple classifier); his verse, 20 s
earlier in the same track, read 201 Hz — ordinary adult male tenor. Confirmed
against energy-band content in the separated stem, not just the pitch
tracker, so it isn't a tracker artifact. Left alone, this would have biased
**every** category toward the loudest, easiest, most climactic moment in each
mix — away from exactly the quieter passages that mattered for the
`dpdfnet_hr` level-sensitivity investigation (§2.1/§2.2). Fixed to pick the
*first* window that clears an energy threshold (the first real vocal entrance)
rather than the global maximum; verified the fix on the same track (204 Hz,
matching the verse) and confirmed at least one singer (`Colors`, FLOW) is
genuinely high-register throughout, unaffected by the bug.

**Corpus composition, 57 clips, 30 s each, ratios 1.0/2.0, `htdemucs` ceiling:**

| Category | Clips | Why |
|---|---|---|
| `anime_op_stereo` (tune) | 30 | main tuning set |
| `anime_op_stereo` (holdout) | 5 | redundant near-duplicates (language covers, extra OPs from an already-represented show) held out so the tune set keeps maximum real diversity, not because they're weaker clips |
| `dual_mono_control` | 2 | expected-negative — S/M ≤ −44 dB, mid/side must do nothing here |
| `dialogue_score` | 2 | 23-minute episodes, dialogue over background score — the closest thing in the corpus to the actual product use case |
| `male_lead` | 8 | the batch's real purpose: the original 39 were **100% female vocal, median F0 331 Hz** — see below |
| `rap` | 3 | speech-like vocals in the actual speech F0 band (85–255 Hz), likely to behave very differently through a speech enhancer |
| `sparse_acoustic` | 4 | everything else is a dense, heavily compressed master (crest ~11.5); these stress mid/side under different conditions |
| `orchestral_dialogue` | 3 | movie trailers, deep-voiced narration over score |

**Why `male_lead` mattered enough to hold up the build.** Screened before the
18 additions landed: the original 39-file batch was not merely
female-skewed, it was **100% female**, median F0 331 Hz, zero clips below
205 Hz. Speech-enhancement models are trained on speech, which sits at
85–255 Hz — almost entirely *below* this corpus's range. That lines up exactly
with the 2026-07-24 complaint that `dpdfnet_hr` "cuts some girl vocals," and
without low-F0 material a sweep can't tell "this model is bad at high female
vocals" apart from "this model is bad at vocals" — different fixes follow from
each. `male_lead` closes that gap (verified median F0 down to 201–204 Hz after
the window-picker fix, vs. the original corpus's floor of 205 Hz).

**Build status: complete.** Finished 2026-08-16 — manifest shows all 57
items, ~3.6 GB. Checkpointing (per-source, resumable) was never actually
needed this run, but stayed in place as the guard against a repeat of the
2026-08-15 OOM kill. Used for the B1 sweep in §5 below — 104 item-ratio pairs
(52 tuning clips × 2 ratios; 5 `anime_op_stereo` holdout clips correctly
excluded from tuning, per their purpose above).

**Remaining gaps, lower priority than what's already covered:** no Western
pop/rock/EDM, no solo-piano-only or fully a cappella-in-the-mix passages, and
`sparse_acoustic` (THE FIRST TAKE piano sessions) reads about as loud/compressed
as the OP/EDs by crest factor despite the name — worth knowing when reading
results, not a defect.

**One gap got sharper when B3 landed (2026-08-18): there is no wide-stereo
stress material.** The corpus is enough to *prove the stereo rebuild works* —
success there is `stereo_width_db` climbing off its −161 dB floor on clips
already separated. What it cannot show is the rebuild failing gracefully,
because at a median S/M of −8.2 dB these are dense, fairly narrow masters and
nothing in them is hard-panned, out-of-phase, or binaural. Those are exactly
the signals that expose a fix which restores width while smearing the image
or inverting phase, and `dual_mono_control` (2 clips) only covers the
opposite end. `bench_quality.py` already reserves the name for it —
`--only-category stereo_torture` appears in its own usage examples — so this
is a labelled hole, not a new idea. **Worth collecting, ~6–10 clips:** hard
L/R panned instrument arrangements (classic-era rock mixes are the easy
source), wide synth/EDM pads, live or binaural recordings with real room
width, at least one deliberately out-of-phase or heavily chorused track, and
one wide mix with a *centred* lead so the "voice survives, width survives"
claim can be checked on the same clip. Same pipeline as the rest: drop the
files somewhere, `scripts/corpus_excerpt.py`, then `--build-refs --category
stereo_torture`.

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

### B1. Quantitative model + mid/side comparison — swept 2026-08-17 ✅, by-ear check still open

Run against the full corpus (§3.1): `--sweep model=gtcrn,dpdfnet,dpdfnet_hr,dtln`
and `--sweep midside=off,on` (at the shipped default `midside_exp=4`), 104
item-ratio pairs each. Results in `~/.local/state/music-assassin/bench/b1/`.

**Model comparison** (in-mixture, whole-corpus — a different, more complete
measurement than §2.2's single-clip isolated separation ratio; see the
reconciliation note at the end of §2.2):

| model | music_supp | vocal_ret | Δ**SI-SDR** | SAR | latency | RTF |
|---|---|---|---|---|---|---|
| `dpdfnet_hr` | −46.1 dB | −5.0 dB | **+4.19 dB** | **4.40** | 50 ms | 0.32 |
| `dtln` | −20.6 dB | **−0.9 dB** | +3.59 dB | 4.24 | **24 ms** | **0.07** |
| `gtcrn` | −28.1 dB | −2.3 dB | +2.64 dB | 3.59 | 16 ms | 0.09 |
| `dpdfnet` (16 kHz baseline) | −52.9 dB | −3.1 dB | **−0.28 dB** | 0.27 | 50 ms | 0.15 |

**`dpdfnet_hr` keeps the best net dSI-SDR of anything tested — the pragmatic
default choice holds up, on a real 57-clip corpus, not just the one clip §2.2
used.** But it is also the *most* vocally damaging of the four (−5.0 dB, worst
in the table); its net win comes from music suppression outweighing that in
the SI-SDR math, which does not necessarily match what a listener notices.
**`dtln` is the standout on vocal preservation** (−0.9 dB, next-best is
3× worse) **and is ~4× faster** at a close second place on dSI-SDR — the
strongest candidate for a real by-ear A/B against the default, especially
given the standing "vocals still getting cut" complaint. **`dpdfnet` (16 kHz
baseline) is net *harmful*** on this corpus (negative dSI-SDR — worse than
doing nothing) despite the deepest raw suppression number; it should not be
recommended even as an alternative.

Failure taxonomy (auto-tagged per clip, all models, 104/104 pairs unless
noted): universal across every model — `hf-loss` (8–20 kHz destroyed) and
`stereo-collapse` (see B3). Per-model standouts: `dpdfnet` clicks on 79/104
clips (worst); `dtln` is boundary-sensitive on 44/104 (worst); `dpdfnet_hr`
shows `vocal-loss` on 36/104 (worst, before mid/side — see below); `gtcrn`'s
`musical_noise` score is a **large outlier (−20.0**, vs +1 to +7 for the
others) that reads as "improved" but is more likely an artifact of its
104/104 `hf-loss` hollowing the spectrum out rather than genuine cleanliness
— don't read that number as a win without listening to it.

**Mid/side, `dpdfnet_hr`, exponent 4 — result is negative, do not flip the
default.** This is the opposite of what the original plan below expected:

| config | music_supp | vocal_ret | Δ**SI-SDR** | vocal-loss tag | stereo width |
|---|---|---|---|---|---|
| `dpdfnet_hr` (off) | −46.1 dB | −5.0 dB | **+4.19 dB** | 36/104 | −161 dB (already collapsed, see B3) |
| `dpdfnet_hr+ms4` (on) | −50.5 dB | −7.4 dB | **+3.23 dB** | **60/104** | −161 dB |

Turning mid/side on deepens music suppression by 4.4 dB, but *net SI-SDR gets
worse* (4.19 → 3.23) and the fraction of clips showing real vocal-loss nearly
doubles (36 → 60 of 104). At exponent 4, stacked with `dpdfnet_hr`, on this
diverse real corpus, mid/side is a net loss, not the hoped-for partial answer
to "vocals get cut" — if anything it's the opposite here, since it's cutting
*more* vocals to buy suppression the SI-SDR math doesn't value as highly.
**Keep mid/side off by default.** This doesn't rule out mid/side entirely —
only exponent 4 stacked with `dpdfnet_hr` was tested; see B2, now the more
promising direction than a flip.

**Category breakdown — `male_lead` (8 clips), the set added specifically to
test whether the "cuts some girl vocals" complaint (2026-07-24) is a
female-vocal-specific problem or a general one. It surfaces a sharper,
different finding than that question was aimed at:**

| category | model | music_supp | vocal_ret | Δ**SI-SDR** |
|---|---|---|---|---|
| `male_lead` | `dpdfnet_hr` | −48.4 dB | **−8.5 dB** | **+0.33 dB** |
| `male_lead` | `dtln` | −20.0 dB | −1.6 dB | +1.07 dB |
| `male_lead` | `gtcrn` | −28.8 dB | −2.9 dB | +0.72 dB |
| `sparse_acoustic` | `dpdfnet_hr` | −48.0 dB | −0.9 dB | **+9.39 dB** |
| `sparse_acoustic` | `dtln` | −21.6 dB | −0.3 dB | +1.11 dB |
| `sparse_acoustic` | `gtcrn` | −30.5 dB | −1.5 dB | +0.96 dB |

**On male vocals specifically, `dpdfnet_hr`'s net advantage nearly vanishes**
— dSI-SDR drops from the whole-corpus +4.19 dB down to +0.33 dB, and vocal
damage (−8.5 dB) is nearly double its whole-corpus average and the worst of
any model/category combination measured this session. `dtln` and `gtcrn` both
net-beat it here. **On sparse/quiet material it's the opposite** — `dpdfnet_hr`
pulls far ahead (+9.39 dB, more than 8× the other two) with its *best* vocal
retention anywhere measured. Its whole-corpus advantage is not uniform; it is
concentrated in sparse/quiet content and largely absent on male leads. This is
not a resolution of the original female-vocal complaint (this category is a
different, adjacent question) but it is a concrete, actionable lead in its
own right and should weigh heavily in the by-ear pass below.

**Caveat before that comparison is acted on: `dtln`/`gtcrn`'s flattering
`vocal_ret` on this category is a broadband energy average, and it hides a
bandwidth collapse the ear will not miss.** The same run's per-band probe, on
`male_lead` alone:

| band | `dpdfnet_hr` | `gtcrn` | `dtln` |
|---|---|---|---|
| sub (0–200 Hz) | −8.1 dB | −2.4 dB | −1.4 dB |
| low (200 Hz–1 k) | −7.3 dB | −2.2 dB | −0.9 dB |
| mid (1–4 k) | −11.3 dB | −3.5 dB | −2.0 dB |
| high (4–8 k) | −11.7 dB | −3.8 dB | −2.4 dB |
| **air (8–20 k)** | **−13.0 dB** | **−64.7 dB** | **−62.5 dB** |

The 16 kHz-native models destroy the air band outright (their sample-rate
ceiling, same effect as the whole-corpus −49 dB noted above), but so little
vocal energy lives there that the broadband `vocal_ret` figure barely moves —
which is exactly why the aggregate reads −1.6 dB. dSI-SDR does not penalise it
either. So the male_lead ranking should be read as "`dpdfnet_hr` damages the
bands that carry the voice more, while `dtln`/`gtcrn` remove the top octave
entirely" — two different kinds of damage that the summary columns score as
though they were comparable. **The by-ear pass is what separates them, and it
should be done on headphones or full-range monitors; anything that rolls off
early will not reproduce the difference this caveat is about.**

**An anomaly in the `dual_mono_control` category (n=4 pairs, expected-negative
— these clips have no real side channel, S/M ≤ −44 dB), flagged rather than
explained away:** turning mid/side ON makes vocal retention measurably *worse*
(−15.3 → −20.8 dB) on content with nothing for it to exploit, yet the
aggregate dSI-SDR simultaneously *improves* (−2.58 → +0.85 dB). Both numbers
moving in seemingly incompatible directions on a tiny, expected-null sample is
a sign something in the metric or the mix path behaves unusually at very low
side-channel energy — not yet root-caused. Don't build on this category's
numbers without investigating further.

**By-ear audio has been rendered, but not yet listened to.** `--dump-audio`
output exists for `male_lead` and `sparse_acoustic` at both ratios, covering
input / oracle / ceiling / `dpdfnet_hr` / `gtcrn` / `dtln`
(`~/.local/state/music-assassin/bench/b1/audio_{male,sparse}/`, see
`scripts/run_b1_sweep.sh`). The harness's own docstring is explicit that the
numbers rank candidates, they don't settle quality — nobody has listened yet,
and three findings above (mid/side hurting, `dpdfnet` scoring net-negative,
`dpdfnet_hr` cratering on male leads) are surprising enough that none should
be treated as final until someone does. **Priority listen: the `male_lead`
files — that's where the numbers disagree most with the standing default
choice.**

### B2. Tune (not just expose) the mid/side exponent

`MidSideFilter(exponent=4.0)` is hardcoded. B1 shows exponent 4 net-hurts
`dpdfnet_hr` on real content (doubles the vocal-loss rate for a suppression
gain SI-SDR doesn't reward) — so the natural next step is sweeping *lower*
exponents (`--sweep midside_exp=1,1.5,2,2.5,3`) against the same corpus before
touching the UI, not exposing 4 as a user-facing default that already measures
worse than off. If a lower exponent finds a real win, a slider (rather than a
fixed value) is still the right shape, since content-dependence is real —
but tune the shipped default first.

### B3. Stop collapsing output to mono — built 2026-08-18, measurement still open

`AudioEngine._callback_body()` writes the mono wet signal to both output
channels (`wet[:,0] = wet[:,1] = wet_mono`). **At 100 % mix the entire system
output is mono.** For a system-wide filter that users leave on while watching
video, losing the stereo image is a large, constant perceptual cost that no
handover had recorded until B1's sweep quantified it directly.

**Measured 2026-08-17, B1 sweep: `stereo_width_db = −161 dB`, on every one of
104 item-ratio pairs, for all four models, with mid/side on or off.** Not an
occasional or content-dependent regression — total, universal collapse, every
single time the filter is active. This is now the single most consistently
measured defect in the whole corpus (the only failure tag that fires 100 % of
the time across every configuration tested).

Options, cheapest first, as originally written:
- Apply the model's per-band gain to the original stereo pair instead of
  emitting mono (requires the processor to expose a mask, not just audio —
  not all do).
- Re-inject the original side channel at reduced gain after processing:
  `L = wet + k·S`, `R = wet − k·S`. Cheap, approximate, restores width.
  Conflicts conceptually with mid/side prefiltering, which is *removing* the
  side channel on purpose — so these two need to be designed together.
- Accept mono, but say so in the UI.

**Built 2026-08-18: `assassin_live/audio/stereo.py`, a variant of the first
option that does not need the processors to expose anything.** The mask is
recovered from the audio the chain already produces rather than from the
model's internals:

    mask[k] = |Wet[k]| / |Dry_mono[k]|        per STFT bin, clamped
    L_out   = istft(mask · stft(L_dry))
    R_out   = istft(mask · stft(R_dry))

One mask, both channels, so every bin's L:R ratio is preserved exactly — the
image is the original one, not a synthesised widening — while the model's
per-band suppression still applies. Bins it zeroed go to zero in both
channels; bins it kept keep whatever width they had. No processor changes, so
it works for all four shipped models at once.

**Why not the second option (side re-injection), which was cheaper.** The
side channel is overwhelmingly the content the model just removed — that is
the entire premise of `midside.py`, which deletes it on purpose. `L = wet +
k·S` hands the music back at gain k, and the two features would fight by
construction. The mask formulation keeps side content only in bands the model
judged worth keeping, which is the distinction that makes them compatible.
A test asserts exactly this: a hard-panned band the model killed does not
reappear (`tests/test_stereo_rebuild.py`).

**Why not a per-sample broadband gain** (`g = wet/dry_mono`, which also
preserves the image and is far cheaper): it throws away the model's spectral
selectivity, which is the whole point of a masking enhancer — removing music
in one band while keeping voice in another *within the same instant*.

**Reusing the dry pair's phase is sound for what ships today**, because all
four enhancers are magnitude-mask models — a real gain per bin, phase
untouched. That is independently established: it is why `estimate_lag`'s raw
waveform correlation resolves to the sample (see its docstring). A future
non-causal separator that scrambles phase must not go through this path,
which is what the `wants_stereo` seam below is for.

**Also landed: the `wants_stereo` seam on `StreamProcessor`** (A1's
prerequisite, called out in the previous handover as blocking three separate
items and needing input from nobody). A processor setting it is handed
`(n, 2)` and returns `(n, 2)`; the engine skips both its mono downmix and the
rebuild, and builds 2-channel resamplers. Nothing shipped sets it — Spleeter
will. The two paths are mutually exclusive by construction, and mid/side is
bypassed for stereo processors since its job is producing a *mono*
centre-emphasised signal for a mono model.

**Shipped OFF (`AudioEngine.set_stereo(False)` is the default).** The rebuild
changes what every pipeline sends to the speakers, and this project does not
flip a shipped default on an expectation — §2.2 records what that cost last
time. The cost is real and needs pricing: width that survives is width in
bands the model kept, so any music sitting in those bands comes back with it.

**What is still open — the measurement.** `scripts/run_b3_sweep.sh`, same
detached/resumable shape as the B1 sweep:

| run | what it answers |
|---|---|
| `--sweep stereo=off,on` | the headline trade, whole corpus, shipped default model |
| `--sweep midside=off,on stereo=off,on` | the interaction above — one deletes the side channel, the other restores an image |
| `--only-category dual_mono_control` | expected-null: nothing to restore, so nothing may be invented (also the category with B1's unexplained anomaly) |
| `--only-category sparse_acoustic / male_lead` | plus `--dump-audio`, since width is a perceptual claim and belongs in the same by-ear pass as B1 |

Flip the default on those numbers: `stereo_width_db` up substantially with
`music_supp_db` and `delta_si_sdr_db` essentially unmoved, and RTF still in
budget. If suppression falls materially instead, the honest answer is the
third option above — keep mono and say so in the UI.

**Measured cost, 2026-08-18 (same machine as the B1 sweep): 0.13 ms per 20 ms
block, RTF +0.0065**, negligible beside `dpdfnet_hr`'s 0.38 — so CPU is not
the axis this decision turns on. **The latency is the cost that matters:
+5.3 ms** of framing delay on top of the model's, which is not free given
latency is a product constraint here (Q3) and the shipped budget is ~50 ms.

Verified without models or hardware in `tests/test_stereo_rebuild.py`: unity
mask reconstructs the dry pair to 1.8e−07, a fully-suppressing chain stays
silent (no width invented from the dry signal), a killed hard-panned band
does not leak back, chunk size does not change the samples, and end-to-end
through `AudioEngine` the default still measures the mono collapse
(−235 dB) while `set_stereo(True)` restores the surviving image (−3.0 dB,
which is the correct answer for a signal whose width is half in the killed
band). A synthetic-corpus run of `evaluate()` moves `stereo_width_db` from
−165.7 dB to −5.2 dB, clears the `stereo-collapse` failure tag, and leaves
every separation metric bit-identical — the rebuild does not perturb the mono
numbers, by design.

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

### C1. Gapless output-device switching ✅ done 2026-08-18, spike run and green

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

**Built 2026-08-18, pending the spike.** The mechanism turned out to already
exist: `pin_process_streams()` writes `target.object` on our stream nodes and
WirePlumber relinks them — nothing was ever calling it on a stream that was
already running. So the live path is the same metadata write, aimed at one
node instead of two, with the capture side deliberately left alone
(`pin_process_streams(pid, capture_sink=None, ...)`) because an output change
does not concern capture or the model, and re-asserting the capture target
would be at best a no-op and at worst an unnecessary relink on the one path
that must not be interrupted.

- `PipeWireBackend.retarget_playback(pid, sink_name)` — the live move.
- `AudioEngine.retarget_output(sink_name)` — returns False rather than
  falling back on its own, because the *caller* is what knows whether a
  heavyweight rebuild is acceptable at that moment.
- The UI tries it first on `real_sink_changed` and drops to `retarget()`
  when it returns False. It also finally says something ("switched output →
  X") where C3 complains there is no feedback.
- On the `RoutingBackend` protocol, separate from `pin_stream()` even though
  PipeWire implements both the same way: they answer different questions and
  will diverge (on Windows the first may be a no-op while this is a real
  device switch).

`tests/test_retarget_live.py` asserts the thing that actually matters — after
a live retarget the processor has **not** been reset, the buffers still hold
what they held, and the stream object is the same one. A version that
returned True while resetting the model would pass a naive test and still be
the bug. Every failure mode (backend says no, backend raises, backend
predates C1, stream already dead, no backend) falls back rather than breaking.

**Spike run 2026-08-18, three times, and it works** —
`scripts/spike_c1_retarget.py` (two null sinks of its own, silence, a live
move; it destroys only the node ids it created, never a sweep by name, which
is precisely how C8's incident happened):

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| link moved | ✅ 0.01 s | ✅ 0.01 s | ✅ 0.04 s |
| stream stayed active | ✅ | ✅ | ✅ |
| callbacks during the move | 18 (≈16 expected) | 17 (≈16) | 23 (≈22) |
| worst callback gap | 21.4 ms | 21.4 ms | 21.5 ms |
| PortAudio status flags | 0 | 0 | 0 |

Against a 20.0 ms block period, a worst gap of 21.4 ms is ~1.5 ms of jitter —
under one block, with no dropped callbacks and no xruns. **So the move is
gapless in audio, not just in routing, and C1's next rung (ramping wet to 0
across the switch) is not needed.** The multi-second dropout and the reset
model are both simply gone for this case.

**The spike also found a live bug in shipped code, which is the whole reason
for running one.** The first attempt could not pin the stream at all.
`pin_process_streams()` identified our nodes by `pipewire.sec.pid`, which is
the pid of whatever *opened the socket* — and anything arriving through
pipewire-pulse (which is how PortAudio's `pulse` device connects) is proxied
by the pipewire-pulse daemon, so the client reports the *daemon's* pid.
Measured on a real graph: Firefox, GNOME's volume control, our own streams
and a dozen others all reporting the same pid 2122. So `pin_stream()`'s
second targeting pass has been finding nothing and returning False on this
machine — the engine's "could not pin audio streams to their targets" warning
fires on every start, and routing has been resting on the `PULSE_SINK` env
vars alone.

Worse, **C8's detector inherited the same matching and was silently
vacuous**: no capture node found means no fault reported, which is
indistinguishable from working. It would never have fired on the very
incident it was written for. Fixed in one place — `_owner_pid()` prefers
`application.process.id` (the application's own pid, carried on both client
and node) and keeps `pipewire.sec.pid` as the fallback for native protocol
clients where it is genuine — and `our_stream_nodes()` is now the single
place all three callers ask "which nodes are mine". Regression-tested in
`tests/test_capture_diagnosis.py` with a fixture shaped like a real
pulse-proxied client.

**Follow-up worth doing, not done here:** now that the metadata pass
demonstrably works, the `PULSE_SINK`/`sd._terminate()/_initialize()`
machinery in `resolve_stream_devices()` may be the dead weight this section
predicted. It governs *initial* device selection rather than moves, so it is
a separate change with its own risk; the test is to null out the env vars and
confirm a freshly-opened stream still lands correctly on the strength of
`pin_stream()` alone.

### C2. Forward volume keys to the real sink ✅ done 2026-08-18 — but its stated reason was wrong

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

---

**The paragraph above is wrong, and is kept only so the correction has
something to point at. Measured 2026-08-18,
`scripts/spike_c2_monitor_volume.py`: a sink's volume does NOT scale its
monitor.** A 440 Hz tone played into a null sink and captured from that sink's
monitor came back at identical RMS at sink volume 1.0 and 0.5 — ratio
**1.000**, and that RMS was exactly the tone's as generated. Monitors are
pre-volume. **The system volume slider was never changing what the model is
fed**, so the measurement-confound argument that promoted this item does not
hold, and §2.1's level-sensitivity has no bearing on it.

**What is actually wrong is worse in one way and easier in another.** The keys
do not mis-scale the capture — they do *nothing at all*. They act on the trap,
because it is the default sink, and the trap's output goes nowhere: the engine
plays to the real device directly. So while filtering is on, volume-down
changes a number on a null sink and the audio does not move.

**And the fix is better than the one specified above.** Pinning the trap at
100% is unnecessary — its volume is harmless — and would have been actively
bad: the system slider and the on-screen volume display follow the *default*
sink, so pinning would have made them snap back to 100% forever, showing a
level that was never the user's. Instead the trap's volume is left exactly
where the user put it, and mirrored:

- `sync_volume()` — mirrors volume and mute onto the real sink, and only when
  the trap's value *changes*, so adjusting the real device directly in a mixer
  is not stamped on once a second.
- `adopt_volume()` — seeds the trap from the real device's level at enable
  time, so switching the filter on neither changes how loud anything is nor
  makes the slider jump.

`tests/test_volume_mirror.py` covers it with a stand-in mixer.

**This reopens a real question.** The `noise_only` swing of −55.7 → −21.7 dB
across two identical hardware runs had C2 as its standing explanation, and
that explanation is now dead. See E2.

### C3. Stop fighting the system output picker ✅ done 2026-08-18

`RoutingSession.check()` re-asserts the trap sink as default within 1 s of
anything stealing it, then waits out a 1.5 s debounce before treating the
change as a real device switch. Net effect when a user picks a device in GNOME
quick settings: the selection visibly flickers back, then 1.5–2.5 s later the
app retargets — with no feedback that it understood. Meanwhile
`_refresh_outputs()` runs every second and can stomp the dropdown selection.

Treat "default sink changed to X" as an explicit user intent: update the
dropdown to X immediately, show "switching output → X", then retarget. Same
mechanism, coherent story.

**Done 2026-08-18, and mostly *because* C1 landed** — three of the four
problems here were consequences of a retarget being expensive.

**The 1.5 s debounce is now 0.25 s.** Its comment said why it existed: acting
meant a full engine restart and "each restart is an audible glitch", so a
flapping device had to be waited out. A retarget is now a metadata write at
0.01–0.04 s (C1), so thrashing is nearly free while the wait is not — 1.5 s
is exactly the lag that makes the system picker feel broken. It does not go
to zero: WirePlumber briefly assigns a default of its own while devices
settle, and following that would move audio somewhere nobody chose. It is now
sized to outlast that race and nothing more.

**`real_sink_changed` split into two events.** It previously also fired when
our device *vanished* and a fallback was picked — so "a human chose this" and
"their headphones went to sleep" were the same signal. That matters now that
the app remembers the choice: conflated, a sleeping Bluetooth headset
silently overwrites the saved output preference with whatever was left.
`real_sink_replaced` is followed but deliberately not remembered.

**One route into an output change.** The app's own dropdown was still calling
the heavy `retarget()` — after C1 landed, picking a device *in the app* was
slower than picking one in the system menu. All three routes (our dropdown,
the system picker, a device vanishing) now go through `_switch_output()`,
which tries the live move and falls back; they differ only in whether the
choice is remembered and what the status line says.

**The dropdown follows the system.** It updates on an external change instead
of continuing to display the old device — a picker that disagrees with the
system being the actual complaint. `_refresh_outputs()` runs first so a
device that just appeared is already in the list.

`tests/test_routing_events.py` covers `check()`, which carried every routing
decision the app makes and had no test at all: steady state, a user pick
crossing the debounce, a flapping device never being followed, our device
vanishing, nothing left to fall back to, and the trap itself vanishing —
including that the last case does **not** re-assert a destroyed node id,
which is what C8's incident did once a second, forever, while reporting
healthy.

### C4. Human-readable status ✅ done 2026-08-18

The status line is a debug dump (`blocks: 48213  fallbacks: 0  xruns: 2`).
Users need state and health: "Filtering → Soundcore Q20i · 12 ms latency ·
healthy". Keep the counters behind a details toggle.

**Done 2026-08-18**, as specified: `Filtering → <device> · <n> ms · <health>`,
with the counters behind a `details ▸` toggle. They were never junk — they are
what diagnoses an underrun — they were just never an answer to "is this
working", which is the only question the line is asked most of the time.

**Health is the part with a judgement in it**, so it is the part with tests.
It reports, in priority order: `stream stopped` (red) when the stream is dead,
`struggling (N blocks dry)` (amber) when more than `FALLBACK_WARN_PER_TICK`
blocks fell back to dry within a tick, `N xruns` (amber), else `healthy`
(green). The failure it exists to catch is the quiet one — audio flowing, the
stream alive, nothing crashed, and the filter not actually filtering.

**Counters are read as deltas per tick, not lifetime totals.** A long session
accumulates fallbacks and xruns forever; reading totals would leave the line
permanently degraded after one hiccup an hour ago, which trains people to
ignore it.

**Every status write now goes through `_say(text, colour)`.** The colour
became meaningful with the health word, and that makes a stale colour a lie —
a red "stream stopped" left in place would tint the next ordinary message.
There are eleven such messages now (C1, C3, C8 added six), so setting both
every time is the only thing that stays true as more are added.

`tests/test_status_line.py` covers the decision without Tk, built with
`object.__new__` because constructing the real app builds widgets and takes
over a display.

### B6. The stereo rebuild's mask was 50 ms out of step ✅ fixed 2026-08-19

**Reported by ear:** "the preserve stereo image option gives like double
audio and it feels very out of sync." Correct, and worse than it sounds.

`StereoRebuild` derives a mask from wet-vs-dry and applies it to the dry
pair, which only means anything if the two refer to the same instant. The
engine paired them by FIFO position, assuming a processor emits `output[k]`
as the processed `input[k]`. Measured against the shipped models on real
corpus audio:

| model | nominal `latency_ms` | **measured lag** |
|---|---|---|
| `dpdfnet_hr` | 10.0 ms | **50.0 ms** |
| `dtln` | 24.0 ms | 8.0 ms |
| `gtcrn` | 16.0 ms | 5.3 ms |

Constant per model, and matching `latency_samples` for **none** of them — in
both directions. So the mask described a moment 50 ms away from the audio it
shaped, on the default model. That is the doubling.

**Fixed** by measuring the lag at stream start (`audio/lag.py`) and delaying
the dry stream by it — padding the worker's dry FIFO by `lag` frames makes
every later pairing correct with no per-sample bookkeeping. The rebuild stays
bypassed until the measurement lands, because a wrong image is worse than
none.

**Two things this got wrong first, both worth keeping:**

*The correlation cannot run on the inference worker.* It costs 5–12 ms, and
spending that there empties the wet FIFO, so the callback falls back to dry
and the dry backlog grows — permanent latency, i.e. the exact defect B7 below
is about. Measured while getting it wrong: the pipeline floor went 20 ms →
60 ms. It runs on its own thread now, and only the worker touches `_dry_work`.

*Periodic content fools it.* A synthetic 220 Hz tone with a true lag of 2400
samples reported 0 at confidence 0.46, because 2400 is ~11 periods of 220 Hz
and the confidence check cannot tell those apart. Real audio has broadband
aperiodic content and does not do this (0.81 on a corpus clip) — but it means
a tone-only test signal silently verifies nothing.

**Why nothing caught this before it shipped.** Every separation metric runs on
the mono wet signal, which never passes through the rebuild; `stereo_width_db`
counts side-channel *energy*, and a misaligned mask restores energy perfectly
well. The B3 sweep's "free on every metric across 104 pairs" was therefore
consistent with a badly broken feature. The `--dump-audio` files are the mono
mix, so listening to those could not have shown it either. And the engine
tests used a fake processor with **zero** lag, so FIFO pairing was trivially
correct — the integration was tested with the one processor incapable of
exhibiting the bug. `tests/test_stereo_rebuild.py` now has `_LaggedGate`,
which trails its input by 2400 samples like the real thing.

### B7. Startup baked a random latency into the whole session ✅ fixed 2026-08-19

**Reported by ear:** "sometimes the audio doesn't play when run from idle or
maybe it starts late so demusiced audio becomes out of sync."

At startup `_out` is empty, so the callback emits live dry and does not drain
`_dry_out`, which grows one block per callback. Whatever piles up before the
worker's first output *is* the steady-state latency from then on, because
both FIFOs drain at the same rate afterwards. Measured over six runs:

| run | first wet output | dry backlog | steady latency |
|---|---|---|---|
| cold | **26 blocks** | 6720 samples | **140.0 ms** |
| warm ×5 | 1 block | 960 samples | **20.0 ms** |

The first inference after load is slow — ONNX warm-up — and that half second
became 140 ms of permanent delay, varying run to run with scheduling, capped
only by `_max_out` at 160 ms.

**Fixed** by warming the processor inside `start()` before the stream opens,
so the first real block meets a hot runtime. It costs a moment on the button,
which is now a state the button can show (C9).

**A backlog trim was tried and removed.** Forcing the dry FIFO to
`mask_lag + one block` looked right and was wrong: the target has to account
for the stereo rebuild's own framing when enabled, and getting it wrong
misaligns the dry/wet mix. Warming removes the cause; a cap is a separate
change that needs its own measurement.

**Found while investigating:** the dry/wet mix does not compensate for the
processor's internal lag at all. `_dry_out` only absorbs what the model holds
back, and `dpdfnet_hr` holds back nothing while delaying internally by 50 ms
— so at any mix below 100 % the dry is 50 ms out of step with the wet.
**Fixed in B8 below**, 2026-08-19; the by-ear check it wants is still open.

### B8. The dry/wet mix carried the same 50 ms skew ✅ fixed 2026-08-19

B6 measured the processor's lag and delayed the dry stream by it — but only
`_dry_work`, the worker-thread copy that feeds the stereo mask. The dry a
listener actually hears below 100 % mix comes from `_dry_out`, drained in the
callback, and it never got the pad. So B6 aligned the mask and left the mix
exactly as it was: on `dpdfnet_hr`, a blend of two streams 50 ms apart, which
is an echo rather than a mix.

Fixed by handing the measured lag to the callback thread the same way
`_pending_lag` is handed to the worker, so each FIFO is padded by the only
thread that touches it. Two consequences handled in the same change:

- **`latency_ms` subtracts the pad.** It reports the depth of `_dry_out`,
  and the pad never drains — it changes *which* dry sample pairs with which
  wet one, not *when* either leaves. Counting it would have inflated C7's
  number by the model's lag while nothing audible changed.
- **The FIFO's cap grows by the pad.** `_dry_out` is trimmed from the head
  when it exceeds `_max_out`, which is precisely the operation that would
  throw the correction away again.

**Measured**, `tests/test_stereo_rebuild.py::test_the_mix_is_aligned_with_a_
processor_that_lags`: the engine is run fully dry and fully wet over the same
input, and how late each stream emerges is compared after subtracting each
run's own queue backlog (which varies run to run — the B7 race in miniature,
and the reason a raw comparison of two pumps is not a measurement). **0.0 ms
skew with the fix, 49.9 ms without it**, against a 50 ms processor lag.

Costs one dry gap of the lag's length, once, at the moment the pad lands.
Inaudible at 100 % mix, where the dry gain is zero.

**Still wants ears.** The mix slider was unreliable as a tool for trading
vocal damage against music removal; whether it now behaves is a by-ear
question, and B4 will want the answer before it starts producing candidates
to compare.

### B6b. The estimator could give up and call it zero ✅ hardened 2026-08-19

Two failure modes in the measurement B6 introduced, both found by reading
the code rather than by a report, and both able to produce exactly the
symptom B6 fixed:

- **Silence consumed the measurement.** Near-silent pairs were buffered like
  any other, so a session starting on a quiet intro could spend all six
  windows on material that could not correlate and give up before the first
  loud bar arrived. `push()` now drops pairs below an RMS floor: silence is
  an absent measurement, not a failed one. Measured: 20 s of silence now
  costs zero windows, where it used to cost thirteen.
- **Exhaustion meant "assume aligned".** `_mask_lag = 0` applies a
  correction of the wrong size and enables the rebuild on it. Falling back
  to the processor's *declared* latency instead would be no better — the
  table above has `dpdfnet_hr` declaring 10 ms and measuring 50 — so
  exhaustion now means **unmeasured**: no correction anywhere, the rebuild
  left bypassed, and a state the UI names. That is the audio you get from
  never having measured, which is the honest floor.

`AudioEngine.lag_state` returns `measured` / `measuring` / `unmeasured` and
the details panel prints it, so the three are distinguishable in a report.
Previously an assumed zero and a measured zero looked identical.

### C9. The ON/OFF button had no in-progress state ✅ done 2026-08-19

**Reported by ear, 2026-08-19:** "when the on/off button is pressed it takes
some time to change but has no effect that it's doing something, so a person
tries re-clicking to reverse".

Not polish — a correctness bug with a UX symptom. Turning on creates the trap
sink (polls the graph up to 3 s), loads an ONNX model cold (~0.5 s, measured)
and opens a PortAudio stream (another poll up to 3 s), and all of it ran on
the Tk thread. So the button could not repaint, and a second click did not
cancel the wait — it **queued**, and undid the action the instant the first
finished. The user's instinct to re-click was the worst possible move, and
nothing in the UI discouraged it.

Fixed by moving the work off the Tk thread (`_work_on` / `_work_off`, which
touch no widgets) and polling for the result every 80 ms. The button carries
four states — `● OFF`, `◐ starting…`, `● ON`, `◑ stopping…` — and is
*disabled* while transitioning, so a re-click is discarded rather than
queued. It repaints **before** the work starts, since the whole complaint is
that nothing visibly happened.

Glyph as well as colour, because a colour-only state is invisible to a
meaningful fraction of users, and "is it on?" is the one question this app's
UI has to answer at a glance.

`_turn_off()` survives as the synchronous emergency path (stream died,
feedback loop, trap gone) — those must not return to a half-on state, and
blocking the UI is acceptable when the alternative is leaving the trap sink
installed.

### C10. The meter shows the input as well as the output ✅ done 2026-08-19

One meter, fed from `outdata`, cannot tell a silent session from an idle one
— which is the whole difficulty with the open "audio sometimes does not play
at all" report, since every *other* failure path writes a status message that
names itself. Recording the input's RMS beside the output's splits it three
ways on sight: input moving and output flat is the engine or the mix, both
flat is capture, both moving with nothing audible is routing past our output
— the case the status line calls healthy because it *is* healthy.

`recent_input_levels()` beside `recent_levels()`; the meter draws two
labelled rows; the details panel prints both as dBFS.

### C11. Control panel redesign ✅ built 2026-08-19

**Reported:** "the status thing is better in the on/off button now but the
button itself does not look good", with a request to research shipping UIs
before changing anything.

Four tools that solve the same layout problem were looked at: SoundSource 6,
Easy Effects, NVIDIA Broadcast, Krisp. They agree far more than they differ,
and the agreements are the material:

- **Booleans are capsule switches**, label left, switch right, in a list.
  Four out of four. Our three full-width `tk.Button` toggles reading
  `Band-Limit (20 Hz-20 kHz): ON` are the single biggest thing making the
  window look homemade — and the band-limit one paints a red bar across the
  window *because it is on*, making the calmest state in the app the loudest
  thing in it.
- **The accent means one thing: active.** Green in three, violet in the
  fourth. We use the same red for an enabled toggle, the fader fill, the
  meter, and `audio stream died`.
- **Numbers are anchored and monospaced**, pinned to a bottom edge (Easy
  Effects: `48,0 kHz  5,0 ms  −19 −19 dB`). Ours are centred body text that
  re-centres as the message changes length.
- **Dropdowns are filled pills** with the label beside them, not stacked
  above — which also buys back ~16 px a row.
- **Grouping is by function**, three or four labelled groups. Ours has eight
  loose blocks.
- **Nothing jumps.** Optional controls expand inside their group; ours packs
  a whole card into the window when `speechdenoiser` is selected.

**Chosen: a 680 × 430 two-column panel** (the "Desk" direction), against the
alternative of keeping the 420 × 600 portrait shape and tidying it. The
references all sit near 1.6:1, but the reason to follow them here is
specific: the two controls that need pixels are the meter and the 0–300 %
fader, the content genuinely is two groups of two (what the audio passes
through, what it is doing), and C5's tray icon would make this a window that
is opened, read and dismissed rather than parked.

Colour, resolved: `#ef4056` stays the brand and means *the amount of music
being removed* (meter, fader fill, icon); `#2ecc71` becomes *running/on*
(power pill, switch tracks, health dot); amber stays transitional; red text
stays faults, with a left stripe so it is not colour alone.

**Cost**, no new dependencies — four more Canvas widgets in the pattern
`HSlider` already established (a `Switch`, a `PillButton`, a `Dropdown`
backed by a styled `tk.Menu` replacing `ttk.Combobox` and its `option_add`
hacks, a two-row `Meter`), plus a `HoldButton` driving the existing
`engine.set_bypass()` for hold-to-compare. Cards stay square-cornered
`tk.Frame`s with a hairline border: rounding them means drawing each group on
a Canvas and placing its children with `create_window`, trading every layout
guarantee `pack()` gives for a radius nobody will notice.

Three things to get right while in there: **pick a font** by probing
`tkinter.font.families()` rather than asking Tk for `"Sans"` and taking
whatever X hands over; **set `tk scaling`** from the display's DPI; and
**keep the colour decisions in pure functions** the way `_health()` already
is, so they stay testable on a CI box with no display. `tests/test_status_line.py`
imports `AMBER`, `ON_COLOR` and `RED` by name — keep them as aliases into
whatever the new token set is called.

**Built the same day.** `ui/widgets.py` gained `Switch`, `PillButton`,
`HoldButton`, `Dropdown` and `Meter` (plus `round_rect` and `db_fraction`);
`ui/app.py` is the two-column layout, the pickers, the switches, the meters,
the status bar and the details overlay. Five things the drawing did not
settle, all decided against a render rather than on paper:

- **The window is sized from its content, floored at 680 × 430.** Nominal
  pixel sizes assume a font, and the font is whatever the machine has:
  measured, the processing card wanted 199 px and had 171, so `pack()`
  silently squeezed the suppression-limit row to 2 px — the redesign's own
  "nothing jumps" rule, broken by the redesign. `_fit_window()` adds the
  cards up and grows the window to fit (446 px with the design's fonts). The
  optional row is packed while measuring, so its space is reserved while it
  is hidden and selecting `speechdenoiser` reveals a control in place.
- **The details fold covers the right column**, not the whole panel. Reading
  counters and watching the meter are different activities; the devices and
  switches stay reachable. It is `place()`d, so the window does not resize.
- **The counters are aligned key/value rows**, not four dense lines of
  `key: value  key: value`. They are read when something is wrong, which is
  the worst moment to make someone parse a line.
- **The meter's dB annotation became two gridlines** at −6 and −20 dBFS
  rather than the axis the mockup drew along the bottom. That axis was
  wrong: the horizontal axis of a level history is *time*, and the mockup
  had copied a spectrum analyser's frequency scale into a place where it
  could not mean anything. Bars are on a dB scale now (`db_fraction`) —
  linear amplitude puts music at 5 % of the row and makes any dB mark a lie.
- **Status messages hold.** The supervision tick rewrote the line every
  second, so `feedback loop detected — turned off` was erased before it
  could be read and replaced with `idle`. Faults hold 20 s, other messages
  4 s. This matters for the open silent-from-idle report, where the user is
  specifically asked to go and read that line.

Also landed with it: hold-to-compare (`_compare()` sets intensity directly
rather than calling `set_bypass()`, which restores 1.0 on release and would
quietly move the user's mix to fully wet), the wet-boost readout, a font
probe, and a display scale applied to every dimension including `HSlider`'s
handle. Verified by rendering the real window under Xvfb at 100 % and 150 %
— `MUSIC_ASSASSIN_UI_SCALE` overrides the probe for exactly that.

The pure formatters — `format_status`, `format_details`, `_lag_text`,
`_level_db`, `_boost_label` — are tested in `tests/test_status_line.py`
without a display, which is the same trick `_health()` has used since C4 and
the reason any of this is testable in CI.

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

### C7. Surface latency ✅ done 2026-08-18, with C4

Nothing in the UI states the added latency (~45–70 ms by design). For anyone
watching video this is the first thing they'll want to know, and it becomes
critical if A1 lands with a chunk-size-driven latency budget.

**Done 2026-08-18** as part of C4's line, and **measured rather than
declared**: `AudioEngine.latency_ms` reads the depth of the lockstep dry FIFO,
which *is* the pipeline's delay — input that has been fed but whose processed
counterpart is not out yet. Summing nominal figures would have been wrong: the
sum of the models' documented `latency_ms` has never matched the ~50 ms
`bench_quality` measures end to end, and this explains why.

**The gap is one block of queue hand-off.** A block goes to the worker and its
result is collected by a later callback, so ~20 ms is the floor this
architecture reaches *even with a zero-latency processor* — measured at 19.9 ms
in `tests/test_stereo_rebuild.py`, which asserts it rather than tolerating it.
Together with the model's own delay and the resamplers, that is the ~50 ms.
Worth knowing before A1: a chunked separator's latency budget starts 20 ms in
debt, and halving `BLOCK` is the lever on that, not the model.

Excludes the output device's own buffer, which the engine cannot see — so the
number is what the app *adds*, which is the number a user watching video
wants.

### C8. Detect an orphaned or hijacked capture stream ✅ done 2026-08-18

**Incident, 2026-08-18.** `tests/test_routing_dry.py` was run on a machine
where the app was already filtering. Its cleanup step destroyed what it
judged a stale trap sink — that was the *live* app's. PipeWire then did the
reasonable thing with the app's now-orphaned capture stream and re-attached
it to the current default sink's monitor. Since the app's playback stream
feeds that same sink, the result was an audio feedback loop: output → sink →
monitor → input → model → output, recirculating indefinitely, audible as
unintelligible speech-like noise (a speech enhancer chewing on its own
recycled output) that survived everything except killing the app. The app
never noticed. `stream_ok` stayed true the whole time — the stream *was*
alive and healthy, it was simply connected to the wrong thing.

Two separate defects, both worth fixing:

- **The app cannot tell what it is capturing.** The supervision loop already
  polls `stream_ok` every tick; it should also confirm the capture stream is
  still linked to the trap sink's monitor and not something else. The name is
  right there in the graph. Mis-targeted capture is not a rare accident —
  losing the trap sink to a crash, a PipeWire restart, or a user removing it
  produces the identical state.
- **Nothing detects the feedback loop itself.** Capturing the monitor of the
  sink you are playing into is unconditionally wrong for this app and is
  cheap to spot structurally (compare the capture node's peer against the
  playback target) rather than by trying to hear it. Worth a hard refusal:
  disable filtering and say why, rather than emit the noise.

Related to C1/C3 (both are about the app and the graph disagreeing about
routing) but distinct: those are about *user-initiated* device changes being
handled ungracefully, this is about the graph changing underneath the app
without it noticing at all.

**Also a testing-hygiene fix, separately:** `test_routing_dry.py` is named
"dry" but manipulates the live PipeWire graph and will happily delete a
running instance's sink. Either it should refuse to run when a
`music-assassin-live` process is alive, or it should be renamed so nobody
reads it as hardware-free again. The offline suite's other members genuinely
are hardware-free; this one is the odd member and the name hides it.

**Built 2026-08-18.** `diagnose_capture()` in `backends/pipewire.py` is a
pure function over one `pw-dump` snapshot — one snapshot rather than three,
because nodes, links and clients read separately are three different moments
and a graph that moves between them reports "capture is connected to nothing"
instead of the race it is. It returns:

| state | meaning | response |
|---|---|---|
| `trap_lost` | the trap sink is gone from the graph | turn off — nothing is left to re-assert, and audio already reaches the speakers directly, so off is both the correct state and the current one |
| `feedback_loop` | capture is linked to the monitor of the sink we play into | turn off **immediately**, no repair attempt |
| `capture_hijacked` | capture is linked to something that is neither | try `pin_stream()` once; turn off if it fails |

`feedback_loop` gets no repair attempt on purpose: re-pinning polls the graph
for up to 3 s, and every one of those seconds is spent howling and
compounding. The loop is also itself evidence the targeting is broken, so the
repair would most likely fail anyway.

Two supporting changes. **`check()` now distinguishes "the trap is gone" from
"something stole the default"** — indistinguishable from the default sink
alone, but needing opposite responses, and previously conflated: `check()`
would call `set_default()` on a destroyed node id, which fails silently, once
a second, forever, while the app reported itself healthy. Free in the common
case, since a live trap that is still the default never reaches that branch.
And **`diagnose_capture` is on the `RoutingBackend` protocol**, not just the
PipeWire backend — every platform that intercepts audio can reach these
states, and a backend that cannot inspect its graph may return `None` always.

The UI runs it every 5 ticks (~5 s) rather than every tick: these states are
structural, they do not appear and clear between ticks, and the check costs a
`pw-dump`. Fast enough that nobody sits in a loop for long, cheap enough to
leave on permanently.

`tests/test_capture_diagnosis.py` covers all of it with no audio graph —
fixtures copied from the pw-dump the incident actually produced, including
that another client capturing our output sink's monitor (a recorder, a
meter, a screen-share) is normal and must not fire. That last one matters:
a guard that cries wolf gets deleted.

**The hygiene fix landed too:** `test_routing_dry.py` now refuses to run when
it finds a live instance (`ALLOW_LIVE=1` overrides), and its docstring no
longer claims to be safe to run while audio is in use. The process match is
on argv shape, not a substring of the command line — this repository's own
directory is named `music-assassin-live`, so a substring test matches every
shell and editor with the repo path in its arguments.

**Not done, deliberately:** `trap_lost` turns the app off rather than
rebuilding the session in place. Auto-rebuild (`disable()` then `enable()`,
then restart the engine) is the nicer behaviour and is a small change, but it
wants to be designed together with C1's retarget path rather than bolted on
here.

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

- **E1. CI ✅ done 2026-08-18.** `.github/workflows/tests.yml` on push/PR,
  calling `scripts/run_tests.sh` — the same entry point developers run, so the
  two cannot drift into testing different things.

  **The scope above was wrong in two ways and is corrected here.**
  `test_routing_dry.py` cannot run in CI: there is no PipeWire there, and it
  is the test that caused C8's incident by mutating a live graph — a guard is
  not a reason to run it unattended. And `test_processors_offline.py` needs
  the ONNX models, which are gitignored, so in CI it has nothing to test.

  That second one was the interesting part: with no models it iterated an
  empty list and printed **"all passed"** — a green tick meaning "no models
  were installed", read as "the processors are fine". Running that in CI
  would have produced a permanently green, permanently meaningless job. It
  now reports `NOTHING TESTED` and exits non-zero (`ALLOW_NO_MODELS=1` to
  override), and the runner reports it as **SKIPPED** rather than dropping it
  silently — a suite that quietly shrinks is how a check stops covering
  anything without anyone noticing.

  What actually runs: the seven hardware-free files, which need no audio, no
  PipeWire and no display. `test_status_line` imports `ui.app` and therefore
  tkinter, but never constructs a root, so only the import has to resolve —
  hence `python3-tk` in the workflow, and `libportaudio2` because
  `sounddevice` binds it at import.

  **Why now rather than later:** both bugs found on 2026-08-18 had shipped and
  stayed silent — `pin_stream()` had never once succeeded, and C8's detector
  could not see anything it was written to catch. Neither surfaced as a
  failure anywhere, because nothing ran automatically. A build-status badge
  now has something behind it.
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
  on `fix/dpdfnet-norm-init`. **The `noise_only` swing is still unexplained,
  and as of 2026-08-18 has no candidate at all.** C2's unpinned trap-sink
  volume was the standing explanation until it was measured and found
  impossible: sink volume does not scale a monitor (ratio 1.000, see C2), so
  the slider cannot have been changing what the model was fed. Something
  varies between two identical hardware runs by 34 dB and nothing currently
  accounts for it. Do not treat that tier's absolute numbers as reproducible
  until it is found.
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
   2026-08-16 — 57 clips, structurally verified, a second methodology bug
   (chorus-biased excerpt windowing) caught and fixed before it could bias
   the ground truth. See §3.1.
8. ~~**Redo the model + mid/side comparison (B1) quantitatively.**~~ Done
   2026-08-17 against the full corpus — see §5 B1. Result: `dpdfnet_hr` stays
   the best net choice; mid/side at the shipped exponent should **not** be
   flipped on (it net-hurts); `dtln` is a real contender worth a direct
   by-ear A/B against the default.
9. ~~**Rebuild the `.deb`** from merged `main` (E4).~~ Done 2026-08-15 —
   0.1.4, binary smoke-tested, four redistributable models bundled
   (`speechdenoiser` correctly excluded, license still unresolved).

**Remaining in this phase — the one thing still gating the 0.1.4 tag:**

10. **By-ear confirmation of B1's numbers.** `--dump-audio` the corpus for
    `dpdfnet_hr` vs `dtln` (with and without mid/side) and actually listen,
    per the harness's own docstring — B1's numbers rank candidates, they
    don't settle quality, and two of its findings (mid/side hurting, `dpdfnet`
    baseline scoring net-negative) are surprising enough to deserve a listen
    before either goes into release notes or gets acted on further.

### Phase 2 — Make it feel like a product (1–2 weeks)

11. ~~**C1 — gapless output switching.**~~ Done 2026-08-18. The spike
    confirmed a live `target.object` write moves the stream in ~0.01 s with
    ~1.5 ms of callback jitter and no xruns, so the teardown path is gone
    for output changes. It also exposed the `pipewire.sec.pid` matching bug
    that had quietly disabled `pin_stream()` (and C8's detector). See C1.
12. ~~**C2 — volume-key forwarding.**~~ Done 2026-08-18 — though the reason
    it was promoted turned out to be false (sink volume does not scale a
    monitor; measured, ratio 1.000). The real bug was that the keys did
    nothing at all, and the fix is better than the one planned. Note that
    E2's unexplained hardware-tier variance has lost its only candidate.
13. ~~**A6 — report input RMS in `bench_offline()`.**~~ Done 2026-08-15 —
    §4 A6 has recorded it as landed since then; this line said otherwise
    until 2026-08-18.
14. ~~**B3 — stop collapsing output to mono.**~~ Built 2026-08-18
    (`audio/stereo.py`, plus the `wants_stereo` seam A1 needs). **Shipped
    off** — what remains is `scripts/run_b3_sweep.sh` and flipping the
    default on its numbers, not more code. See B3.
15. ~~**C8 — detect an orphaned/hijacked capture stream.**~~ Done
    2026-08-18, the same day it was found by causing it. The app no longer
    reports itself healthy while capturing its own output. See C8.
16. ~~**C3 — coherent system-picker behavior**, **C4 — human status line**
    (and **C7**, latency, which C4's line carries).~~ All done 2026-08-18.
17. ~~**E1 — CI**~~ Done 2026-08-18 — `scripts/run_tests.sh` plus a GitHub
    Actions workflow that calls it. Phase 2 is complete.

### Phase 3 — Make it actually remove music (weeks)

17. **A1 — separator spike** on `feat/separator-spike`: stereo processor
    contract, chunked Spleeter wrapper, latency-vs-quality curve.
18. **A3 — data-driven model registry** (also unblocks C6, E5).
19. **A2 — HS-TasNet training** running in the research repo in parallel
    throughout.

### Phase 4 — Second platform (parallel, own branch)

20. **D4 — close the VB-Audio licensing question.**
21. **D3 — Windows routing backend**, then a real Windows build environment,
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

**Q5 (was: blocks B3 design — now largely answered by building it).** Is mono
output while filtering acceptable, or is stereo preservation a requirement?
It interacts with the mid/side prefilter, which removes the side channel by
design. **Sharper as of B1 (2026-08-17):** measured at −161 dB stereo width on
104/104 pairs, every model, every config tested. **Answered in part as of B3
(2026-08-18):** the "how much engineering" half is settled — restoring the
image turned out to cost one 130-line module and no processor changes, which
is cheap enough that it did not need a decision. What is left is not a
judgement call but a measurement: `run_b3_sweep.sh` prices the suppression it
trades for the image, and the default flips on that. The only judgement left
is the fallback — if the trade is bad, is shipping mono *and saying so in the
UI* acceptable, or does that make the product not worth shipping?

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

- **"The system volume slider changes what the model is fed."** Believed since
  2026-08-14, and the reason C2 was promoted from a UX wart to a measurement
  confound. Disproven 2026-08-18 by direct measurement: a tone captured from a
  sink's monitor is identical at sink volume 1.0 and 0.5 (ratio 1.000) —
  monitors are pre-volume. The volume keys were inert, not corrupting.

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
