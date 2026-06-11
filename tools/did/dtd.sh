#!/bin/zsh
# dtd — fuzzy task picker that runs /did directly (no Claude needed)
# UI-first: fzf stays responsive, background worker processes tasks serially,
# fzf header shows latest completion status.
# KEY: cache is snapshotted ONCE at startup. No mid-session re-reads.

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
UNDO_FAST="$HOME/i446-monorepo/tools/did/undo-fast.py"
DTD_RESOLVE="$HOME/i446-monorepo/tools/did/dtd_resolve.py"
TG_FAST="$HOME/i446-monorepo/tools/tg/tg-fast.py"
TOGGL_CLI="$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
# Machine-local runtime state (not synced). See lib/state_paths.py + architecture.md
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jm"
mkdir -p "$STATE_DIR"
CACHE="$STATE_DIR/task-queue.json"
DONE="$STATE_DIR/completed-today.json"
DTD_FIFO="/tmp/dtd-$$.fifo"
DTD_HDR="/tmp/dtd-$$.hdr"
DTD_LOG="/tmp/dtd-$$.log"
# ctrl-z undo state: journal of reversible actions + in-flight counters
DTD_JOURNAL="/tmp/dtd-$$.undo.jsonl"
DTD_PUSHED="/tmp/dtd-$$.pushed"
DTD_PROCESSED="/tmp/dtd-$$.processed"
DTD_SESSION="/tmp/dtd-$$.session"
DTD_TIMER="/tmp/dtd-$$.timer"

if [[ ! -f "$CACHE" ]]; then
  echo "No task cache found at $CACHE" >&2
  return 1 2>/dev/null || exit 1
fi

if [[ $(jq '.today | length // 0' "$CACHE") -lt 5 ]]; then
  echo "Refreshing task cache..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
fi

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
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "/tmp/dtd-$$.start.sh" \
      "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER"
mkfifo "$DTD_FIFO"
echo "ready" > "$DTD_HDR"
touch "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER"

(
  while IFS= read -r task_clean; do
    [[ -z "$task_clean" ]] && continue
    echo "⏳ $task_clean" > "$DTD_HDR"
    result=$(python3 "$DID_FAST" "$task_clean" 2>>"$DTD_LOG.err")
    # Journal for ctrl-z undo BEFORE signalling done (the undo guard compares
    # the pushed/processed counters, so the journal entry must land first)
    echo "$result" | python3 "$UNDO_FAST" --journal-done "$DTD_JOURNAL" 2>/dev/null
    ok=$(echo "$result" | jq -r '.results[]? | "\(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null)
    if [[ -n "$ok" ]]; then
      echo "✓ $ok" > "$DTD_HDR"
      echo "✓ $ok" >> "$DTD_LOG"
    else
      echo "? $task_clean" > "$DTD_HDR"
      echo "? $task_clean" >> "$DTD_LOG"
    fi
    echo "x" >> "$DTD_PROCESSED"
  done < "$DTD_FIFO"
  echo "done" > "$DTD_HDR"
) &
WORKER_PID=$!

exec 3>"$DTD_FIFO"

# --- Temp files for list generation (defined before the binding scripts
# below so their heredocs expand to real paths, not empty strings) ---
DTD_CACHE_FILE="/tmp/dtd-$$.cache.json"
DTD_REMOVED="/tmp/dtd-$$.removed"
# Skips persist across dtd sessions for the duration of one day (stable
# path + date guard), unlike the other per-session temp files
DTD_SKIPPED="$STATE_DIR/dtd-skipped-today.txt"
if [[ -f "$DTD_SKIPPED.date" && "$(cat "$DTD_SKIPPED.date" 2>/dev/null)" != "$LOCAL_TODAY" ]]; then
  rm -f "$DTD_SKIPPED"
fi
echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
DTD_DONE_FILE="/tmp/dtd-$$.done.json"
echo "$CACHE_SNAPSHOT" > "$DTD_CACHE_FILE"
touch "$DTD_REMOVED"
touch "$DTD_SKIPPED"

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

# --- Start script used by fzf enter/ctrl-s binding ---
DTD_START="/tmp/dtd-$$.start.sh"
cat > "$DTD_START" << STARTEOF
#!/bin/zsh
TOGGL_CLI="\$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST="\$HOME/i446-monorepo/tools/tg/tg-fast.py"
HDR="$DTD_HDR"
TIMER="$DTD_TIMER"
task="\$1"
# Strip ANSI codes first
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
project=\$(python3 "\$TG_FAST" --resolve "\$clean" 2>/dev/null)
python3 "\$TOGGL_CLI" stop >/dev/null 2>&1
python3 "\$TOGGL_CLI" start "\$clean" \$project >/dev/null 2>&1
printf '%s\t%s\n' "\$clean" "\$(date +%s)" > "\$TIMER"
echo "▶ Started: \$clean → \$project" > "\$HDR"
STARTEOF
chmod +x "$DTD_START"

# --- Enter script: start selected task; if already timing, complete it ---
DTD_ENTER="/tmp/dtd-$$.enter.sh"
cat > "$DTD_ENTER" << ENTEREOF
#!/bin/zsh
TOGGL_CLI="\$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
START="$DTD_START"
HDR="$DTD_HDR"
FIFO="$DTD_FIFO"
SESSION="$DTD_SESSION"
PUSHED="$DTD_PUSHED"
REMOVED="$DTD_REMOVED"
TIMER="$DTD_TIMER"
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/  +/ /g; s/ *\$//')
clean_for_filter=\$(echo "\$clean" | sed -E 's/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
clean_lower=\$(echo "\$clean_for_filter" | tr '[:upper:]' '[:lower:]')

cur=\$(python3 "\$TOGGL_CLI" current 2>/dev/null)
cur_desc=""
if [[ "\$cur" == Running:* ]]; then
  cur_desc=\$(echo "\$cur" | sed -E 's/^Running: [0-9]{2}:[0-9]{2}-running //; s/ *@.*//; s/ *\\(running\\).*//; s/ *\\[id:[0-9]*\\].*//; s/ *\$//' | tr '[:upper:]' '[:lower:]')
fi
timer_desc=\$(cut -f1 "\$TIMER" 2>/dev/null | tr '[:upper:]' '[:lower:]')

if [[ "\$cur_desc" == "\$clean_lower" || "\$timer_desc" == "\$clean_lower" ]]; then
  echo "\$clean_for_filter" >> "\$SESSION"
  echo "\$clean_for_filter" >> "\$REMOVED"
  echo "x" >> "\$PUSHED"
  : > "\$TIMER"
  echo "⏳ completing: \$clean_for_filter" > "\$HDR"
  printf '%s\n' "\$clean" > "\$FIFO"
else
  "\$START" "\$task"
fi
ENTEREOF
chmod +x "$DTD_ENTER"

# --- Complete-now script used by fzf alt-enter binding (ctrl+enter via the
# Ghostty keybind remap ctrl+enter -> ESC CR). Unlike enter, this never starts
# a timer: it always completes the selected task via the /did worker. ---
DTD_DONE="/tmp/dtd-$$.done.sh"
cat > "$DTD_DONE" << DONEEOF
#!/bin/zsh
HDR="$DTD_HDR"
FIFO="$DTD_FIFO"
SESSION="$DTD_SESSION"
PUSHED="$DTD_PUSHED"
REMOVED="$DTD_REMOVED"
TIMER="$DTD_TIMER"
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/  +/ /g; s/ *\$//')
clean_for_filter=\$(echo "\$clean" | sed -E 's/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "\$clean_for_filter" >> "\$SESSION"
echo "\$clean_for_filter" >> "\$REMOVED"
echo "x" >> "\$PUSHED"
: > "\$TIMER"
echo "⏳ completing: \$clean_for_filter" > "\$HDR"
printf '%s\n' "\$clean" > "\$FIFO"
DONEEOF
chmod +x "$DTD_DONE"

# --- Defer script used by fzf ctrl-d binding ---
DTD_DEFER="/tmp/dtd-$$.defer.sh"
cat > "$DTD_DEFER" << DEFEREOF
#!/bin/zsh
DEFER_FAST="\$HOME/i446-monorepo/tools/did/defer-fast.py"
HDR="$DTD_HDR"
REMOVED="$DTD_REMOVED"
task="\$1"
# Strip ANSI codes and recurring indicator
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
# Query with the FULL row content (annotations intact) so duplicate names
# differing only in (N)/[N] resolve to the exact selected task; fall back
# to the stripped prefix when fzf truncated the row (regression 2026-06-06:
# "defer failed: call dad" with two call-dad tasks)
query="\$task"
if [[ "\$clean" == *"…"* ]]; then
  clean="\${clean%%…*}"
  query="\$clean"
fi
# Prompt for the defer target — N days or an absolute date; empty/0 = "auto":
# recurring tasks skip to their next occurrence, non-recurring default to +1
# day (0 = today). Gated on DTD_DEFER_PROMPT, which only dtd's fzf session
# exports: the test harness and any scripted caller run the script with the
# flag unset and get the non-interactive default.
days=""
if [[ -n "\${DTD_DEFER_PROMPT:-}" && -r /dev/tty ]]; then
  printf "\nDefer '%s' by N days / YYYY-MM-DD (blank or 0 = next occurrence if recurring)> " "\$clean" > /dev/tty
  read days < /dev/tty
fi
days=\${days// /}
[[ -z "\$days" ]] && days=auto
case "\$days" in
  auto|<->|[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "✗ invalid defer target: \$days (cancelled)" > "\$HDR"; exit 0 ;;
esac
defer_label="+\$days"
[[ "\$days" == "auto" || "\$days" == "0" ]] && defer_label="auto"
# Optimistic UI: hide the task and show status IMMEDIATELY, then run the
# Todoist round trips (paginated search, reschedule, posthoc create+close —
# 3-10s) detached so fzf never blocks on the network. On failure the hide is
# rolled back so the task reappears. The pushed/processed counters keep
# ctrl-z honest while the defer is in flight.
echo "\$clean" >> "\$REMOVED"
echo "⏳ deferring (\$defer_label): \$clean" > "\$HDR"
echo "x" >> "$DTD_PUSHED"
(
  result=\$(python3 "\$DEFER_FAST" "\$query" "\$days" 2>/dev/null)
  ok=\$(echo "\$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'→ {d[\"target_date\"]} [{d[\"claimed_points\"]}] today / [{d[\"remaining_points\"]}] later')" 2>/dev/null)
  if [[ -n "\$ok" ]]; then
    # Journal for ctrl-z undo
    echo "\$result" | python3 "$UNDO_FAST" --journal-defer "$DTD_JOURNAL" "\$clean" 2>/dev/null
    echo "⏭ \$clean \$ok" > "\$HDR"
  else
    # Roll back the optimistic hide so the task reappears on next reload
    grep -v -x -F -- "\$clean" "\$REMOVED" > "\$REMOVED.tmp" 2>/dev/null
    mv "\$REMOVED.tmp" "\$REMOVED"
    echo "? defer failed: \$clean (restored to list)" > "\$HDR"
  fi
  echo "x" >> "$DTD_PROCESSED"
) >/dev/null 2>&1 &!
DEFEREOF
chmod +x "$DTD_DEFER"

# --- Change-points script used by fzf ctrl-v binding ---
# Prompts for a new [N] value (needs a tty, so the binding uses execute(), not
# execute-silent), updates the task in Todoist, and patches $CACHE so the new
# value shows on reload.
DTD_POINTS="/tmp/dtd-$$.points.sh"
cat > "$DTD_POINTS" << POINTSEOF
#!/bin/zsh
POINTS_FAST="\$HOME/i446-monorepo/tools/did/points-fast.py"
HDR="$DTD_HDR"
CACHE="$CACHE"
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
query="\$task"
if [[ "\$clean" == *"…"* ]]; then
  clean="\${clean%%…*}"
  query="\$clean"
fi
printf "\nNew points for: %s\n[N]> " "\$clean" > /dev/tty
read newpts < /dev/tty
out=\$(python3 "\$POINTS_FAST" "\$query" "\$newpts" "\$CACHE" 2>/dev/null)
echo "\${out:-✗ points update failed}" > "\$HDR"
POINTSEOF
chmod +x "$DTD_POINTS"

# --- List generation script (reloadable by fzf) ---
DTD_LIST="/tmp/dtd-$$.list.sh"
cat > "$DTD_LIST" << 'LISTEOF'
#!/bin/zsh
# Args: $1=cache_file $2=done_file_path $3=removed_file $4=today $5=columns $6=skipped_file $7=timer_file
python3 -c "
import json, sys, re, time

cache_file, done_file, removed_file, today, cols = sys.argv[1:6]
skipped_file = sys.argv[6] if len(sys.argv) > 6 else ''
timer_file = sys.argv[7] if len(sys.argv) > 7 else ''
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

# Load skipped items (display at bottom, not hidden)
try:
    with open(skipped_file) as f:
        skipped = [l.strip().lower() for l in f if l.strip()]
except: skipped = []

# Load running timer hint written by dtd's Enter/ctrl-s start path.
running_clean = ''
running_started = 0
try:
    timer_raw = open(timer_file).read().strip()
    if timer_raw:
        parts = timer_raw.split('\t')
        running_clean = parts[0].strip().lower()
        running_started = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
except: pass

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
    'hcmr': '\033[38;2;189;166;255m',  '家':   '\033[38;2;255;65;54m',
    '睡觉': '\033[38;2;102;102;102m',
}
RESET = '\033[0m'

def prank(p):
    return -(p or 1)

def strip_ann(s):
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', s)).strip()

# Right-justify trailing (N)/[N]/{N} estimates to the window edge so they line
# up in a column. target = cols - 3 leaves room for fzf's pointer/gutter (2) and
# the scrollbar (1). If there is no room (long/truncated rows), leave inline.
_EST_TOK = r'(?:\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\})'
_EST_TAIL = re.compile(r'(\s*(?:' + _EST_TOK + r'\s*)+)$')
def rjust_est(s, cols):
    m = _EST_TAIL.search(s)
    if not m:
        return s
    est = re.sub(r'\s+', ' ', m.group(1).strip())
    head = s[:m.start()].rstrip()
    pad = (cols - 3) - len(head) - len(est)
    if pad < 2:
        return (head + ' ' + est) if head else est
    return head + ' ' * pad + est

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

DIM = '\033[2m'
running_lines = []
normal_lines = []
skipped_lines = []

for t in unique:
    raw = t['content']
    clean = strip_ann(raw).lower()
    prefix = clean.split(' - ')[0]
    # removed entries may be truncated prefixes (fzf middle-truncates long
    # names in the defer/split bindings) — match by startswith, not equality
    # (regression 2026-06-06: split task stayed in the list)
    if (clean in completed or prefix in completed
            or any(clean == r or (r and clean.startswith(r)) for r in removed)):
        continue

    is_skipped = clean in skipped

    # Find color from labels
    color = ''
    for lbl in t.get('labels', []):
        if lbl in COLORS:
            color = COLORS[lbl]
            break

    # Recurring indicator
    recurring = t.get('recurring', False)

    # Display the cached short (Haiku) name when present so long m5x2-style
    # tasks keep their (N)/[N] estimates visible; fall back to full content.
    display = t.get('short') or raw

    # Middle-truncate if needed (fallback; short names usually fit)
    line = display
    if len(line) > cols - 2:
        # Find trailing annotations
        tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][ ]*[\(\[\{]\d*[\)\]\}].*$', line)
        if not tail_m:
            tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][^()\[\]{}]*$', line)
        tail = tail_m.group() if tail_m else line[-15:]
        head_len = max(10, cols - len(tail) - 2)
        line = line[:head_len] + '…' + tail

    # Hidden field 2 carries the task id so bindings resolve the real task.
    # fzf shows field 1 only (--with-nth=1); search therefore matches the
    # visible short name (Haiku keeps key codes/names, so this stays usable).
    sfx = '\t' + str(t.get('id', ''))

    repeat = '↻ ' if recurring else ''
    is_running = bool(running_clean and clean == running_clean)
    if is_running:
        elapsed = max(0, int((time.time() - running_started) // 60)) if running_started else 0
        prefix = f'▶ {elapsed}m · '
    else:
        prefix = repeat
    # Build the full visible row, then right-justify its trailing estimates so
    # they align in a column regardless of the prefix. ANSI is added after.
    body = rjust_est(prefix + line, cols)
    if is_running:
        # NB: this python lives inside a zsh double-quoted string — never use
        # double quotes in here, they terminate the -c argument.
        running_lines.append(f'{color}{body}{RESET}{sfx}')
    elif is_skipped:
        skipped_lines.append(f'{color}{body}{RESET}{sfx}')
    elif color:
        normal_lines.append(f'{color}{body}{RESET}{sfx}')
    else:
        normal_lines.append(f'{body}{sfx}')

for l in running_lines:
    print(l)
for l in normal_lines:
    print(l)
for l in skipped_lines:
    print(l)
" "$1" "$2" "$3" "$4" "$5" "$6" "$7"
LISTEOF
chmod +x "$DTD_LIST"

# --- Skip script used by fzf ctrl-k binding ---
DTD_SKIP="/tmp/dtd-$$.skip.sh"
cat > "$DTD_SKIP" << SKIPEOF
#!/bin/zsh
SKIPPED="$DTD_SKIPPED"
HDR="$DTD_HDR"
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "\$clean" | tr '[:upper:]' '[:lower:]' >> "\$SKIPPED"
echo "⏭ \$clean" > "\$HDR"
SKIPEOF
chmod +x "$DTD_SKIP"

# --- Delete script used by fzf ctrl-x binding ---
DTD_DELETE="/tmp/dtd-$$.delete.sh"
cat > "$DTD_DELETE" << DELETEEOF
#!/bin/zsh
HDR="$DTD_HDR"
CACHE_FILE="$DTD_CACHE_FILE"
REMOVED="$DTD_REMOVED"
task="\$1"
# Strip ANSI codes and recurring indicator
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
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
  # Pre-image for ctrl-z undo — fetched before the DELETE, journaled only
  # after a successful DELETE (a failed delete must not be undoable, or
  # ctrl-z would recreate a task that still exists)
  pre=\$(curl -s "https://api.todoist.com/api/v1/tasks/\$tid" \
    -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" 2>/dev/null)
  code=\$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "https://api.todoist.com/api/v1/tasks/\$tid" \
    -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" 2>/dev/null)
  if [[ "\$code" == 2* ]]; then
    echo "\${fullname:-\$clean}" >> "\$REMOVED"
    printf '%s' "\$pre" | python3 -c "
import json, sys
name, fallback = sys.argv[1], sys.argv[2]
try:
    task = json.load(sys.stdin)
except Exception:
    task = {}
if not isinstance(task, dict) or not task.get('content'):
    task = {'content': fallback}
print(json.dumps({'type': 'delete', 'names': [name], 'task': task},
                 ensure_ascii=False))
" "\${fullname:-\$clean}" "\$clean" | python3 "$UNDO_FAST" --append "$DTD_JOURNAL"
    echo "🗑 Deleted: \$clean" > "\$HDR"
  else
    echo "? delete failed (HTTP \$code): \$clean" > "\$HDR"
  fi
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
task=$(python3 "$HOME/i446-monorepo/tools/did/dtd_resolve.py" "$CACHE_FILE" "$1")  # id -> canonical content

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
# Strip truncation: if fzf middle-truncated the name with …, search by the
# prefix before it — otherwise the Todoist substring match fails with
# "task not found" after the user already answered all three dialogs
# (regression 2026-06-06; same fix as the defer script)
if [[ "$clean" == *"…"* ]]; then
  clean="${clean%%…*}"
  clean=$(echo "$clean" | sed 's/ *$//')
fi
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
# Pre-image for ctrl-z undo
prev_content = task.get('content', '')
prev_due = (task.get('due') or {}).get('date', '')

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

# 3. Log points to 0分 via did-fast (use original task's labels for column
#    mapping). --points-only skips Todoist matching: without it did-fast
#    re-finds the just-renamed remainder task and closes it.
import subprocess
label_arg = ''
for l in labels:
    if l in ('i9','i447','f693','f694','m5x2','g245','infra','cc','hcmc','hcb','hcbp','xk87','xk88','s897'):
        label_arg = f'@{l}'
        break
# did-fast splits its input on commas/semicolons — a task name containing
# one would be parsed as multiple items, detaching [pts]/@label from the
# name and scattering the points (regression 2026-06-06: "Rev on ground
# transit. Buy nightshade, 2" logged its 10 points as a task named "2")
safe_name = re.sub(r'[,;]+', ' ', clean)
df = subprocess.run(['python3', '$HOME/i446-monorepo/tools/did/did-fast.py',
                '--points-only', f'{safe_name} [{pts_today}] {label_arg}'],
               capture_output=True, text=True, timeout=30)
try:
    didfast_out = json.loads(df.stdout)
except Exception:
    didfast_out = None

# 4. Journal for ctrl-z undo
record = {
    'type': 'split',
    'names': [clean],
    'task_id': tid,
    'prev_content': prev_content,
    'prev_due': prev_due,
    'posthoc_id': posthoc['id'] if posthoc else None,
    'didfast': didfast_out,
}
subprocess.run(['python3', '$HOME/i446-monorepo/tools/did/undo-fast.py',
                '--append', 'PLACEHOLDER_JOURNAL'],
               input=json.dumps(record, ensure_ascii=False), text=True,
               capture_output=True, timeout=10)

# Write results
with open(removed_file, 'a') as f: f.write(clean.lower() + '\n')
msg = f'✂ +{pts_today} today / [{remaining_pts}] deferred to {tomorrow}'
with open(hdr_file, 'w') as f: f.write(msg)
" "$clean" "$pts_today" "${total:-?}" "${done_desc:-}" "${remaining_desc:-}" "${duration:-}" "$HDR" "$REMOVED"

SPLITEOF
# Substitute placeholder paths
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_REMOVED|$DTD_REMOVED|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g; s|PLACEHOLDER_JOURNAL|$DTD_JOURNAL|g" "$DTD_SPLIT"
chmod +x "$DTD_SPLIT"

# --- Agent script used by fzf ctrl-a binding ---
DTD_AGENT="/tmp/dtd-$$.agent.sh"
cat > "$DTD_AGENT" << 'AGENTEOF'
#!/bin/zsh
# Spawn a Claude agent in a new cmux tab to work on the selected task.
# Starts a Toggl timer, fetches task context, launches claude interactively.

TOGGL_CLI="$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST="$HOME/i446-monorepo/tools/tg/tg-fast.py"
HDR="PLACEHOLDER_HDR"
CACHE_FILE="PLACEHOLDER_CACHE"

task="$1"
task=$(python3 "$HOME/i446-monorepo/tools/did/dtd_resolve.py" "$CACHE_FILE" "$1")  # id -> canonical content
clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

# Handle truncation
if [[ "$clean" == *"…"* ]]; then
  clean="${clean%%…*}"
fi

# 1. Start Toggl timer
project=$(python3 "$TG_FAST" --resolve "$clean" 2>/dev/null)
python3 "$TOGGL_CLI" stop >/dev/null 2>&1
python3 "$TOGGL_CLI" start "$clean" $project >/dev/null 2>&1

# 1b. Auto-tag @agent in Todoist if not already tagged
python3 -c "
import json, sys, urllib.request
q = sys.argv[1].lower()
TOKEN = '7eb82f47aba8b334769351368e4e3e3284f980e5'
try:
    with open(sys.argv[2]) as f:
        d = json.load(f)
    for section in d.values():
        if not isinstance(section, list): continue
        for t in section:
            if not isinstance(t, dict): continue
            if q in t.get('content','').lower():
                labels = t.get('labels', [])
                if 'a' not in labels:
                    labels.append('a')
                    req = urllib.request.Request(
                        f'https://api.todoist.com/api/v1/tasks/{t[\"id\"]}',
                        data=json.dumps({'labels': labels}).encode(),
                        method='POST',
                        headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
                    urllib.request.urlopen(req, timeout=10)
                sys.exit(0)
except: pass
" "$clean" "$CACHE_FILE" 2>/dev/null &

# 2. Get task description from cache
desc=$(python3 -c "
import json, sys
try:
    d = json.load(open('$CACHE_FILE'))
    q = sys.argv[1].lower()
    for section in d.values():
        if not isinstance(section, list): continue
        for t in section:
            if not isinstance(t, dict): continue
            if q in t.get('content','').lower():
                print(t.get('description',''))
                sys.exit(0)
except: pass
" "$clean" 2>/dev/null)

# 2b. Get task ID and journal history
task_id=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[2]))
    q = sys.argv[1].lower()
    for section in d.values():
        if not isinstance(section, list): continue
        for t in section:
            if not isinstance(t, dict): continue
            if q in t.get('content','').lower():
                print(t.get('id',''))
                sys.exit(0)
except: pass
" "$clean" "$CACHE_FILE" 2>/dev/null)

journal=""
JOURNAL_DIR="$HOME/vault/z_ibx/task-journal"
if [[ -n "$task_id" && -f "$JOURNAL_DIR/$task_id.md" ]]; then
  journal=$(cat "$JOURNAL_DIR/$task_id.md")
fi

# 3. Build the prompt
prompt="Work on this task: $task"
if [[ -n "$desc" ]]; then
  prompt="$prompt

Context from Todoist:
$desc"
fi
if [[ -n "$journal" ]]; then
  prompt="$prompt

Prior attempts (task journal):
$journal"
fi
prompt="$prompt

When you're done, ask me if the task is complete. If I say yes, run /did to close it and stop the Toggl timer. If I say no or we didn't finish, append a journal entry to ~/vault/z_ibx/task-journal/$task_id.md with what you attempted, completed, what's blocked, and next steps."

# 4. Write prompt to temp file (avoids shell quoting issues)
PROMPT_FILE="/tmp/dtd-agent-$$.md"
echo "$prompt" > "$PROMPT_FILE"

# 5. Spawn in cmux tab
if command -v cmux &>/dev/null; then
  surface_output=$(cmux new-surface --type terminal 2>&1)
  surface_id=$(echo "$surface_output" | grep -oE 'surface:[0-9]+' | head -1)
  pane_id=$(echo "$surface_output" | grep -oE 'pane:[0-9]+' | head -1)
  if [[ -n "$surface_id" ]]; then
    cmux respawn-pane --surface "$surface_id" --command "sleep 0.5 && cc \"\$(cat $PROMPT_FILE)\"; rm -f $PROMPT_FILE" 2>/dev/null
    if [[ -n "$pane_id" ]]; then
      cmux focus-pane --pane "$pane_id" 2>/dev/null
    fi
    echo "🤖 agent → $clean (cmux)" > "$HDR"
  else
    # cmux failed, fall back to Terminal.app
    osascript -e "tell application \"Terminal\" to do script \"cc \\\"\$(cat $PROMPT_FILE)\\\"; rm -f $PROMPT_FILE\"" 2>/dev/null
    echo "🤖 agent → $clean (Terminal)" > "$HDR"
  fi
else
  # No cmux: open Terminal.app tab
  osascript -e "tell application \"Terminal\" to do script \"cc \\\"\$(cat $PROMPT_FILE)\\\"; rm -f $PROMPT_FILE\"" 2>/dev/null
  echo "🤖 agent → $clean (Terminal)" > "$HDR"
fi
AGENTEOF
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g" "$DTD_AGENT"
chmod +x "$DTD_AGENT"

# --- Undo script used by fzf ctrl-z binding ---
# Pops the last journaled action (done/split/defer) and reverses it via
# undo-fast.py, which also removes the task from the session/removed/done
# filter files so it reappears in the list on reload.
DTD_UNDO="/tmp/dtd-$$.undo.sh"
cat > "$DTD_UNDO" << UNDOEOF
#!/bin/zsh
HDR="$DTD_HDR"
pushed=\$(wc -l < "$DTD_PUSHED" 2>/dev/null || echo 0)
processed=\$(wc -l < "$DTD_PROCESSED" 2>/dev/null || echo 0)
if (( pushed > processed )); then
  echo "⏳ \$((pushed - processed)) task(s) still processing — retry ctrl-z in a moment" > "\$HDR"
  exit 0
fi
result=\$(python3 "$UNDO_FAST" --undo "$DTD_JOURNAL" \\
  --session "$DTD_SESSION" --removed "$DTD_REMOVED" --done-json "$DTD_DONE_FILE" 2>&1)
summary=\$(echo "\$result" | jq -r '.summary // .error // "undo failed"' 2>/dev/null)
if [[ \$(echo "\$result" | jq -r '.ok // empty' 2>/dev/null) == "true" ]]; then
  echo "↩ \$summary" > "\$HDR"
else
  echo "? \${summary:-undo failed}" > "\$HDR"
fi
UNDOEOF
chmod +x "$DTD_UNDO"

# Clear leftover terminal scrollback so the picker starts on a clean screen
# (fzf --height renders inline below whatever was already on the terminal).
clear

# Keybinding hints shown on the footer. Exported so the transform-footer
# bindings (which run in fzf's child shell) can read it. The footer is a
# single bottom line: "<tasks left>   <keybindings>", with the live match
# count ($FZF_MATCH_COUNT) refreshed on load/result.
export DTD_KEYS="enter: start/complete | ⌃⏎: done | ctrl-s: timer | ctrl-d: defer | ctrl-p: split | ctrl-v: pts | ctrl-a: agent | ctrl-k: skip | ctrl-x: del | ctrl-z: undo | ctrl-r: refresh"

# ctrl-d prompts for the defer target (N days / date) on the tty. Only set
# here so the extracted script stays non-interactive for tests and scripts.
export DTD_DEFER_PROMPT=1

# --- UI loop (reads from CACHE_SNAPSHOT variable, never the file) ---
while true; do
  # Refresh date and completed-today on each iteration (handles midnight rollover)
  NEW_TODAY=$(date +%Y-%m-%d)
  if [[ "$NEW_TODAY" != "$LOCAL_TODAY" ]]; then
    LOCAL_TODAY="$NEW_TODAY"
    : > "$DTD_SESSION"   # reset session completions for new day
    : > "$DTD_JOURNAL"   # yesterday's actions are no longer undoable
  fi
  DONE_NAMES=$(jq -c --arg today "$LOCAL_TODAY" \
    'if .date == $today then [.names[] | ascii_downcase] else [] end' "$DONE" 2>/dev/null || echo '[]')

  TOGGL_CURRENT=$(python3 "$TOGGL_CLI" current 2>/dev/null)
  TIMER_HDR=$(_parse_toggl "$TOGGL_CURRENT")
  worker_hdr=$(cat "$DTD_HDR" 2>/dev/null || echo "")
  combined_hdr="$TIMER_HDR
  $worker_hdr"

  session_exclude=$(jq -c -R -s 'split("\n") | map(select(. != ""))' < "$DTD_SESSION")
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  # Write completed list to file to avoid shell quoting issues (apostrophes in task names)
  echo "$all_completed" > "$DTD_DONE_FILE"

  # Generate task list via reloadable script (supports colors + removal)
  # Pass done file path instead of JSON string to avoid quoting issues
  # Every reload copies the live cache so external changes (/todo, other terminals) appear
  DTD_SYNC="cp '$CACHE' '$DTD_CACHE_FILE' 2>/dev/null;"
  DTD_LIST_CMD="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$LOCAL_TODAY' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER'"
  DTD_RELOAD="${DTD_SYNC}${DTD_LIST_CMD}"
  # --no-sort: keep dtd's priority order while filtering, so matches stay in
  # dtd's priority order instead of fuzzy-rank order (regression 2026-06-06).
  # --bind change:first: with --no-sort, fzf does not snap the cursor back to
  # the top as you type, so it can land on the last match. Force it to the
  # first (highest-priority) match on every query keystroke.
  # --delimiter/--with-nth: each row is "display<TAB>id". fzf shows only the
  # display (short name + estimates) and bindings get the hidden id via {2} to
  # resolve the real task. (fzf searches whatever is displayed; the short names
  # keep key codes/names so search stays usable.)
  # Full-screen (no --height) so the input block is bottom-justified to the
  # terminal like Claude. NB: under --layout=reverse-list fzf renders --footer
  # at the TOP and --header at the BOTTOM (just above the prompt). So the live
  # "<tasks left>   <keybindings>" line goes in --header (renders at the bottom,
  # where we want it) and the timer/worker-status goes in --footer (renders at
  # the top). transform-header refreshes the count on load/result; the action
  # bindings push their status into the footer via transform-footer.
  fzf_output=$(eval "$DTD_LIST_CMD" | fzf --prompt="> " --layout=reverse-list --no-sort --ansi \
      --info=inline-right \
      --input-border=horizontal \
      --header="$DTD_KEYS" \
      --bind 'load:transform-header(printf "%s left   %s" "$FZF_MATCH_COUNT" "$DTD_KEYS")' \
      --bind 'result:transform-header(printf "%s left   %s" "$FZF_MATCH_COUNT" "$DTD_KEYS")' \
      --delimiter=$'\t' --with-nth=1 \
      --bind "change:first" \
      --bind "enter:execute-silent($DTD_ENTER {2})+reload($DTD_RELOAD)+clear-query+transform-footer(cat $DTD_HDR)" \
      --bind "alt-enter:execute-silent($DTD_DONE {2})+reload($DTD_RELOAD)+clear-query+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-s:execute-silent($DTD_START {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-d:execute($DTD_DEFER {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-x:execute-silent($DTD_DELETE {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-p:execute-silent($DTD_SPLIT {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-v:execute($DTD_POINTS {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-a:execute-silent($DTD_AGENT {2})+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-k:execute-silent($DTD_SKIP {2})+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-z:execute-silent($DTD_UNDO)+reload($DTD_RELOAD)+transform-footer(cat $DTD_HDR)" \
      --bind "ctrl-r:execute-silent(python3 $DID_FAST --refresh-cache && cp $CACHE $DTD_CACHE_FILE)+reload($DTD_RELOAD)+transform-footer(echo '🔄 refreshed')" \
      --footer="$combined_hdr")

  task="$fzf_output"

  if [[ -z "$task" ]]; then
    break
  fi

  # Selected row is "display<TAB>id<TAB>canonical" — resolve via the id field.
  task=$(printf '%s' "$task" | cut -f2)
  task=$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "$task")

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
      # If a Toggl timer for this exact task is running, use its elapsed
      # minutes as the value instead of prompting. Stop it here to read the
      # duration; did-fast then sees the explicit number (clean + N) and the
      # already-stopped timer, so it won't re-stop or override.
      timer_mins=""
      cur=$(python3 "$TOGGL_CLI" current 2>/dev/null)
      if [[ "$cur" == Running:* ]]; then
        cur_desc=$(echo "$cur" | sed -E 's/^Running: [0-9]{2}:[0-9]{2}-running //; s/ *@.*//; s/ *\(running\).*//; s/ *\[id:[0-9]*\].*//; s/ *$//' | tr '[:upper:]' '[:lower:]')
        if [[ "$cur_desc" == "$clean_lower" ]]; then
          stop_out=$(python3 "$TOGGL_CLI" stop 2>/dev/null)
          # Reuse did-fast's duration grammar: (39m) (48min) (1h03m) (2h)
          timer_mins=$(echo "$stop_out" | python3 -c "import sys,re; o=sys.stdin.read(); m=re.search(r'\((?:(\d+)h)?(\d+)m(?:in)?\)',o); hm=re.search(r'\((\d+)h\)',o); print((int(m.group(1) or 0)*60+int(m.group(2))) if m else (int(hm.group(1))*60 if hm else ''))" 2>/dev/null)
        fi
      fi
      if [[ -n "$timer_mins" ]]; then
        clean="$clean $timer_mins"
        echo "▶ $clean (from timer)" > "$DTD_HDR"
      else
        REPLY="$clean "
        vared -p "→ " REPLY
        clean="$REPLY"
      fi
      ;;
  esac

  echo "$clean_for_filter" >> "$DTD_SESSION"
  echo "x" >> "$DTD_PUSHED"
  echo "$clean" >&3
done

exec 3>&-

session_count=$(grep -c . "$DTD_SESSION" 2>/dev/null)
session_count=${session_count:-0}
if [[ $session_count -gt 0 ]]; then
  echo ""
  echo "Waiting for $session_count tasks..."
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
  if [[ $logged -lt $session_count ]]; then
    echo "⚠ $logged/$session_count processed. Running remaining..."
    while IFS= read -r clean; do
      [[ -z "$clean" ]] && continue
      if ! grep -qi "$(echo "$clean" | head -c 20)" "$DTD_LOG" 2>/dev/null; then
        echo "  → /did $clean"
        python3 "$DID_FAST" "$clean" 2>&1 | jq -r '.results[]? | "  ✓ \(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null
      fi
    done < "$DTD_SESSION"
  fi
fi

# Note: DTD_SKIPPED is deliberately NOT removed — skips persist for the day
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "$DTD_START" "$DTD_ENTER" "$DTD_DONE" "$DTD_DEFER" "$DTD_DELETE" "$DTD_SPLIT" "$DTD_AGENT" "$DTD_SKIP" "$DTD_UNDO" "$DTD_CACHE_FILE" "$DTD_REMOVED" "$DTD_LIST" "$DTD_DONE_FILE" "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER"
