#!/bin/zsh
# open-tui.sh [--no-hold] <name> <command> — open a survey form in its own
# terminal, from wherever this Claude session runs, and return at once.
# 2026-11-05: every survey skill spoke cmux only, so on Ix (a cc-* tmux session
# with no cmux) they gave up and asked JM to run the form by hand.
#   cmux            new tab
#   tmux            new window in this session; closes when the form exits
#   local macOS     new Terminal.app tab
#   anything else   exit 3 and print the line for JM to run
# A form that fails in a tmux window holds it open so the error can be read;
# --no-hold is for forms that already do that. Not for the inbox pollers (ibx,
# -2n, inbound), which never exit, or for launching Claude: cc is inline in
# tmux, and a second Claude in one cc-* session confuses cs.
hold=1
[[ "$1" == --no-hold ]] && { hold=0; shift; }
name="$1"; cmd="$2"
[[ -n "$name" && -n "$cmd" ]] || { print -u2 "usage: open-tui.sh [--no-hold] <name> <command>"; exit 2; }

if command -v cmux >/dev/null 2>&1; then
  out="$(cmux new-surface --type terminal 2>&1)"
  if [[ "$out" =~ 'surface:([0-9]+)' ]]; then
    surface="${match[1]}"
    pane=""
    [[ "$out" =~ 'pane:([0-9]+)' ]] && pane="${match[1]}"
    cmux respawn-pane --surface "surface:$surface" --command "$cmd" >/dev/null 2>&1
    [[ -n "$pane" ]] && cmux focus-pane --pane "pane:$pane" >/dev/null 2>&1
    print -r -- "$name opened in a new cmux tab"
    exit 0
  fi
fi

if [[ -n "$TMUX" ]] && command -v tmux >/dev/null 2>&1; then
  # Argv form, so $cmd crosses no extra shell. -i as well as -l: aliases and
  # ~/bin live in .zshrc, and a tmux server's own environment is bare.
  tmux new-window -n "$name" zsh -lic \
    'eval "$1"; rc=$?; if (( rc && $2 )); then print -r -- "exited $rc, Enter closes"; read -r _; fi; exit $rc' \
    _ "$cmd" "$hold" && { print -r -- "$name opened in a new tmux window; it closes when you finish"; exit 0; }
fi

if [[ "$OSTYPE" == darwin* && -z "$SSH_CONNECTION" ]] && [[ -z "$TMUX" ]]; then
  osascript -e 'on run argv' -e 'tell application "Terminal" to do script (item 1 of argv)' \
            -e 'tell application "Terminal" to activate' -e 'end run' -- "$cmd" >/dev/null 2>&1 \
    && { print -r -- "$name opened in a new Terminal.app tab"; exit 0; }
fi

print -r -- "couldn't open a terminal from here; run: ! $cmd"
exit 3
