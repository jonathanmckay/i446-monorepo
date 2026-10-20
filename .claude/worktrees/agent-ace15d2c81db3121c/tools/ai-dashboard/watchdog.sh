#!/bin/bash
# watchdog.sh — independent heartbeat check for the periodic-sync pipeline.
#
# Fails loudly (macOS notification + alerts.jsonl) if the m5x2-ai-stats repo
# hasn't received a commit touching this host's user files within
# THRESHOLD_MIN minutes. Designed to live in cron at a cadence shorter than
# THRESHOLD_MIN so two consecutive misses still alert.
#
# Independent of periodic-sync itself: if periodic-sync's cron entry vanishes,
# this watchdog still runs and screams. (Both have to die before you go silent.)
#
# We check the git-commit timestamp (not periodic-sync's own log) because the
# log is only written on errors and `git diff --cached --quiet` legitimately
# skips commits on no-op runs. The commit timestamp is the user-visible signal:
# it's exactly what feeds the dashboard's freshness banner.

THRESHOLD_MIN=${THRESHOLD_MIN:-90}
USER_ID="${M5X2_USER_ID:-jm}"
REPO_DIR="$HOME/m5x2-ai-stats"
ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
WATCHDOG_LOG="$HOME/i446-monorepo/tools/ai-dashboard/.watchdog.log"
HOST="$(hostname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$ALERTS")"
touch "$WATCHDOG_LOG"

emit_alert() {
  local reason="$1" detail="$2"
  printf '{"ts":"%s","host":"%s","tool":"ai-dashboard-watchdog","severity":"warning","reason":"%s","detail":"%s"}\n' \
    "$NOW" "$HOST" "$reason" "$detail" >> "$ALERTS"
  echo "[$(date '+%F %T')] ALERT $reason — $detail" >> "$WATCHDOG_LOG"
  osascript -e "display notification \"$detail\" with title \"AI Dashboard sync stalled\" subtitle \"$reason\" sound name \"Basso\"" 2>/dev/null || true
}

if [ ! -d "$REPO_DIR/.git" ]; then
  emit_alert "repo_missing" "$REPO_DIR is not a git repo on $HOST"
  exit 1
fi

# Last commit timestamp touching this user's files
LAST_TS=$(cd "$REPO_DIR" && git log -1 --format=%at -- \
  "$USER_ID/stats-cache.json" "$USER_ID/session-stats.json" 2>/dev/null)

if [ -z "$LAST_TS" ]; then
  emit_alert "no_commits" "No commits touching $USER_ID/* in $REPO_DIR"
  exit 1
fi

NOW_EPOCH=$(date +%s)
AGE_MIN=$(( (NOW_EPOCH - LAST_TS) / 60 ))

if [ "$AGE_MIN" -gt "$THRESHOLD_MIN" ]; then
  emit_alert "sync_stalled" "m5x2-ai-stats hasn't received a $USER_ID commit in ${AGE_MIN}min (threshold ${THRESHOLD_MIN}min) on $HOST"
  exit 1
fi

echo "[$(date '+%F %T')] ok user=$USER_ID age=${AGE_MIN}min" >> "$WATCHDOG_LOG"
exit 0

