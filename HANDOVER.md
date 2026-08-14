# Handover — current state

Updated 2026-08-14. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.
(An earlier, much longer `HANDOVER.md` was superseded by the roadmap; this
replaces it.)

## Where the code is

`main` is at `06f0ac8` and **clean**. All work sits on branches. **Nothing has
been pushed. Nothing has been merged.**

| Branch | What it is | Ready to merge? |
|---|---|---|
| `fix/stream-recovery` | 0.1.4 — `stream_ok`, callback crash containment, soft limiter, 300 % wet boost, `tests/test_engine_recovery.py` | Yes |
| `feature/quality-harness` | `tests/bench_quality.py` — the quality measurement harness | Yes |
| `fix/e2e-alignment` | `test_live_e2e.py` cross-correlation alignment + engine health counters | Yes |
| `refactor/routing-backend` | `RoutingBackend` seam; engine decoupled from PipeWire | Yes |
| `fix/dpdfnet-norm-init` | seeds DPDFNet state from ONNX metadata | Yes — **but read ROADMAP §2.2 first** |
| `feature/windows-packaging` | installer scaffolding | No — app can't run on Windows yet |
| `docs/roadmap` | `docs/ROADMAP.md` | Yes |

**Merge in dependency order:** `fix/stream-recovery` → `feature/quality-harness`
→ `fix/e2e-alignment`. The harness imports `_soft_limit` from the engine and
the e2e test imports `estimate_lag` from the harness. The other branches are
independent of that chain.

All four code branches have been test-merged together and verified green
(engine recovery, five processors offline, routing dry, imports). Two defects
surfaced *only* by doing that and are already fixed — including one that git
merged with **zero textual conflicts** because it was semantic, not textual.

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

1. **Merge the branches** in the order above; rebuild the `.deb` (the one in
   `dist/` predates all of this and was never verified). **Hold the 0.1.4 tag**
   until item 2, so release notes don't quote numbers §2.2 just invalidated.
2. **Redo the by-ear model comparison.** Now genuinely blocking — the current
   default was chosen by ear against a misbehaving model, and all four
   enhancers are within ~0.4 dB of each other.
3. **Build a varied stereo corpus** for `bench_quality.py`. Everything measured
   so far used one 15-second *mono* clip, which makes every mid/side result
   meaningless by construction — there is no side channel to exploit. This
   blocks the mid/side default decision. **Needs real source material.**
4. Then Phase 2 in the roadmap: C1 gapless device switching (the originally
   reported pain point), C2 volume forwarding.

## Open questions for the user

Listed in full as ROADMAP §10. The ones that block work right now:

- **Latency ceiling** — hard product constraint for the separator work (A1);
  ~50–70 ms and ~500 ms lead to different designs.
- **Stereo source material** for the corpus (item 3 above).
- **Mono output while filtering** — currently total (−122 dB side-channel at
  100 % wet). Acceptable, or is stereo preservation required?

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
