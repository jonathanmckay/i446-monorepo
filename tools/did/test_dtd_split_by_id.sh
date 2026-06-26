#!/bin/zsh
# Regression test: dtd's ctrl-p split must locate the task by the ID dtd passed
# it, NOT by re-searching 'today | overdue' and substring-matching the display
# name. The old search silently failed AFTER all three dialogs for any dtd task
# outside that window (weekly/1neon habits, future-due goals) or whose name
# didn't substring-match — the user answered every prompt and got nothing
# (reported 2026-06-26: "went through the dialogs, then nothing happened").
set -e
cd "$(dirname "$0")"
DTD=dtd.sh
fail() { echo "FAIL: $1"; exit 1; }

# Extract the split script body from the heredoc.
split=$(awk "/cat > \"\\\$DTD_SPLIT\" << 'SPLITEOF'/{f=1;next} /^SPLITEOF/{f=0} f" "$DTD")
[[ -n "$split" ]] || fail "could not extract DTD_SPLIT body"

# 1. The id is captured (not overwritten by the content resolve) and passed in.
grep -q 'task_id="\$1"' "$DTD" || fail "split does not preserve the task id (\$1)"
echo "$split" | grep -q 'task_id = sys.argv\[9\]' || fail "python does not read task_id arg"
echo "$split" | grep -q '"\$REMOVED" "\$task_id"' || fail "task_id not passed to python invocation"

# 2. It fetches the task by id, and the old today|overdue search is gone.
echo "$split" | grep -q "api('GET', f'/tasks/{task_id}')" || fail "split does not fetch the task by id"
echo "$split" | grep -q 'today%20%7C%20overdue' && fail "split still re-searches today|overdue (the bug)"

# 3. A missing/invalid task still fails closed (no half-applied split).
echo "$split" | grep -q "split: task not found" || fail "no not-found guard"

echo "PASS: dtd split locates the task by id, not a scoped substring search"
