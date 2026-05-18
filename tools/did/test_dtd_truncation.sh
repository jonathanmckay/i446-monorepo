#!/bin/bash
# Regression test: truncated task names (containing "…") must be resolved
# back to their full original name before being sent to the worker or
# recorded in session_done. Otherwise the truncated name won't match
# the cache's full name, and the task stays in the list after completion.
#
# Bug: fzf awk middle-truncation produced "check wechat conversation on both …(10) [10]"
# which didn't match "check wechat conversation on both phones" in the filter.

set -e

SCRIPT="$HOME/i446-monorepo/tools/did/dtd.sh"

# Check that the truncation resolution block exists
if grep -q '"…"' "$SCRIPT" && grep -q 'prefix=.*%%…' "$SCRIPT"; then
  echo "PASS: truncation resolution block exists (checks for … and extracts prefix)"
else
  echo "FAIL: dtd.sh must resolve truncated fzf names back to full names"
  exit 1
fi

# Check that the resolution happens BEFORE annotation stripping
LINE_TRUNC=$(grep -n '%%…' "$SCRIPT" | head -1 | cut -d: -f1)
LINE_STRIP=$(grep -n "Strip annotations" "$SCRIPT" | head -1 | cut -d: -f1)
if [ "$LINE_TRUNC" -lt "$LINE_STRIP" ]; then
  echo "PASS: truncation resolution happens before annotation stripping"
else
  echo "FAIL: truncation resolution must happen before stripping annotations"
  exit 1
fi

# Check that full name lookup uses CACHE_SNAPSHOT
if grep -q 'CACHE_SNAPSHOT.*jq.*pfx' "$SCRIPT"; then
  echo "PASS: full name lookup uses CACHE_SNAPSHOT"
else
  echo "FAIL: truncation resolution should look up full name from CACHE_SNAPSHOT"
  exit 1
fi

echo "All tests passed."
