#!/bin/bash
# dream-watchdog-lib.sh — activity probe for the dream-launch.sh stall watchdog.
# Sourced by dream-launch.sh; also sourced by tests/test_dream_watchdog.py.
#
# Bug fixed 2026-10-26 (killed v166, v167, v168): the watchdog used
#   find "$RUN_DIR" -type f -not -name "*.log" -newer PROMPT.md -print -quit
# which returns the FIRST match in readdir order (in practice dream-intake.json,
# written once at the end of intake), not the NEWEST. Its age grew monotonically
# from launch, so every run was killed ~15-20 min in no matter how busy it was,
# and logs/heartbeat (the documented keep-alive) was never consulted. This
# helper takes the max mtime over every file in the run dir, logs included.

# dream_newest_activity_epoch RUN_DIR REF_FILE
#   Prints the newest mtime (epoch s) of any file under RUN_DIR modified after
#   REF_FILE; falls back to REF_FILE's own mtime when nothing is newer.
dream_newest_activity_epoch() {
  local dir="$1" ref="$2" newest=""
  newest=$(find "$dir" -type f -newer "$ref" -exec stat -f '%m' {} + 2>/dev/null \
           | sort -n | tail -1) || true
  if [[ -z "$newest" ]]; then
    newest=$(stat -f '%m' "$ref" 2>/dev/null) || newest=0
  fi
  echo "$newest"
}

# dream_activity_age RUN_DIR REF_FILE [NOW_EPOCH]
#   Seconds since the most recent write anywhere in RUN_DIR.
dream_activity_age() {
  local now="${3:-$(date +%s)}"
  echo $(( now - $(dream_newest_activity_epoch "$1" "$2") ))
}

# dream_kill_tree PID — TERM the process and its direct children, then KILL.
# Never fails (safe under set -e: the old `kill -9` on an already-dead PID
# returned 1 and aborted the launcher before READY/pass3-retry/email).
dream_kill_tree() {
  local pid="$1"
  pkill -TERM -P "$pid" 2>/dev/null || true
  kill -TERM "$pid" 2>/dev/null || true
  sleep 2
  pkill -KILL -P "$pid" 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
}
