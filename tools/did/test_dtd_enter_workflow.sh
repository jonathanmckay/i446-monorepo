#!/bin/bash
# Regression test for dtd's Enter-first task flow.
# Enter should start a timer for the selected task, then complete the task if
# that same timer is already running. Ctrl-S remains only a compatibility alias.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

grep -q 'DTD_ENTER=' "$SCRIPT" \
  || { echo "FAIL: dtd must define a DTD_ENTER action script"; exit 1; }

# Enter goes through the schedule-screen router since 2026-10-04 (transform,
# so a typed day count works even with zero matching rows); the router runs
# DTD_ENTER for a normal row.
grep -q -- '--bind "enter:transform($DTD_PICKENTER {2} {q})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)"' "$SCRIPT" \
  || { echo "FAIL: Enter must route via DTD_PICKENTER (hidden id {2} + query), reload, clear the query, refresh the status header, and keep fzf open"; exit 1; }
grep -q '"$DTD_ENTER" "\\$id"' "$SCRIPT" \
  || { echo "FAIL: DTD_PICKENTER must run DTD_ENTER on the cursor row's id"; exit 1; }

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
grep -q 'transform($DTD_PICKENTER {2} {q})' "$SCRIPT" \
  || { echo "FAIL: bindings must pass the hidden id field {2}"; exit 1; }

RESOLVER="$HOME/i446-monorepo/tools/did/dtd_resolve.py"
grep -Fq '^▶ [^·]* · ' "$RESOLVER" \
  || { echo "FAIL: dtd_resolve.py must strip the running-task timer prefix"; exit 1; }

echo "PASS: dtd Enter start/complete workflow is wired (id-threaded)"
