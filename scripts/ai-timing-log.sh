#!/bin/bash
# Log AI turn timing events to a JSONL file for dashboard latency analysis.
# Usage: ai-timing-log.sh <event> [session_id]
#   event: prompt_start | stop
#
# On prompt_start: save epoch to START_FILE; finalize previous turn to JSONL
# On stop: update LAST_FILE with latest elapsed (overwrites each time)
# Result: each user turn = one JSONL entry with full wall time (prompt_start → last stop)

EVENT=${1:-unknown}
SESSION_ID=${2:-unknown}
LOG_DIR="$HOME/.claude/timing"
LOG_FILE="$LOG_DIR/turns.jsonl"
START_FILE="/tmp/claude-turn-start"
LAST_FILE="/tmp/claude-turn-last"

mkdir -p "$LOG_DIR"

case "$EVENT" in
  prompt_start)
    # Finalize previous turn: flush LAST_FILE to JSONL
    if [ -f "$LAST_FILE" ]; then
      cat "$LAST_FILE" >> "$LOG_FILE"
      rm -f "$LAST_FILE"
    fi
    # Record new start time
    echo "$(date +%s.%N) $SESSION_ID" > "$START_FILE"
    ;;
  stop)
    if [ -f "$START_FILE" ]; then
      read START_EPOCH START_SID < "$START_FILE"
      NOW=$(date +%s.%N)
      ELAPSED=$(echo "$NOW - $START_EPOCH" | bc 2>/dev/null || echo "0")
      DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      LOCAL_DATE=$(date +%Y-%m-%d)
      # Overwrite LAST_FILE (not append) — keeps only the latest stop for this turn
      echo "{\"event\":\"turn\",\"ts\":\"$DATE\",\"date\":\"$LOCAL_DATE\",\"elapsed_s\":$ELAPSED,\"session\":\"$START_SID\"}" > "$LAST_FILE"
    fi
    ;;
esac
