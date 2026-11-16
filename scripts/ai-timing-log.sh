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
    # tty = "a person typed this". Headless runs (`claude -p` / `--print` from
    # cron, daemons, detached helpers) fire this hook too; until 2026-11-16 they
    # were logged tty=True and the hourly d357-auto-file cron lit 🖥️ desk in
    # every block of the /1-1n heatmap. Two signals, either is enough: the
    # entrypoint Claude Code exports (sdk-cli = headless) and the parent
    # process's argv (covers a `claude -p` that inherits `cli` from the
    # interactive session that spawned it).
    JMS_TTY=1
    [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "sdk-cli" ] && JMS_TTY=0
    case " $(ps -o args= -p "$PPID" 2>/dev/null) " in
      *" -p "*|*" --print "*) JMS_TTY=0 ;;
    esac
    JMS_TTY=$JMS_TTY JMS_SID=$SESSION_ID nohup python3 -c 'import os,sys; sys.path.insert(0, os.path.expanduser("~/i446-monorepo/lib")); import jmsource; jmsource.record("prompt", os.environ["JMS_SID"], source="cli", via="claude", tty=os.environ.get("JMS_TTY") != "0")' >/dev/null 2>&1 &
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
