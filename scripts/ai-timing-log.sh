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
    # Hooks pass session_id in stdin JSON, not env ($SESSION_ID is always empty).
    if [ ! -t 0 ]; then
      HOOK_SID=$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
      [ -n "$HOOK_SID" ] && SESSION_ID=$HOOK_SID
    fi
    # Desk/mobile attribution: log the prompt to the synced source-log with
    # net / ssh_from (lib/jmsource.py). Backgrounded: never slow the prompt.
    # tty = whether this claude has a controlling terminal (this hook, or an
    # ancestor up to claude, on a tty). Was hardcoded True, so cron's hourly
    # `claude -p` (export-claude-transcripts, :17) and Dream at 03:30 logged
    # as typed prompts and /1-1n painted 🖥️ desk into every block (2026-11-03).
    JMS_TTY=0; _p=$$
    for _i in 1 2 3 4; do
      case "$(ps -o tty= -p "$_p" 2>/dev/null | tr -d ' ')" in ""|"?"|"??") ;; *) JMS_TTY=1; break;; esac
      _p=$(ps -o ppid= -p "$_p" 2>/dev/null | tr -d ' ')
      [ -n "$_p" ] && [ "$_p" -gt 1 ] 2>/dev/null || break
    done
    JMS_TTY=$JMS_TTY JMS_SID=$SESSION_ID nohup python3 -c 'import os,sys; sys.path.insert(0, os.path.expanduser("~/i446-monorepo/lib")); import jmsource; jmsource.record("prompt", os.environ["JMS_SID"], source="cli", via="claude", tty=os.environ.get("JMS_TTY") == "1")' >/dev/null 2>&1 &
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
      DEVICE=$(python3 "$HOME/.claude/resolve-device.py" 2>/dev/null || echo "unknown")
      # Overwrite LAST_FILE (not append) — keeps only the latest stop for this turn
      echo "{\"event\":\"turn\",\"ts\":\"$DATE\",\"date\":\"$LOCAL_DATE\",\"elapsed_s\":$ELAPSED,\"session\":\"$START_SID\",\"device\":\"$DEVICE\"}" > "$LAST_FILE"
    fi
    ;;
esac
