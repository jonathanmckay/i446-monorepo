#!/bin/zsh
# dtd — fuzzy task picker that runs /did directly (no Claude needed)
# UI-first: fzf stays responsive, background worker processes tasks serially,
# fzf header shows latest completion status.

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
CACHE="$HOME/vault/z_ibx/task-queue.json"
DONE="$HOME/vault/z_ibx/completed-today.json"
DTD_FIFO="/tmp/dtd-$$.fifo"
DTD_HDR="/tmp/dtd-$$.hdr"
DTD_LOG="/tmp/dtd-$$.log"

if [[ ! -f "$CACHE" ]]; then
  echo "No task cache found at $CACHE" >&2
  return 1 2>/dev/null || exit 1
fi

if [[ $(jq '.today | length // 0' "$CACHE") -lt 5 ]]; then
  echo "Refreshing task cache..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
fi

typeset -a session_done
setopt NO_MONITOR 2>/dev/null
LOCAL_TODAY=$(date +%Y-%m-%d)

# --- Background worker: reads task names from FIFO, processes serially ---
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG"
mkfifo "$DTD_FIFO"
echo "ready" > "$DTD_HDR"

(
  while IFS= read -r task_clean; do
    [[ -z "$task_clean" ]] && continue
    echo "⏳ $task_clean" > "$DTD_HDR"

    result=$(python3 "$DID_FAST" "$task_clean" 2>&1)
    ok=$(echo "$result" | jq -r '.results[]? | "\(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null)
    agent=$(echo "$result" | jq -r '.agent_needed[]?.name' 2>/dev/null)

    if [[ -n "$ok" ]]; then
      echo "✓ $ok" > "$DTD_HDR"
      echo "✓ $ok" >> "$DTD_LOG"
    else
      echo "? $task_clean (no result)" > "$DTD_HDR"
      echo "? $task_clean (no result)" >> "$DTD_LOG"
    fi

    if [[ -n "$agent" ]]; then
      echo "⚠ agent: $agent" >> "$DTD_LOG"
    fi
  done < "$DTD_FIFO"

  # No cache refresh here — it races with the API and can nuke the "today" section.
  # The cache was populated before dtd started; it's good enough for this session.
  echo "done" > "$DTD_HDR"
) &
WORKER_PID=$!

# Keep FIFO write end open for entire session (prevents EOF on each write)
exec 3>"$DTD_FIFO"

# --- UI loop ---
while true; do
  # Read latest header
  hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")

  session_exclude=$(printf '%s\n' "${session_done[@]}" | jq -R -s 'split("\n") | map(select(. != ""))')

  task=$(jq -r --slurpfile done "$DONE" --argjson session "$session_exclude" --arg today "$LOCAL_TODAY" '
    (
      (if ($done[0].date == $today) then ($done[0].names | map(ascii_downcase)) else [] end)
      + ($session | map(ascii_downcase))
    ) as $completed |
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
  ' "$CACHE" | fzf --height 40 --prompt="did> " --layout=reverse --header="  $hdr")

  if [[ -z "$task" ]]; then
    break
  fi

  # Strip annotations: (N), [N], {N}
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')
  session_done+=("$clean")

  # Send to worker via FIFO (non-blocking, goes into pipe buffer)
  echo "$clean" >&3
done

# Close FIFO write end → worker sees EOF → finishes remaining tasks → exits
exec 3>&-

echo ""
echo "Waiting for background tasks to finish..."
wait $WORKER_PID 2>/dev/null

# Show full log
if [[ -s "$DTD_LOG" ]]; then
  echo ""
  cat "$DTD_LOG"
fi

# Cleanup
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG"
