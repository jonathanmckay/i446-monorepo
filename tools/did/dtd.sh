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
DONE_NAMES=$(jq -c --arg today "$LOCAL_TODAY" \
  'if .date == $today then [.names[] | ascii_downcase] else [] end' "$DONE" 2>/dev/null || echo '[]')

# --- Background worker ---
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG"
mkfifo "$DTD_FIFO"
echo "ready" > "$DTD_HDR"

(
  while IFS= read -r task_clean; do
    [[ -z "$task_clean" ]] && continue
    echo "⏳ $task_clean" > "$DTD_HDR"
    result=$(python3 "$DID_FAST" "$task_clean" 2>&1)
    ok=$(echo "$result" | jq -r '.results[]? | "\(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null)
    if [[ -n "$ok" ]]; then
      echo "✓ $ok" > "$DTD_HDR"
      echo "✓ $ok" >> "$DTD_LOG"
    else
      echo "? $task_clean" > "$DTD_HDR"
      echo "? $task_clean" >> "$DTD_LOG"
    fi
  done < "$DTD_FIFO"
  echo "done" > "$DTD_HDR"
) &
WORKER_PID=$!

exec 3>"$DTD_FIFO"

# --- UI loop ---
while true; do
  hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")

  session_exclude=$(printf '%s\n' "${session_done[@]}" | jq -c -R -s 'split("\n") | map(select(. != ""))')
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  task=$(jq -r --argjson completed "$all_completed" --arg today "$LOCAL_TODAY" '
    (
      [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
      | flatten
      | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
    ) + [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today)]
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

  # Strip annotations
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

  # Let user edit/append args
  REPLY="$clean"
  vared -p "→ " REPLY
  clean="$REPLY"

  session_done+=("$clean")
  echo "$clean" >&3
done

exec 3>&-

if [[ ${#session_done[@]} -gt 0 ]]; then
  echo ""
  echo "Waiting for ${#session_done[@]} tasks..."
  while kill -0 $WORKER_PID 2>/dev/null; do
    sleep 1
    printf "."
  done
  echo ""

  if [[ -s "$DTD_LOG" ]]; then
    cat "$DTD_LOG"
  fi

  logged=$(wc -l < "$DTD_LOG" 2>/dev/null || echo 0)
  logged=${logged// /}
  if [[ $logged -lt ${#session_done[@]} ]]; then
    echo "⚠ $logged/${#session_done[@]} processed. Running remaining..."
    for clean in "${session_done[@]}"; do
      if ! grep -qi "$(echo "$clean" | head -c 20)" "$DTD_LOG" 2>/dev/null; then
        echo "  → /did $clean"
        python3 "$DID_FAST" "$clean" 2>&1 | jq -r '.results[]? | "  ✓ \(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null
      fi
    done
  fi
fi

rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG"
