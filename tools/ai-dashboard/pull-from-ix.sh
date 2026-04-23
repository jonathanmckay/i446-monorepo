#!/bin/bash
# pull-from-ix.sh — capture ix-local AI session data BEFORE push-to-ix
# clobbers anything. Pulls into host-scoped side paths on Straylight so
# downstream exporters can union them with local data.
#
# Outputs on Straylight:
#   ~/.claude/projects-ix/                  (mirror of ix:~/.claude/projects/)
#   ~/.copilot/session-store-ix.db          (sqlite .backup snapshot from ix)
#   ~/.copilot/session-state-ix/            (mirror of ix:~/.copilot/session-state/)
set -u
REMOTE="mckay@ix.tail9c51d5.ts.net"
LOG="$HOME/i446-monorepo/tools/ai-dashboard/.pull-from-ix.log"
ts() { date '+%F %T'; }

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" true 2>/dev/null; then
  echo "[$(ts)] skip: $REMOTE unreachable" >> "$LOG"
  exit 0
fi

rsync_one() {
  local src="$1" dst="$2"
  rsync -az --force \
    --exclude='*.tmp' --exclude='.DS_Store' \
    -e 'ssh -o ConnectTimeout=10 -o BatchMode=yes' \
    "$REMOTE:$src" "$dst" 2>&1 | tail -5
}

{
  echo "[$(ts)] starting pull"

  # Snapshot ix's live Copilot DB on the remote (safe sqlite copy), then pull.
  ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE" \
    'sqlite3 ~/.copilot/session-store.db ".backup ~/.copilot/session-store.snapshot.db" 2>&1' \
    || echo "  warn: remote sqlite .backup failed"

  mkdir -p "$HOME/.claude/projects-ix"
  mkdir -p "$HOME/.copilot/session-state-ix"

  rsync_one '.claude/projects/'                       "$HOME/.claude/projects-ix/"
  rsync_one '.copilot/session-store.snapshot.db'      "$HOME/.copilot/session-store-ix.db"
  rsync_one '.copilot/session-state/'                 "$HOME/.copilot/session-state-ix/"

  echo "[$(ts)] done"
} >> "$LOG" 2>&1
