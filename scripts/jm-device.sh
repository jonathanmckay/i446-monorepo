#!/bin/zsh
# jm-device.sh — SessionStart hook: say which device JM is sitting at.
# 2026-11-15 (JM: "you often get confused as to which device I'm using"). A cc
# session almost always runs on Ix inside a cc-* tmux session, while JM reaches
# it from Straylight (cmux over mosh) or the phone. hostname and
# ~/.claude/.host-name name the session's host only, so "this tab", "dtd" and
# "my screen" were read as Ix's. SessionStart stdout lands in the model's
# context: one line naming both machines.
#
# The device is the origin of the connection the session is attached through:
# the tmux session's SSH_CONNECTION (tmux refreshes it from each client that
# attaches, so it follows a reattach from another device), else this process's
# own. The address is named through `tailscale status`. No connection at all
# means JM is at the machine the session runs on.
# Can be rerun by hand mid-session: the attached device changes when JM picks
# the thread up from his phone. Never fails: a hook error must not disturb the
# session.
set +e
host="$(cat "$HOME/.claude/.host-name" 2>/dev/null)"
host="${host//[[:space:]]/}"
[[ -n "$host" ]] || host="$(hostname -s 2>/dev/null)"

conn=""
if [[ -n "$TMUX" ]] && command -v tmux >/dev/null 2>&1; then
  conn="$(tmux show-environment ${TMUX_PANE:+-t "$TMUX_PANE"} SSH_CONNECTION 2>/dev/null)"
  [[ "$conn" == SSH_CONNECTION=* ]] && conn="${conn#SSH_CONNECTION=}" || conn=""
fi
[[ -n "$conn" ]] || conn="${SSH_CONNECTION:-}"
ip="${conn%% *}"

if [[ -z "$ip" ]]; then
  print -r -- "[device] JM is at this machine ($host); the session runs here too."
  exit 0
fi

ts="$(command -v tailscale 2>/dev/null)"
[[ -n "$ts" ]] || ts="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
name="" os=""
if [[ -x "$ts" ]]; then
  # "100.71.83.90  straylight-refit  mckay@  macOS  -": address, name, owner, OS.
  line="$("$ts" status 2>/dev/null | awk -v ip="$ip" '$1 == ip {print $2, $4; exit}')"
  name="${line%% *}"
  [[ "$line" == *" "* ]] && os="${line#* }"
fi
dev="${name:-$ip}"
[[ -n "$os" ]] && dev="$dev ($os)"

if [[ "$name" == "$host" ]]; then
  print -r -- "[device] JM is at this machine ($host); the session runs here too."
  exit 0
fi
print -r -- "[device] JM is at $dev, attached to this session remotely; the session itself runs on $host. \"This tab\", dtd, janus, the browser and his screen mean $dev, not $host: its local files and processes cannot be read from here, so say so instead of answering from $host's copy. The device can change mid-session; rerun ~/i446-monorepo/scripts/jm-device.sh to recheck."
exit 0
