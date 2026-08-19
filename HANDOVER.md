# Handover — current state

Updated 2026-08-19. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.

## Where the code is

**Nothing is pushed, and the 0.1.4 tag is still not cut.**

| Branch | Contents | State |
|---|---|---|
| `main` | 0.1.4, green on the offline suite | at `8df4f83` |
| `feature/stereo-output` | B3 stereo rebuild, `wants_stereo`, C1, C2, C3, C4, C7, C8, E1 | **13 commits, unpushed** |
| `feat/separator-spike` | A1 spike + the SpleeterProcessor and its sweep, then B6/B7/C9 | 5 commits, off the above |
| `feature/windows-packaging` | installer scaffolding | not merged — app can't run on Windows (D3) |

Pushing needs to happen from a machine with credentials — this session had
none (`gh` installed but not logged in, no credential helper):

```
git push -u origin feature/stereo-output
git push -u origin feat/separator-spike
```

That first push is also the **first exercise of the new CI workflow**
(`.github/workflows/tests.yml`). Expect a possible iteration on the apt/pip
step; it has never run.

## What gates the 0.1.4 tag

**The app has now been run** (2026-08-19, from the repo:
`.venv/bin/python -m assassin_live` — not the `.deb`, which is still `main`).
That session produced four defect reports, three fixed and one open. What
remains before tagging:

**1. Doubling still reported with the stereo rebuild on `dpdfnet_hr`.**
B6's fix is confirmed working on `dtln` (8 ms lag) and in the offline
regression test with a processor lagging 2400 samples, which the estimator
measures to 0.1 ms. So the mechanism is right and something about
`dpdfnet_hr` in the live app is not.

Leading suspects, in order: the runtime measurement lands on a wrong value
for real content at stream start; or the residual is the rebuild's own
framing rather than the model lag. The estimator's two ways of failing
quietly have since been closed (ROADMAP B6b), so a bad measurement can no
longer disguise itself as a good one — but the reading still decides which
of the remaining suspects it is.

**Read `processor lag:` in the details panel while you can hear it.** It now
says one of three things, and they are different answers:

| reading | meaning |
|---|---|
| `50.0 ms` | measured, correction applied — the residual is something else |
| `measuring…` | not yet decided; it needs 1.5 s of non-silent audio |
| `unmeasured (rebuild bypassed)` | no peak worth believing; nothing corrected, rebuild off |

Note what `mix:` says too. Below 100 % it used to be a second, independent
source of doubling — fixed the same day (ROADMAP B8), and worth confirming
by ear.

**2. One unexplained report: audio sometimes does not play at all when
starting from idle.** Not the old broken `pin_stream()` — that is fixed on
this branch and the run was from the repo. Startup lag (B7) explains *late*,
not *silent*.

**When it happens, read the status line and the meter.** Every path that
stops the audio writes a distinct message, so it identifies itself — and the
meter now draws the captured input above the emitted output (C10), which
splits the one case the messages cannot name:

| meter | meaning |
|---|---|
| `in` moving, `out` flat | the engine or the mix — not capture |
| both flat | capture: nothing is arriving |
| both moving, nothing audible | routing past our output — the "healthy but silent" case |

The status-line messages:

| message | meaning |
|---|---|
| `feedback loop detected (capturing our own output) — turned off` | C8 fired — possibly correctly, possibly a false positive |
| `capture is connected to the wrong device — turned off` | C8, wrong capture peer |
| `audio device disappeared — turned off, switch back on to rebuild` | trap sink gone |
| `audio stream died, restart failed: …` | PortAudio stream lost |
| `Filtering → … · healthy` **while silent** | the interesting case: healthy stream, no sound — routing, not the engine |

**By-ear: which model** — partly done and **partly confounded**, see below.

**3. By-ear: does the stereo rebuild bring music back** — still unanswered,
and now blocked behind item 1.

**4. The dry/wet mix was misaligned by the processor's internal lag** —
**fixed 2026-08-19 (B8)**, and the last thing it needs is ears. B6 padded the
worker's dry copy, which feeds the stereo mask, and left the callback's
alone, so below 100 % mix the blend was two streams 50 ms apart. Both are
padded now: measured 0.0 ms skew with the fix against 49.9 ms without it. The
mix slider should now work as the tool it was meant to be — trading vocal
damage against music removal — which is worth confirming before B4 starts
producing candidates to compare with it.

## The by-ear results so far (2026-08-19)

| finding | status |
|---|---|
| `dtln` leaves audible music noise and sounds muffled | matches the numbers: −20.6 dB music left vs `dpdfnet_hr`'s −46.1, and an air band at −59.7 dB (it is 16 kHz-native) |
| `dpdfnet_hr` drops vocals more, but leaves no music noise | matches: `vocal-loss` 36/104 pairs, `music_supp` −46.1 dB |
| stereo rebuild doubled the audio | **B6 fixed** — confirmed gone on `dtln`, but **still reported on `dpdfnet_hr`**, see open items |
| starting from idle came up late / out of sync | **B7, fixed** |
| the ON/OFF button gave no feedback and re-clicking reversed it | **C9, fixed** |

**`dpdfnet_hr` stays the default.** `dtln` and `gtcrn` are both 16 kHz-native
and delete everything above 8 kHz, which is disqualifying for anyone who
notices it — so the choice was never really three-way.

**But treat the model ranking as provisional.** After B6 was fixed the same
listener reported `dtln` with "better audio retention and much less music
noise", which is a different judgement from the first pass. If the stereo
toggle was on during that first comparison, it ran through a mask 50 ms out
of step and every model would have sounded smeared and noisier. The
comparison is worth redoing now that the rebuild is correct — blind, via
`scripts/ab_listen.sh`, rather than through the app.

**The standing product judgement, which no metric captures:** vocal
preservation matters more than music removal. B1 ranked by dSI-SDR, which
weights them the other way, and that is how `dtln` came out ahead on paper
while destroying the top octave of every voice.

## The stereo work (B3) — built, measured, still shipped OFF

`assassin_live/audio/stereo.py` rebuilds the image by recovering the chain's
implied spectral mask from the audio (`|Wet| / |Dry_mono|` per bin) and
applying that one mask to both original channels. No processor changes, so all
four models get it at once. Full B3 sweep, whole corpus, 104 item-ratio pairs:

| config | music_supp | vocal_ret | dSI-SDR | stereo_width |
|---|---|---|---|---|
| `dpdfnet_hr` | −46.13 | −4.96 | **+4.19** | −160.93 |
| `dpdfnet_hr+st` | −46.13 | −4.96 | **+4.19** | **−6.14** |
| `dpdfnet_hr+ms4` | −50.52 | −7.41 | +3.23 | −160.93 |
| `dpdfnet_hr+ms4+st` | −50.52 | −7.41 | +3.23 | −8.17 |

Per category, stereo off → on: `sparse_acoustic` −161.5 → **−1.99 dB**,
`male_lead` −163.0 → −10.6 dB. The `stereo-collapse` failure tag goes
104/104 → 4/104. RTF +0.0065, latency +5.3 ms. Orthogonal to mid/side, which
keeps its own B1 penalty either way.

**Read the identical columns correctly.** Every mono metric is unchanged *by
construction*, not by luck: one mask applied to both channels leaves the mono
downmix arithmetically the same signal. The music that returns lives only in
the side channel, and **nothing in that table measures the side channel**
except `stereo_width_db`, which counts its energy without caring whether it is
voice or a guitar. So the sweep says "free as far as the harness can see", and
the harness is structurally blind to the one cost this change could have.
Hence gate 3 above. Off by default until someone listens; the
"Preserve Stereo Image" toggle is in the UI and persisted.

## Two retractions and one hole — read before trusting older claims

**`pipewire.sec.pid` is not the application's pid.** Anything arriving through
pipewire-pulse (how PortAudio's `pulse` device connects) is proxied, so the
client reports the *daemon's* pid — on this machine Firefox, GNOME's volume
control and our own streams all reported 2122. `pin_stream()` had therefore
been finding nothing and returning False **since it was written**, and C8's
capture detector inherited the same lookup and was silently vacuous — it would
never have fired on the incident it was written for. Use `our_stream_nodes()` /
`_owner_pid()` (`application.process.id`, falling back to `pipewire.sec.pid`
for native clients). Found only by running the C1 spike.

**A sink's volume does not scale its monitor.** C2 was promoted from a UX wart
to a *measurement confound* on the belief that the volume slider was changing
what the model is fed. Measured: a tone captured from a monitor is identical at
sink volume 1.0 and 0.5, **ratio 1.000**. The keys were inert, not corrupting.
The mirror still shipped (they work now), but —

**E2's `noise_only` swing of −55.7 → −21.7 dB across two identical hardware
runs has lost its only explanation and is now unaccounted for.** 34 dB of
run-to-run variance with no candidate. Do not treat that tier's absolute
numbers as reproducible.

## A1 (Phase 3) — spiked, and the framing was wrong

A1 is written around "RTF 0.067, ~15× headroom". Real, but measured on a 60 s
buffer. Against chunk size (1 thread, `scripts/spike_a1_separator.py`):

| chunk | ms/call | RTF | headroom |
|---|---|---|---|
| 0.25 s | 153.3 | 0.613 | 1.6× |
| 1.00 s | 129.0 | 0.129 | 7.8× |
| 8.00 s | 205.1 | 0.026 | 39.0× |

**Cost per call is flat.** It is fixed overhead, not work proportional to the
audio, so RTF improves only because the denominator grows. Latency is the
binding constraint and RTF is nearly irrelevant. Practical floor ≈ 1 s chunks
→ **≥1.1 s end-to-end**, so this can only ever be an additional high-latency
mode; the ~50 ms path is unreachable with it. That answers **Q3 by force**.

Also: peak RSS 478 MB; stems[0] is vocals, [1] accompaniment, confirmed by
cross-correlation.

## A1 — the processor exists now, and it corrected three of the spike's notes

`assassin_live/processors/spleeter.py` is the first `wants_stereo` processor:
chunked, overlap-discard, `spleeter_<n>ms` registry variants so the chunk is a
`--sweep` axis. `tests/test_spleeter_chunking.py` pins the behaviour;
`scripts/run_a1_sweep.sh` prices it. Building it moved three of the spike's
notes, all by measurement:

- **There is no remainder to carry.** Output length is the input truncated to
  a multiple of 1024 — *when fed at 44100*. The spike saw 44100 → 44032 and
  read it as loss; it is quantisation. Feed any other rate and sherpa
  resamples internally, the returned length is in the **output** rate, and
  input offsets stop matching output offsets. Hence `sample_rate = 44100`.
- **The impulse is at both ends, and it is far bigger than recorded.** Not
  ~26 samples at |466| at the head: on real corpus audio, **11043** in the
  first 8 samples against an interior peak of **1.28**, plus a smaller one
  (863) at the tail. Context is discarded on both sides. Emitted peak is now
  0.57× the input peak, no sample above 2.0.
- **Chunking is not an approximation of one-shot, and context does not fix
  it.** Interior chunks land **8–11 dB** below a whole-buffer run, flat across
  0.25/0.5 s of context and 1/2/4 s chunks. Not a seam artifact: one fitted
  gain per chunk explains almost none of it (−7.8 → −8.1 dB) and the residual
  is spread through the chunk, not piled at its edges. Spleeter masks a
  spectrogram whose context is the whole buffer, so a chunked run is a
  different computation. Whether that costs anything *audible* is what the
  sweep asks.

**The bench measured a chunked processor's latency as zero**, and its >120 ms
lip-sync failure tag could therefore never fire for the one processor it was
written for. A chunk-buffering processor emits output whose first sample still
corresponds to input sample 0 — the delay is in when samples become
*available*, and concatenating a file offline erases exactly that. `evaluate()`
now reports `max(measured, declared)`. This is the same blindness B3 has with
the side channel, in a different column.

**Do not read RTF from the A1 sweep as headroom.** It averages a 20 ms block
budget over a process that only runs every `chunk_ms`. The spike's table is
the latency authority.

### A1 sweep — complete, all three steps, and the curve never flattens

`~/.local/state/music-assassin/bench/a1`. Whole corpus, 104 item-ratio pairs:

| config | music | vocal | dSI-SDR | musNoise | stereo | lat ms |
|---|---|---|---|---|---|---|
| `spleeter_4000ms` | −35.03 | **−0.17** | **9.72** | 8.91 | −11.63 | 4093 |
| `spleeter_2000ms` | −36.83 | −0.22 | 7.89 | 15.84 | −12.72 | 2093 |
| `spleeter_1000ms` | −37.79 | −0.32 | 6.58 | 23.17 | −12.97 | 1093 |
| `spleeter_500ms` | −38.06 | −0.63 | 4.56 | 26.57 | −14.53 | 593 |
| `dpdfnet_hr` | **−46.13** | −4.96 | 4.19 | **1.02** | −160.93 | 50 |
| `spleeter_250ms` | −35.39 | −1.32 | 2.11 | 13.62 | −15.55 | 343 |

**The `dpdfnet_hr` row reproduces B3's whole-corpus row exactly** (−46.13 /
−4.96 / +4.19 / −160.93). That is the cross-check that the `wants_stereo`
branch added to `run_chain` left the mono path untouched — the baseline is
bit-for-bit the number B3 measured before the change.

Whole-corpus per-band vocal damage, sub/low/mid/high/air: `dpdfnet_hr`
−4.8 / −4.3 / −7.0 / −8.6 / −9.1 against `spleeter_4000ms` −0.3 / −0.1 /
−0.2 / −0.6 / −2.6. `dpdfnet_hr` fires `vocal-loss` on 36/104,
`hf-loss-4k` 30/104, `hf-loss` 28/104 and `stereo-collapse` 104/104; Spleeter
fires none of the first three at any chunk (bar 2/104 at 250 ms). It also
removes **109%** of the offline ceiling's music — it over-suppresses — while
reaching 17% of its dSI-SDR against `spleeter_4000ms`'s 40%.

`male_lead` (8 clips × 2 ratios), the category where B1 found `dpdfnet_hr`
weakest and most vocally damaging, is the same story amplified:

| config | music | vocal | dSI-SDR | musNoise | stereo | lat ms |
|---|---|---|---|---|---|---|
| `spleeter_4000ms` | −38.85 | **−0.40** | **8.65** | 21.29 | −12.86 | 4093 |
| `spleeter_2000ms` | −41.04 | −0.46 | 6.65 | 33.58 | −13.95 | 2093 |
| `spleeter_1000ms` | −40.51 | −0.53 | 4.77 | 38.98 | −14.75 | 1093 |
| `spleeter_500ms` | −41.81 | −0.77 | 2.38 | 47.97 | −17.04 | 593 |
| `dpdfnet_hr` | **−48.38** | −8.47 | 0.33 | **6.81** | −162.98 | 50 |
| `spleeter_250ms` | −35.54 | −1.43 | 0.13 | 30.72 | −18.56 | 343 |

**dSI-SDR rises monotonically with chunk size and is still rising at 4 s**, on
both categories swept. The sweep was written to find where it flattens so the
smallest acceptable chunk could be read off; in this range there is no such
point. Quality keeps buying latency for as long as you will pay, which
sharpens A1 rather than settling it — the configs that beat the shipped
default are the least shippable ones.

**Per-band is where the case actually is.** On `male_lead`,
sub/low/mid/high/air vocal damage: `dpdfnet_hr` −8.1 / −7.3 / **−11.3** /
**−11.7** / **−13.0** against `spleeter_4000ms` −0.7 / −0.3 / −0.4 / −1.6 /
−2.8. `dpdfnet_hr` fires `vocal-loss` 8/16, `hf-loss` 4/16, `hf-loss-4k`
2/16; no Spleeter config fires any of them. It reaches **1%** of the offline
ceiling's dSI-SDR here, against 38% for `spleeter_4000ms`. This is not the
SI-SDR scoring trap — the band table measures the damage directly.

**What Spleeter pays for it:** 7–10 dB less music removed (−39 vs −48) and
3–7× the musical noise (tag fires 8–15/16 vs 5/16). `long-burst` and
`pumping` also fire more. So the trade is the mirror image of the shipped
default's: voice intact, more music left, noisier.

**The chunking penalty scales with chunk size, as the −8 to −11 dB
chunked-vs-one-shot gap predicted.** `boundary-sensitive` over the whole
corpus: 102/104 at 250 ms, 70 at 500 ms, 28 at 1 s, 14 at 2 s, 4 at 4 s.
Musical noise tracks it (26.6 → 8.9 across the same range), so the artifact
cost and the quality gain point the same way — both argue for bigger chunks,
which is the whole problem. 250 ms is the only config whose vocal damage is
worse than `dpdfnet_hr`'s in any band.

The `chunk_all` step took 7.5 h; the two scoped steps 34 and 72 min.

On `sparse_acoustic` the same shape holds (dSI-SDR 4.27 → 13.06 across the
range, `dpdfnet_hr` 9.39) but the gap is far smaller, because `dpdfnet_hr`
is not damaging voices there — vocal −0.88 rather than −8.47.

**Still needs ears.** Audio is dumped at `a1/audio_sparse` and `a1/audio_male`.
The open question is the product one: is "voice intact, more music left,
noisier" better than "music gone, voice chewed"? No metric here settles it.

Environment: sherpa-onnx stays **out** of `requirements.txt` (imported lazily,
so the app venv is unchanged and the separator simply does not appear there).
Use `~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python` (1.13.4). Models
come from `Music-Assassin/models/sherpa_onnx/sherpa-onnx-spleeter-2stems-int8/`
(2 × 26 MB — note the `sherpa_onnx/` path component); `scripts/import_models.py`
now copies them in under the names the registry looks up.

## What landed 2026-08-19 (second session)

- **B8** — the dry/wet mix carried B6's skew. The lag pad reached
  `_dry_work` (the mask) and not `_dry_out` (the mix). Both now, handed to
  the callback thread the way `_pending_lag` is handed to the worker.
  `latency_ms` subtracts the pad and the FIFO's cap grows by it, or the
  correction would be trimmed away again. Regression test measures 0.0 ms
  skew, and 49.9 ms with the fix disabled.
- **B6b** — the lag estimator could give up and call it zero. Silence no
  longer consumes windows (20 s of it used to cost thirteen), and exhaustion
  now means *unmeasured* — no correction, rebuild bypassed, and a state the
  UI names — rather than a silent assumption of zero. Falling back to the
  processor's declared latency was considered and rejected: `dpdfnet_hr`
  declares 10 ms and measures 50, so that is a 40 ms error, not a fallback.
- **C10** — the meter draws input above output, and the details panel prints
  both as dBFS. Diagnostic for open item 2 above.
- **C11** — control panel redesign researched against four shipping audio
  tools, direction chosen (680 × 430 two-column), **not built** — sequenced
  after the tag as the first piece of 0.2. Rules, colour resolution and the
  widget-kit cost are in ROADMAP C11.

Suite green after all of it, including the model tier
(`test_processors_offline`).

## What landed 2026-08-19 (first session)

- **B6** — the stereo rebuild's mask was 50 ms out of step on `dpdfnet_hr`,
  because the engine paired wet with dry by FIFO position. The lag is now
  measured at stream start (`audio/lag.py`) and the dry stream delayed by it.
  Two mistakes on the way, both recorded in ROADMAP B6: correlating on the
  inference worker took the pipeline floor from 20 ms to 60 ms, and a
  tone-only test signal reported lag 0 with confidence 0.46 because the true
  lag was ~11 periods of the test tone.
- **B7** — cold ONNX warm-up took 26 blocks against 1 warm, and everything
  arriving meanwhile piled into the dry FIFO, whose depth *is* the session's
  latency. 140 ms baked in instead of 20, varying run to run. The processor
  is now warmed inside `start()` before the stream opens.
- **C9** — the ON/OFF button ran its work on the Tk thread, so it could not
  repaint and a second click *queued* rather than cancelling, undoing the
  action. Now threaded, with four states and disabled while transitioning.

## What else landed 2026-08-18

- **C1** live output retarget: a `target.object` write moves a running stream
  in 0.01–0.04 s with ~1.5 ms of callback jitter and no xruns (spiked 3×). The
  teardown path is gone for output changes, and the model keeps its state.
- **C3** the picker: debounce 1.5 s → 0.25 s (its stated reason — "each
  restart is an audible glitch" — died with C1), dropdown follows external
  changes, one code path for all three ways an output changes.
  `real_sink_changed` split from `real_sink_replaced` so a sleeping headset no
  longer overwrites a saved preference.
- **C4/C7** status line: `Filtering → device · latency · health`, counters
  behind a toggle. Latency is **measured** from the lockstep FIFO. Its floor
  is one block of queue hand-off (~20 ms) even with a zero-latency model —
  relevant to A1, whose budget starts 20 ms in debt.
- **C8** capture verification every ~5 s: `trap_lost`, `feedback_loop`,
  `capture_hijacked`. Turns off immediately on a loop rather than attempting a
  repair that would spend 3 s howling.
- **E1** CI: `scripts/run_tests.sh`, one entry point for humans and CI.
  `test_processors_offline.py` used to print "all passed" with no models
  installed — a vacuous green; it now says `NOTHING TESTED` and exits
  non-zero, and the runner reports it as SKIPPED.

## Open questions for the user

Full list in ROADMAP §10. Live ones:

- **Q3 latency ceiling** — effectively answered by the A1 spike: a separator
  costs ≥1.1 s. The remaining question is a product one: ship it as a
  high-latency mode, or not at all?
- **Q5 mono output** — the engineering half is settled (the fix cost one
  module). What is left: if the by-ear pass finds the restored side channel
  brings back audible music, is shipping mono *and saying so in the UI*
  acceptable?
- **Wide-stereo corpus** — ~6–10 hard-panned / EDM / out-of-phase / binaural
  clips. The corpus proves the rebuild works but cannot show it failing
  gracefully (median S/M −8.2 dB, nothing hard-panned).
  `bench_quality.py` already reserves the category name `stereo_torture`.
- **Q1** (blocks B4) and **Q2** (blocks C5) unchanged.

## Environment notes

- Run the suite with `./scripts/run_tests.sh` — seven hardware-free files
  (~10 s), plus the model benchmark (~80 s) when the ONNX files are present.
- **`tests/test_routing_dry.py` is not hardware-free** despite its name and is
  deliberately excluded from the runner. It destroys every sink named
  `MusicAssassin`; on 2026-08-18 it did that to a running instance, and
  PipeWire re-attached the orphaned capture stream to the real output's
  monitor — an audio feedback loop that ran until the app was killed. It now
  refuses when it finds a live instance (`ALLOW_LIVE=1` overrides).
  `tests/test_live_e2e.py`'s hardware tier also takes over the default sink;
  `python -m assassin_live --recover` restores it.
- Python: `.venv/bin/python` in the repo root. Models resolve from
  `~/.local/share/music-assassin/models`, the repo's `models/`, or
  `/usr/share/music-assassin-live/models` (the `.deb`'s).
- **`models_dir()` resolves somewhere surprising under the VS Code snap.** It
  honours `XDG_DATA_HOME`, and a terminal inside the snap exports
  `XDG_DATA_HOME=~/snap/code/<rev>/.local/share` — so `~/.local/share/
  music-assassin/models` is never consulted, resolution falls through to the
  `.deb`'s `/usr/share` subset, and a run dies with *"model(s) not installed"*
  naming models that are installed. Set `MUSIC_ASSASSIN_MODELS` explicitly for
  anything long-running; `run_a1_sweep.sh` warns when it is unset.
- Corpus and sweep outputs under `~/.local/state/music-assassin/bench/`
  (`corpus/`, `b1/`, `b3/`). **The corpus lives on the machine that built it**
  — `bench_quality.py --corpus` and the sweep scripts' `CORPUS=`/`OUT=` let a
  run read it over a mount, but RTF and ms/block are then properties of
  whichever machine ran it and are not comparable to B1's.
- Building the harness's reference corpus needs `demucs` (torch), which this
  app deliberately never depends on — run it out-of-process via
  `--demucs-python ~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python`.
- **Detach long runs, and never edit a running script.** `setsid nohup … &`,
  verify SID == PID. bash reads scripts incrementally by byte offset, so
  editing one mid-run makes it resume at a shifted position — observed
  2026-08-18: the sweep jumped backwards and re-ran a completed step, silently
  skipping the next one.
- **Excerpt before separating, and use `htdemucs`.**
  `scripts/corpus_excerpt.py` cuts a 30 s excerpt, refuses acapella-looking
  sources, and biases its window toward the first vocal entrance rather than
  the loudest moment. `mdx_extra` peaks at 3.6 GB / 132 s; `htdemucs` is
  1.2 GB / 39 s and is the corpus's separator of record.
- `.claude/worktrees/` is scratch space for parallel agent worktrees; still
  not gitignored.
