#!/usr/bin/env bash
# A1: what does chunking the separator cost in QUALITY?
# (docs/ROADMAP.md A1, processors/spleeter.py). Long job -- launch detached,
# see NOTES at the bottom. Same shape as run_b1_sweep.sh / run_b3_sweep.sh.
#
# THE QUESTION. The A1 spike settled the cost side: per-call time is flat in
# the chunk length, so latency binds and RTF is nearly irrelevant, and the
# practical floor of ~1 s chunks puts the separator at >=1.1 s end-to-end --
# an additional high-latency mode at best, never the ~50 ms path. What the
# spike could NOT say is whether a chunked separator is any GOOD, because a
# hand-rolled probe scored both stems below the mixture and the fault was the
# probe (linear-interp resampling, single-lag alignment). This sweep asks the
# question through bench_quality.py instead, which aligns carefully and scores
# against the corpus's own reference stems.
#
# Chunk size is the axis because it is the one knob that trades the two things
# against each other: bigger chunks buy the model more spectrogram context and
# cost proportionally more delay. The measured floor is that chunked
# separation lands 8-11 dB below a whole-buffer run and MORE CONTEXT DOES NOT
# CLOSE IT (measured over 0.25 s and 0.5 s of context, 1/2/4 s chunks), so the
# curve here is not expected to converge on the offline ceiling -- the useful
# reading is where it flattens, i.e. the smallest chunk that is not clearly
# worse than a larger one.
#
# WHAT WOULD MAKE IT A YES: a chunk somewhere on this curve that beats
# dpdfnet_hr on music_supp_db without giving up vocal_ret_db, at a latency the
# product is willing to sell as a separate mode. dpdfnet_hr is swept alongside
# in the SAME run for exactly that comparison -- across runs the numbers are
# not comparable (see the RTF caveat in NOTES).
#
# WHAT WOULD MAKE IT A NO: the curve flat and below dpdfnet_hr, or vocal
# damage that grows with chunk size. Then A1 is a dead end at this model size
# and the honest outcome is to say so in ROADMAP rather than ship a 1.1 s mode
# nobody wants.
#
# READ THE LATENCY COLUMN CAREFULLY. bench_quality.py derives latency_ms from
# the measured alignment lag, and a chunk-buffering processor measures ZERO
# there: it emits output whose first sample still corresponds to input sample
# 0, and concatenating a file offline erases the delay, which is entirely
# about when samples become AVAILABLE. evaluate() now reports
# max(measured, processor's declared latency) so the column and its >120 ms
# lip-sync tag mean what they say -- but the underlying blindness is the same
# one B3 has with the side channel, so do not read anything else in this table
# as a statement about realtime behaviour. RTF is per 20 ms block of a chunk
# that only actually runs every chunk_ms, so it is an average over a spiky
# process, not a headroom figure. The spike's table is the latency authority.
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Spleeter needs sherpa-onnx, which is deliberately NOT an app dependency and
# is not in .venv -- hence a different default interpreter to every other
# sweep script here. It also needs the two spleeter ONNX files under the names
# the registry looks up (scripts/import_models.py renames them on the way in).
PY="${PY:-$HOME/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python} -u"
OUT="${OUT:-$HOME/.local/state/music-assassin/bench/a1}"
CORPUS="${CORPUS:-$HOME/.local/state/music-assassin/bench/corpus}"
mkdir -p "$OUT"

# SET MUSIC_ASSASSIN_MODELS unless you are certain models_dir() resolves where
# you think. It honours XDG_DATA_HOME, and a terminal inside the VS Code SNAP
# exports XDG_DATA_HOME=~/snap/code/<rev>/.local/share -- so ~/.local/share/
# music-assassin/models is not consulted, resolution falls through to the
# .deb's /usr/share subset, and the run dies with "model(s) not installed"
# naming models that are, in fact, installed. Cost 40 s and one confusing
# failure on 2026-08-19; the explicit override is immune to all of it.
if [ -z "${MUSIC_ASSASSIN_MODELS:-}" ]; then
  echo "WARNING: MUSIC_ASSASSIN_MODELS unset -- models_dir() will guess," \
       "and under the VS Code snap it guesses wrong. See the note above." >&2
fi

# dpdfnet_hr rides along in every step as the within-run baseline: it is the
# shipped default and the thing a separator has to beat to be worth 1.1 s.
MODELS="model=spleeter_250ms,spleeter_500ms,spleeter_1000ms,spleeter_2000ms,spleeter_4000ms,dpdfnet_hr"

run_once () {  # run_once <name> <args...>
  local name="$1"; shift
  if [ -s "$OUT/$name.csv" ]; then
    echo "== $name: already have $OUT/$name.csv, skipping"
    return 0
  fi
  echo "== $name: starting $(date '+%H:%M:%S')"
  $PY tests/bench_quality.py "$@" --corpus "$CORPUS" \
      --csv "$OUT/$name.csv" 2>&1 | tee "$OUT/$name.txt"
  # PIPESTATUS, not $?, which is tee's -- a step that dies on a usage error
  # otherwise prints "done" seconds later and the run reads as a success.
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    echo "== $name: FAILED (exit $rc) $(date '+%H:%M:%S') -- see $OUT/$name.txt"
    rm -f "$OUT/$name.csv"          # never leave a partial CSV to be skipped
    return "$rc"
  fi
  echo "== $name: done $(date '+%H:%M:%S')"
}

# Ordered smallest first, deliberately: the separator costs ~70 s per
# item-ratio pair per config on one thread, so the whole-corpus step is many
# hours and the two scoped ones answer the shape of the curve long before it
# finishes. If the curve is already flat and below dpdfnet_hr after these two,
# the third step is not worth waiting for.

# --- 4 clips: where a wide image and a sparse mix should favour a separator -
run_once chunk_sparse --only-category sparse_acoustic --sweep "$MODELS" \
                      --dump-audio "$OUT/audio_sparse"

# --- 8 clips: the category where B1's ranking and the by-ear pass disagree --
# Also the one where dpdfnet_hr is weakest (+0.33 dB dSI-SDR), so it is the
# most winnable ground a separator has.
run_once chunk_male   --only-category male_lead --sweep "$MODELS" \
                      --dump-audio "$OUT/audio_male"

# --- the whole corpus: the headline curve --------------------------------
run_once chunk_all    --sweep "$MODELS"

echo "######## A1 SWEEP COMPLETE $(date '+%F %H:%M:%S') ########"

# NOTES
#   WHERE: on the box that holds the corpus if you can. CORPUS= and OUT= let
#   it read the clips over a mount, which is fine for the quality metrics.
#
#   BUT THE RTF COLUMN IS THEN NOT COMPARABLE TO B1's OR THE A1 SPIKE's.
#   Every ms-per-block and RTF number is a property of the machine that
#   produced it. Within one run the model-to-model comparison is valid --
#   same hardware, same corpus, which is the comparison this sweep exists to
#   make -- but nothing here transfers to another box.
#
#   launch:   mkdir -p ~/.local/state/music-assassin/bench/a1
#             setsid nohup bash scripts/run_a1_sweep.sh \
#               >> ~/.local/state/music-assassin/bench/a1/run.log 2>&1 < /dev/null &
#             (the mkdir is not optional -- the shell opens run.log before the
#             script runs, so it cannot be the script's own mkdir -p that
#             creates the directory)
#   DO NOT EDIT THIS FILE WHILE A RUN IS IN FLIGHT. bash reads a script
#   incrementally by byte offset, so inserting or removing lines above the
#   currently-executing command makes it resume at a shifted position --
#   observed 2026-08-18 on the B3 sweep: it jumped backwards, re-ran a
#   completed step and silently skipped the next. Copy the file, edit the
#   copy, run that; or stop the run first (completed steps skip on their CSV).
#
#   progress: grep -E '^== ' ~/.local/state/music-assassin/bench/a1/run.log
#   resume:   rerun the same command; completed steps skip on their CSV
#   by ear:   B=~/.local/state/music-assassin/bench/a1 scripts/ab_listen.sh \
#               sparse a200 --sighted
