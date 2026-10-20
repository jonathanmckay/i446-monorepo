#!/bin/bash
# Regression test: the ctrl-p split script must strip fzf's "…" truncation
# from the task name before searching Todoist.
#
# Bug (2026-06-06): splitting a long task ran all three dialogs, then the
# Todoist substring search used the truncated display name (containing "…")
# and failed with "? split: task not found". The defer script (ctrl-d) had
# this handling; the split script did not.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# Isolate the split-script heredoc (between the SPLITEOF markers)
SPLIT_BLOCK=$(sed -n '/--- Split script used by fzf ctrl-p binding ---/,/^SPLITEOF$/p' "$SCRIPT")

if [ -z "$SPLIT_BLOCK" ]; then
  echo "FAIL: could not locate split script block in dtd.sh"
  exit 1
fi

# 1. The split block must contain truncation handling
if echo "$SPLIT_BLOCK" | grep -q '"…"' && echo "$SPLIT_BLOCK" | grep -q '%%…'; then
  echo "PASS: split script strips … truncation"
else
  echo "FAIL: split script must strip fzf … truncation before Todoist search"
  exit 1
fi

# 2. Truncation handling must come BEFORE the python Todoist search
LINE_TRUNC=$(echo "$SPLIT_BLOCK" | grep -n '%%…' | head -1 | cut -d: -f1)
LINE_SEARCH=$(echo "$SPLIT_BLOCK" | grep -n 'Find the original Todoist task' | head -1 | cut -d: -f1)
if [ -n "$LINE_TRUNC" ] && [ -n "$LINE_SEARCH" ] && [ "$LINE_TRUNC" -lt "$LINE_SEARCH" ]; then
  echo "PASS: truncation strip happens before the Todoist search"
else
  echo "FAIL: truncation strip must precede the Todoist search (trunc=$LINE_TRUNC search=$LINE_SEARCH)"
  exit 1
fi

# 3. Behavior check: simulate the zsh truncation logic on a truncated name
RESULT=$(zsh -c '
clean="check wechat conversation on both …(10) [10]"
clean=$(echo "$clean" | sed -E "s/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//")
if [[ "$clean" == *"…"* ]]; then
  clean="${clean%%…*}"
  clean=$(echo "$clean" | sed "s/ *$//")
fi
echo "$clean"')
if [ "$RESULT" = "check wechat conversation on both" ]; then
  echo "PASS: truncated name resolves to clean search prefix ('$RESULT')"
else
  echo "FAIL: expected clean prefix, got '$RESULT'"
  exit 1
fi

echo "All tests passed."
