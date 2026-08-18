#!/usr/bin/env bash
# By-ear A/B for the B1 sweep's --dump-audio output (docs/ROADMAP.md §5 B1,
# step 10 of the sequenced plan). B1 ranked the models numerically; this is
# the pass that decides, per the harness's own docstring.
#
# WHY BLIND BY DEFAULT: this listen exists to settle a disagreement between
# the metric and the standing default (`dpdfnet_hr`), and whoever runs it
# already knows which model the numbers favour. Sighted, that is exactly the
# setup where you hear what you expect. --blind shuffles the three models per
# clip and reveals only after you have committed a score.
#
# WHAT TO LISTEN FOR, in this order (see ROADMAP §5 B1 male_lead breakdown):
#   1. Music still audible?  dtln removes -20 dB, dpdfnet_hr -48 dB. dSI-SDR
#      rewards not-wrecking-the-voice more than removing-the-music, which may
#      simply not be what this app is for. This is the judgement to make.
#   2. Voice chewed?         dpdfnet_hr measures -8.5 dB vocal damage on
#      male_lead -- thinning, warble, syllables dropping out.
#   3. Dull / muffled?       dtln and gtcrn are 16 kHz-native and delete
#      everything above 8 kHz (-62 dB). Needs headphones or full-range
#      monitors; laptop speakers cannot show you this.
#
# Do not run this while the app is filtering -- it owns the default sink and
# you would be auditioning the filter twice.
set -u

CAT="${1:-male}"                      # male | sparse
RATIO="${2:-a200}"                    # a100 | a200
BLIND=1
SECS="${SECS:-0}"                     # 0 = whole clip
for a in "$@"; do [ "$a" = "--sighted" ] && BLIND=0; done

B="${B:-$HOME/.local/state/music-assassin/bench/b1}"
case "$CAT" in
  male)   DIR="$B/audio_male";   PREFIX="male_lead__" ;;
  sparse) DIR="$B/audio_sparse"; PREFIX="sparse_acoustic__" ;;
  *) echo "usage: $0 [male|sparse] [a100|a200] [--sighted]"; exit 2 ;;
esac
[ -d "$DIR" ] || { echo "no such dir: $DIR (run scripts/run_b1_sweep.sh first)"; exit 1; }

# Player: ffplay can honour SECS (set SECS=12 to audition only the first 12 s
# of every file, which is usually enough and keeps a 96-file set tractable);
# paplay/aplay are fallbacks and always play the whole clip.
if command -v ffplay >/dev/null 2>&1; then
  if [ "$SECS" != "0" ]; then
    play () { ffplay -nodisp -autoexit -loglevel quiet -t "$SECS" "$1" 2>/dev/null; }
  else
    play () { ffplay -nodisp -autoexit -loglevel quiet "$1" 2>/dev/null; }
  fi
elif command -v paplay >/dev/null 2>&1; then
  play () { paplay "$1"; }
else
  play () { aplay -q "$1"; }
fi

# One clip = one stem name; variants are suffixes on it.
mapfile -t CLIPS < <(ls "$DIR" | grep -- "_${RATIO}_input\.wav$" | sed "s/_${RATIO}_input\.wav$//" | sort)
[ "${#CLIPS[@]}" -gt 0 ] || { echo "no ${RATIO} clips in $DIR"; exit 1; }

LOG="$B/listen_${CAT}_${RATIO}.log"
echo "== ${#CLIPS[@]} clips, ratio $RATIO, $( [ $BLIND = 1 ] && echo blind || echo sighted ) =="
echo "   scores append to $LOG"
echo

for clip in "${CLIPS[@]}"; do
  short="${clip#$PREFIX}"
  echo "──────── $short ────────"

  order=(dpdfnet_hr dtln gtcrn)
  if [ $BLIND = 1 ]; then
    mapfile -t order < <(printf '%s\n' "${order[@]}" | shuf)
  fi

  while :; do
    echo "  [i] unprocessed input   [o] oracle (perfect vocals)   [c] htdemucs ceiling"
    for n in 1 2 3; do echo "  [$n] candidate $n"; done
    echo "  [s] skip clip   [q] quit"
    read -r -p "  play> " k
    case "$k" in
      i) play "$DIR/${clip}_${RATIO}_input.wav" ;;
      o) play "$DIR/${clip}_${RATIO}_oracle.wav" ;;
      c) play "$DIR/${clip}_${RATIO}_ceiling.wav" ;;
      1|2|3) play "$DIR/${clip}_${RATIO}_${order[$((k-1))]}.wav" ;;
      s) break ;;
      q) echo "stopped."; exit 0 ;;
      "") ;;
      *) echo "  ?" ;;
    esac
    read -r -p "  rank best->worst (e.g. 213), or Enter to keep listening: " rank
    [ -n "$rank" ] || continue
    named=""
    for ((j=0; j<${#rank}; j++)); do
      d="${rank:$j:1}"
      case "$d" in 1|2|3) named="$named ${order[$((d-1))]}" ;; esac
    done
    echo "  -> was:$named"
    read -r -p "  note (optional): " note
    printf '%s\t%s\t%s\t%s\n' "$short" "$RATIO" "${named# }" "$note" >> "$LOG"
    break
  done
  echo
done

echo "done. results: $LOG"
echo
echo "Tally the winner column before deciding anything -- and read it against"
echo "ROADMAP §5 B1: if dtln wins by ear too, the default is wrong; if it wins"
echo "on numbers but leaves audible music, the metric is wrong for this product."
