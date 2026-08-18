# Handover — current state

Updated 2026-08-18. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.

## Where the code is

**Merged to `main` on 2026-08-15.** Five of the six branches are in; `main` is
at version 0.1.4 and green on the full offline suite (engine recovery, all five
processors, routing dry, imports). **Nothing has been pushed. The 0.1.4 tag is
deliberately not cut yet** — see next actions.

| Branch | What it is | State |
|---|---|---|
| `fix/stream-recovery` | 0.1.4 — `stream_ok`, callback crash containment, soft limiter, 300 % wet boost, `tests/test_engine_recovery.py` | merged (`42fe75d`) |
| `feature/quality-harness` | `tests/bench_quality.py` — the quality measurement harness | merged (`42fe75d`) |
| `fix/e2e-alignment` | `test_live_e2e.py` cross-correlation alignment + engine health counters | merged (`42fe75d`) |
| `refactor/routing-backend` | `RoutingBackend` seam; engine decoupled from PipeWire | merged (`92aeb0a`) |
| `fix/dpdfnet-norm-init` | seeds DPDFNet state from ONNX metadata | merged (`9877b0c`) — **read ROADMAP §2.2/§2.3** |
| `docs/roadmap` | `docs/ROADMAP.md` | merged (`01f6acb`) |
| `feature/windows-packaging` | installer scaffolding | **not merged** — app can't run on Windows yet (D3) |

The pre-merge state is tagged `pre-merge-backup-20260815` (`06f0ac8`) if any of
this needs to be unwound.

## The two things to read before touching model quality

**ROADMAP §2.2/§2.3 — `dpdfnet_hr`'s ONNX norm-init fix.** Its old −30 dB music
suppression was an artifact of a real defect (discarded ONNX normalization
metadata → mis-seeded internal normalizer → output depended on input level by
~36 dB), now fixed. Every previously-recorded suppression figure for this
model from before 2026-08-14 is invalidated.

**ROADMAP §5 B1 — the model + mid/side comparison, done 2026-08-17.** Run
against the real 57-clip stereo corpus (§3.1, see below), 104 item-ratio pairs.
Headline results:

| model | net dSI-SDR | vocal damage | notes |
|---|---|---|---|
| `dpdfnet_hr` | **+4.19 dB (best)** | −5.0 dB (worst) | stays the pragmatic default — best net, at the cost of the most vocal damage |
| `dtln` | +3.59 dB | **−0.9 dB (best)** | ~4× faster; the strongest by-ear candidate against the default |
| `gtcrn` | +2.64 dB | −2.3 dB | its "improved" musical-noise score is likely a hollowed-spectrum artifact, not real |
| `dpdfnet` (16 kHz) | **−0.28 dB (net harmful)** | −3.1 dB | worse than doing nothing on this corpus; don't recommend it |

**Mid/side at the shipped exponent (4), stacked with `dpdfnet_hr`, should
*not* be flipped on** — it deepens suppression 4.4 dB but net SI-SDR drops
(4.19 → 3.23) and vocal-loss frames nearly double (36/104 → 60/104). The
opposite of the hoped-for outcome. Keep it off; a lower exponent is the more
promising next experiment (ROADMAP B2), not exposed as a default yet.

**Also newly measured, universal: stereo width collapses to −161 dB on every
single one of 104 pairs, every model, mid/side on or off.** Not occasional —
every time the filter runs, the output goes fully mono. **Fixed in code
2026-08-18 but shipped OFF** — see "The stereo work" below.

**Sharper finding, category breakdown (`male_lead`, 8 clips — added
specifically to test the 2026-07-24 "cuts girl vocals" complaint from the
other direction).** `dpdfnet_hr`'s net advantage nearly disappears on male
leads specifically: dSI-SDR +4.19 dB whole-corpus → **+0.33 dB** on this
category, with its worst vocal damage anywhere (−8.5 dB). `dtln` and `gtcrn`
both net-beat it here. On `sparse_acoustic` the opposite holds — `dpdfnet_hr`
pulls far ahead (+9.39 dB, 8× the others). Its advantage is concentrated in
sparse/quiet content, not uniform. Not a resolution of the original
female-vocal complaint (adjacent question, different vocals) but the single
most actionable lead from this session.

**One unresolved anomaly, flagged rather than papered over:** on
`dual_mono_control` (n=4, expected-null — no real side channel), turning
mid/side on makes vocal retention *worse* (−15.3 → −20.8 dB) while aggregate
dSI-SDR simultaneously *improves* (−2.58 → +0.85 dB). Something in the metric
or mix path behaves oddly at very low side-channel energy. Not root-caused —
don't build on this category's numbers yet.

**By-ear audio exists — for `male_lead` and `sparse_acoustic` — but nobody has
listened yet.** `~/.local/state/music-assassin/bench/b1/audio_{male,sparse}/`,
covering input/oracle/ceiling/`dpdfnet_hr`/`gtcrn`/`dtln` at two ratios each.
Not yet rendered for the full corpus or for `dtln` vs `dpdfnet_hr` head-to-head
outside those two categories. Priority listen: `male_lead` — that's where the
numbers disagree most with the standing default.

## The stereo work (2026-08-18) — built, not yet switched on

**`assassin_live/audio/stereo.py`** rebuilds the stereo image around the
mono enhancers instead of writing the processed signal to both channels. It
recovers the chain's implied spectral mask from the audio itself
(`|Wet| / |Dry_mono|` per bin) and applies that one mask to both original
channels, so panning is preserved exactly while the model's per-band
suppression still lands. No processor changes — it works for all four shipped
models at once. Full reasoning, including why the cheaper side-re-injection
option was rejected (it hands back the music the model just removed), is in
ROADMAP B3.

**`StreamProcessor.wants_stereo`** landed with it — the A1 prerequisite the
previous handover flagged as blocking three items and needing input from
nobody. A processor that sets it gets `(n, 2)` and returns `(n, 2)`; the
engine skips both the downmix and the rebuild and builds 2-channel
resamplers. Nothing shipped sets it; Spleeter will.

**It is OFF by default (`AudioEngine.set_stereo`).** It changes what every
pipeline sends to the speakers, and §2.2 records what flipping a default on
an expectation cost last time. The trade is real: width that survives is
width in bands the model kept, so music sitting in those bands comes back
too.

**What remains is measurement, not code.** `scripts/run_b3_sweep.sh` —
whole-corpus `stereo=off,on`, the mid/side interaction, the
`dual_mono_control` expected-null, and `--dump-audio` for the by-ear pass.
Flip the default if `stereo_width_db` climbs substantially with
`music_supp_db` and `delta_si_sdr_db` essentially unmoved and RTF still in
budget. If suppression drops materially, ship mono and say so in the UI.

Verified without models or hardware (`tests/test_stereo_rebuild.py`, all
green): unity mask reconstructs the dry pair to 1.8e−07, a fully-suppressing
chain invents no width, a killed hard-panned band does not leak back, chunk
size does not change the samples, and end-to-end through `AudioEngine` the
default still measures the mono collapse (−235 dB) while `set_stereo(True)`
restores the surviving image. A synthetic-corpus `evaluate()` run moves
`stereo_width_db` −165.7 → −5.2 dB, clears the `stereo-collapse` tag, and
leaves every separation metric bit-identical.

## An incident worth reading before running the test suite

`tests/test_routing_dry.py` is named "dry" but **manipulates the live
PipeWire graph**, and its cleanup step will delete a *running* instance's
trap sink, judging it stale. That happened on 2026-08-18. PipeWire then
re-attached the app's orphaned capture stream to the real output sink's
monitor — which the app also plays into — producing an audio feedback loop
that ran until the app was killed. `stream_ok` stayed true throughout: the
stream was alive and healthy, just wired to the wrong thing.

**Both follow-ups are fixed as of 2026-08-18 (ROADMAP C8).** The app now
verifies *what* it is capturing every ~5 s, not just that the stream lives:
`diagnose_capture()` reports `trap_lost`, `feedback_loop` (capturing the
monitor of the sink we play into) or `capture_hijacked`, and the UI turns off
on a loop immediately rather than attempting a repair that would spend 3 s
howling. `check()` also now tells "the trap is gone" apart from "something
stole the default" — it used to conflate them and call `set_default()` on a
destroyed node id once a second, forever, while reporting healthy. And
`test_routing_dry.py` refuses to run when it finds a live instance
(`ALLOW_LIVE=1` overrides).

Still true, and worth keeping in mind: **that test is not hardware-free** and
should not be run casually just because it sits in `tests/`. The guard covers
the case it caused; it does not make the test safe in general.

## Immediate next actions

1. **Listen to the rendered audio.** `~/.local/state/music-assassin/bench/b1/audio_male/`
   first (the male-vocal finding above), then `audio_sparse/`. This is the one
   thing still gating the 0.1.4 tag — release notes shouldn't quote numbers
   nobody has heard, especially the surprising ones above. If it confirms the
   quantitative lead, extend `--dump-audio` to the rest of the corpus and to a
   direct `dpdfnet_hr` vs `dtln` A/B before deciding anything about the
   default.
2. **Cut the 0.1.4 tag and push** once item 1 confirms (or revises) what the
   release notes should say. The `.deb` is already rebuilt from merged `main`
   (2026-08-15, binary smoke-tested, `speechdenoiser` correctly excluded for
   its unresolved license).
3. **Run `scripts/run_b3_sweep.sh`** (detached, same as the B1 sweep) and
   flip — or don't flip — the stereo default on what it says. This is the
   only thing standing between the −161 dB image collapse and it being
   fixed for real; the code is written and tested.
4. Then Phase 2 in the roadmap: **C1** gapless device switching (the
   originally reported pain point), **C2** volume forwarding, **C4** a
   human-readable status line. (**C8** is done — see above.)

The **stereo processor contract** that previous handovers flagged as buried
inside A1 is no longer a blocker: `wants_stereo` landed 2026-08-18, so A1 can
hand Spleeter a real stereo pair on day one.

## Open questions for the user

Listed in full as ROADMAP §10. The ones that block work right now:

- **Latency ceiling** — hard product constraint for the separator work (A1);
  ~50–70 ms and ~500 ms lead to different designs. Gates A1's *conclusion*,
  not its start — the spike's own job is to produce the latency-vs-quality
  curve that answers this, not to be handed the answer up front.
- **Mono output while filtering** (Q5) — mostly overtaken by events: the fix
  cost one module and no processor changes, so "is it worth engineering
  around" no longer needs an answer, and the sweep decides the rest. The one
  judgement left: if the trade turns out bad, is shipping mono *with the UI
  saying so* acceptable?
- **Wide-stereo corpus material** — the corpus can prove the rebuild works
  but not that it fails gracefully (median S/M −8.2 dB, nothing hard-panned
  or out-of-phase). ~6–10 clips would close it; `bench_quality.py` already
  reserves the category name `stereo_torture`. Shopping list in ROADMAP
  §3.1.

## What got resolved this session (2026-08-16/17)

- **Stereo corpus**: built and used. 57 real clips (`~/Music/MusicAssassin/Corpus/`),
  8 categories, structurally verified before separation. A 2026-08-15 attempt
  from `~/Music/Acapella/` failed outright — that folder is 100% vocal-only,
  don't retry it. A second methodology bug (the excerpt window-picker biasing
  toward the loudest/chorus moment, understating how low male vocals in this
  material actually go) was caught and fixed before it could bias results —
  see ROADMAP §3.1 for the full story, including why `male_lead`/`rap`/
  `sparse_acoustic`/`orchestral_dialogue` were added (the original 39 files
  were 100% female vocal, median F0 331 Hz).
- **B1**: run to completion, real findings above.

## Environment notes

- Python: `.venv/bin/python` in the repo root. Models live in
  `~/.local/share/music-assassin/models/`.
- Corpus and sweep outputs live under `~/.local/state/music-assassin/bench/`
  (`corpus/` for the built stems, `b1/` for this session's sweep CSVs/logs,
  `quality/latest.json` for the harness's own diff-vs-previous state).
- `scripts/run_b1_sweep.sh` is what actually produced §5 B1's numbers — one
  `bench_quality.py` invocation per model rather than one combined `--sweep`,
  because the harness only prints its comparison table once the whole
  invocation finishes, and a combined run took ~7 h with nothing on disk until
  it did. Detached the same way as corpus builds; reruns skip whatever already
  has a CSV. `speechdenoiser` is deliberately excluded from it — its license
  is unresolved, so it can't be the shipped default no matter how it scores.
- Building the harness's reference corpus needs `demucs` (torch), which this
  app deliberately never depends on — run it out-of-process via
  `--demucs-python ~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python`.
- **Detach long runs from the editor.** VS Code crashing has already killed one
  corpus build mid-run (and once triggered an OOM kill). Start them with
  `setsid nohup … &` and verify with `ps -o pid,sid` that SID == PID.
  `build_refs` checkpoints its manifest after every source and skips completed
  clips, so rerunning the same command resumes; `--rebuild` forces a redo.
- **Excerpt before separating, and use `htdemucs` not `mdx_extra`.**
  `scripts/corpus_excerpt.py` cuts a 30 s excerpt per source before demucs ever
  sees it. It also refuses acapella-looking sources outright and biases its
  window pick toward the first vocal entrance rather than the loudest moment.
  `mdx_extra` peaks at 3.6 GB / 132 s even on a 30 s excerpt; `htdemucs` is
  1.2 GB / 39 s for a modest quality loss — use it on this machine, and it's
  the corpus's separator of record.
- `tests/test_live_e2e.py`'s hardware tier and anything calling
  `RoutingSession.enable()` **take over the system default audio sink**.
  `python -m assassin_live --recover` restores it if something dies mid-run.
  Nothing in this session's work touched system audio.
- `.claude/worktrees/` is scratch space for parallel agent worktrees; still
  not gitignored (noted in the previous handover, still true) — worth adding
  if it keeps appearing in `git status`.
