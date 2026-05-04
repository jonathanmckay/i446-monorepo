#!/bin/zsh
# dtd — fuzzy task picker for /did
# Reads from task-queue.json cache, launches fzf, prefills the terminal input buffer.
# Zero API credits. Works from any terminal window.

CACHE="$HOME/vault/z_ibx/task-queue.json"

if [[ ! -f "$CACHE" ]]; then
  echo "No task cache found at $CACHE" >&2
  return 1 2>/dev/null || exit 1
fi

# Extract task content from all categories, strip (N) [N] annotations for cleaner display
# but keep the raw content for the /did command
task=$(jq -r '
  [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
  | flatten
  | .[].content
' "$CACHE" | fzf --height 20 --prompt="did> " --layout=reverse)

if [[ -z "$task" ]]; then
  return 0 2>/dev/null || exit 0
fi

# Strip (N) and [N] annotations — /did parses these from Todoist, not from input
clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

# Prefill the terminal input buffer with /did <task>
print -z "/did $clean"
