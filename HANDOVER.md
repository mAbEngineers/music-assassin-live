# Handover — current state

Updated 2026-08-18. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.

## Where the code is

**Nothing is pushed, and the 0.1.4 tag is still not cut.**

| Branch | Contents | State |
|---|---|---|
| `main` | 0.1.4, green on the offline suite | at `8df4f83` |
| `feature/stereo-output` | B3 stereo rebuild, `wants_stereo`, C1, C2, C3, C4, C7, C8, E1 | **13 commits, unpushed** |
| `feat/separator-spike` | A1 spike (measurement only, no processor yet) | 2 commits, off the above |
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

## What gates the 0.1.4 tag — three things, all needing a human

**1. The app has never been launched with any of this.** Fourteen commits
changed the engine, the routing backend and the UI. Eight test files cover the
logic offline and two spikes verified mechanisms against a real PipeWire
graph, but the actual application has not been run once: `_switch_output`,
`_check_capture`, `_say`, `sync_volume`/`adopt_volume`, the status line, the
details toggle. Smoke checklist:

| check | expected |
|---|---|
| toggle ON | `Filtering → <device> · ~50 ms · healthy` |
| `details ▸` | counters appear/disappear |
| change output in the app | no multi-second dropout |
| change output in GNOME | dropdown follows in ~0.25 s, status says `output → X` |
| volume keys | audio level changes; slider stays where you put it |
| on launch | **no** `could not pin audio streams to their targets` warning |

That last line is the direct test of the pid fix below. If it still warns,
`pin_stream()` is still dead and C1/C8 rest on nothing.

**2. By-ear: which model.** B1's numbers rank candidates; they do not settle
quality, and the harness says so itself.

```
B=~/.local/state/music-assassin/bench/b1 SECS=12 scripts/ab_listen.sh male a200
```

Blind by default. The trap is specific: `dtln` beats `dpdfnet_hr` on
`male_lead` dSI-SDR (+1.07 vs +0.33) while removing **28 dB less music**
(−20.0 vs −48.4). SI-SDR may simply not be scoring what this app is for.

**3. By-ear: does the stereo rebuild bring music back.**

```
B=~/.local/state/music-assassin/bench/b3 scripts/ab_listen.sh sparse a200 --sighted
```

Sighted, because this compares one model against itself with one flag flipped.
**This is the question the numbers cannot answer** — see below.

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

Also: peak RSS 478 MB; every call emits a startup transient (~26 samples
reaching |466| against a p99.99 of 0.6) that a chunked wrapper must trim or it
clicks once per chunk; output is shorter than input (44100 → 44032); stems[0]
is vocals, [1] accompaniment, confirmed by cross-correlation.

**No quality curve yet, deliberately.** An ad-hoc probe gave nonsense (both
stems below the mixture) and the cause was the probe — linear-interp
resampling and single-lag alignment, exactly what `estimate_lag`'s docstring
warns about. The curve comes from writing the `StreamProcessor` and sweeping
it through `bench_quality.py`, which already solves alignment carefully.

Next step for A1: `SpleeterProcessor` (`wants_stereo=True`, chunked, transient
trimmed, remainder carried), registered as `spleeter_<chunk>ms` variants so
`--sweep model=...` produces the curve. sherpa-onnx is **not** in the app venv
and should not be added to `requirements.txt`; use
`~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python`, which has 1.13.4.
Models: `Music-Assassin/models/sherpa_onnx/sherpa-onnx-spleeter-2stems-int8/`
(2 × 26 MB) — note the `sherpa_onnx/` path component.

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
