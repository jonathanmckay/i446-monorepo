#!/bin/zsh
# dtd — fuzzy task picker that runs /did directly (no Claude needed)
# UI-first: fzf stays responsive, background worker processes tasks serially,
# fzf header shows latest completion status.
# KEY: cache is snapshotted ONCE at startup. No mid-session re-reads.

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
TG_FAST="$HOME/i446-monorepo/tools/tg/tg-fast.py"
TOGGL_CLI="$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
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

# ── SNAPSHOT the cache into a variable. Never read the file again. ──
CACHE_SNAPSHOT=$(cat "$CACHE")

# Invariant check on the snapshot
due_today=$(echo "$CACHE_SNAPSHOT" | jq --arg today "$LOCAL_TODAY" '
  (
    [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
    | flatten
    | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
  ) + [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today)]
  | map(select(.content != null))
  | group_by(.id) | map(.[0])
  | length
')
if [[ $due_today -lt 30 ]]; then
  echo "⚠ Only $due_today tasks due today (expected ~80+). Refreshing..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  CACHE_SNAPSHOT=$(cat "$CACHE")
  due_today=$(echo "$CACHE_SNAPSHOT" | jq --arg today "$LOCAL_TODAY" '
    (
      [.["0neon"], .["1neon"], .["夜neon"], .["关键路径"]]
      | flatten
      | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
    ) + [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today)]
    | map(select(.content != null))
    | group_by(.id) | map(.[0])
    | length
  ')
  echo "  After refresh: $due_today tasks"
fi

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

# --- Helper: fetch current Toggl timer as 1-line string ---
_toggl_header() {
  local cur
  cur=$(python3 "$TOGGL_CLI" current 2>/dev/null)
  if [[ "$cur" == Running:* ]]; then
    # Parse "Running: HH:MM-running <desc> @<project> (running) [id:NNN]"
    local body="${cur#Running: }"
    # Strip time prefix "HH:MM-running "
    body=$(echo "$body" | sed -E 's/^[0-9]{2}:[0-9]{2}-running //')
    # Strip trailing [id:...] and (running)
    body=$(echo "$body" | sed -E 's/ *\(running\)//; s/ *\[id:[0-9]*\]//')
    echo "▶ $body"
  else
    echo "▶ (idle)"
  fi
}

# --- UI loop (reads from CACHE_SNAPSHOT variable, never the file) ---
while true; do
  worker_hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")
  timer_hdr=$(_toggl_header)
  combined_hdr="$timer_hdr
  $worker_hdr"

  session_exclude=$(printf '%s\n' "${session_done[@]}" | jq -c -R -s 'split("\n") | map(select(. != ""))')
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  # Priority-ordered jq: 0neon → 1neon → 関键路径 → today (sorted by priority)
  fzf_output=$(echo "$CACHE_SNAPSHOT" | jq -r --argjson completed "$all_completed" --arg today "$LOCAL_TODAY" '
    # Todoist API: priority 4=urgent(p1), 3=high(p2), 2=medium(p3), 1=normal(p4)
    # Negate so sort_by puts highest priority first
    def prank: (. // 1) | (- .);


    # Ordered sections: 0neon first, then 1neon, then critical path
    (
      [(.["0neon"] // [])[]] +
      [(.["1neon"] // [])[]] +
      [(.["关键路径"] // [])[]] +
      [(.["夜neon"] // [])[]]
      | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
    ) as $neon |
    # Today bucket sorted by priority
    (
      [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today)]
      | sort_by(.priority | prank)
    ) as $today_sorted |
    ($neon + $today_sorted)
    | map(select(.content != null))
    | reduce .[] as $t ([]; if [.[] | .id] | index($t.id) then . else . + [$t] end)
    | .[]
    | .content as $raw |
    ($raw | gsub(" *\\([0-9]*\\)"; "") | gsub(" *\\[[0-9]*\\]"; "") | gsub(" *\\{[0-9]*\\}"; "") | gsub(" +$"; "") | gsub("  +"; " ") | ascii_downcase) as $clean |
    ($clean | split(" - ") | .[0]) as $prefix |
    select(
      ($completed | index($clean) | not) and
      ($completed | index($prefix) | not)
    )
    | $raw
  ' | fzf --height 40 --prompt="did> " --layout=reverse --print-query --header="$combined_hdr")

  # --print-query: line 1 = typed query, line 2 = selected item
  query=$(echo "$fzf_output" | head -1)
  task=$(echo "$fzf_output" | tail -n +2 | head -1)

  if [[ -z "$task" ]]; then
    break
  fi

  # Strip annotations
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

  # --- START MODE: query begins with ">" ---
  if [[ "$query" == ">"* ]]; then
    # Resolve project via tg-fast.py --resolve
    project=$(python3 "$TG_FAST" --resolve "$clean" 2>/dev/null)
    python3 "$TOGGL_CLI" stop >/dev/null 2>&1
    start_out=$(python3 "$TOGGL_CLI" start "$clean" $project 2>&1)
    echo "Started: $clean → $project" > "$DTD_HDR"
    continue
  fi

  # --- DONE MODE (existing behavior) ---
  # Tasks that need args (e.g. cpap needs a score)
  clean_lower=$(echo "$clean" | tr '[:upper:]' '[:lower:]')
  case "$clean_lower" in
    cpap)
      REPLY="$clean "
      vared -p "→ " REPLY
      clean="$REPLY"
      ;;
  esac

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
