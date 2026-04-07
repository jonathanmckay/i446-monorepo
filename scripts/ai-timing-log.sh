#!/bin/bash
# Log AI turn timing events to a JSONL file for dashboard latency analysis.
# Usage: ai-timing-log.sh <event> [session_id]
#   event: prompt_start | stop
#
# On prompt_start: record epoch + session_id to /tmp/claude-turn-start
# On stop: compute elapsed, append to timing log

EVENT=${1:-unknown}
SESSION_ID=${2:-unknown}
LOG_DIR="$HOME/.claude/timing"
LOG_FILE="$LOG_DIR/turns.jsonl"
START_FILE="/tmp/claude-turn-start"

mkdir -p "$LOG_DIR"

case "$EVENT" in
  prompt_start)
    echo "$(date +%s.%N) $SESSION_ID" > "$START_FILE"
    ;;
  stop)
    if [ -f "$START_FILE" ]; then
      read START_EPOCH START_SID < "$START_FILE"
      NOW=$(date +%s.%N)
      ELAPSED=$(echo "$NOW - $START_EPOCH" | bc 2>/dev/null || echo "0")
      DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      LOCAL_DATE=$(date +%Y-%m-%d)
      echo "{\"event\":\"turn\",\"ts\":\"$DATE\",\"date\":\"$LOCAL_DATE\",\"elapsed_s\":$ELAPSED,\"session\":\"$START_SID\"}" >> "$LOG_FILE"
      rm -f "$START_FILE"
    fi
    ;;
esac
