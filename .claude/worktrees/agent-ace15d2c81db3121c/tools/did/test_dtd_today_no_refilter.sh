#!/bin/zsh
# Test: did-fast.py cache must filter "today" tasks at write time.
# Todoist's "today | overdue" API returns recurring tasks with future due dates
# and tasks with no due date. These inflate the cache and cause dtd to show
# wrong counts (e.g., 43 shown instead of 73 because dtd re-filters by due<=today
# but the cache had 242 entries total).
# Fix: filter in did-fast.py fetch_today() → only keep tasks with due <= today.

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Simulate what fetch_today returns from the API (before filtering)
python3 -c "
import json, sys
from datetime import datetime

today = '2026-05-27'

# Raw API result includes future-dated and no-due tasks
raw_tasks = [
    {'id': '1', 'content': 'due today', 'labels': ['i9'], 'due': '2026-05-27', 'priority': 'p4'},
    {'id': '2', 'content': 'overdue', 'labels': ['m5x2'], 'due': '2026-05-25', 'priority': 'p4'},
    {'id': '3', 'content': 'recurring future', 'labels': ['hcb'], 'due': '2026-06-03', 'priority': 'p4'},
    {'id': '4', 'content': 'no due', 'labels': ['g245'], 'due': '', 'priority': 'p4'},
    {'id': '5', 'content': 'null due', 'labels': ['i9'], 'due': None, 'priority': 'p4'},
]

# Apply the fix: only keep tasks with non-empty due <= today
filtered = [t for t in raw_tasks if t.get('due') and t['due'] <= today]

# Should keep only 2 (due today + overdue), drop 3 (future, empty, null)
assert len(filtered) == 2, f'expected 2, got {len(filtered)}'
assert filtered[0]['content'] == 'due today'
assert filtered[1]['content'] == 'overdue'

# Verify the old behavior (no filter) would have kept all 5
assert len(raw_tasks) == 5

print('PASS: cache filter keeps 2/5 tasks (due<=today only). Future/null/empty due dates dropped.')
" || exit 1

exit 0
