#!/bin/bash
# Regression test for dtd's Enter-first task flow.
# Enter should start a timer for the selected task, then complete the task if
# that same timer is already running. Ctrl-S remains only a compatibility alias.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

grep -q 'DTD_ENTER=' "$SCRIPT" \
  || { echo "FAIL: dtd must define a DTD_ENTER action script"; exit 1; }

grep -q -- '--bind "enter:execute-silent($DTD_ENTER {2})+reload($DTD_RELOAD)+transform-header(cat $DTD_HDR)"' "$SCRIPT" \
  || { echo "FAIL: Enter must run DTD_ENTER (with the hidden id field {2}), reload, and keep fzf open"; exit 1; }

grep -q 'printf.*> "\\$FIFO"' "$SCRIPT" \
  || { echo "FAIL: DTD_ENTER must send matching running tasks to the completion FIFO"; exit 1; }

grep -q 'printf.*> "\\$TIMER"' "$SCRIPT" \
  || { echo "FAIL: DTD_START must persist the running task for list promotion"; exit 1; }

grep -q 'running_lines' "$SCRIPT" \
  || { echo "FAIL: list generator must promote the running task to the top"; exit 1; }

grep -q '▶ .* · ' "$SCRIPT" \
  || { echo "FAIL: running task display must include a timer prefix"; exit 1; }

# Bindings now pass the hidden id ({2}); dtd_resolve.py maps it back to the
# canonical task content and strips the running-task timer prefix in the legacy
# text-fallback path.
grep -q 'execute-silent($DTD_ENTER {2})' "$SCRIPT" \
  || { echo "FAIL: bindings must pass the hidden id field {2}"; exit 1; }

RESOLVER="$HOME/i446-monorepo/tools/did/dtd_resolve.py"
grep -Fq '^▶ [^·]* · ' "$RESOLVER" \
  || { echo "FAIL: dtd_resolve.py must strip the running-task timer prefix"; exit 1; }

echo "PASS: dtd Enter start/complete workflow is wired (id-threaded)"
