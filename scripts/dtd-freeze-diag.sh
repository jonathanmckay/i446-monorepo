#!/bin/zsh
# dtd-freeze-diag.sh: snapshot a stuck dtd BEFORE relaunching it (a relaunch
# destroys the evidence). Read-only. Run it in any shell on the machine whose
# dtd is frozen, not in the dtd tab. Writes ~/files/i447/dtd-freeze-diag.txt,
# which Syncthing carries to Ix (Straylight takes no inbound ssh, so an Ix
# session cannot look for itself). Set 2026-11-18.
#
# What it answers:
#   - local dtd: is fzf alive, does its --listen port answer, and which child
#     of fzf has been running a long time (fzf blocks the whole UI while an
#     execute()/transform() binding's script runs, so that child IS the freeze)
#   - ix-mode dtd: is this pane a mosh-client whose server is gone
out="${1:-$HOME/files/i447/dtd-freeze-diag.txt}"
{
  print -- "== $(hostname) $(date '+%F %T')"
  found=""
  for f in $(pgrep -x fzf); do
    args="$(ps -o command= -p "$f")"
    [[ "$args" == *"/tmp/dtd-"* ]] || continue
    found=1
    id="${${args#*/tmp/dtd-}%%.*}"
    tty="$(ps -o tty= -p "$f" | tr -d ' ')"
    print -- "\n== local dtd: fzf pid $f, session $id, tty $tty"
    print -- "-- every process on that tty (anything old under fzf is the hung binding)"
    ps -o pid,ppid,stat,etime,time,command -t "$tty" | cut -c1-220
    print -- "-- fzf --listen probe"
    port="$(cat "/tmp/dtd-$id.port" 2>/dev/null)"
    curl -s -m 3 -o /dev/null -w "port $port: http %{http_code} in %{time_total}s\n" "localhost:$port?limit=1" \
      || print -- "port $port: no answer within 3s"
    print -- "-- what fzf is blocked in"
    sample "$f" 1 2>/dev/null | sed -n '/Call graph/,/Total number/p' | head -40
    print -- "-- runtime files, newest first"
    ls -lt /tmp/dtd-$id.* 2>/dev/null | head -15
    print -- "-- timing log tail"
    tail -n 12 "/tmp/dtd-$id.timing.log" 2>/dev/null
  done
  [[ -z "$found" ]] && print -- "\n== no local dtd fzf on this machine"
  print -- "\n== ix-mode panes (mosh-client / winsize-guard)"
  ps -axo pid,ppid,stat,etime,command | grep -E 'mosh-client|winsize-guard|ssh .*ix' | grep -v grep | cut -c1-200
  print -- "\n== ix-tui.log tail"
  tail -n 15 "${XDG_STATE_HOME:-$HOME/.local/state}/jm/ix-tui.log" 2>/dev/null
} > "$out" 2>&1
print -- "wrote $out"
