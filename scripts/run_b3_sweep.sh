#!/usr/bin/env bash
# B3: does the stereo rebuild earn its place, and what does it cost?
# (docs/ROADMAP.md B3, audio/stereo.py). Long job -- launch detached, see
# NOTES at the bottom. Same shape as run_b1_sweep.sh, same reasons.
#
# THE QUESTION. B1 measured the defect: stereo_width_db = -161 dB on 104/104
# pairs, every model, every config -- the filter always outputs mono.
# audio/stereo.py rebuilds the image by re-applying the chain's implied
# spectral gain to the original stereo pair. That cannot be free: width that
# survives is width in bands the model kept, and any music sitting in those
# bands comes back with it. This sweep prices that trade before the default
# is flipped -- `set_stereo()` ships OFF until these numbers say otherwise.
#
# WHAT WOULD MAKE IT A YES: stereo_width_db up substantially (the -161 dB
# floor is the thing being fixed) with music_supp_db and delta_si_sdr_db
# essentially unmoved, and RTF still inside budget -- the rebuild adds 6
# FFTs of length 512 per 256-sample hop on top of the model.
#
# WHAT WOULD MAKE IT A NO: suppression falling materially. Reinstating the
# side channel means reinstating the wide-panned instruments the mono
# downmix was accidentally removing. If that shows up, the honest answer is
# the third option B3 lists -- keep mono, say so in the UI.
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$PWD/.venv/bin/python -u"
# Both overridable so the sweep can run on a machine that reaches the corpus
# over a mount rather than holding it locally -- see WHERE in the NOTES, and
# read the RTF caveat there before comparing those numbers to B1's.
OUT="${OUT:-$HOME/.local/state/music-assassin/bench/b3}"
CORPUS="${CORPUS:-$HOME/.local/state/music-assassin/bench/corpus}"
mkdir -p "$OUT"

run_once () {  # run_once <name> <args...>
  local name="$1"; shift
  if [ -s "$OUT/$name.csv" ]; then
    echo "== $name: already have $OUT/$name.csv, skipping"
    return 0
  fi
  echo "== $name: starting $(date '+%H:%M:%S')"
  $PY tests/bench_quality.py "$@" --corpus "$CORPUS" \
      --csv "$OUT/$name.csv" 2>&1 | tee "$OUT/$name.txt"
  # PIPESTATUS, not $?, which is tee's. Without this a step that dies on a
  # usage error prints "done" thirteen seconds later and the run reads as a
  # success -- which is exactly what happened to stereo_midside on the first
  # launch, and would have been found only by noticing a missing CSV.
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    echo "== $name: FAILED (exit $rc) $(date '+%H:%M:%S') -- see $OUT/$name.txt"
    rm -f "$OUT/$name.csv"          # never leave a partial CSV to be skipped
    return "$rc"
  fi
  echo "== $name: done $(date '+%H:%M:%S')"
}

# --- the headline question, whole corpus, shipped default model ------------
# Both arms in ONE invocation: on-vs-off is a within-run comparison and the
# harness's frontier marking is what makes the trade readable.
run_once stereo         --sweep stereo=off,on

# --- the interaction B3 flagged as needing design, not just measurement ----
# mid/side DELETES the side channel on purpose; the rebuild puts an image
# back. Stacked, they are pulling opposite directions, and B1 already found
# mid/side at exponent 4 net-harmful on its own. Four configs, one run.
# One --sweep per axis: the flag is action="append", so a second key=values
# passed bare is a positional and argparse rejects the whole invocation.
run_once stereo_midside --sweep midside=off,on --sweep stereo=off,on

# --- expected-negative -----------------------------------------------------
# No side channel exists in these clips (S/M <= -44 dB), so the rebuild has
# nothing to restore and must not invent any. Also the category with B1's
# unexplained mid/side anomaly -- watch whether it moves here too.
run_once control        --only-category dual_mono_control --sweep stereo=off,on

# --- does it hold up where the image is actually wide? ---------------------
run_once sparse_ears    --only-category sparse_acoustic --sweep stereo=off,on \
                        --dump-audio "$OUT/audio_sparse"
run_once male_ears      --only-category male_lead --sweep stereo=off,on \
                        --dump-audio "$OUT/audio_male"

echo "######## B3 SWEEP COMPLETE $(date '+%F %H:%M:%S') ########"

# NOTES
#   WHERE: preferably on the box that holds the corpus (~/.local/state/
#   music-assassin/bench/corpus). CORPUS= and OUT= let it run elsewhere and
#   read the clips over a mount, which is fine for the quality metrics.
#
#   BUT THE RTF COLUMN IS THEN NOT COMPARABLE TO B1's. Every ms-per-block
#   and RTF number is a property of the machine that produced it, and B1's
#   came from the corpus box. Within one B3 run the stereo=off vs stereo=on
#   delta is still valid -- both arms ran on the same hardware, which is the
#   comparison this sweep exists to make -- but "RTF still inside budget"
#   is only a verdict about the machine it ran on. Re-check that one number
#   on the target hardware before flipping the default on it.
#
#   launch:   mkdir -p ~/.local/state/music-assassin/bench/b3
#             setsid nohup bash scripts/run_b3_sweep.sh \
#               >> ~/.local/state/music-assassin/bench/b3/run.log 2>&1 < /dev/null &
#             (the mkdir is not optional -- the shell opens run.log before the
#             script runs, so it cannot be the script's own mkdir -p that
#             creates the directory)
#   DO NOT EDIT THIS FILE WHILE A RUN IS IN FLIGHT. bash reads a script
#   incrementally by byte offset, so inserting or removing lines above the
#   currently-executing command makes it resume at a shifted position --
#   observed 2026-08-18: after a mid-run edit the sweep jumped backwards and
#   re-ran a completed step instead of continuing, silently skipping the one
#   that should have followed. Copy the file, edit the copy, run that; or
#   stop the run first (completed steps skip on their CSV, so little is lost).
#
#   progress: grep -E '^== ' ~/.local/state/music-assassin/bench/b3/run.log
#   resume:   rerun the same command; completed steps skip on their CSV
#   by ear:   scripts/ab_listen.sh reads the b1 dumps; point it at b3's with
#             B=~/.local/state/music-assassin/bench/b3 scripts/ab_listen.sh
