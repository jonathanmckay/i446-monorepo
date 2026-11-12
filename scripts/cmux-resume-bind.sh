#!/bin/bash
# cmux-resume-bind.sh set <checkpoint> <shell command>
# cmux-resume-bind.sh clear <checkpoint>
#
# Record (or drop) the cmux resume binding of the tab this process runs in.
# cmux restores window/tab layout, cwd and scrollback after a Straylight
# restart but not processes; a resume binding is the command it re-runs in
# that tab instead, once JM has approved the command prefix once in
# Settings > Terminal > Resume Commands (2026-11-12, "CMUX on restart").
#
# Callers: scripts/winsize-guard.py (the pane's local process when dtd/janus
# run on ix over mosh), tools/did/dtd.sh and tools/tg/janus.py (when they run
# locally), ~/vault/i447/i446/claude-tracked (cc tabs, `cc --reattach <tmux>`).
#
# Silent no-op (exit 0) outside a cmux surface (ix, tmux on ix, the phone) or
# without the cmux CLI. Never fails or blocks the caller: the cmux call is
# capped at 3 s and errors go to ~/.local/state/jm/cmux-resume-bind.log.
set -u
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/jm/cmux-resume-bind.log"
log() { mkdir -p "${LOG%/*}" 2>/dev/null; printf '%s [%s] %s\n' "$(date '+%F %T')" "$$" "$*" >> "$LOG" 2>/dev/null; }

[ -n "${CMUX_SURFACE_ID:-}" ] || exit 0
CMUX=$(command -v cmux 2>/dev/null || true)
if [ -z "$CMUX" ] && [ -x /Applications/cmux.app/Contents/Resources/bin/cmux ]; then
  CMUX=/Applications/cmux.app/Contents/Resources/bin/cmux
fi
[ -n "$CMUX" ] || exit 0
export CMUX_QUIET=1

# run <args...>: the cmux call with a 3 s watchdog (macOS has no `timeout`).
run() {
  local out rc pid wd
  out=$(
    "$CMUX" "$@" 2>&1 &
    pid=$!
    ( sleep 3; kill "$pid" 2>/dev/null ) &
    wd=$!
    wait "$pid"; rc=$?
    kill "$wd" 2>/dev/null
    exit "$rc"
  ); rc=$?
  [ "$rc" -eq 0 ] || log "$* -> rc $rc: $out"
  return "$rc"
}

act=${1:-}; ck=${2:-}
case "$act" in
  set)
    [ -n "$ck" ] && [ -n "${3:-}" ] || { log "set: need <checkpoint> <shell>"; exit 0; }
    # --kind tmux is the only kind cmux documents. The checkpoint is our own
    # label (dtd, janus, or the cc tmux session name); --shell is what cmux
    # re-runs. cs.py ignores bindings whose kind is not 'claude', so a cc
    # tab's tmux name is never mistaken for a Claude session id.
    run surface resume set --kind tmux --checkpoint "$ck" --shell "$3" >/dev/null
    ;;
  clear)
    [ -n "$ck" ] || { log "clear: need <checkpoint>"; exit 0; }
    run surface resume clear --checkpoint "$ck" >/dev/null
    ;;
  *)
    log "unknown action '$act'"
    ;;
esac
exit 0
