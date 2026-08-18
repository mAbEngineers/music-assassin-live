# Handover — current state

Updated 2026-08-17. **This file is deliberately short.** It covers only where
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
every time the filter runs, the output goes fully mono. Promoted to Phase 2 in
the roadmap (was Phase 3, gated on the separator work; it no longer needs
that).

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
3. Then Phase 2 in the roadmap: **C1** gapless device switching (the
   originally reported pain point), **C2** volume forwarding, **B3** stop
   collapsing to mono (promoted this session — see above), **A6** report
   input RMS in `bench_offline()` so a repeat of the §2.1 investigation is
   visible at a glance instead of taking hours.

Worth reordering ahead of Phase 3 when you get there: the **stereo processor
contract** (`wants_stereo`, engine stops downmixing) is currently buried inside
A1, but three separate blocked items sit behind it — A1 (Spleeter crashes on
mono input), B3 (the mono-output regression, now measured universal), and Q5
(mono-acceptability stops being a question once stereo is possible). It needs
no input from anyone.

## Open questions for the user

Listed in full as ROADMAP §10. The ones that block work right now:

- **Latency ceiling** — hard product constraint for the separator work (A1);
  ~50–70 ms and ~500 ms lead to different designs. Gates A1's *conclusion*,
  not its start — the spike's own job is to produce the latency-vs-quality
  curve that answers this, not to be handed the answer up front.
- **Mono output while filtering** (Q5) — now measured universal, −161 dB,
  every model, every config. Acceptable for now, or worth engineering around
  before A1 changes the output path anyway?

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
