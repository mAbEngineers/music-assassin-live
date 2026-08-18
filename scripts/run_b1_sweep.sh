#!/usr/bin/env bash
# B1: which enhancer, and does the mid/side prefilter earn its place?
# (docs/ROADMAP.md B1). Long job -- launch detached, see NOTES at the bottom.
#
# WHY ONE INVOCATION PER MODEL, rather than one --sweep with five:
# bench_quality.py prints its comparison table only when the whole invocation
# finishes, because it ranks configs against each other and marks the Pareto
# frontier. A five-model sweep therefore yields nothing at all until every
# model is done -- and on this hardware that is ~7 hours. A reboot mid-run
# discards all of it. One invocation per model costs a repeated reference pass
# but means each model's numbers land on disk as soon as that model finishes,
# and a rerun skips whatever already has a CSV.
#
# WHY OUTPUT GOES TO ~/.local/state AND NOT /tmp: /tmp is cleared on reboot.
# Seven hours of sweep were lost to exactly that.
#
# speechdenoiser is deliberately NOT swept: its upstream license is unresolved,
# scripts/build_deb.sh already excludes it from the package, so it cannot be
# the shipped default no matter how it scores. Benchmarking it here would be
# spending the most expensive resource on hand for an unusable answer.
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$PWD/.venv/bin/python -u"
OUT="$HOME/.local/state/music-assassin/bench/b1"
mkdir -p "$OUT"

run_once () {  # run_once <name> <args...>
  local name="$1"; shift
  if [ -s "$OUT/$name.csv" ]; then
    echo "== $name: already have $OUT/$name.csv, skipping"
    return 0
  fi
  echo "== $name: starting $(date '+%H:%M:%S')"
  $PY tests/bench_quality.py "$@" --csv "$OUT/$name.csv" 2>&1 | tee "$OUT/$name.txt"
  echo "== $name: done $(date '+%H:%M:%S')"
}

# --- the model question: one model per invocation, cheapest first -----------
# Cheapest first so the fastest evidence arrives soonest; dpdfnet_hr (RTF 0.50,
# the current default) is the slowest and runs last.
run_once dtln           --sweep model=dtln
run_once gtcrn          --sweep model=gtcrn
run_once dpdfnet        --sweep model=dpdfnet
run_once dpdfnet_hr     --sweep model=dpdfnet_hr

# --- the mid/side question --------------------------------------------------
# Both arms in ONE invocation: on-vs-off is a within-run comparison and the
# harness's frontier marking is what makes it readable.
run_once midside        --sweep midside=off,on

# --- expected-negative ------------------------------------------------------
# No side channel exists in these two clips. A mid/side "benefit" here would
# mean the metric is measuring something other than stereo exploitation.
run_once control        --only-category dual_mono_control --sweep midside=off,on

# --- audio for the by-ear pass, which is what actually decides -------------
run_once male_ears      --only-category male_lead \
                        --sweep model=dpdfnet_hr,gtcrn,dtln --dump-audio "$OUT/audio_male"
run_once sparse_ears    --only-category sparse_acoustic \
                        --sweep model=dpdfnet_hr,gtcrn,dtln --dump-audio "$OUT/audio_sparse"

echo "######## B1 SWEEP COMPLETE $(date '+%F %H:%M:%S') ########"

# NOTES
#   launch:   setsid nohup bash scripts/run_b1_sweep.sh \
#               >> ~/.local/state/music-assassin/bench/b1/run.log 2>&1 < /dev/null &
#   progress: grep -E '^== ' ~/.local/state/music-assassin/bench/b1/run.log
#   resume:   rerun the same command; completed steps skip on their CSV
