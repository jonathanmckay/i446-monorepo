#!/bin/bash
# Daily /0t safety net — runs on Ix via cron at 14:00 PT (the sleep window's
# cutoff, so last night's number is final). Sibling of run_0r_archive.sh.
#
#   gate  : skip if 0t is already done today (tools/0t/0t_done_today.py —
#           completed-today mirror OR Todoist due date; either suffices)
#   run   : tools/0t/0t-fast.py, exactly what the /0t skill's fast path does
#   fallback: only if the script fails, headless `claude -p "/0t"` so the
#           skill's manual steps get a chance (needs Claude logged in on Ix)
#   alert : any failure -> ~/vault/z_ibx/alerts.jsonl via bin/cron-alert.sh
#
# Usage: run_0t.sh [--dry-run]   (--dry-run stops after the gate)
export PATH="/opt/homebrew/bin:/usr/bin:/bin"
MONO="$HOME/i446-monorepo"
LOGDIR="$HOME/.cache/0t"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(date +%Y-%m-%d).log"
LOCK="$LOGDIR/run_0t.lock"
cd "$HOME" || exit 1
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
fail() { log "FAILED: $2"; "$HOME/bin/cron-alert.sh" run_0t "$1" "$2 (see $LOG)"; }

if ! shlock -f "$LOCK" -p $$; then
    log "another run_0t.sh holds $LOCK; exiting"
    exit 0
fi
trap 'rm -f "$LOCK"' EXIT

log "=== run_0t start ==="
if python3 "$MONO/tools/0t/0t_done_today.py" >> "$LOG" 2>&1; then
    log "skip: 0t already done today"
    exit 0
fi
if [ "${1:-}" = "--dry-run" ]; then
    log "dry-run: 0t not done today; would run 0t-fast.py now"
    exit 0
fi

log "running 0t-fast.py"
if python3 "$MONO/tools/0t/0t-fast.py" >> "$LOG" 2>&1; then
    log "0t-fast.py ok"
    code=0
else
    log "0t-fast.py exited non-zero; escalating to claude -p /0t"
    claude -p "$(cat "$HOME/bin/0t-prompt.txt")" \
      --dangerously-skip-permissions --max-budget-usd 1 >> "$LOG" 2>&1
    code=$?
    log "claude exit $code"
    [ $code -ne 0 ] && fail "0t_failed" "0t-fast.py failed and claude -p /0t exited $code"
fi
find "$LOGDIR" -name "*.log" -mtime +30 -delete 2>/dev/null
log "=== run_0t exit $code ==="
exit $code
