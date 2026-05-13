#!/bin/zsh
# Test: dtd must show tasks from BOTH neon-labeled sections AND the "today" section.
# When "today" has tasks, the total count should include them (not just neon tasks).
# Bug: stale cache had today=[] causing dtd to show only ~25 tasks instead of ~90.

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Mock cache with tasks in both neon and today sections
cat > "$TMPDIR/cache.json" << 'CACHE'
{
  "0neon": [
    {"id": "1", "content": "wake up [6]", "labels": ["0neon"], "due": "2026-05-13"},
    {"id": "2", "content": "0g [8]", "labels": ["0neon"], "due": "2026-05-13"}
  ],
  "1neon": [
    {"id": "3", "content": "1 i9 [15]", "labels": ["1neon"], "due": "2026-05-13"}
  ],
  "夜neon": [],
  "关键路径": [],
  "today": [
    {"id": "4", "content": "review deck [20]", "labels": ["i9"], "due": "2026-05-13"},
    {"id": "5", "content": "call stuart [10]", "labels": ["s897"], "due": "2026-05-13"},
    {"id": "6", "content": "buy groceries [5]", "labels": ["xk87"], "due": "2026-05-12"}
  ]
}
CACHE

cat > "$TMPDIR/done.json" << 'DONE'
{"date": "2026-05-13", "names": []}
DONE

# Run the dtd jq filter
count=$(jq --slurpfile done "$TMPDIR/done.json" --arg today "2026-05-13" '
  (
    (if ($done[0].date == $today) then ($done[0].names | map(ascii_downcase)) else [] end)
  ) as $completed |
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(.due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(.due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | length
' "$TMPDIR/cache.json")

# Should be 6 (3 neon + 3 today, no duplicates)
if [[ "$count" -ne 6 ]]; then
  echo "FAIL: expected 6 tasks, got $count"
  exit 1
fi

# Test with empty today (the bug scenario)
cat > "$TMPDIR/cache_empty.json" << 'CACHE'
{
  "0neon": [
    {"id": "1", "content": "wake up [6]", "labels": ["0neon"], "due": "2026-05-13"},
    {"id": "2", "content": "0g [8]", "labels": ["0neon"], "due": "2026-05-13"}
  ],
  "1neon": [
    {"id": "3", "content": "1 i9 [15]", "labels": ["1neon"], "due": "2026-05-13"}
  ],
  "夜neon": [],
  "关键路径": [],
  "today": []
}
CACHE

count_empty=$(jq --slurpfile done "$TMPDIR/done.json" --arg today "2026-05-13" '
  (
    (if ($done[0].date == $today) then ($done[0].names | map(ascii_downcase)) else [] end)
  ) as $completed |
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(.due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(.due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | length
' "$TMPDIR/cache_empty.json")

# With empty today, only 3 neon tasks
if [[ "$count_empty" -ne 3 ]]; then
  echo "FAIL: with empty today, expected 3, got $count_empty"
  exit 1
fi

# The difference proves the bug: 6 vs 3 tasks depending on whether today is populated
echo "PASS: today section contributes tasks (6 with, 3 without). Cache must keep today populated."
exit 0
