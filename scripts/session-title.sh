#!/bin/zsh
# Claude Code Stop/UserPromptSubmit hook: push the session's title onto the cmux tab.
# Claude Code already writes {"type":"ai-title"} (auto) and {"type":"custom-title"}
# (/rename) lines into the session jsonl; this just surfaces the latest one.
# Stdin: hook JSON with transcript_path + session_id. Never fails the hook.
set +e
input=$(cat)
tp=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)
[ -n "$tp" ] && [ -f "$tp" ] || exit 0
title=$(python3 - "$tp" <<'PY' 2>/dev/null
import sys, json
custom = ai = None
with open(sys.argv[1], 'rb') as f:
    for line in f:
        if b'"custom-title"' not in line and b'"ai-title"' not in line:
            continue
        try: o = json.loads(line)
        except Exception: continue
        t = o.get('type')
        if t == 'custom-title' and o.get('customTitle'): custom = o['customTitle']
        elif t == 'ai-title' and o.get('aiTitle'): ai = o['aiTitle']
print((custom or ai or '')[:60])
PY
)
[ -n "$title" ] || exit 0
state=~/.local/state/jm/session-titles; mkdir -p "$state"
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -n "$sid" ] && printf '%s\n' "$title" > "$state/$sid"
if [ -n "$CMUX_PANEL_ID" ] && command -v cmux >/dev/null 2>&1; then
  # `cs retitle` renames every numbered Claude pane in this workspace to "N · title" (reading
  # order), so each session's hook also refreshes its neighbours' numbers. If it did not cover
  # this surface (cs missing, pane not numbered), fall back to the plain title.
  done_=$(~/.local/bin/cs retitle 2>/dev/null | grep -F "$CMUX_PANEL_ID")
  [ -n "$done_" ] || cmux rename-tab --surface "$CMUX_PANEL_ID" -- "$title" >/dev/null 2>&1
else
  printf '\033]0;%s\007' "$title" > /dev/tty 2>/dev/null
fi
exit 0
