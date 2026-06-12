#!/bin/zsh
# Regression test: dtd reloads must NOT re-read the live cache mid-session.
#
# Bug (2026-06): marking one task done (e.g. "push") made many other tasks
# vanish at once — the visible count collapsed (26 -> 19). Root cause: every
# fzf reload ran `DTD_SYNC="cp $CACHE $DTD_CACHE_FILE"`, re-copying the LIVE
# task-queue cache over the frozen startup snapshot. When an external process
# (the morning routine / /todo / another terminal) had refreshed the live cache
# — dropping completed recurring habits whose due date advanced — the next
# reload pulled that smaller cache and tasks disappeared.
#
# This directly violated the documented invariant at the top of dtd.sh:
#   "cache is snapshotted ONCE at startup. No mid-session re-reads."
#
# Fix: reloads read the frozen $DTD_CACHE_FILE snapshot. Only ctrl-r (explicit
# user refresh) re-copies the live cache.

DTD="${DTD:-$HOME/i446-monorepo/tools/did/dtd.sh}"
fail=0

# --- 1. The invariant comment is still documented ---
if ! grep -q "snapshotted ONCE at startup" "$DTD"; then
  echo "FAIL: missing snapshot-once invariant comment in dtd.sh"
  fail=1
fi

# --- 2. The auto-reload command must NOT copy the live cache ---
# DTD_RELOAD is what every passive binding (enter/alt-enter/defer/...) runs.
# It must be the list command alone, with no `cp ... $CACHE ... $DTD_CACHE_FILE`.
reload_def=$(grep -n 'DTD_RELOAD=' "$DTD" | grep -v '\$DTD_RELOAD' | head -1)
if [[ -z "$reload_def" ]]; then
  echo "FAIL: could not find DTD_RELOAD definition"
  fail=1
elif echo "$reload_def" | grep -q 'DTD_SYNC'; then
  echo "FAIL: DTD_RELOAD still includes DTD_SYNC (re-copies live cache on every reload)"
  echo "      offending line: $reload_def"
  fail=1
fi

# Belt-and-suspenders: no DTD_SYNC variable that copies $CACHE should survive.
if grep -q 'DTD_SYNC=' "$DTD"; then
  echo "FAIL: DTD_SYNC variable reintroduced — reloads will re-read the live cache"
  fail=1
fi

# --- 3. ctrl-r (explicit refresh) MUST still pull the live cache ---
# This is the sanctioned path for external changes to appear.
if ! grep -E 'ctrl-r:.*cp .*\$CACHE .*\$DTD_CACHE_FILE' "$DTD" >/dev/null; then
  echo "FAIL: ctrl-r no longer refreshes the snapshot from the live cache"
  fail=1
fi

# --- 4. ctrl-v (points edit) patches the snapshot, not the live cache ---
# Otherwise the new [N] would not show until ctrl-r and the snapshot would drift.
if ! grep -F 'POINTS_FAST" "\$query" "\$newpts" "$DTD_CACHE_FILE"' "$DTD" >/dev/null; then
  echo "FAIL: ctrl-v points edit no longer targets the snapshot cache (\$DTD_CACHE_FILE)"
  fail=1
fi

if [[ $fail -eq 0 ]]; then
  echo "PASS: reloads honor the frozen snapshot; only ctrl-r refreshes the live cache."
  exit 0
fi
exit 1
