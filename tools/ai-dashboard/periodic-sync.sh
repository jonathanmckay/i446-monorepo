#!/bin/bash
# periodic-sync.sh — merge llm-sessions.db (Copilot CLI etc.) into the
# m5x2 dashboard cache and push, even when Claude Code's Stop hook hasn't
# fired today. Designed for cron (every ~30 min).
#
# Smoke test: tools/ai-dashboard/test_smoke.py hits the three local
# dashboards (5555/5556/5558) + the 5555 JSON APIs. Not wired in here
# (too noisy for cron) — run manually after restarts/deploys:
#   python3 "$(dirname "$0")/test_smoke.py"

REPO_DIR="$HOME/m5x2-ai-stats"
USER_ID="${M5X2_USER_ID:-jm}"
STATS_FILE="$HOME/.claude/stats-cache.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -f "$STATS_FILE" ] || exit 0
[ -d "$REPO_DIR/.git" ] || exit 0

mkdir -p "$REPO_DIR/$USER_ID"

if ! python3 "$SCRIPT_DIR/merge-llm-sessions.py" \
      --src "$STATS_FILE" \
      --out "$REPO_DIR/$USER_ID/stats-cache.json" \
      --device "$(hostname -s | tr '[:upper:]' '[:lower:]')" 2>>"$SCRIPT_DIR/.periodic-sync.log"; then
  echo "[$(date '+%F %T')] merge failed (exit=$?), falling back to cp" >>"$SCRIPT_DIR/.periodic-sync.log"
  cp "$STATS_FILE" "$REPO_DIR/$USER_ID/stats-cache.json"
fi

# Refresh session-stats.json too (MCP/skill/latency from JSONL)
python3 "$SCRIPT_DIR/compute-session-stats.py" \
  --user "$USER_ID" \
  --out "$REPO_DIR/$USER_ID/session-stats.json" \
  >/dev/null 2>&1 || true

cd "$REPO_DIR" || exit 0
git add "$USER_ID/stats-cache.json" "$USER_ID/session-stats.json" 2>/dev/null
git diff --cached --quiet && exit 0

git commit -m "stats: $USER_ID periodic $(date '+%Y-%m-%d %H:%M')" -q
git pull --rebase origin main -q 2>/dev/null || true
git push origin main -q 2>/dev/null || true
