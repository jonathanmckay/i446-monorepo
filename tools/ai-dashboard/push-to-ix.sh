#!/bin/bash
# push-to-ix.sh — rsync laptop-only data to the mac mini ("ix") so the
# always-on dashboards there can read live numbers. Runs every 5 min via cron.
set -u
REMOTE="mckay@ix.tail9c51d5.ts.net"
LOG="$HOME/i446-monorepo/tools/ai-dashboard/.push-to-ix.log"
ts() { date '+%F %T'; }

# Skip if mini isn't reachable (laptop on a plane, mini offline, etc.)
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" true 2>/dev/null; then
  echo "[$(ts)] skip: $REMOTE unreachable" >> "$LOG"
  exit 0
fi

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
  rsync_one "$HOME/.copilot/session-store.db"           ".copilot/session-store.db"
  rsync_one "$HOME/m5x2-ai-stats/jm/stats-cache.json"   "m5x2-ai-stats/jm/stats-cache.json"
  rsync_one "$HOME/vault/i447/i446/llm-sessions.db"     "vault/i447/i446/llm-sessions.db"
  echo "[$(ts)] done"
} >> "$LOG" 2>&1
