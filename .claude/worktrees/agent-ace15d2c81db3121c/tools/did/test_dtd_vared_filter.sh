#!/bin/bash
# Regression test: tasks requiring extra input (cpap, ibx s897, etc.)
# must use the ORIGINAL name (before vared) for session_done filtering,
# not the modified name with appended args.
#
# Bug: session_done stored "ibx s897 15" after vared, which didn't match
# "ibx s897" in the jq completed filter, so the task reappeared.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# Verify that the session filter gets clean_for_filter (original), not clean
# (modified). The session list lives in $DTD_SESSION (a file, so the ctrl-z
# undo binding can edit it); the append must use clean_for_filter, and
# echo "$clean" (the vared-modified version) goes to the worker.

# Check session filter file uses clean_for_filter
if grep -q 'echo "$clean_for_filter" >> "$DTD_SESSION"' "$SCRIPT"; then
  echo "PASS: session filter uses clean_for_filter (original name)"
else
  echo "FAIL: session filter should use clean_for_filter, not clean"
  exit 1
fi

# Check worker gets the modified clean (with user-appended args)
if grep -q 'echo "$clean" >&3' "$SCRIPT"; then
  echo "PASS: worker receives modified clean (with args)"
else
  echo "FAIL: worker should receive modified clean"
  exit 1
fi

# Check clean_for_filter is set before the case block
LINE_FILTER=$(grep -n 'clean_for_filter=' "$SCRIPT" | head -1 | cut -d: -f1)
LINE_CASE=$(grep -n 'case "$clean_lower"' "$SCRIPT" | head -1 | cut -d: -f1)
if [ "$LINE_FILTER" -lt "$LINE_CASE" ]; then
  echo "PASS: clean_for_filter assigned before case block"
else
  echo "FAIL: clean_for_filter must be set before the vared case block"
  exit 1
fi

echo "All tests passed."
