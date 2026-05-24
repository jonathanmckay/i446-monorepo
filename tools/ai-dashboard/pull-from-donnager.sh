#!/bin/bash
# pull-from-donnager.sh — mirror donnager's Claude data to Straylight.
# Donnager (Windows 11) has no rsync; use scp -r. Volume is small (10s of
# jsonls), so a full-tree scp every 15 min is fine. Files are content-
# addressed (UUID names) so re-copy is idempotent.
#
# Outputs on Straylight:
#   ~/.claude/projects-donnager/   (mirror of donnager:~/.claude/projects/)
set -u
REMOTE="donnager"
LOG="$HOME/i446-monorepo/tools/ai-dashboard/.pull-from-donnager.log"
DST="$HOME/.claude/projects-donnager"
ts() { date '+%F %T'; }

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" 'echo ok' >/dev/null 2>&1; then
  echo "[$(ts)] skip: $REMOTE unreachable" >> "$LOG"
  exit 0
fi

mkdir -p "$DST"

{
  echo "[$(ts)] starting pull"
  scp -q -r -o ConnectTimeout=10 -o BatchMode=yes \
    "$REMOTE:.claude/projects/*" "$DST/" 2>&1 | tail -5
  count=$(find "$DST" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
  echo "[$(ts)] done ($count jsonls in mirror)"
} >> "$LOG" 2>&1
