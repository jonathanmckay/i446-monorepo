#!/bin/bash
# Weekly d359 <-> Apple Contacts sync, driven by a daily launchd tick.
# The 6-day gate below is what makes a daily tick behave as "weekly with
# retries": if this week's attempt fails, tomorrow's tick just tries again
# (last-success timestamp is only updated on a clean run).

set -u
TOOLDIR="$HOME/i446-monorepo/tools/d359"
STATE_DIR="$HOME/.local/state/jm/d359-apple-sync"
LAST_SUCCESS="$STATE_DIR/last-success"
LOG="$STATE_DIR/weekly.log"
MIN_INTERVAL_DAYS=6
MAX_ATTEMPTS=3
RETRY_SLEEP_SECS=60

mkdir -p "$STATE_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

if [ -f "$LAST_SUCCESS" ]; then
    last=$(cat "$LAST_SUCCESS")
    now=$(date +%s)
    age_days=$(( (now - last) / 86400 ))
    if [ "$age_days" -lt "$MIN_INTERVAL_DAYS" ]; then
        exit 0
    fi
fi

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    log "attempt $attempt/$MAX_ATTEMPTS: running --apply"
    output=$(python3 "$TOOLDIR/d359-contacts-sync.py" --apply 2>&1)
    status=$?
    if [ "$status" -eq 0 ]; then
        log "success on attempt $attempt"
        echo "$output" >> "$LOG"
        date +%s > "$LAST_SUCCESS"
        exit 0
    fi
    log "attempt $attempt failed (exit $status):"
    echo "$output" >> "$LOG"
    attempt=$((attempt + 1))
    [ "$attempt" -le "$MAX_ATTEMPTS" ] && sleep "$RETRY_SLEEP_SECS"
done

log "all $MAX_ATTEMPTS attempts failed this cycle; will retry on next daily tick"
exit 1
