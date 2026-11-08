#!/bin/sh
# claude-ix-keepwarm.sh — keep headless Claude on Ix logged in (Max subscription).
#
# Run hourly by ~/Library/LaunchAgents/com.jm.claude-ix-keepwarm.plist
# (SessionCreate, so the login Keychain is readable). The Keychain item is
# canonical on Ix; cron/launchd/ssh runs can't read it, so they fall back to
# ~/.claude/.credentials.json. A keychain-context claude run refreshes the
# token and DELETES that file, which is how d357-auto-file logged
# "Not logged in" 1051 times. Each run:
#   1. refreshes the token (a trivial claude call) if it expires within 90 min
#   2. mirrors Keychain -> ~/.claude/.credentials.json (0600)
#
# 2026-05-28 original was never committed and was lost; rebuilt 2026-11-08.
# Never use `claude setup-token` here: it issues API-billed tokens.

LOG="$HOME/.claude/ix-keepwarm.log"
CREDS="$HOME/.claude/.credentials.json"
CLAUDE=/opt/homebrew/bin/claude
ts() { date '+%Y-%m-%d %H:%M:%S'; }

blob() { security find-generic-password -s "Claude Code-credentials" -a mckay -w 2>/dev/null; }

mins_left() {
  blob | /usr/bin/python3 -c '
import json, sys, time
try:
    d = json.load(sys.stdin)["claudeAiOauth"]
    print(int((d["expiresAt"] / 1000 - time.time()) / 60))
except Exception:
    print(-1)'
}

left=$(mins_left)
if [ "$left" -lt 0 ]; then
  echo "$(ts)  keychain locked/empty — file left as-is (need GUI login on ix)" >> "$LOG"
  exit 0
fi

state=fresh
if [ "$left" -lt 90 ]; then
  "$CLAUDE" --print --model haiku "reply ok" >/dev/null 2>&1 </dev/null
  state="refreshed (had ${left}m)"
fi

umask 077
tmp="$CREDS.tmp.$$"
if blob > "$tmp" && [ -s "$tmp" ]; then
  chmod 600 "$tmp" && mv "$tmp" "$CREDS"
  echo "$(ts)  $state | mirrored->file" >> "$LOG"
else
  rm -f "$tmp"
  echo "$(ts)  $state | mirror FAILED" >> "$LOG"
fi
