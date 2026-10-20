#!/bin/bash
# Feature test (2026-06-06): ctrl-k skips persist across dtd sessions for the
# duration of one day. The skipped file lives at a stable path (not /tmp per
# PID), is NOT deleted on session exit, and resets when the date changes.

set -e
SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# 1. Stable path, not per-PID temp
if grep -q 'DTD_SKIPPED="\$STATE_DIR/dtd-skipped-today.txt"' "$SCRIPT"; then
  echo "PASS: skipped file at stable per-day path (machine-local state dir)"
else
  echo "FAIL: DTD_SKIPPED must be a stable path, not /tmp/dtd-\$\$"
  exit 1
fi
if grep -q 'DTD_SKIPPED="/tmp/dtd-\$\$' "$SCRIPT"; then
  echo "FAIL: per-PID DTD_SKIPPED definition still present"
  exit 1
fi

# 2. Not deleted in the exit cleanup line
CLEANUP=$(grep '^rm -f "\$DTD_FIFO"' "$SCRIPT")
if echo "$CLEANUP" | grep -q 'DTD_SKIPPED'; then
  echo "FAIL: exit cleanup must not delete DTD_SKIPPED"
  exit 1
fi
echo "PASS: exit cleanup preserves the skipped file"

# 3. Date-guard behavior: stale (older) date resets the file, same date
# preserves it, and — the 2026-08-23 travel-hardening fix — a BACKWARD date
# move (an OS TZ correction, an International Date Line crossing while
# traveling) must NOT reset it either. This snippet is kept in lockstep with
# the actual gate in dtd.sh (search for DTD_SKIPPED.date there); mirroring
# it inline is the established pattern in this test rather than sourcing the
# whole fzf-launching script.
GATE='
_dtd_skipped_stored="$(cat "$DTD_SKIPPED.date" 2>/dev/null)"
if [[ -z "$_dtd_skipped_stored" || "$LOCAL_TODAY" > "$_dtd_skipped_stored" ]]; then
  rm -f "$DTD_SKIPPED"
  echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
fi
unset _dtd_skipped_stored
'
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
RESULT=$(zsh -c '
LOCAL_TODAY="2026-06-06"
DTD_SKIPPED="'"$TMP"'/dtd-skipped-today.txt"
echo "old skip" > "$DTD_SKIPPED"
echo "2026-06-05" > "$DTD_SKIPPED.date"
'"$GATE"'
touch "$DTD_SKIPPED"
[[ -s "$DTD_SKIPPED" ]] && echo "stale-kept" || echo "stale-cleared"
# Same-day second session
echo "todays skip" >> "$DTD_SKIPPED"
'"$GATE"'
touch "$DTD_SKIPPED"
grep -q "todays skip" "$DTD_SKIPPED" && echo "same-day-kept" || echo "same-day-lost"
# Backward date move (travel): stored date is AHEAD of LOCAL_TODAY — must
# NOT reset, and the stamp must not regress either.
LOCAL_TODAY="2026-06-05"
'"$GATE"'
touch "$DTD_SKIPPED"
grep -q "todays skip" "$DTD_SKIPPED" && echo "backward-kept" || echo "backward-lost"
[[ "$(cat "$DTD_SKIPPED.date")" == "2026-06-06" ]] && echo "stamp-not-regressed" || echo "stamp-regressed"')
echo "$RESULT" | grep -q "stale-cleared" || { echo "FAIL: yesterday's skips not cleared"; exit 1; }
echo "PASS: stale (yesterday) skips cleared on new day"
echo "$RESULT" | grep -q "same-day-kept" || { echo "FAIL: same-day skips lost between sessions"; exit 1; }
echo "PASS: same-day skips survive a second session"
echo "$RESULT" | grep -q "backward-kept" || { echo "FAIL: a backward date move wiped the skip list"; exit 1; }
echo "PASS: a backward date move (travel TZ correction) does not wipe skips"
echo "$RESULT" | grep -q "stamp-not-regressed" || { echo "FAIL: the stored date regressed on a backward move"; exit 1; }
echo "PASS: the stored date does not regress on a backward move"

echo "All tests passed."
