#!/usr/bin/env bash
# The suite that can run anywhere: no audio hardware, no PipeWire, no display.
# One entry point for humans and for CI (.github/workflows/tests.yml calls
# this file), so the two cannot drift into testing different things.
#
# WHAT IS DELIBERATELY NOT HERE:
#
#   tests/test_routing_dry.py  -- mutates the live PipeWire graph. It is not
#   "dry" despite the name, it destroys every sink named MusicAssassin, and
#   on 2026-08-18 it did exactly that to a running instance and left the app
#   capturing its own output (ROADMAP C8). It has a guard now, but a guard is
#   not a reason to put it somewhere it runs unattended. Run it by hand.
#
#   tests/test_live_e2e.py     -- takes over the system default sink.
#
# tests/test_processors_offline.py needs the ONNX models, which are not in
# this repository. It runs when they are present and is REPORTED AS SKIPPED
# when they are not -- never silently dropped, because a suite that quietly
# shrinks is how a check stops covering anything without anyone noticing.
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

HARDWARE_FREE=(
  test_engine_recovery      # callback crash containment, soft limiter
  test_stereo_rebuild       # B3's mask rebuild + measured latency (C4/C7)
  test_capture_diagnosis    # C8, incl. the pulse-proxied pid trap
  test_retarget_live        # C1's live move and its fallbacks
  test_routing_events       # check()'s four outcomes (C1/C3)
  test_status_line          # C4's health word
  test_volume_mirror        # C2's mirror
)

fail=0
skipped=()
echo "== hardware-free suite =="
for t in "${HARDWARE_FREE[@]}"; do
  printf '  %-26s ' "$t"
  if out=$("$PY" "tests/$t.py" 2>&1); then
    echo "PASS"
  else
    echo "FAIL"
    echo "$out" | sed 's/^/      /'
    fail=1
  fi
done

echo
echo "== needs the ONNX models =="
printf '  %-26s ' "test_processors_offline"
if out=$("$PY" tests/test_processors_offline.py 2>&1); then
  echo "PASS"
elif grep -q "NOTHING TESTED" <<<"$out"; then
  echo "SKIPPED (no models installed)"
  skipped+=("test_processors_offline")
else
  echo "FAIL"
  echo "$out" | sed 's/^/      /'
  fail=1
fi

echo
if [ ${#skipped[@]} -gt 0 ]; then
  echo "skipped: ${skipped[*]}"
fi
if [ "$fail" -eq 0 ]; then
  echo "PASS"
else
  echo "FAIL"
fi
exit "$fail"
