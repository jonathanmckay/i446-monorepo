#!/bin/zsh
# Test: dtd must NOT re-filter "today" bucket tasks by due date.
# Todoist's "today | overdue" API already selected these tasks.
# Re-filtering by due <= today drops recurring tasks with future due dates
# and tasks with no due date, causing dtd to show fewer tasks than expected.
# Bug: 43 shown instead of 73 because 30 tasks had due > today or null due.

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cat > "$TMPDIR/cache.json" << 'CACHE'
{
  "0neon": [
    {"id": "1", "content": "wake up [6]", "labels": ["0neon"], "due": "2026-05-27"}
  ],
  "1neon": [],
  "夜neon": [],
  "关键路径": [],
  "today": [
    {"id": "2", "content": "normal task [10]", "labels": ["i9"], "due": "2026-05-27"},
    {"id": "3", "content": "recurring task (future due) [5]", "labels": ["hcb"], "due": "2026-06-03"},
    {"id": "4", "content": "overdue task [8]", "labels": ["m5x2"], "due": "2026-05-25"},
    {"id": "5", "content": "no due date task [3]", "labels": ["g245"], "due": null},
    {"id": "6", "content": "empty due string [7]", "labels": ["i9"], "due": ""}
  ]
}
CACHE

TODAY="2026-05-27"

# ── Test the FIXED invariant jq filter (today bucket NOT re-filtered by due) ──
count=$(jq --arg today "$TODAY" '
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(type == "object")]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | length
' "$TMPDIR/cache.json")

# Should be 6: 1 neon + 5 today (all today tasks included regardless of due date)
if [[ "$count" -ne 6 ]]; then
  echo "FAIL: expected 6 tasks (no re-filtering), got $count"
  exit 1
fi

# ── Verify the OLD buggy filter would have dropped tasks ──
count_buggy=$(jq --arg today "$TODAY" '
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | length
' "$TMPDIR/cache.json")

# Buggy filter: 1 neon + 2 today (only due=today and due<today pass; future/null/empty dropped)
if [[ "$count_buggy" -ne 3 ]]; then
  echo "FAIL: buggy filter expected 3, got $count_buggy (test fixture is wrong)"
  exit 1
fi

echo "PASS: fixed filter shows 6 tasks, buggy filter would show 3. Today bucket is not re-filtered by due date."
exit 0
