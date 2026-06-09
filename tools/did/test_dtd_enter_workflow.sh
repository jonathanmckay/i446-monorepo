#!/bin/bash
# Regression test for dtd's Enter-first task flow.
# Enter should start a timer for the selected task, then complete the task if
# that same timer is already running. Ctrl-S remains only a compatibility alias.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

grep -q 'DTD_ENTER=' "$SCRIPT" \
  || { echo "FAIL: dtd must define a DTD_ENTER action script"; exit 1; }

grep -q -- '--bind "enter:execute-silent($DTD_ENTER {})+reload($DTD_RELOAD)+transform-header(cat $DTD_HDR)"' "$SCRIPT" \
  || { echo "FAIL: Enter must run DTD_ENTER, reload, and keep fzf open"; exit 1; }

grep -q 'printf.*> "\\$FIFO"' "$SCRIPT" \
  || { echo "FAIL: DTD_ENTER must send matching running tasks to the completion FIFO"; exit 1; }

grep -q 'printf.*> "\\$TIMER"' "$SCRIPT" \
  || { echo "FAIL: DTD_START must persist the running task for list promotion"; exit 1; }

grep -q 'running_lines' "$SCRIPT" \
  || { echo "FAIL: list generator must promote the running task to the top"; exit 1; }

grep -q '▶ .* · ' "$SCRIPT" \
  || { echo "FAIL: running task display must include a timer prefix"; exit 1; }

grep -Fq 's/^↻ //; s/^▶ [^·]* · //' "$SCRIPT" \
  || { echo "FAIL: task cleaners must strip the running-task timer prefix"; exit 1; }

echo "PASS: dtd Enter start/complete workflow is wired"
