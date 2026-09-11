#!/bin/bash
# Nightly /0r time-record archive — runs on Ix (not Straylight).
# Merges yesterday Toggl + m5c7 Google calendars (incl. MSFT slow-sync) into
# the vault daily record + the Google Calendar archive (the jbm past-time view).
export PATH="/opt/homebrew/bin:/usr/bin:/bin"
LOG="$HOME/.cache/0r/$(date +%Y-%m-%d).log"
echo "=== 0r archive start $(date) ===" >> "$LOG"
cd "$HOME" || exit 1
claude -p "$(cat "$HOME/bin/0r-prompt.txt")" \
  --dangerously-skip-permissions --max-budget-usd 3 >> "$LOG" 2>&1
code=$?
echo "=== 0r archive exit $code at $(date) ===" >> "$LOG"
# prune logs older than 30 days
find "$HOME/.cache/0r" -name "*.log" -mtime +30 -delete 2>/dev/null
exit $code
