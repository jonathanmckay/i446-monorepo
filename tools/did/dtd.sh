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

# Per-launch id for all temp paths. dtd.sh is *sourced*, so $$ is the
# (long-lived) shell PID and is identical on every re-run. A bare $$ made
# re-entrant launches (suspend an open picker, re-source) collide on the same
# FIFO/temp files, and one run's `exec 3>&-` + cleanup killed another run's
# worker, after which completions written to the now-orphaned FIFO were
# silently dropped. The unique suffix isolates each launch; the $$ prefix lets
# the sweep below reclaim files left by dead shells.
DTD_ID="$$-$(date +%s)-$RANDOM"
# Reclaim temp files from dtd launches whose owning shell is gone. mtime >1h so
# a detached did-fast/defer child of a just-exited run is never cut off.
for _f in /tmp/dtd-<->-*(Nmh+1); do
  _pid=${${_f:t}#dtd-}; _pid=${_pid%%-*}
  [[ "$_pid" == <-> ]] && ! kill -0 "$_pid" 2>/dev/null && rm -f "$_f"
done
unset _f _pid

DTD_FIFO="/tmp/dtd-$DTD_ID.fifo"
DTD_HDR="/tmp/dtd-$DTD_ID.hdr"
DTD_LOG="/tmp/dtd-$DTD_ID.log"
# ctrl-z undo state: journal of reversible actions + in-flight counters
DTD_JOURNAL="/tmp/dtd-$DTD_ID.undo.jsonl"
DTD_PUSHED="/tmp/dtd-$DTD_ID.pushed"
DTD_PROCESSED="/tmp/dtd-$DTD_ID.processed"
DTD_SESSION="/tmp/dtd-$DTD_ID.session"
DTD_TIMER="/tmp/dtd-$DTD_ID.timer"
# fzf --listen port (written by the start binding) + the live-timer ticker that
# POSTs change-footer to it ~10x/s. See dtd-ticker.py.
DTD_PORT="/tmp/dtd-$DTD_ID.port"
DTD_HDRGEN="/tmp/dtd-$DTD_ID.hdrgen"
# Day tally ("<points> 分 · <done> done") for the header. A background loop pulls
# it from the Ix mobile server's /api/summary (the single cross-machine
# computation) into this file; the header generator reads the file (no network
# in the header hot path).
DTD_TALLY="/tmp/dtd-$DTD_ID.tally"
DTD_TICKER="$HOME/i446-monorepo/tools/did/dtd-ticker.py"

if [[ ! -f "$CACHE" ]]; then
  echo "No task cache found at $CACHE" >&2
  return 1 2>/dev/null || exit 1
fi

# Time-based freshness guard. The count-based guards below only fire when the
# cache is grossly wrong (<5 today / <30 due). They miss SINGLE-task staleness:
# a daily habit whose occurrence rolled to today stays hidden behind a cache
# built before the rollover, because the total count barely changes (regression
# 2026-06-21: 早餐 missing from dtd; cache was stale and no daemon refreshed it).
# Refresh whenever the cache 'updated' stamp is older than DTD_CACHE_MAX_AGE.
DTD_CACHE_MAX_AGE=${DTD_CACHE_MAX_AGE:-600}  # seconds
cache_age=$(python3 -c "
import json, sys, datetime as dt
try:
    u = json.load(open(sys.argv[1])).get('updated')
    print(int((dt.datetime.now() - dt.datetime.fromisoformat(u)).total_seconds()) if u else 10**9)
except Exception:
    print(10**9)
" "$CACHE" 2>/dev/null || echo 1000000000)
if [[ "$cache_age" -gt "$DTD_CACHE_MAX_AGE" ]]; then
  echo "Task cache is ${cache_age}s old (>${DTD_CACHE_MAX_AGE}s). Refreshing..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
fi

# Block-aware freshness. -1neon ritual cards roll over every 2h 地支 block (the
# daemon deletes the old block's cards and creates the new block's). A cache
# built in an EARLIER block is missing the current block's rituals even when it's
# younger than DTD_CACHE_MAX_AGE — e.g. a refresh at 13:58 then dtd opened at
# 14:05 is only 7min old but predates 未's rituals. Time-based staleness can't
# catch this; compare the cache's block to now's and refresh on a mismatch, so a
# new block always surfaces its -1n cards at the top (regression 2026-06-29).
stale_block=$(python3 -c "
import json, sys, datetime as dt
def blk(t): return (t.date().isoformat(), max(0, min(8, (t.hour - 4) // 2)))
try:
    u = json.load(open(sys.argv[1])).get('updated')
    print('1' if (not u or blk(dt.datetime.fromisoformat(u)) != blk(dt.datetime.now())) else '0')
except Exception:
    print('1')
" "$CACHE" 2>/dev/null || echo 1)
if [[ "$stale_block" == "1" ]]; then
  echo "Cache built in a previous block. Refreshing for new-block rituals..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
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
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "/tmp/dtd-$DTD_ID.start.sh" \
      "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER" \
      "/tmp/dtd-$DTD_ID.removed.ids"
mkfifo "$DTD_FIFO"
echo "ready" > "$DTD_HDR"
touch "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER"

(
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    # FIFO lines are "id<TAB>content" (enter.sh/done.sh send the fzf row id so
    # completion closes the EXACT selected task, not a name match — duplicate
    # names would otherwise complete the wrong instance). Bare content (no tab)
    # is still accepted for safety.
    if [[ "$line" == *$'\t'* ]]; then
      task_id="${line%%$'\t'*}"; task_clean="${line#*$'\t'}"
    else
      task_id=""; task_clean="$line"
    fi
    echo "⏳ $task_clean" > "$DTD_HDR"
    if [[ -n "$task_id" ]]; then
      result=$(python3 "$DID_FAST" --task-id "$task_id" "$task_clean" 2>>"$DTD_LOG.err")
    else
      result=$(python3 "$DID_FAST" "$task_clean" 2>>"$DTD_LOG.err")
    fi
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
DTD_CACHE_FILE="/tmp/dtd-$DTD_ID.cache.json"
DTD_REMOVED="/tmp/dtd-$DTD_ID.removed"
# View mode (ctrl-t toggles): empty = default priority order, 'project' = grouped
# by domain label. Per-session; the list generator reads it as its 8th arg.
DTD_VIEW="/tmp/dtd-$DTD_ID.view"
DTD_VIEWTOGGLE="/tmp/dtd-$DTD_ID.view-toggle.sh"
# Skips persist across dtd sessions for the duration of one day (stable
# path + date guard), unlike the other per-session temp files
DTD_SKIPPED="$STATE_DIR/dtd-skipped-today.txt"
if [[ -f "$DTD_SKIPPED.date" && "$(cat "$DTD_SKIPPED.date" 2>/dev/null)" != "$LOCAL_TODAY" ]]; then
  rm -f "$DTD_SKIPPED"
fi
echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
DTD_DONE_FILE="/tmp/dtd-$DTD_ID.done.json"
echo "$CACHE_SNAPSHOT" > "$DTD_CACHE_FILE"
touch "$DTD_REMOVED"
touch "$DTD_SKIPPED"

# --- Helper: format toggl current output into 1-line string ---
# The running-timer line is rendered by the background ticker (dtd-ticker.py),
# which polls Toggl itself and POSTs change-footer to fzf. No startup fetch or
# footer string is needed here anymore.

# --- Start script used by fzf enter/ctrl-s binding ---
DTD_START="/tmp/dtd-$DTD_ID.start.sh"
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
# Ritual (-1neon) cards carry the 😈 marker; their Toggl project comes from the
# ritual→domain map — the SAME source as their row color (keep in sync with
# RITUAL_DOMAIN in the list generator) — NOT tg-fast, whose shortcodes differ
# (e.g. -1ibx→m5x2 there but i9 here) and which can't resolve the 😈-prefixed
# name. The python also strips 😈 so the Toggl entry reads '-1g', not '😈 -1g'.
# Non-ritual tasks pass through unchanged and fall back to tg-fast below.
_rr=\$(python3 -c "
import sys
RITUAL_DOMAIN = {'-1ibx':'i9','-1l':'g245','-1t':'n156','سمش':'hcm'}
c = sys.argv[1]; bare = c.replace('😈','').strip(); proj=''
for tag,dom in RITUAL_DOMAIN.items():
    if bare == tag or tag in bare.split():
        proj = dom; break
print(bare); print(proj)
" "\$clean" 2>/dev/null)
clean=\$(printf '%s' "\$_rr" | sed -n 1p)
project=\$(printf '%s' "\$_rr" | sed -n 2p)
[ -z "\$project" ] && project=\$(python3 "\$TG_FAST" --resolve "\$clean" 2>/dev/null)
python3 "\$TOGGL_CLI" stop >/dev/null 2>&1
python3 "\$TOGGL_CLI" start "\$clean" \$project >/dev/null 2>&1
printf '%s\t%s\n' "\$clean" "\$(date +%s)" > "\$TIMER"
echo "▶ Started: \$clean → \$project" > "\$HDR"
STARTEOF
chmod +x "$DTD_START"

# --- Enter script: start selected task; if already timing, complete it ---
DTD_ENTER="/tmp/dtd-$DTD_ID.enter.sh"
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
  # Optimistic id-hide, RITUAL cards only (name carries 😈): they are name-EXEMPT
  # from the \$REMOVED hide (so a completed card can't suppress the next block's
  # same-named card), so they'd otherwise linger visible for the full ~7s
  # worker+refresh until the daemon overlay learns their id. Record the id (\$1 =
  # the {2} id field) so the list builder hides the completed card at once.
  # Gated to rituals: a normal task already hides instantly by name, and ctrl-z
  # undo (which reopens by clearing \$REMOVED) can't clear this id file — but undo
  # skips ritual entries anyway, so rituals never hit that path.
  [[ "\$clean" == *😈* ]] && echo "\$1" >> "\$REMOVED.ids"
  echo "x" >> "\$PUSHED"
  : > "\$TIMER"
  echo "⏳ completing: \$clean_for_filter" > "\$HDR"
  printf '%s\t%s\n' "\$1" "\$clean" > "\$FIFO"
else
  "\$START" "\$task"
fi
ENTEREOF
chmod +x "$DTD_ENTER"

# --- Complete-now script used by fzf alt-enter binding (ctrl+enter via the
# Ghostty keybind remap ctrl+enter -> ESC CR). Unlike enter, this never starts
# a timer: it always completes the selected task via the /did worker. ---
DTD_DONE="/tmp/dtd-$DTD_ID.done.sh"
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
# Reinstated (2026-07-03): cpap asks for a 1-3 sleep-quality score on completion.
# The number is appended so did-fast writes it to cpap's 0n column. Needs a tty,
# so alt-enter is bound with execute (not execute-silent). Blank input just
# completes with no score.
clean_lower=\$(echo "\$clean_for_filter" | tr '[:upper:]' '[:lower:]')
if [[ "\$clean_lower" == cpap && -r /dev/tty ]]; then
  printf "\n→ CPAP quality (1-3): " > /dev/tty
  read cpap_q < /dev/tty
  cpap_q=\${cpap_q// /}
  [[ -n "\$cpap_q" ]] && clean="\$clean \$cpap_q"
fi
echo "\$clean_for_filter" >> "\$SESSION"
echo "\$clean_for_filter" >> "\$REMOVED"
# Optimistic id-hide (see enter.sh), RITUAL cards only (name carries 😈): they
# are name-exempt from \$REMOVED, so record the id (\$1 = {2}) to hide the
# completed card instantly instead of after the ~7s worker+refresh.
[[ "\$clean" == *😈* ]] && echo "\$1" >> "\$REMOVED.ids"
echo "x" >> "\$PUSHED"
: > "\$TIMER"
echo "⏳ completing: \$clean_for_filter" > "\$HDR"
printf '%s\t%s\n' "\$1" "\$clean" > "\$FIFO"
DONEEOF
chmod +x "$DTD_DONE"

# --- Done ROUTER used by the fzf alt-enter (⌃⏎) binding via `transform` ---
# Only cpap needs a tty (for its 1-3 quality prompt), so route cpap → execute
# (which gives the DONE script a terminal) and every other task → execute-silent
# (flicker-free, as before). The router emits ONLY the execute/execute-silent
# action; the reload/clear-query/transform-header chain stays in the binding
# where $DTD_RELOAD/$DTD_HDRGEN are live. Baking the resolved id into the emitted
# action keeps the transform output free of fzf placeholders; task ids are
# alphanumeric, so no quoting is needed around \$_id.
DTD_DONE_ROUTER="/tmp/dtd-$DTD_ID.done-router.sh"
cat > "$DTD_DONE_ROUTER" << ROUTEREOF
#!/bin/zsh
_id="\$1"
_t=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$_id" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//' | tr '[:upper:]' '[:lower:]')
if [[ "\$_t" == cpap ]]; then
  printf 'execute(%s %s)' "$DTD_DONE" "\$_id"
else
  printf 'execute-silent(%s %s)' "$DTD_DONE" "\$_id"
fi
ROUTEREOF
chmod +x "$DTD_DONE_ROUTER"

# --- Defer script used by fzf ctrl-d binding ---
DTD_DEFER="/tmp/dtd-$DTD_ID.defer.sh"
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
#
# Hide by id (\$REMOVED.ids), NOT by name (\$REMOVED): defer already resolves
# the exact task via --id (collision-proof), but hiding by its annotation-
# stripped name suppressed EVERY task sharing that name — e.g. two identical
# "AoS (15) [15]" tasks (a recurring one + an unrelated one-off due later)
# both vanished from the list when only one was deferred (2026-07-13). The
# id-keyed \$REMOVED.ids file is the same mechanism enter.sh/done.sh already
# use for this exact reason (see the removed_ids check in dtd's list script).
echo "\$1" >> "\$REMOVED.ids"
echo "⏳ deferring (\$defer_label): \$clean" > "\$HDR"
echo "x" >> "$DTD_PUSHED"
(
  result=\$(python3 "\$DEFER_FAST" --id "\$1" "\$days" 2>/dev/null)
  ok=\$(echo "\$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'→ {d[\"target_date\"]} [{d[\"claimed_points\"]}] today / [{d[\"remaining_points\"]}] later')" 2>/dev/null)
  if [[ -n "\$ok" ]]; then
    # Journal for ctrl-z undo
    echo "\$result" | python3 "$UNDO_FAST" --journal-defer "$DTD_JOURNAL" "\$clean" 2>/dev/null
    echo "⏭ \$clean \$ok" > "\$HDR"
  else
    # Roll back the optimistic hide so the task reappears on next reload
    grep -v -x -F -- "\$1" "\$REMOVED.ids" > "\$REMOVED.ids.tmp" 2>/dev/null
    mv "\$REMOVED.ids.tmp" "\$REMOVED.ids"
    echo "? defer failed: \$clean (restored to list)" > "\$HDR"
  fi
  echo "x" >> "$DTD_PROCESSED"
) >/dev/null 2>&1 &!
# Reset any mouse-tracking mode a child enabled — leaked SGR motion
# sequences type themselves into fzf's query (bug 2026-07-05).
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l' > /dev/tty 2>/dev/null || true

DEFEREOF
chmod +x "$DTD_DEFER"

# --- Change-points script used by fzf ctrl-v binding ---
# Prompts for a new [N] value (needs a tty, so the binding uses execute(), not
# execute-silent), updates the task in Todoist, and patches the snapshot cache
# ($DTD_CACHE_FILE) so the new value shows on reload. Todoist is the source of
# truth; the live cache catches up on the next refresh.
DTD_POINTS="/tmp/dtd-$DTD_ID.points.sh"
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
out=\$(python3 "\$POINTS_FAST" --id "\$1" "\$newpts" "$DTD_CACHE_FILE" 2>/dev/null)
echo "\${out:-✗ points update failed}" > "\$HDR"
# Reset any mouse-tracking mode a child enabled — leaked SGR motion
# sequences type themselves into fzf's query (bug 2026-07-05).
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l' > /dev/tty 2>/dev/null || true

POINTSEOF
chmod +x "$DTD_POINTS"

# --- Unified edit script used by fzf ctrl-g binding ---
# One prompt edits name + domain + points from a single line (needs a tty, so
# the binding uses execute(), not execute-silent): @code → domain, a standalone
# number → points, the rest → new name. Patches the snapshot cache
# ($DTD_CACHE_FILE) so the row updates on reload.
DTD_EDIT="/tmp/dtd-$DTD_ID.edit.sh"
cat > "$DTD_EDIT" << EDITEOF
#!/bin/zsh
EDIT_FAST="\$HOME/i446-monorepo/tools/did/edit-fast.py"
HDR="$DTD_HDR"
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
query="\$task"
if [[ "\$clean" == *"…"* ]]; then
  clean="\${clean%%…*}"
  query="\$clean"
fi
printf "\nEdit: %s\n(text=rename · @code=domain · N=points)> " "\$clean" > /dev/tty
read edits < /dev/tty
if [[ -z "\${edits// /}" ]]; then
  echo "edit cancelled" > "\$HDR"
  exit 0
fi
out=\$(python3 "\$EDIT_FAST" --id "\$1" "\$edits" "$DTD_CACHE_FILE" 2>/dev/null)
echo "\${out:-✗ edit failed}" > "\$HDR"
# Reset any mouse-tracking mode a child enabled — leaked SGR motion
# sequences type themselves into fzf's query (bug 2026-07-05).
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l' > /dev/tty 2>/dev/null || true

EDITEOF
chmod +x "$DTD_EDIT"

# --- List generation script (reloadable by fzf) ---
DTD_LIST="/tmp/dtd-$DTD_ID.list.sh"
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
        _done_raw = json.load(f)
    # completed-today.json is {date, names, points} — the completed habit names
    # live in .names, NOT the top-level keys. The old code kept the whole dict, so
    # 'clean in completed' tested membership against {date,names,points} and never
    # matched, leaving DONE habits (0t, etc.) in the list. Extract the names list,
    # lowercased, gated to today's date so a stale file can't hide live tasks.
    if isinstance(_done_raw, dict):
        _gated = _done_raw.get('date') == today
        completed = ([n.lower() for n in _done_raw.get('names', [])] if _gated else [])
        _ids_map = (_done_raw.get('ids') or {}) if _gated else {}
    else:
        completed = [str(n).lower() for n in _done_raw]
        _ids_map = {}
except:
    completed = []; _ids_map = {}
# Tasks closed today, keyed by id. A cache task whose id is here is definitively
# done and hidden regardless of its name. Names that are id-backed must hide by
# id ONLY: hiding them by name too would suppress a different open task sharing
# the same annotation-stripped name (regression 2026-06-26: 'stats').
completed_ids = {str(v) for v in _ids_map.values()}
id_backed_names = {str(k).lower() for k in _ids_map.keys()}
# Name-only completions (habits, legacy records, in-session completions) have no
# id, so they still hide by name as before.
name_only_completed = [n for n in completed if n not in id_backed_names]

# Load removed items
try:
    with open(removed_file) as f:
        removed = [l.strip().lower() for l in f if l.strip()]
except: removed = []
# Optimistically-removed Todoist ids (written by enter.sh/done.sh on completion).
# id-based so it hides a just-completed RITUAL card immediately — rituals are
# exempt from the name-based removed-hide, so without this they linger for the
# whole ~7s worker+refresh until the daemon overlay learns the id.
try:
    with open(removed_file + '.ids') as f:
        removed_ids = {l.strip() for l in f if l.strip()}
except: removed_ids = set()

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
    'hcmr': '\033[38;2;189;166;255m',  '家':   '\033[38;2;0;184;212m',
    '睡觉': '\033[38;2;102;102;102m',
}
RESET = '\033[0m'

# -1neon ritual cards carry only the '-1neon' label (no domain), so they render
# colorless. Map each ritual tag to its natural domain color (user 2026-07-07).
RITUAL_DOMAIN = {'-1ibx': 'i9', '-1l': 'g245', '-1t': 'n156', 'سمش': 'hcm'}

def prank(p):
    return -(p or 1)

def strip_ann(s):
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}', '', s)).strip()

# Right-justify trailing (N)/[N]/{N} estimates into a column. target = cols - 8
# pulls the estimate column ~5 cols in from the edge (vs the old cols - 3): it
# keeps fzf's pointer/gutter (2) + scrollbar (1) clear AND adds a 5-col right
# margin so estimates stay visible in a narrow pane and the name→estimate gap
# shrinks. If there is no room (long/truncated rows), leave inline.
_EST_TOK = r'(?:\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\})'
_EST_TAIL = re.compile(r'(\s*(?:' + _EST_TOK + r'\s*)+)$')
def rjust_est(s, cols):
    m = _EST_TAIL.search(s)
    if not m:
        return s
    est = re.sub(r'\s+', ' ', m.group(1).strip())
    # Canonicalize estimate order so the column reads the same regardless of how
    # the source task wrote them: (time) then [value] then {bonus}. Some tasks are
    # stored '[20] (30)', others '(30) [20]' — both render as '(30) [20]'.
    _toks = re.findall(_EST_TOK, est)
    if len(_toks) > 1:
        _rank = {'(': 0, '[': 1, '{': 2}
        _toks.sort(key=lambda tk: _rank.get(tk[0], 9))
        est = ' '.join(_toks)
    head = s[:m.start()].rstrip()
    pad = (cols - 8) - len(head) - len(est)
    if pad < 2:
        return (head + ' ' + est) if head else est
    return head + ' ' * pad + est

# Build task list in priority order
import datetime as _dt
_tomorrow = (_dt.date.fromisoformat(today) + _dt.timedelta(days=1)).isoformat()
# Fixed list order (user spec 2026-06-29): -1n → -1g → 0n → 1n → 0g, then any
# critical-path / uncategorized tasks. Daily habits (0neon/夜neon) recur every
# day; if one over-advances (completed twice, due drifts +1) a strict due<=today
# bound hides it (regression 2026-06-27: 0t due tomorrow vanished), so daily
# sections bound to tomorrow; weekly (1neon) / critical-path keep the today bound.
def _sec(key, bound):
    return [t for t in d.get(key, []) if isinstance(t, dict)
            and t.get('due') and t['due'] <= bound]

today_tasks = [t for t in d.get('today', []) if isinstance(t, dict)
               and t.get('due') and t['due'] <= today]
def _has(t, lab): return lab in t.get('labels', [])

# -1n: block-ritual cards (سمش / -1g / -1ibx) — the current 2h block's quick rituals.
rituals = [t for t in today_tasks if _has(t, '-1neon')]
# -1g: this block's goals.
neg1g = [t for t in today_tasks if _has(t, '#-1g') and not _has(t, '-1neon')]
# 0n: daily habits (0neon + evening 夜neon). 1n: weekly habits.
zeroneon = _sec('0neon', _tomorrow) + _sec('夜neon', _tomorrow)
oneneon = _sec('1neon', today)
# 0g: today's daily goals.
zerog = [t for t in today_tasks if _has(t, '#0g') and not _has(t, '-1neon') and not _has(t, '#-1g')]
# critical-path + any other uncategorized today task, by priority, at the end.
_placed = lambda t: _has(t, '-1neon') or _has(t, '#-1g') or _has(t, '#0g')
critical = _sec('关键路径', today)
rest = sorted([t for t in today_tasks if not _placed(t)],
              key=lambda t: prank(t.get('priority')))
all_tasks = rituals + neg1g + zeroneon + oneneon + zerog + critical + rest

# Deduplicate by id
seen = set()
unique = []
for t in all_tasks:
    if t.get('content') and t['id'] not in seen:
        seen.add(t['id'])
        unique.append(t)

# View mode (8th arg, written by the ctrl-t toggle). 'project' groups the list
# by domain label instead of the default priority tiers.
view = ''
try:
    view = open(sys.argv[8]).read().strip() if len(sys.argv) > 8 else ''
except: view = ''

def domain_of(t):
    for lbl in t.get('labels', []):
        if lbl in COLORS:
            return lbl
    return 'zzz'   # unlabelled tasks sort to the end

if view == 'project':
    unique.sort(key=lambda t: (domain_of(t), prank(t.get('priority'))))

DIM = '\033[2m'
running_lines = []
normal_lines = []
skipped_lines = []

for t in unique:
    raw = t['content']
    clean = strip_ann(raw).lower()
    prefix = clean.split(' - ')[0]
    # Hide by id first: definitive, and immune to same-name collisions.
    if t.get('id') is not None and str(t['id']) in completed_ids:
        continue
    # Optimistic id-hide: a just-completed card (esp. a name-exempt ritual)
    # whose id was recorded by enter.sh/done.sh — hide it at once, not after
    # the ~7s worker+refresh.
    if t.get('id') is not None and str(t['id']) in removed_ids:
        continue
    # -1neon ritual cards (😈 سمش / -1g / -1ibx / -1t / -1l) RECUR every 2h block
    # with IDENTICAL names. Name-based hiding therefore suppresses the current
    # block's fresh card whenever an earlier block's same-named card was
    # completed/skipped today (bug 2026-07-10: prayer + -1g vanished all day
    # after being done once). Hide rituals by id ONLY (the check above); the
    # name/removed pass below is for one-off tasks whose names are unique.
    is_ritual = '-1neon' in t.get('labels', [])
    # removed entries may be truncated prefixes (fzf middle-truncates long
    # names in the defer/split bindings) — match by startswith, not equality
    # (regression 2026-06-06: split task stayed in the list). Name hide uses
    # name_only_completed so an id-backed completion can't suppress a different
    # open task with the same name (regression 2026-06-26: 'stats').
    if not is_ritual and (clean in name_only_completed or prefix in name_only_completed
            or any(clean == r or (r and clean.startswith(r)) for r in removed)):
        continue

    is_skipped = clean in skipped

    # Find color from labels. Ritual cards (label '-1neon') have no domain
    # label — resolve their color from the ritual tag in the name instead.
    color = ''
    if '-1neon' in t.get('labels', []):
        bare = clean.replace('😈', '').strip()
        for tag, dom in RITUAL_DOMAIN.items():
            if bare == tag or tag in bare.split():
                color = COLORS[dom]
                break
    if not color:
        for lbl in t.get('labels', []):
            if lbl in COLORS:
                color = COLORS[lbl]
                break

    # Recurring indicator
    recurring = t.get('recurring', False)

    # Display the cached short (Haiku) name when present so long m5x2-style
    # tasks keep their (N)/[N] estimates visible; fall back to full content.
    display = t.get('short') or raw

    # Middle-truncate if needed (fallback; short names usually fit). cols - 7
    # keeps the whole row ~5 cols thinner, matching the estimate margin above.
    line = display
    if len(line) > cols - 7:
        # Find trailing annotations
        tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][ ]*[\(\[\{]\d*[\)\]\}].*$', line)
        if not tail_m:
            tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][^()\[\]{}]*$', line)
        tail = tail_m.group() if tail_m else line[-15:]
        head_len = max(10, cols - len(tail) - 7)
        line = line[:head_len] + '…' + tail

    # Hidden field 2 carries the task id so bindings resolve the real task.
    # fzf shows field 1 only (--with-nth=1); search therefore matches the
    # visible short name (Haiku keeps key codes/names, so this stays usable).
    sfx = '\t' + str(t.get('id', ''))

    repeat = '↻ ' if recurring else ''
    # In project view, tag each row with its domain so groups are unmistakable
    # (color already encodes it, but adjacent palettes can blur).
    dom_tag = ''
    if view == 'project':
        _dd = domain_of(t)
        if _dd != 'zzz':
            dom_tag = _dd + ' '
    is_running = bool(running_clean and clean == running_clean)
    if is_running:
        elapsed = max(0, int((time.time() - running_started) // 60)) if running_started else 0
        prefix = f'▶ {elapsed}m · {dom_tag}'
    else:
        prefix = repeat + dom_tag
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
" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8"
LISTEOF
chmod +x "$DTD_LIST"

# View-cycle script (ctrl-t): advance the view-state file to the next view,
# wrapping around. Add a view by appending to `views` here and handling its
# name in the list generator. The subsequent reload re-runs the generator.
cat > "$DTD_VIEWTOGGLE" << 'VTEOF'
#!/bin/zsh
VIEW="PLACEHOLDER_VIEW"
HDR="PLACEHOLDER_HDR"
views=(default project)          # cycle order; append new views here
typeset -A labels
labels=(default "default" project "by project")
cur="$(cat "$VIEW" 2>/dev/null)"
[[ -z "$cur" ]] && cur=default
next="${views[1]}"               # default wrap target
for i in {1..$#views}; do
  if [[ "${views[$i]}" == "$cur" ]]; then
    next="${views[$(( i % $#views + 1 ))]}"
    break
  fi
done
echo "$next" > "$VIEW"
echo "↹ view: ${labels[$next]:-$next}" > "$HDR"
VTEOF
sed -i '' "s|PLACEHOLDER_VIEW|$DTD_VIEW|g; s|PLACEHOLDER_HDR|$DTD_HDR|g" "$DTD_VIEWTOGGLE"
chmod +x "$DTD_VIEWTOGGLE"
echo default > "$DTD_VIEW"   # start in default view each session

# --- Skip script used by fzf ctrl-k binding ---
DTD_SKIP="/tmp/dtd-$DTD_ID.skip.sh"
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
DTD_DELETE="/tmp/dtd-$DTD_ID.delete.sh"
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
# fzf field 2 (\$1) IS the Todoist id — override any name-based match so a
# duplicate name can never delete the wrong row (id-based, 2026-07-12).
tid="\$1"
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
DTD_SPLIT="/tmp/dtd-$DTD_ID.split.sh"
cat > "$DTD_SPLIT" << 'SPLITEOF'
#!/bin/zsh
# Split a task: claim partial points today, defer the rest to tomorrow.
# Three dialogs: points, what you did, what remains.
# All Todoist/Neon writes done inline via Python.

HDR="PLACEHOLDER_HDR"
REMOVED="PLACEHOLDER_REMOVED"
CACHE_FILE="PLACEHOLDER_CACHE"
DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"

task_id="$1"
task=$(python3 "$HOME/i446-monorepo/tools/did/dtd_resolve.py" "$CACHE_FILE" "$1")  # id -> canonical content (display/clean only)

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
task_id = sys.argv[9]

remaining_pts = max(0, total - pts_today) if total > 0 else 0

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f'{BASE}{path}', data=data, method=method, headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

# Fetch the EXACT task by the id dtd passed us. The old path re-searched
# 'today | overdue' and substring-matched on the (possibly munged) display
# name, which silently failed after all three dialogs for any dtd task outside
# that window (weekly/1neon habits, future-due goals) or whose name didn't
# substring-match — the user answered every prompt and got nothing (regression
# 2026-06-26). Fetching by id is scope-independent and unambiguous.
try:
    task = api('GET', f'/tasks/{task_id}')
except Exception:
    task = None
if not task or not task.get('id'):
    with open(hdr_file, 'w') as f: f.write('? split: task not found')
    sys.exit(1)
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
# name and scattering the points (regression 2026-06-06: a name like
# 'Rev on ground transit. Buy nightshade, 2' logged its 10 points as '2').
# NB: NO bare double quotes in this comment — they terminate the enclosing
# python -c string and silently break the whole split (see line ~551).
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
" "$clean" "$pts_today" "${total:-?}" "${done_desc:-}" "${remaining_desc:-}" "${duration:-}" "$HDR" "$REMOVED" "$task_id"

SPLITEOF
# Substitute placeholder paths
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_REMOVED|$DTD_REMOVED|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g; s|PLACEHOLDER_JOURNAL|$DTD_JOURNAL|g" "$DTD_SPLIT"
chmod +x "$DTD_SPLIT"

# --- Agent script used by fzf ctrl-a binding ---
DTD_AGENT="/tmp/dtd-$DTD_ID.agent.sh"
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
DTD_UNDO="/tmp/dtd-$DTD_ID.undo.sh"
cat > "$DTD_UNDO" << UNDOEOF
#!/bin/zsh
HDR="$DTD_HDR"
# Don't drop a ctrl-z that lands mid-completion. If a task is still in flight
# (pushed > processed) the journal entry we need to reverse hasn't been written
# yet, so QUEUE the undo: poll for the worker to settle (up to ~5s, 100ms steps)
# instead of bailing immediately. The reload + transform-header in the fzf
# binding still fire after this script returns, so the undone task reappears.
for _ in {1..50}; do
  pushed=\$(wc -l < "$DTD_PUSHED" 2>/dev/null || echo 0)
  processed=\$(wc -l < "$DTD_PROCESSED" 2>/dev/null || echo 0)
  (( pushed <= processed )) && break
  echo "⏳ \$((pushed - processed)) task(s) processing — undo queued…" > "\$HDR"
  sleep 0.1
done
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

# Keybinding hints shown on the status line. Exported so the transform-header
# bindings (which run in fzf's child shell) can read it. With --header-first the
# header renders BELOW the prompt (Claude-style status line): the live match
# count ($FZF_MATCH_COUNT), any worker status ($DTD_HDR), and these keys.
export DTD_KEYS="enter: start/complete | ⌃⏎: done | ctrl-s: timer | ctrl-d: defer | ctrl-p: split | ctrl-v: pts | ctrl-g: edit | ctrl-a: agent | ctrl-k: skip | ctrl-x: del | ctrl-z: undo | ctrl-r: refresh | ctrl-t: view"

# Status-line generator (the header, below the prompt): "<N left>   <worker
# status>   <keys>". fzf exports $FZF_MATCH_COUNT to this child; $DTD_KEYS is
# exported above; the worker-status file path is baked in here. Used by the
# load/result binds and after every action so worker confirmations persist.
cat > "$DTD_HDRGEN" <<HDRGENEOF
#!/bin/zsh
ws=\$(cat "$DTD_HDR" 2>/dev/null | tr '\n' ' ')
tally=\$(cat "$DTD_TALLY" 2>/dev/null | tr '\n' ' ')
if [ -n "\$tally" ]; then
  printf '%s   %s left   %s   %s' "\$tally" "\${FZF_MATCH_COUNT:-0}" "\$ws" "\$DTD_KEYS"
else
  printf '%s left   %s   %s' "\${FZF_MATCH_COUNT:-0}" "\$ws" "\$DTD_KEYS"
fi
HDRGENEOF
chmod +x "$DTD_HDRGEN"

# ctrl-d prompts for the defer target (N days / date) on the tty. Only set
# here so the extracted script stays non-interactive for tests and scripts.
export DTD_DEFER_PROMPT=1

# Toggl 429 fast-fail for everything dtd launches (the execute-silent action
# scripts AND the ticker, which inherit this env). The action scripts call Toggl
# synchronously, so the default backoff (~60s) freezes the whole picker on a
# rate-limit. Cap it at one quick retry / 1s so a 429 degrades gracefully (timer
# read/switch is skipped) instead of hanging the UI mid-keystroke.
export TOGGL_MAX_429_RETRIES=2
export TOGGL_MAX_429_DELAY=1

# Live-timer ticker: owns the footer (top line), POSTing change-footer ~10x/s to
# the fzf --listen port the start binding writes to $DTD_PORT. Best-effort and
# self-terminating (exits when $DTD_PORT vanishes at cleanup).
rm -f "$DTD_PORT"
python3 "$DTD_TICKER" "$DTD_PORT" "$DTD_TIMER" &>/dev/null &
TICKER_PID=$!

# Day-tally refresher: pull the single Ix computation (points 分 + tasks done)
# every 15s into $DTD_TALLY so the header shows today's real totals. It reads
# from the always-on Ix mobile server (ix:5560/api/summary) rather than
# recomputing here — the Excel daemon is on Ix and this machine's cache lags.
# Best-effort; self-exits when the session sentinel is gone.
: > "$DTD_TALLY"
(
  while [[ -f "$DTD_SESSION" ]]; do
    t=$(curl -fsS --max-time 3 http://ix:5560/api/summary 2>/dev/null \
        | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    if d.get("ok"): print("%s 分 · %s done" % (d.get("points",0), d.get("done",0)))
except Exception: pass' 2>/dev/null)
    [[ -n "$t" ]] && printf '%s' "$t" > "$DTD_TALLY"
    sleep 15
  done
) &>/dev/null &
TALLY_PID=$!

# Auto-reload watcher: when the LIVE task cache ($CACHE) changes on its own —
# e.g. a /-1g or /0g add elsewhere runs `did-fast --refresh-cache` — pull it
# into the frozen snapshot and POST a reload to fzf, so the open picker shows
# the new task without a manual ctrl-r. The optimistic hide/done overlays
# (DTD_REMOVED/DTD_DONE_FILE) are passed to the list cmd, so refreshing the
# snapshot never un-hides a just-completed task. Mirrors the ticker: best-
# effort, self-exits when $DTD_PORT vanishes at cleanup. Fires only on an
# actual mtime advance (cache changes ~once per add/refresh), so it stays quiet
# during normal navigation. The reload cmd matches DTD_RELOAD built in the UI
# loop (same constant args).
# NOTE: the reload cmd and completed-today overlay are rebuilt INSIDE the loop
# with a freshly-computed date ($watch_today), NOT the startup $LOCAL_TODAY. The
# UI loop's fzf call blocks, so its own midnight-rollover code never runs while
# dtd sits open; an idle-open dtd that crosses midnight must still filter to the
# NEW day. Baking in the frozen startup date showed yesterday's tasks even after
# the cache refreshed with today's (bug 2026-07-02: "new day, but dtd didn't
# refresh").
(
  # Close the inherited copy of fd 3 (the persistent FIFO writer opened by
  # `exec 3>"$DTD_FIFO"` above). This subshell is forked AFTER that exec, so
  # it inherits fd 3 by default and, left open, keeps its own independent
  # write-end on the FIFO for as long as the watcher runs. On exit the main
  # loop does `exec 3>&-` to close ITS copy and signal EOF to the background
  # worker's `read < "$DTD_FIFO"` loop — but the watcher's inherited copy
  # keeps the FIFO's writer count above zero, so the worker never sees EOF and
  # blocks on read() forever, the "Waiting for N tasks..." exit-time
  # `kill -0 $WORKER_PID` loop spins forever (kill "$WATCHER_PID" only runs
  # AFTER that loop), and dtd hangs on exit whenever the session completed at
  # least one task (regression 2026-07-11: dtd never returns to the prompt).
  exec 3>&-
  # Wait for fzf's start binding to publish the listen port before entering the
  # loop. The port file is created ~100ms+ AFTER fzf boots, but this subshell
  # spawns before fzf — checking `-f $DTD_PORT` immediately made the loop
  # condition false and the watcher EXITED AT BIRTH, every session (bug
  # 2026-07-07: block turnover never auto-refreshed; the session snapshot
  # stayed frozen at startup mtime). Mirrors the ticker's port wait; if fzf
  # never publishes (died at boot), fall through — the while sees no file and
  # exits, same as before.
  for _w in {1..150}; do
    [[ -f "$DTD_PORT" ]] && break
    sleep 0.2
  done
  last_m=$(stat -f %m "$CACHE" 2>/dev/null)
  last_blk="$(date +%Y%m%d)-$(( ( $(date +%H) - 4 ) / 2 ))"
  last_day="$(date +%Y-%m-%d)"
  while [[ -f "$DTD_PORT" ]]; do
    sleep 2
    watch_today="$(date +%Y-%m-%d)"
    # Day rollover while dtd sits open: reset the per-day overlays (mirrors the
    # UI-loop rollover, which can't run behind the blocking fzf) and pull today's
    # tasks so the new day's recurring set surfaces without a relaunch.
    if [[ "$watch_today" != "$last_day" ]]; then
      last_day="$watch_today"
      : > "$DTD_SESSION"; : > "$DTD_JOURNAL"; : > "$DTD_SKIPPED"
      echo "$watch_today" > "$DTD_SKIPPED.date"
      ( python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1 ) &
    fi
    # New 2h 地支 block: the daemon rolls the -1neon ritual cards at the boundary.
    # Refresh the local cache so an idle-open dtd surfaces the new block's -1n
    # cards without a relaunch (regression 2026-06-29). The daemon does its
    # scoring/reconcile FIRST and only then deletes+creates the cards, so they
    # don't exist until ~boundary+25-30s (later still on a Todoist 503 retry). A
    # single +15s refresh raced ahead of that and never retried, so the new
    # block's -1n cards never appeared until a manual ctrl-r (bug 2026-07-01).
    # Fire STAGGERED refreshes across the first ~90s instead (cumulative +20/+45/
    # +90s); each bumps the cache mtime and trips the reload below, so whichever
    # lands after the daemon finishes surfaces the cards. Backgrounded so the
    # mtime poll keeps running.
    cur_blk="$(date +%Y%m%d)-$(( ( $(date +%H) - 4 ) / 2 ))"
    if [[ "$cur_blk" != "$last_blk" ]]; then
      last_blk="$cur_blk"
      # Cumulative +20/45/90/150/270s. did-fast now unions the -1neon cards in
      # via the direct label endpoint (fresh in seconds; the today|overdue FILTER
      # query lags minutes), so the +45s shot usually catches the daemon's
      # boundary+30s card creation. The later shots backstop a slow daemon
      # (Todoist 503 retries) so the new block's -1n cards still surface in-
      # session, not only on the next ~3min did-refresh-cache daemon cycle.
      ( for _s in 20 25 45 60 120; do sleep "$_s"; python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1; done ) &
    fi
    cur_m=$(stat -f %m "$CACHE" 2>/dev/null)
    [[ -z "$cur_m" || "$cur_m" == "$last_m" ]] && continue
    last_m="$cur_m"
    cp "$CACHE" "$DTD_CACHE_FILE" 2>/dev/null
    # Rebuild the completed-today overlay from the LIVE $DONE before reloading.
    # The UI loop only regenerates $DTD_DONE_FILE when it cycles (on user
    # interaction); while dtd sits idle that overlay goes stale. Without this, a
    # task just completed in /inbound (which writes the live $DONE AND refreshes
    # the cache, tripping this watcher) reloads against a stale overlay and stays
    # visible if the refreshed cache still carries it (Todoist "today | overdue"
    # propagation lag) — e.g. a -1g ritual completed in inbound not coming off
    # dtd. Mirrors the UI-loop build (see below); atomic write so a concurrent
    # reload never reads a torn file.
    _dn=$(jq -c --arg t "$watch_today" 'if .date == $t then [.names[] | ascii_downcase] else [] end' "$DONE" 2>/dev/null || echo '[]')
    _di=$(jq -c --arg t "$watch_today" 'if .date == $t then (.ids // {}) else {} end' "$DONE" 2>/dev/null || echo '{}')
    _se=$(jq -c -R -s 'split("\n") | map(select(. != ""))' < "$DTD_SESSION" 2>/dev/null || echo '[]')
    _ac=$(echo "[$_dn, $_se]" | jq -c 'add | map(ascii_downcase)' 2>/dev/null || echo '[]')
    if jq -cn --arg t "$watch_today" --argjson names "$_ac" --argjson ids "$_di" \
         '{date: $t, names: $names, ids: $ids}' > "$DTD_DONE_FILE.tmp" 2>/dev/null; then
      mv "$DTD_DONE_FILE.tmp" "$DTD_DONE_FILE"
    fi
    port=$(cat "$DTD_PORT" 2>/dev/null)
    [[ -z "$port" ]] && continue
    # Rebuild with the freshly-computed date so a post-midnight reload filters to
    # today, not the frozen startup $LOCAL_TODAY. Mirrors DTD_RELOAD in the UI loop.
    watch_reload="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$watch_today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW'"
    if [[ -n "$FZF_API_KEY" ]]; then
      curl -s -H "X-API-Key: $FZF_API_KEY" -XPOST "localhost:$port" --data "reload($watch_reload)" >/dev/null 2>&1
    else
      curl -s -XPOST "localhost:$port" --data "reload($watch_reload)" >/dev/null 2>&1
    fi
  done
) &>/dev/null &
WATCHER_PID=$!

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
  # name -> Todoist id map for tasks closed today. Lets the list builder hide a
  # completed task by its id (collision-proof) instead of only by name, so one
  # task's completion never suppresses a different open task with the same name.
  DONE_IDS=$(jq -c --arg today "$LOCAL_TODAY" \
    'if .date == $today then (.ids // {}) else {} end' "$DONE" 2>/dev/null || echo '{}')

  # The running-timer line is owned by the background ticker (footer, top line);
  # worker status now lives in the header. No footer string is built here.
  session_exclude=$(jq -c -R -s 'split("\n") | map(select(. != ""))' < "$DTD_SESSION")
  all_completed=$(echo "[$DONE_NAMES, $session_exclude]" | jq -c 'add | map(ascii_downcase)')

  # Write {date,names,ids} so the list builder can hide by id AND name. Session
  # completions contribute names only (no id); those still hide by name.
  jq -cn --arg today "$LOCAL_TODAY" --argjson names "$all_completed" --argjson ids "$DONE_IDS" \
    '{date: $today, names: $names, ids: $ids}' > "$DTD_DONE_FILE"

  # Generate task list via reloadable script (supports colors + removal)
  # Pass done file path instead of JSON string to avoid quoting issues
  # INVARIANT (see top of file): reloads read the FROZEN startup snapshot, never
  # the live cache. This prevents tasks vanishing mid-session when an external
  # process (morning routine, /todo, other terminals) rewrites the live cache
  # after startup. Use ctrl-r to explicitly pull external changes.
  DTD_LIST_CMD="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$LOCAL_TODAY' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW'"
  DTD_RELOAD="${DTD_LIST_CMD}"
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
  # terminal like Claude. Under --layout=reverse-list, --footer renders at the
  # TOP and --header at the BOTTOM; with --header-first the header renders just
  # BELOW the prompt (Claude-style status line). So:
  #   footer (top)         = live running timer, owned by the background ticker
  #                          via --listen/change-footer (not built here).
  #   header (below input) = "<N left>   <worker status>   <keys>", produced by
  #                          $DTD_HDRGEN on load/result and after every action so
  #                          worker confirmations persist alongside the count.
  # The start binding publishes fzf's --listen port for the ticker to POST to.
  # --no-mouse + the mode reset below: SGR mouse-motion sequences (ESC[<34;x;yM)
  # were leaking into the query as literal text (bug 2026-07-05). fzf only
  # parses the click/scroll events it subscribes to; motion events — forwarded
  # by cmux once ANY mouse mode is on, or left enabled (1002/1003) by a child
  # program from an execute() binding — fall through the parser into the input
  # box. dtd is keyboard-driven, so disable fzf's mouse subscription entirely
  # and defensively turn off every stray tracking mode before each launch.
  printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l' > /dev/tty 2>/dev/null || true
  fzf_output=$(eval "$DTD_LIST_CMD" | fzf --prompt="> " --layout=reverse-list --no-sort --ansi \
      --no-mouse \
      --info=inline-right \
      --input-border=horizontal \
      --listen --header-first \
      --header="$DTD_KEYS" \
      --bind "start:execute-silent(echo \$FZF_PORT > $DTD_PORT)" \
      --bind "load:transform-header($DTD_HDRGEN)" \
      --bind "result:transform-header($DTD_HDRGEN)" \
      --delimiter=$'\t' --with-nth=1 \
      --bind "change:first" \
      --bind "enter:execute-silent($DTD_ENTER {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "alt-enter:transform($DTD_DONE_ROUTER {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-s:execute-silent($DTD_START {2})+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-d:execute($DTD_DEFER {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-x:execute-silent($DTD_DELETE {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-p:execute-silent($DTD_SPLIT {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-v:execute($DTD_POINTS {2})+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-g:execute($DTD_EDIT {2})+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-a:execute-silent($DTD_AGENT {2})+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-k:execute-silent($DTD_SKIP {2})+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-z:execute-silent($DTD_UNDO)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-r:execute-silent(python3 $DID_FAST --refresh-cache && cp $CACHE $DTD_CACHE_FILE && echo '🔄 refreshed' > $DTD_HDR)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-t:execute-silent($DTD_VIEWTOGGLE)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)")

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

# Stop the live-timer ticker + cache watcher (both also self-exit once
# $DTD_PORT is gone, below).
kill "$TICKER_PID" 2>/dev/null
kill "$WATCHER_PID" 2>/dev/null
kill "$TALLY_PID" 2>/dev/null
# Note: DTD_SKIPPED is deliberately NOT removed — skips persist for the day
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "$DTD_START" "$DTD_ENTER" "$DTD_DONE" "$DTD_DONE_ROUTER" "$DTD_DEFER" "$DTD_DELETE" "$DTD_SPLIT" "$DTD_AGENT" "$DTD_SKIP" "$DTD_UNDO" "$DTD_CACHE_FILE" "$DTD_REMOVED" "$DTD_REMOVED.ids" "$DTD_LIST" "$DTD_DONE_FILE" "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_SESSION" "$DTD_TIMER" "$DTD_PORT" "$DTD_HDRGEN" "$DTD_TALLY" "$DTD_VIEW" "$DTD_VIEWTOGGLE"
