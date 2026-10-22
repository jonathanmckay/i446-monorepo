#!/bin/zsh
# tmux session picker for SSH logins.
# Used by the auto-attach block in ~/.zshrc.

CC_CMD="$HOME/vault/i447/i446/claude-tracked"

# Show at most 4 sessions, matching cc-pick's MAX_ROWS=4 ("4 max"): a login
# picker should fit on one glance, not page through every stale pane. Capping
# the array (not just the printout) keeps hidden sessions unselectable.
MAX=4

sessions=("${(@f)$(tmux ls -F '#{session_name}' 2>/dev/null)}")
sessions=(${sessions:#})
(( ${#sessions[@]} > MAX )) && sessions=("${sessions[@]:0:$MAX}")

if (( ${#sessions[@]} == 0 )); then
  exec tmux new -A -s cc "$CC_CMD"
fi

print
print "cc sessions on $(hostname -s):"
for i in {1..${#sessions[@]}}; do
  name="${sessions[$i]}"
  print "  $i) ${name[1,20]}"   # truncate long names so each row stays terse
done
print "  n) new"
print "  s) sh"
print

printf "Pick [1]: "
read -k1 choice
print

case "$choice" in
  ""|$'\n'|$'\r')
    exec tmux attach -t "${sessions[1]}"
    ;;
  [1-9])
    if (( choice <= ${#sessions[@]} )); then
      exec tmux attach -t "${sessions[$choice]}"
    else
      print "no session $choice"
      sleep 1
      exec "$0"
    fi
    ;;
  n|N)
    name="cc"
    i=2
    while (( ${sessions[(I)$name]} > 0 )); do
      name="cc$i"
      ((i++))
    done
    exec tmux new -s "$name" "$CC_CMD"
    ;;
  s|S)
    ;;
  *)
    exec tmux attach -t "${sessions[1]}"
    ;;
esac
