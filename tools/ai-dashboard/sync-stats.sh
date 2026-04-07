#!/bin/bash
# sync-stats.sh — auto-syncs Claude Code stats to m5x2/ai-stats repo
# Runs as a Claude Code Stop hook. Set M5X2_USER_ID in your shell profile.

REPO_DIR="$HOME/m5x2-ai-stats"
USER_ID="${M5X2_USER_ID:-}"
STATS_FILE="$HOME/.claude/stats-cache.json"

[ -z "$USER_ID" ] && exit 0
[ -f "$STATS_FILE" ] || exit 0
[ -d "$REPO_DIR/.git" ] || exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$REPO_DIR/$USER_ID"
cp "$STATS_FILE" "$REPO_DIR/$USER_ID/stats-cache.json"

# Compute MCP/skill/latency stats from local JSONL session files
python3 "$SCRIPT_DIR/compute-session-stats.py" \
  --user "$USER_ID" \
  --out "$REPO_DIR/$USER_ID/session-stats.json" \
  2>/dev/null || true

cd "$REPO_DIR"
git add "$USER_ID/stats-cache.json" "$USER_ID/session-stats.json"
git diff --cached --quiet && exit 0  # nothing changed

git commit -m "stats: $USER_ID $(date '+%Y-%m-%d %H:%M')" -q
git pull --rebase origin main -q 2>/dev/null || true
git push origin main -q 2>/dev/null || true
