# Handover — current state

Updated 2026-08-15. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.
(An earlier, much longer `HANDOVER.md` was superseded by the roadmap; this
replaces it.)

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
| `fix/dpdfnet-norm-init` | seeds DPDFNet state from ONNX metadata | merged (`9877b0c`) — **read ROADMAP §2.2** |
| `docs/roadmap` | `docs/ROADMAP.md` | merged (`01f6acb`) |
| `feature/windows-packaging` | installer scaffolding | **not merged** — app can't run on Windows yet (D3) |

The pre-merge state is tagged `pre-merge-backup-20260815` (`06f0ac8`) if any of
this needs to be unwound.

The first three branches were a **stack, not siblings** — `fix/e2e-alignment`
already contained the other two, so one merge brought all three in order. (An
earlier revision of this file and of ROADMAP §1.3 described them as a chain to
be merged in sequence, and stated the sequence in opposite directions. Both are
corrected; the underlying dependency is real — `bench_quality.py` imports
`_soft_limit` from the engine, `test_live_e2e.py` imports `estimate_lag` from
the harness — it just never required manual ordering.)

Two defects had surfaced earlier from test-merging, and are already fixed —
including one that git merged with **zero textual conflicts** because it was
semantic, not textual.

## The one thing to read before touching model quality

**ROADMAP §2.2.** In short: `dpdfnet_hr`'s headline −30 dB music suppression
was an artifact of a real defect (its ONNX normalization metadata was being
discarded, so the model's internal normalizer started mis-seeded and its
behaviour depended on input level by ~36 dB). With that fixed, it separates
vocals from music by **0.83 dB — identical to `gtcrn`**.

This disproves the belief, held since 2026-07-24, that `dpdfnet_hr` was doing
real music-vs-voice separation. It is not. No shipped model removes music; all
four are ~1 dB separators. Keep it as default for the *correct* reason — it is
48 kHz-native and preserves high frequencies (−4 dB in the 8–20 kHz band vs
`gtcrn`'s −49 dB) — not because it removes music better.

Every previously-recorded suppression figure for this model is invalidated.

## Immediate next actions

1. **The by-ear model comparison (B1)** — the one thing gating the 0.1.4 tag,
   and the only remaining Phase 1 item that needs a human rather than the
   machine. The current default was chosen by ear against a misbehaving model,
   and all four enhancers are within ~0.4 dB of each other. The stereo corpus
   (ROADMAP §3.1) is built, so the same listening pass settles mid/side too.
2. **Rebuild the `.deb`** from merged `main` — the one in `dist/` predates
   everything. Then cut the 0.1.4 tag, once item 1 has confirmed what the
   release notes should say about the default model.
3. Then Phase 2 in the roadmap: C1 gapless device switching (the originally
   reported pain point), C2 volume forwarding.

Worth reordering ahead of Phase 3 when you get there: the **stereo processor
contract** (`wants_stereo`, engine stops downmixing) is currently buried inside
A1, but three separate blocked items sit behind it — A1 (Spleeter crashes on
mono input), B3 (the mono-output regression), and Q5 (whether mono is
acceptable stops being a question once stereo is possible). It needs no input
from anyone. Likewise Q3 does not really block the A1 spike: A1's own text says
the spike should *produce* a latency-vs-quality curve, which is what answers
Q3 — the ceiling only gates the final chunk-size pick.

## Open questions for the user

Listed in full as ROADMAP §10. The ones that block work right now:

- **Latency ceiling** — hard product constraint for the separator work (A1);
  ~50–70 ms and ~500 ms lead to different designs. (Gates A1's *conclusion*,
  not its start — see above.)
- **Mono output while filtering** — currently total (−122 dB side-channel at
  100 % wet). Acceptable, or is stereo preservation required?

~~Stereo source material for the corpus.~~ **Resolved 2026-08-15** — this was
recorded as blocked on you across three handovers and it was not. There were 61
usable full mixes in `~/Music/Acapella/`. Corpus built; see ROADMAP §3.1,
including the trap that 7 of those "stereo" files are actually dual-mono.

## Environment notes

- Python: `.venv/bin/python` in the repo root. Models live in
  `~/.local/share/music-assassin/models/`.
- Building the harness's reference corpus needs `demucs` (torch), which this
  app deliberately never depends on — run it out-of-process via
  `--demucs-python ~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python`.
- `tests/test_live_e2e.py`'s hardware tier and anything calling
  `RoutingSession.enable()` **take over the system default audio sink**.
  `python -m assassin_live --recover` restores it if something dies mid-run.
- `.claude/worktrees/` is scratch space for parallel agent worktrees; it is not
  project content and should be gitignored if it starts appearing in status.
