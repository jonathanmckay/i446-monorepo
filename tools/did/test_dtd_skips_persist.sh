#!/bin/bash
# Feature test (2026-06-06): ctrl-k skips persist across dtd sessions for the
# duration of one day. The skipped file lives at a stable path (not /tmp per
# PID), is NOT deleted on session exit, and resets when the date changes.

set -e
SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# 1. Stable path, not per-PID temp
if grep -q 'DTD_SKIPPED="\$HOME/vault/z_ibx/dtd-skipped-today.txt"' "$SCRIPT"; then
  echo "PASS: skipped file at stable per-day path"
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

# 3. Date-guard behavior: stale date resets the file, same date preserves it
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
RESULT=$(zsh -c '
LOCAL_TODAY="2026-06-06"
DTD_SKIPPED="'"$TMP"'/dtd-skipped-today.txt"
echo "old skip" > "$DTD_SKIPPED"
echo "2026-06-05" > "$DTD_SKIPPED.date"
if [[ -f "$DTD_SKIPPED.date" && "$(cat "$DTD_SKIPPED.date" 2>/dev/null)" != "$LOCAL_TODAY" ]]; then
  rm -f "$DTD_SKIPPED"
fi
echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
touch "$DTD_SKIPPED"
[[ -s "$DTD_SKIPPED" ]] && echo "stale-kept" || echo "stale-cleared"
# Same-day second session
echo "todays skip" >> "$DTD_SKIPPED"
if [[ -f "$DTD_SKIPPED.date" && "$(cat "$DTD_SKIPPED.date" 2>/dev/null)" != "$LOCAL_TODAY" ]]; then
  rm -f "$DTD_SKIPPED"
fi
echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
touch "$DTD_SKIPPED"
grep -q "todays skip" "$DTD_SKIPPED" && echo "same-day-kept" || echo "same-day-lost"')
echo "$RESULT" | grep -q "stale-cleared" || { echo "FAIL: yesterday's skips not cleared"; exit 1; }
echo "PASS: stale (yesterday) skips cleared on new day"
echo "$RESULT" | grep -q "same-day-kept" || { echo "FAIL: same-day skips lost between sessions"; exit 1; }
echo "PASS: same-day skips survive a second session"

echo "All tests passed."
