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
# Strip ANSI codes first
task=\$(echo "\$task" | sed $'s/\033\[[0-9;]*m//g')
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
REMOVED="$DTD_REMOVED"
task="\$1"
# Strip ANSI codes first
task=\$(echo "\$task" | sed $'s/\033\[[0-9;]*m//g')
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "⏳ deferring: \$clean" > "\$HDR"
result=\$(python3 "\$DEFER_FAST" "\$clean" 2>/dev/null)
ok=\$(echo "\$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'→ {d[\"target_date\"]} [{d[\"claimed_points\"]}] today / [{d[\"remaining_points\"]}] later')" 2>/dev/null)
if [[ -n "\$ok" ]]; then
  echo "\$clean" >> "\$REMOVED"
  echo "⏭ \$clean \$ok" > "\$HDR"
else
  echo "? defer failed: \$clean" > "\$HDR"
fi
DEFEREOF
chmod +x "$DTD_DEFER"

# --- Temp files for list generation ---
DTD_CACHE_FILE="/tmp/dtd-$$.cache.json"
DTD_REMOVED="/tmp/dtd-$$.removed"
echo "$CACHE_SNAPSHOT" > "$DTD_CACHE_FILE"
touch "$DTD_REMOVED"

# --- List generation script (reloadable by fzf) ---
DTD_LIST="/tmp/dtd-$$.list.sh"
cat > "$DTD_LIST" << 'LISTEOF'
#!/bin/zsh
# Args: $1=cache_file $2=done_file_path $3=removed_file $4=today $5=columns
python3 -c "
import json, sys, re

cache_file, done_file, removed_file, today, cols = sys.argv[1:6]
cols = int(cols)

with open(cache_file) as f:
    d = json.load(f)
try:
    with open(done_file) as f:
        completed = json.load(f)
except: completed = []

# Load removed items
try:
    with open(removed_file) as f:
        removed = [l.strip().lower() for l in f if l.strip()]
except: removed = []

# Neon color palette (label → ANSI 256-color)
COLORS = {
    'g245': '\033[38;2;0;230;118m',    'epcn': '\033[38;2;0;191;165m',
    's897': '\033[38;2;27;94;32m',     'hcmc2': '\033[38;2;255;214;0m',
    'xk87': '\033[38;2;253;108;29m',   'xk88': '\033[38;2;230;81;0m',
    'hci':  '\033[38;2;99;237;224m',   'i9':   '\033[38;2;41;121;255m',
    'n156': '\033[38;2;18;73;180m',    'hcmc': '\033[38;2;13;59;102m',
    'm5x2': '\033[38;2;213;0;50m',     'hcb':  '\033[38;2;248;29;120m',
    'hcbp': '\033[38;2;255;64;129m',   'infra':'\033[38;2;158;158;158m',
    'i444': '\033[38;2;97;97;97m',     'i447': '\033[38;2;168;156;138m',
    'hcm':  '\033[38;2;170;0;255m',    'hcmp': '\033[38;2;124;77;255m',
    'hcmr': '\033[38;2;189;166;255m',
}
RESET = '\033[0m'

def prank(p):
    return -(p or 1)

def strip_ann(s):
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', s)).strip()

# Build task list in priority order
sections = []
for key in ['0neon', '1neon', '关键路径', '夜neon']:
    sections.extend([t for t in d.get(key, []) if isinstance(t, dict)
                     and t.get('due') and t['due'] <= today])
# #0g tasks from today
today_tasks = [t for t in d.get('today', []) if isinstance(t, dict)
               and t.get('due') and t['due'] <= today]
goals = [t for t in today_tasks if any(l in ('#0g', '#-1g') for l in t.get('labels', []))]
rest = sorted([t for t in today_tasks if not any(l in ('#0g', '#-1g') for l in t.get('labels', []))],
              key=lambda t: prank(t.get('priority')))
all_tasks = sections + goals + rest

# Deduplicate by id
seen = set()
unique = []
for t in all_tasks:
    if t.get('content') and t['id'] not in seen:
        seen.add(t['id'])
        unique.append(t)

for t in unique:
    raw = t['content']
    clean = strip_ann(raw).lower()
    prefix = clean.split(' - ')[0]
    if clean in completed or prefix in completed or clean in removed:
        continue

    # Find color from labels
    color = ''
    for lbl in t.get('labels', []):
        if lbl in COLORS:
            color = COLORS[lbl]
            break

    # Middle-truncate if needed
    line = raw
    if len(line) > cols - 2:
        # Find trailing annotations
        tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][ ]*[\(\[\{]\d*[\)\]\}].*$', line)
        if not tail_m:
            tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][^()\[\]{}]*$', line)
        tail = tail_m.group() if tail_m else line[-15:]
        head_len = max(10, cols - len(tail) - 2)
        line = line[:head_len] + '…' + tail

    print(f'{color}{line}{RESET}' if color else line)
" "$1" "$2" "$3" "$4" "$5"
LISTEOF
chmod +x "$DTD_LIST"

# --- Delete script used by fzf ctrl-x binding ---
DTD_DELETE="/tmp/dtd-$$.delete.sh"
cat > "$DTD_DELETE" << DELETEEOF
#!/bin/zsh
HDR="$DTD_HDR"
CACHE_FILE="$DTD_CACHE_FILE"
REMOVED="$DTD_REMOVED"
task="\$1"
# Strip ANSI codes first
task=\$(echo "\$task" | sed $'s/\033\[[0-9;]*m//g')
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "⏳ deleting: \$clean" > "\$HDR"
tid=\$(python3 -c "
import json, re, sys
q = sys.argv[1].lower()
with open(sys.argv[2]) as f:
    d = json.load(f)
# Handle truncated names (contain …): match by prefix before …
prefix = q.split('\u2026')[0].strip() if '\u2026' in q else None
for s in d.values():
    if not isinstance(s, list): continue
    for t in s:
        if not isinstance(t, dict): continue
        c = re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', t.get('content','')).strip().lower()
        if c == q or (prefix and c.startswith(prefix)):
            print(t['id']); sys.exit(0)
" "\$clean" "\$CACHE_FILE" 2>/dev/null)
if [[ -n "\$tid" ]]; then
  # Get full name from cache for the removed list (clean may be truncated)
  fullname=\$(python3 -c "
import json, re, sys
tid = sys.argv[1]
with open(sys.argv[2]) as f:
    d = json.load(f)
for s in d.values():
    if not isinstance(s, list): continue
    for t in s:
        if isinstance(t, dict) and t.get('id') == tid:
            print(re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', t.get('content','')).strip().lower())
            sys.exit(0)
" "\$tid" "\$CACHE_FILE" 2>/dev/null)
  curl -s -X DELETE "https://api.todoist.com/api/v1/tasks/\$tid" \
    -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" >/dev/null 2>&1
  echo "\${fullname:-\$clean}" >> "\$REMOVED"
  echo "🗑 Deleted: \$clean" > "\$HDR"
else
  echo "? delete: task not found" > "\$HDR"
fi
DELETEEOF
chmod +x "$DTD_DELETE"

# --- Split script used by fzf ctrl-p binding ---
DTD_SPLIT="/tmp/dtd-$$.split.sh"
cat > "$DTD_SPLIT" << 'SPLITEOF'
#!/bin/zsh
# Split a task: claim partial points today, defer the rest to tomorrow.
# Three dialogs: points, what you did, what remains.
# All Todoist/Neon writes done inline via Python.

HDR="PLACEHOLDER_HDR"
REMOVED="PLACEHOLDER_REMOVED"
CACHE_FILE="PLACEHOLDER_CACHE"
DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"

task="$1"
task=$(echo "$task" | sed $'s/\033\[[0-9;]*m//g')

# Extract [N] and (N) from task
total=$(echo "$task" | grep -oE '\[[0-9]+\]' | head -1 | tr -d '[]')
duration=$(echo "$task" | grep -oE '\([0-9]+\)' | head -1 | tr -d '()')
[[ -z "$total" ]] && total="?"

# Dialog 1: points
pts_today=$(/usr/bin/osascript -e 'display dialog "Split: points done today? (total: ['"$total"'])" default answer "" buttons {"Cancel","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)
[[ -z "$pts_today" || ! "$pts_today" =~ ^[0-9]+$ ]] && { echo "cancelled" > "$HDR"; exit 0; }

# Dialog 2: what you did
done_desc=$(/usr/bin/osascript -e 'display dialog "What did you do?" default answer "" buttons {"Skip","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)

# Dialog 3: what remains
remaining_desc=$(/usr/bin/osascript -e 'display dialog "What remains?" default answer "" buttons {"Skip","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)

clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')
echo "⏳ splitting: $clean" > "$HDR"

# Find the original Todoist task and get its labels/project
python3 -c "
import json, re, sys, urllib.request

TOKEN = '7eb82f47aba8b334769351368e4e3e3284f980e5'
BASE = 'https://api.todoist.com/api/v1'
HDR = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}

clean = sys.argv[1]
pts_today = int(sys.argv[2])
total = int(sys.argv[3]) if sys.argv[3] != '?' else 0
done_desc = sys.argv[4]
remaining_desc = sys.argv[5]
duration = sys.argv[6]
hdr_file = sys.argv[7]
removed_file = sys.argv[8]

remaining_pts = max(0, total - pts_today) if total > 0 else 0

# Search for the task
def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f'{BASE}{path}', data=data, method=method, headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

# Find task by substring in today|overdue
tasks = []
cursor = None
for _ in range(3):
    url = f'{BASE}/tasks?filter=today%20%7C%20overdue&limit=200'
    if cursor: url += f'&cursor={cursor}'
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    batch = data if isinstance(data, list) else data.get('results', [])
    tasks.extend(batch)
    cursor = data.get('next_cursor') if isinstance(data, dict) else None
    if not cursor: break

clean_lower = clean.lower()
matches = [t for t in tasks if clean_lower in t.get('content','').lower()]
if not matches:
    with open(hdr_file, 'w') as f: f.write(f'? split: task not found')
    sys.exit(1)
task = matches[0]
tid = task['id']
labels = task.get('labels', [])
project_id = task.get('project_id')

# 1. Create completed posthoc for today's portion
today_label = done_desc if done_desc else clean
posthoc_content = f'{today_label} ({duration or pts_today}) [{pts_today}]'
from datetime import date
today_iso = date.today().isoformat()
posthoc = api('POST', '/tasks', {
    'content': posthoc_content,
    'labels': labels + ['posthoc'],
    'due_date': today_iso,
    'project_id': project_id,
})
if posthoc:
    api('POST', f'/tasks/{posthoc[\"id\"]}/close')

# 2. Update original task: new content with remaining description + reschedule
from datetime import timedelta
tomorrow = (date.today() + timedelta(days=1)).isoformat()
new_content = f'{remaining_desc or clean} ({duration}) [{remaining_pts}]' if remaining_pts > 0 else f'{remaining_desc or clean}'
api('POST', f'/tasks/{tid}', {
    'content': new_content,
    'due_date': tomorrow,
})

# 3. Log points to 0分 via did-fast (use original task's labels for column mapping)
import subprocess
label_arg = ''
for l in labels:
    if l in ('i9','i447','f693','f694','m5x2','g245','infra','cc','hcmc','hcb','hcbp','xk87','xk88','s897'):
        label_arg = f'@{l}'
        break
subprocess.run(['python3', '$HOME/i446-monorepo/tools/did/did-fast.py',
                f'{clean} [{pts_today}] {label_arg}'],
               capture_output=True, timeout=30)

# Write results
with open(removed_file, 'a') as f: f.write(clean.lower() + '\n')
msg = f'✂ +{pts_today} today / [{remaining_pts}] deferred to {tomorrow}'
with open(hdr_file, 'w') as f: f.write(msg)
" "$clean" "$pts_today" "${total:-?}" "${done_desc:-}" "${remaining_desc:-}" "${duration:-}" "$HDR" "$REMOVED"

SPLITEOF
# Substitute placeholder paths
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_REMOVED|$DTD_REMOVED|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g" "$DTD_SPLIT"
chmod +x "$DTD_SPLIT"

# --- UI loop (reads from CACHE_SNAPSHOT variable, never the file) ---
while true; do
  # Refresh date and completed-today on each iteration (handles midnight rollover)
  NEW_TODAY=$(date +%Y-%m-%d)
  if [[ "$NEW_TODAY" != "$LOCAL_TODAY" ]]; then
    LOCAL_TODAY="$NEW_TODAY"
    session_done=()  # reset session completions for new day
  fi
  DONE_NAMES=$(jq -c --arg today "$LOCAL_TODAY" \
    'if .date == $today then [.names[] | ascii_downcase] else [] end' "$DONE" 2>/dev/null || echo '[]')

  worker_hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")
  combined_hdr="$TIMER_HDR
  $worker_hdr"

  session_exclude=$(printf '%s\n' "${session_done[@]}" | jq -c -R -s 'split("\n") | map(select(. != ""))')
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  # Write completed list to file to avoid shell quoting issues (apostrophes in task names)
  DTD_DONE_FILE="/tmp/dtd-$$.done.json"
  echo "$all_completed" > "$DTD_DONE_FILE"

  # Generate task list via reloadable script (supports colors + removal)
  # Pass done file path instead of JSON string to avoid quoting issues
  DTD_LIST_CMD="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$LOCAL_TODAY' '${COLUMNS:-80}'"
  fzf_output=$(eval "$DTD_LIST_CMD" | fzf --height 40 --prompt="did> " --layout=reverse --ansi \
      --bind "ctrl-s:execute-silent($DTD_START {})+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-d:execute-silent($DTD_DEFER {})+reload($DTD_LIST_CMD)+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-x:execute-silent($DTD_DELETE {})+reload($DTD_LIST_CMD)+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-p:execute-silent($DTD_SPLIT {})+reload($DTD_LIST_CMD)+transform-header(cat $DTD_HDR)" \
      --bind "ctrl-r:execute-silent(python3 $DID_FAST --refresh-cache && cp $CACHE $DTD_CACHE_FILE)+reload($DTD_LIST_CMD)+transform-header(echo '🔄 refreshed')" \
      --header="$combined_hdr  [ctrl-s: timer | ctrl-d: defer | ctrl-p: split | ctrl-x: delete | ctrl-r: refresh]")

  task="$fzf_output"

  if [[ -z "$task" ]]; then
    break
  fi

  # Strip ANSI color codes from fzf selection
  task=$(echo "$task" | sed $'s/\033\[[0-9;]*m//g')

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

  # Strip annotations — keep {N} for did-fast.py (0g bonus), strip for filter
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/  +/ /g; s/ *$//')

  # --- DONE MODE (existing behavior) ---
  # Track original name for list filtering (strip {N} too for matching)
  clean_for_filter=$(echo "$clean" | sed -E 's/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

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

rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_START" "$DTD_DEFER" "$DTD_DELETE" "$DTD_SPLIT" "$DTD_CACHE_FILE" "$DTD_REMOVED" "$DTD_LIST" "/tmp/dtd-$$.done.json"
