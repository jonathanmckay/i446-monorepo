#!/bin/zsh
# Claude Code SessionEnd hook: hand the cmux tab back to the shell. The tab keeps the
# conversation title otherwise (Claude Code only ever sets it). Renumbers the surviving Claude
# panes first, excluding this session, which is still in ps while the hook runs. Never fails.
set +e
input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
if [ -n "$CMUX_PANEL_ID" ] && command -v cmux >/dev/null 2>&1; then
  CS_EXCLUDE_SID="$sid" ~/.local/bin/cs retitle >/dev/null 2>&1
  cmux rename-tab --surface "$CMUX_PANEL_ID" -- "Command Line" >/dev/null 2>&1
else
  printf '\033]0;Command Line\007' > /dev/tty 2>/dev/null
fi
exit 0
