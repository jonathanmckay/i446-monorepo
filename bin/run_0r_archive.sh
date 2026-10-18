#!/bin/bash
# Nightly /0r time-record archive — runs on Ix (not Straylight).
# Merges yesterday Toggl + m5c7 Google calendars (incl. MSFT slow-sync) into
# the vault daily record + the Google Calendar archive (the jbm past-time view).
export PATH="/opt/homebrew/bin:/usr/bin:/bin"
# Headless auth (2026-10-17): cron cannot read the Claude Code keychain entry
# (every nightly run since 2026-09-27 died with "Not logged in"). A long-lived
# token from `claude setup-token` lives in ~/.claude/oauth-token (mode 600).
TOKEN_FILE="$HOME/.claude/oauth-token"
[ -s "$TOKEN_FILE" ] && export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")"
LOG="$HOME/.cache/0r/$(date +%Y-%m-%d).log"
echo "=== 0r archive start $(date) ===" >> "$LOG"
cd "$HOME" || exit 1
claude -p "$(cat "$HOME/bin/0r-prompt.txt")" \
  --dangerously-skip-permissions --max-budget-usd 3 >> "$LOG" 2>&1
code=$?
echo "=== 0r archive exit $code at $(date) ===" >> "$LOG"
[ $code -ne 0 ] && "$HOME/bin/cron-alert.sh" run_0r_archive "0r_failed" "claude -p /0r exited $code (see $LOG)"
# prune logs older than 30 days
find "$HOME/.cache/0r" -name "*.log" -mtime +30 -delete 2>/dev/null
exit $code
