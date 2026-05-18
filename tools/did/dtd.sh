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
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "/tmp/dtd-$$.start.sh"
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

# --- Helper: format toggl current output into 1-line string ---
_parse_toggl() {
  local cur="$1"
  if [[ "$cur" == Running:* ]]; then
    local body="${cur#Running: }"
    body=$(echo "$body" | sed -E 's/^[0-9]{2}:[0-9]{2}-running //')
    body=$(echo "$body" | sed -E 's/ *\(running\)//; s/ *\[id:[0-9]*\]//')
    echo "▶ $body"
  else
    echo "▶ (idle)"
  fi
}

# Fetch timer ONCE at startup (not every loop iteration)
TOGGL_CURRENT=$(python3 "$TOGGL_CLI" current 2>/dev/null)
TIMER_HDR=$(_parse_toggl "$TOGGL_CURRENT")

# --- Start script used by fzf ctrl-s binding ---
DTD_START="/tmp/dtd-$$.start.sh"
cat > "$DTD_START" << STARTEOF
#!/bin/zsh
TOGGL_CLI="\$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST="\$HOME/i446-monorepo/tools/tg/tg-fast.py"
HDR="$DTD_HDR"
task="\$1"
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
project=\$(python3 "\$TG_FAST" --resolve "\$clean" 2>/dev/null)
python3 "\$TOGGL_CLI" stop >/dev/null 2>&1
python3 "\$TOGGL_CLI" start "\$clean" \$project >/dev/null 2>&1
echo "▶ Started: \$clean → \$project" > "\$HDR"
STARTEOF
chmod +x "$DTD_START"

# --- Defer script used by fzf ctrl-d binding ---
DTD_DEFER="/tmp/dtd-$$.defer.sh"
cat > "$DTD_DEFER" << DEFEREOF
#!/bin/zsh
DEFER_FAST="\$HOME/i446-monorepo/tools/did/defer-fast.py"
HDR="$DTD_HDR"
task="\$1"
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "⏳ deferring: \$clean" > "\$HDR"
result=\$(python3 "\$DEFER_FAST" "\$clean" 2>/dev/null)
ok=\$(echo "\$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'→ {d[\"target_date\"]} [{d[\"claimed_points\"]}] today / [{d[\"remaining_points\"]}] later')" 2>/dev/null)
if [[ -n "\$ok" ]]; then
  echo "⏭ \$clean \$ok" > "\$HDR"
else
  echo "? defer failed: \$clean" > "\$HDR"
fi
DEFEREOF
chmod +x "$DTD_DEFER"

# --- Delete script used by fzf ctrl-x binding ---
DTD_DELETE="/tmp/dtd-$$.delete.sh"
CACHE_PATH="$CACHE"
cat > "$DTD_DELETE" << DELETEEOF
#!/bin/zsh
HDR="$DTD_HDR"
CACHE_FILE="$CACHE_PATH"
task="\$1"
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "⏳ deleting: \$clean" > "\$HDR"
tid=\$(python3 -c "
import json, re, sys
q = sys.argv[1].lower()
with open(sys.argv[2]) as f:
    d = json.load(f)
for s in d.values():
    if not isinstance(s, list): continue
    for t in s:
        if not isinstance(t, dict): continue
        c = re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', t.get('content','')).strip().lower()
        if c == q:
            print(t['id']); sys.exit(0)
" "\$clean" "\$CACHE_FILE" 2>/dev/null)
if [[ -n "\$tid" ]]; then
  curl -s -X DELETE "https://api.todoist.com/api/v1/tasks/\$tid" \
    -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" >/dev/null 2>&1
  echo "🗑 Deleted: \$clean" > "\$HDR"
else
  echo "? delete: task not found" > "\$HDR"
fi
DELETEEOF
chmod +x "$DTD_DELETE"

# --- UI loop (reads from CACHE_SNAPSHOT variable, never the file) ---
while true; do
  worker_hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")
  combined_hdr="$TIMER_HDR
  $worker_hdr"

  session_exclude=$(printf '%s\n' "${session_done[@]}" | jq -c -R -s 'split("\n") | map(select(. != ""))')
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  # Priority-ordered jq: 0neon → 1neon → 関键路径 → today (sorted by priority)
  fzf_output=$(echo "$CACHE_SNAPSHOT" | jq -r --argjson completed "$all_completed" --arg today "$LOCAL_TODAY" '
    # Todoist API: priority 4=urgent(p1), 3=high(p2), 2=medium(p3), 1=normal(p4)
    # Negate so sort_by puts highest priority first
    def prank: (. // 1) | (- .);


    # Ordered sections: 0neon → 1neon → #0g → 関键路径 → 夜neon → today by priority
    (
      [(.["0neon"] // [])[]] +
      [(.["1neon"] // [])[]] +
      [(.["关键路径"] // [])[]] +
      [(.["夜neon"] // [])[]]
      | map(select(type == "object" and .due != null and .due != "" and .due <= $today))
    ) as $neon |
    # Extract #0g tasks from today bucket (daily goals, shown after 1neon)
    (
      [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today
        and ((.labels // []) | any(. == "#0g" or . == "#-1g")))]
    ) as $goals |
    # Remaining today tasks sorted by priority
    (
      [(.["today"] // [])[] | select(type == "object" and .due != null and .due != "" and .due <= $today
        and ((.labels // []) | any(. == "#0g" or . == "#-1g") | not))]
      | sort_by(.priority | prank)
    ) as $today_sorted |
    ($neon + $goals + $today_sorted)
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
  ' | awk -v w="${COLUMNS:-80}" '{
    # Middle-truncate: keep beginning + "…" + trailing annotations
    if (length <= w-2) { print; next }
    # Find trailing annotations: capture everything from last ( or [ to end
    tail = ""
    s = $0
    while (match(s, /[ ]*[\(\[\{][0-9]*[\)\]\}][ ]*[\(\[\{][0-9]*[\)\]\}].*$/)) {
      tail = substr(s, RSTART)
      break
    }
    if (tail == "" && match(s, /[ ]*[\(\[\{][0-9]*[\)\]\}][^()[\]{}]*$/)) {
      tail = substr(s, RSTART)
    }
    if (tail == "") { tail = substr(s, length-14) }
    tl = length(tail)
    head_len = w - tl - 2
    if (head_len < 10) head_len = 10
    printf "%s…%s\n", substr($0, 1, head_len), tail
  }' | fzf --height 40 --prompt="did> " --layout=reverse \
      --bind "ctrl-s:execute-silent($DTD_START {})+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-d:execute-silent($DTD_DEFER {})+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-x:execute-silent($DTD_DELETE {})+transform-header(cat $DTD_HDR)" \
      --header="$combined_hdr  [ctrl-s: timer | ctrl-d: defer | ctrl-x: delete]")

  task="$fzf_output"

  if [[ -z "$task" ]]; then
    break
  fi

  # Resolve truncated names: if fzf output contains "…", find the original
  # full name from the cache snapshot by matching the prefix before "…"
  if [[ "$task" == *"…"* ]]; then
    prefix="${task%%…*}"
    full=$(echo "$CACHE_SNAPSHOT" | jq -r --arg pfx "$prefix" '
      [.. | objects | .content? // empty]
      | map(select(startswith($pfx)))
      | first // empty
    ' 2>/dev/null)
    if [[ -n "$full" ]]; then
      task="$full"
    fi
  fi

  # Strip annotations
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

  # --- DONE MODE (existing behavior) ---
  # Track original name for list filtering (before vared modifies it)
  clean_for_filter="$clean"

  # Tasks that need args (e.g. cpap needs a score)
  clean_lower=$(echo "$clean" | tr '[:upper:]' '[:lower:]')
  case "$clean_lower" in
    cpap|ibx\ s897|ibx\ i9|ibx\ m5x2)
      REPLY="$clean "
      vared -p "→ " REPLY
      clean="$REPLY"
      ;;
  esac

  session_done+=("$clean_for_filter")
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

rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_START" "$DTD_DEFER" "$DTD_DELETE"
