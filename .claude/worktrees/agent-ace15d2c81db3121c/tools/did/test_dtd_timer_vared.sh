#!/bin/bash
# Regression test: when a vared-prompt task (cpap, ibx i9, ...) is selected in
# dtd AND a Toggl timer with that exact name is running, dtd must use the
# timer's elapsed minutes as the value instead of prompting.
#
# Feature (2026-06-07): hitting enter on a variable-time task that is timing
# in Toggl should not ask for the input — pull the minutes from the timer.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# 1. The vared case must first check the running timer before prompting:
#    the `python3 "$TOGGL_CLI" current` probe must appear inside the case arm,
#    above the `vared` fallback.
CASE_BLOCK=$(awk '/cpap\|ibx.*ibx.*m5x2\)/,/^      ;;/' "$SCRIPT")
if [ -z "$CASE_BLOCK" ]; then
  # awk pattern is brittle to escaping; fall back to a line-range slice.
  START=$(grep -n 'cpap|ibx\\? s897' "$SCRIPT" | head -1 | cut -d: -f1)
  [ -z "$START" ] && START=$(grep -n 'cpap|ibx' "$SCRIPT" | head -1 | cut -d: -f1)
  CASE_BLOCK=$(sed -n "${START},$((START+30))p" "$SCRIPT")
fi

if [ -z "$CASE_BLOCK" ]; then
  echo "FAIL: could not locate the vared case block in dtd.sh"
  exit 1
fi

if echo "$CASE_BLOCK" | grep -q 'TOGGL_CLI" current' \
   && echo "$CASE_BLOCK" | grep -q 'timer_mins'; then
  echo "PASS: vared case probes the running timer (timer_mins)"
else
  echo "FAIL: vared case must probe the Toggl timer before prompting"
  exit 1
fi

# 2. The timer probe (current) must come before the vared fallback in the arm.
L_PROBE=$(echo "$CASE_BLOCK" | grep -n 'TOGGL_CLI" current' | head -1 | cut -d: -f1)
L_VARED=$(echo "$CASE_BLOCK" | grep -n 'vared -p' | head -1 | cut -d: -f1)
if [ -n "$L_PROBE" ] && [ -n "$L_VARED" ] && [ "$L_PROBE" -lt "$L_VARED" ]; then
  echo "PASS: timer probe precedes the vared fallback"
else
  echo "FAIL: timer probe must precede vared (probe=$L_PROBE vared=$L_VARED)"
  exit 1
fi

# 3. The duration parser must understand toggl_cli's stop grammar:
#    (39m) (48min) (1h03m) (2h). Exercise the exact python snippet dtd uses.
parse() {
  echo "$1" | python3 -c "import sys,re; o=sys.stdin.read(); m=re.search(r'\((?:(\d+)h)?(\d+)m(?:in)?\)',o); hm=re.search(r'\((\d+)h\)',o); print((int(m.group(1) or 0)*60+int(m.group(2))) if m else (int(hm.group(1))*60 if hm else ''))"
}
[ "$(parse 'Stopped: x (39m) [id:1]')" = "39" ]   || { echo "FAIL: (39m) → $(parse 'x (39m)')"; exit 1; }
[ "$(parse 'Stopped: x (48min) [id:1]')" = "48" ] || { echo "FAIL: (48min)"; exit 1; }
[ "$(parse 'Stopped: x (1h03m) [id:1]')" = "63" ] || { echo "FAIL: (1h03m)"; exit 1; }
[ "$(parse 'Stopped: x (2h) [id:1]')" = "120" ]   || { echo "FAIL: (2h)"; exit 1; }
[ "$(parse 'Stopped: x (0min) [id:1]')" = "0" ]   || { echo "FAIL: (0min)"; exit 1; }
echo "PASS: duration parser handles m / min / Hh0Mm / Hh"

# 4. Description parser strips the time prefix, @project, (running), [id:N].
desc=$(echo "Running: 16:48-running ibx i9 @i9 (running) [id:4436702108]" \
  | sed -E 's/^Running: [0-9]{2}:[0-9]{2}-running //; s/ *@.*//; s/ *\(running\).*//; s/ *\[id:[0-9]*\].*//; s/ *$//' | tr '[:upper:]' '[:lower:]')
if [ "$desc" = "ibx i9" ]; then
  echo "PASS: timer description parses to clean task name ('$desc')"
else
  echo "FAIL: expected 'ibx i9', got '$desc'"
  exit 1
fi

echo "All tests passed."
