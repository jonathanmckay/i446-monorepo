#!/bin/bash
# git-autopush-healthcheck.sh — independent watchdog for git-autopush.sh.
#
# git-autopush.sh logs "WARNING: pull failed" / "WARNING: push failed" when it
# detects trouble, then continues. That alone isn't enough: on 2026-06-18 the
# Ix checkout entered a rebase that never finished, and autopush kept logging
# warnings for 9 days while everyone assumed sync was healthy. This watchdog
# screams when the repo enters any of those silent-failure states.
#
# Usage: git-autopush-healthcheck.sh [REPO_DIR]   (default: $HOME/i446-monorepo)
# Cron-friendly: exit 0 = healthy, exit 1 = at least one alert emitted.
#
# Alert sink: appends a JSON line to $HOME/vault/z_ibx/alerts.jsonl and fires a
# macOS notification (same pattern as tools/ai-dashboard/watchdog.sh).

REPO_DIR="${1:-$HOME/i446-monorepo}"
DIVERGENCE_THRESHOLD="${DIVERGENCE_THRESHOLD:-20}"   # behind-by commits
STALE_PUSH_HOURS="${STALE_PUSH_HOURS:-4}"            # hours since last "pushed"
ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
HEALTH_LOG="$HOME/i446-monorepo/scripts/.autopush-health.log"
HOST="$(hostname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$ALERTS")"
touch "$HEALTH_LOG"

alerts_emitted=0

emit_alert() {
  local reason="$1" detail="$2"
  printf '{"ts":"%s","host":"%s","tool":"git-autopush-healthcheck","severity":"warning","repo":"%s","reason":"%s","detail":"%s"}\n' \
    "$NOW" "$HOST" "$REPO_DIR" "$reason" "$detail" >> "$ALERTS"
  echo "[$(date '+%F %T')] ALERT $reason — $detail" >> "$HEALTH_LOG"
  osascript -e "display notification \"$detail\" with title \"git-autopush stuck\" subtitle \"$reason\" sound name \"Basso\"" 2>/dev/null || true
  alerts_emitted=$((alerts_emitted + 1))
}

if [ ! -d "$REPO_DIR/.git" ]; then
  emit_alert "repo_missing" "$REPO_DIR is not a git repo on $HOST"
  exit 1
fi

cd "$REPO_DIR" || { emit_alert "cd_failed" "cannot cd to $REPO_DIR"; exit 1; }

# 1. Stuck rebase / merge / cherry-pick — the actual 9-day failure mode.
for state_dir in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD; do
  if [ -e ".git/$state_dir" ]; then
    age_days=$(( ( $(date +%s) - $(stat -f %m ".git/$state_dir") ) / 86400 ))
    emit_alert "stuck_$state_dir" ".git/$state_dir present (age ${age_days}d) — autopush cannot pull/push"
  fi
done

# 2. Detached HEAD — autopush commits into a dangling ref that nobody pulls.
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$BRANCH" = "HEAD" ]; then
  emit_alert "detached_head" "HEAD is detached at $(git rev-parse --short HEAD) — autopush commits will not reach origin"
fi

# 3. Divergence from upstream — silent rebase failures let local fall behind.
if [ "$BRANCH" != "HEAD" ] && git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch -q origin "$BRANCH" 2>/dev/null || true
  read -r ahead behind < <(git rev-list --left-right --count "HEAD...origin/$BRANCH" 2>/dev/null | awk '{print $1, $2}')
  if [ -n "$behind" ] && [ "$behind" -ge "$DIVERGENCE_THRESHOLD" ]; then
    emit_alert "behind_upstream" "$BRANCH is $behind commits behind origin/$BRANCH (ahead $ahead) — pull is failing"
  fi
fi

# 4. Stale push — last "pushed" log line is too old.
AUTOPUSH_LOG="$REPO_DIR/scripts/.autopush.log"
if [ -f "$AUTOPUSH_LOG" ]; then
  LAST_PUSH_LINE=$(grep '\] pushed' "$AUTOPUSH_LOG" 2>/dev/null | tail -1)
  if [ -n "$LAST_PUSH_LINE" ]; then
    LAST_PUSH_TS=$(echo "$LAST_PUSH_LINE" | sed -E 's/^\[([0-9-]+ [0-9:]+)\].*/\1/')
    LAST_EPOCH=$(date -j -f '%Y-%m-%d %H:%M:%S' "$LAST_PUSH_TS" +%s 2>/dev/null)
    if [ -n "$LAST_EPOCH" ]; then
      age_hours=$(( ( $(date +%s) - LAST_EPOCH ) / 3600 ))
      if [ "$age_hours" -ge "$STALE_PUSH_HOURS" ]; then
        emit_alert "stale_push" "last successful push was ${age_hours}h ago ($LAST_PUSH_TS)"
      fi
    fi
  fi
fi

if [ "$alerts_emitted" -eq 0 ]; then
  echo "[$(date '+%F %T')] ok — branch=$BRANCH ahead=${ahead:-?} behind=${behind:-?}" >> "$HEALTH_LOG"
  exit 0
fi

exit 1
