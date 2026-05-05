#!/bin/zsh
# dtd — fuzzy task picker that runs /did directly (no Claude needed)
# Reads from task-queue.json cache, filters to today/overdue + not completed, launches fzf.
# Calls did-fast.py directly. Zero API credits.

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
CACHE="$HOME/vault/z_ibx/task-queue.json"
DONE="$HOME/vault/z_ibx/completed-today.json"

if [[ ! -f "$CACHE" ]]; then
  echo "No task cache found at $CACHE" >&2
  return 1 2>/dev/null || exit 1
fi

# If the cache has no "today" key (stale session refreshed it), rebuild it
if [[ $(jq '.today | length // 0' "$CACHE") -lt 5 ]]; then
  echo "Refreshing task cache..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
fi

task=$(jq -r --slurpfile done "$DONE" '
  (now | strftime("%Y-%m-%d")) as $today |
  (if ($done[0].date == $today) then ($done[0].names | map(ascii_downcase)) else [] end) as $completed |
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(.due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(.due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | .[]
  | .content as $raw |
  ($raw | gsub(" *\\([0-9]*\\)"; "") | gsub(" *\\[[0-9]*\\]"; "") | gsub(" *\\{[0-9]*\\}"; "") | gsub(" +$"; "") | gsub("  +"; " ") | ascii_downcase) as $clean |
  ($clean | split(" - ") | .[0]) as $prefix |
  select(
    ($completed | index($clean) | not) and
    ($completed | index($prefix) | not)
  )
  | $raw
' "$CACHE" | fzf --height 40 --prompt="did> " --layout=reverse)

if [[ -z "$task" ]]; then
  return 0 2>/dev/null || exit 0
fi

# Strip (N) [N] {N} annotations
clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

echo "→ /did $clean"
result=$(python3 "$DID_FAST" "$clean" 2>&1)

# Parse result
ok=$(echo "$result" | jq -r '.results[]? | "\(.name) → \(.value) [\(.step)] \(if .todoist.closed then "+ todoist ✓" else "" end)"' 2>/dev/null)
agent=$(echo "$result" | jq -r '.agent_needed[]?.name' 2>/dev/null)

if [[ -n "$ok" ]]; then
  echo "$ok"
fi

if [[ -n "$agent" ]]; then
  echo "⚠ Needs Claude for: $agent"
  claude -p "/did $agent" 2>&1
fi

# Refresh cache in background
python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1 &
