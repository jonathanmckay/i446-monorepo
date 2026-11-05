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

# dream_fallback_brief RUN_DIR VERSION EXIT_CODE
#   Writes a mechanical morning-brief.md (one line per draft/approval/branch
#   note, with its first heading) when every claude pass failed to write one.
#   No-op if a brief already exists or there is nothing to surface.
dream_fallback_brief() {
  local dir="$1" ver="$2" rc="${3:-?}" brief="$1/morning-brief.md" f n=0 title
  [[ -f "$brief" ]] && return 0
  for f in "$dir"/drafts/*.md "$dir"/approvals/*.md "$dir"/branches/*.md; do
    [[ -f "$f" ]] && n=$((n + 1))
  done
  [[ $n -eq 0 ]] && return 0
  {
    echo "# Dream $ver — FALLBACK brief (Pass 3 never wrote one; exit=$rc)"
    echo
    echo "Every claude pass died before ranking cards. Raw Pass-2 output, unranked,"
    echo "unfiltered — read before acting. Log: logs/agent-run.log"
    echo
    for f in "$dir"/drafts/*.md "$dir"/approvals/*.md "$dir"/branches/*.md; do
      [[ -f "$f" ]] || continue
      title=$(grep -m1 -E '^(#|title:)' "$f" 2>/dev/null | sed -E 's/^(#+|title:)[[:space:]]*//' || true)
      echo "- [${f#"$dir"/}](${f#"$dir"/})${title:+ — $title}"
    done
  } > "$brief"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] FALLBACK brief written: $n items"
}
