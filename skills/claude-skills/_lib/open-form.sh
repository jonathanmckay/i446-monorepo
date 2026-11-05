#!/usr/bin/env bash
# open-form.sh — surface a full-screen terminal form/TUI where JM can see it.
#
#   open-form.sh <window-name> <command-string>
#
# Tiers, first that works wins:
#   1. cmux  — `cmux` on PATH (Straylight): new surface, respawn, focus.
#   2. tmux  — Claude itself runs inside tmux ($TMUX set, e.g. the cc-HHMMSS
#              sessions on Ix) AND a client is attached to that session:
#              new window in Claude's own session, so it pops on JM's screen.
#   3. none  — exit 10, print NO_SURFACE. The calling skill must then ask the
#              questions in chat and write via the tool's --from-json /
#              --from-text path. Never tell JM to run the command himself.
#
# stdout on success: `OPENED cmux surface:N` or `OPENED tmux <session>:<window>`.
#
# Env:
#   OPEN_FORM_MODE=cmux|tmux|chat   force one tier (no fall-through, except
#                                   that a failing forced tier still exits 10)
#   OPEN_FORM_DRY_RUN=1             print the commands instead of running them
#
# Exit: 0 opened · 10 no visible surface (use chat fallback) · 2 usage.

set -u

name="${1:-}"
cmd="${2:-}"
if [[ -z "$name" || -z "$cmd" ]]; then
  echo "usage: open-form.sh <window-name> <command-string>" >&2
  exit 2
fi

mode="${OPEN_FORM_MODE:-auto}"
dry="${OPEN_FORM_DRY_RUN:-}"

run() {  # run (stdout discarded) or, in dry-run, echo the argv
  if [[ -n "$dry" ]]; then
    printf 'DRY:'; printf ' %q' "$@"; printf '\n'
  else
    "$@" >/dev/null
  fi
}

try_cmux() {
  command -v cmux >/dev/null 2>&1 || return 1
  local out surf pane
  if [[ -n "$dry" ]]; then
    run cmux new-surface --type terminal
    out="OK surface:N pane:N"
  else
    out="$(cmux new-surface --type terminal 2>/dev/null)" || return 1
  fi
  surf="$(grep -oE 'surface:[0-9N]+' <<<"$out" | head -1)"
  pane="$(grep -oE 'pane:[0-9N]+' <<<"$out" | head -1)"
  [[ -n "$surf" ]] || return 1
  run cmux respawn-pane --surface "$surf" --command "$cmd" || return 1
  [[ -n "$pane" ]] && run cmux focus-pane --pane "$pane"
  echo "OPENED cmux $surf"
}

try_tmux() {
  [[ -n "${TMUX:-}" ]] || return 1
  command -v tmux >/dev/null 2>&1 || return 1
  local session clients wrapped
  if [[ -n "${TMUX_PANE:-}" ]]; then
    session="$(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null)"
  else
    session="$(tmux display-message -p '#S' 2>/dev/null)"
  fi
  [[ -n "$session" ]] || return 1
  # A window in a session nobody is watching is not "surfaced".
  clients="$(tmux list-clients -t "$session" 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${clients:-0}" -gt 0 ]] || return 1
  # sh -c so a compound command is guarded as a whole; strip cmux vars so a
  # form's own `cmux close-surface` can't close an unrelated cmux tab; keep the
  # window open on failure so the error / recovery path stays visible.
  printf -v wrapped 'env -u CMUX_SURFACE_ID -u CMUX_PANEL_ID -u CMUX_SOCKET_PATH -u CMUX_WORKSPACE_ID sh -c %q || { echo; echo "form exited with an error; Enter closes this window"; read -r _; }' "$cmd"
  run tmux new-window -t "$session:" -n "$name" "$wrapped" || return 1
  echo "OPENED tmux $session:$name"
}

no_surface() {
  echo "NO_SURFACE: no cmux and no attached tmux session; collect the answers in chat and use the tool's non-interactive flag"
  exit 10
}

case "$mode" in
  cmux) try_cmux && exit 0; no_surface ;;
  tmux) try_tmux && exit 0; no_surface ;;
  chat) no_surface ;;
  auto) try_cmux && exit 0; try_tmux && exit 0; no_surface ;;
  *) echo "open-form.sh: bad OPEN_FORM_MODE '$mode'" >&2; exit 2 ;;
esac
