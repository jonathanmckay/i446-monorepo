#!/bin/zsh
# Open a new Claude Code tab on Straylight with the prompt read from stdin.
# Called by the personal dashboard on Ix over `ssh straylight-refit` (the
# jmreads "Write review" button). The prompt arrives on stdin, never on the
# command line, so book titles need no shell quoting.
#
# cmux first. Its default socket mode ("cmuxOnly") refuses processes that
# were not started inside a cmux terminal, which includes this ssh session;
# setting automation.socketControlMode to "automation" in
# ~/.config/cmux/cmux.json lets it through. Until then, fall back to a
# Terminal.app window via AppleScript.
set -u
CC="$HOME/vault/i447/i446/claude-tracked"
CMUX=/Applications/cmux.app/Contents/Resources/bin/cmux
export CMUX_SOCKET_PATH="${CMUX_SOCKET_PATH:-$HOME/.local/state/cmux/cmux-501.sock}"
dir="$HOME/.cache/claude-tabs"; mkdir -p "$dir"
id="$(date +%s)-$$"
prompt_file="$dir/$id.prompt"
cat > "$prompt_file"
[[ -s "$prompt_file" ]] || { echo "ERROR: empty prompt" >&2; exit 2; }
launch="cd \$HOME && \"$CC\" \"\$(cat '$prompt_file')\"; rm -f '$prompt_file'"

if out=$("$CMUX" new-surface --type terminal 2>&1); then
  surface=$(print -r -- "$out" | grep -oE 'surface:[0-9]+' | head -1)
  pane=$(print -r -- "$out" | grep -oE 'pane:[0-9]+' | head -1)
  if [[ -n "$surface" ]] && "$CMUX" respawn-pane --surface "$surface" --command "sleep 0.5 && $launch" >/dev/null 2>&1; then
    [[ -n "$pane" ]] && "$CMUX" focus-pane --pane "$pane" >/dev/null 2>&1
    open -a cmux >/dev/null 2>&1
    echo "cmux"; exit 0
  fi
fi

# Terminal fallback. A command typed into a brand-new Terminal window is
# swallowed while the login shell starts (seen 2026-10-05, also with .command
# files), so open the window first and send the command once it is ready.
osascript - "$launch" <<'OSA' >/dev/null && { echo "terminal"; exit 0; }
on run argv
  tell application "Terminal"
    set t to do script ""
    delay 2.5
    do script (item 1 of argv) in t
    activate
  end tell
end run
OSA
echo "ERROR: could not open cmux or Terminal" >&2; exit 1
