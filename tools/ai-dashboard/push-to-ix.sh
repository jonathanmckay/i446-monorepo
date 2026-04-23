#!/bin/bash
# push-to-ix.sh — rsync laptop-only data to the mac mini ("ix") so the
# always-on dashboards there can read live numbers. Runs every 5 min via cron.
#
# IMPORTANT: We first call pull-from-ix.sh to capture ix's local Claude/Copilot
# session data BEFORE we (potentially) overwrite anything on ix. We push
# Straylight's session-store.db to a renamed path on ix
# (~/.copilot/session-store-straylight.db) so we never destroy ix's live DB.
# ix dashboards that want Straylight Copilot data should read that renamed file.
set -u
REMOTE="mckay@ix.tail9c51d5.ts.net"
LOG="$HOME/i446-monorepo/tools/ai-dashboard/.push-to-ix.log"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ts() { date '+%F %T'; }

# Pull ix-local data first (idempotent, fast, fails gracefully).
"$SCRIPT_DIR/pull-from-ix.sh" || true

# Skip if mini isn't reachable (laptop on a plane, mini offline, etc.)
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" true 2>/dev/null; then
  echo "[$(ts)] skip: $REMOTE unreachable" >> "$LOG"
  exit 0
fi

# Snapshot Straylight's live Copilot DB before pushing (safe sqlite copy).
sqlite3 "$HOME/.copilot/session-store.db" \
  ".backup $HOME/.copilot/session-store.snapshot.db" 2>>"$LOG" \
  || echo "[$(ts)] warn: local sqlite .backup failed" >> "$LOG"

rsync_one() {
  local src="$1" dst="$2"
  [ -e "$src" ] || return 0
  # --force lets rsync delete non-empty dirs on the destination when the
  # source no longer has them (fixes "unlinkat: Directory not empty" on
  # the deeply-nested ~/.claude/skills/did/did/... tree).
  rsync -az --delete-excluded --force \
    --exclude='*.tmp' --exclude='.DS_Store' \
    -e 'ssh -o ConnectTimeout=10 -o BatchMode=yes' \
    "$src" "$REMOTE:$dst" 2>&1 | tail -5
}

{
  echo "[$(ts)] starting push"
  rsync_one "$HOME/.claude/stats-cache.json"            ".claude/stats-cache.json"
  rsync_one "$HOME/.claude/projects/"                   ".claude/projects/"
  rsync_one "$HOME/.claude/timing/"                     ".claude/timing/"
  # ~/.claude/skills/ is now a symlink on both machines to
  # ~/i446-monorepo/skills/claude-skills/, synced via git. Do not rsync.
  rsync_one "$HOME/.copilot/session-store.snapshot.db"  ".copilot/session-store-straylight.db"
  rsync_one "$HOME/m5x2-ai-stats/jm/stats-cache.json"   "m5x2-ai-stats/jm/stats-cache.json"
  rsync_one "$HOME/vault/i447/i446/llm-sessions.db"     "vault/i447/i446/llm-sessions.db"
  echo "[$(ts)] done"
} >> "$LOG" 2>&1
