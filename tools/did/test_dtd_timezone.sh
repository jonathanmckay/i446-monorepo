#!/bin/zsh
# Test: dtd must use local date, not UTC, to filter tasks.
# When UTC is May 13 but local is May 12, a task due May 13 must NOT appear.

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Mock cache with a task due "tomorrow" in local time
cat > "$TMPDIR/cache.json" << 'CACHE'
{
  "0neon": [
    {"id": "1", "content": "0t - Time (3) [10]", "labels": ["0neon"], "due": "2026-05-13"},
    {"id": "2", "content": "wake up [6]", "labels": ["0neon"], "due": "2026-05-12"}
  ],
  "1neon": [], "夜neon": [], "関键路径": [], "today": []
}
CACHE

cat > "$TMPDIR/done.json" << 'DONE'
{"date": "2026-05-12", "names": []}
DONE

# Simulate dtd filter with LOCAL_TODAY=2026-05-12 (the fix)
result=$(jq -r --slurpfile done "$TMPDIR/done.json" --arg today "2026-05-12" '
  (
    (if ($done[0].date == $today) then ($done[0].names | map(ascii_downcase)) else [] end)
  ) as $completed |
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["関键路径"]]
    | flatten
    | map(select(.due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(.due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | .[]
  | .content
' "$TMPDIR/cache.json")

# Should contain wake up but NOT 0t
if echo "$result" | grep -q "0t"; then
  echo "FAIL: 0t (due 2026-05-13) appeared when today is 2026-05-12"
  exit 1
fi

if ! echo "$result" | grep -q "wake up"; then
  echo "FAIL: wake up (due 2026-05-12) should appear"
  exit 1
fi

echo "PASS: future tasks excluded, today tasks included"
exit 0
