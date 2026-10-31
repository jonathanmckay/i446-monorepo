#!/bin/zsh
# dtd — fuzzy task picker that runs /did directly (no Claude needed)
# UI-first: fzf stays responsive, background worker processes tasks serially,
# fzf header shows latest completion status.
# KEY: cache is snapshotted ONCE at startup. No mid-session re-reads.

# Reset the terminal tab color on every launch. Without this, an "orange"
# left over from a PREVIOUS session's FIFO-race alert (line ~214 below)
# stays on the tab indefinitely — nothing else ever clears it — so a fresh,
# error-free dtd session can still look like it's mid-error. Backgrounded:
# term-color.sh's TTY walk (+ AppleScript on Terminal.app) shouldn't add
# latency to fzf startup.
( bash "$HOME/i446-monorepo/scripts/term-color.sh" reset 2>/dev/null ) &

# UTF-8 locale guard (bug 2026-10-13): dtd runs on ix inside a tmux server
# whose environment has no LANG, so zsh treated input as bytes and the edit
# screen's vared showed a typed 中 as "\xe4<0088><0086>". Exported, so every
# generated script and python child inherits it.
if [[ "${LC_ALL:-${LC_CTYPE:-$LANG}}" != *[Uu][Tt][Ff]-8* && "${LC_ALL:-${LC_CTYPE:-$LANG}}" != *[Uu][Tt][Ff]8* ]]; then
  export LANG=en_US.UTF-8 LC_CTYPE=en_US.UTF-8
  unset LC_ALL
fi
# END UTF-8 locale guard

# python3 preflight guard (bug 2026-10-19): /usr/bin/python3 is an Xcode
# shim. Installing Xcode.app pointed xcode-select at it with the license
# unaccepted, and every python3 call below died with "You have not agreed to
# the Xcode license agreements" — all of them 2>/dev/null, so dtd launched to
# a silent, empty list ("0 left", 0/0). Prefer the Command Line Tools
# toolchain when the default one is broken (exported, so the worker/router
# scripts and every did-fast child inherit it); otherwise die with the real
# error instead of painting nothing.
_py_err=$(python3 -c 'pass' 2>&1 >/dev/null)
if (( $? != 0 )); then
  if DEVELOPER_DIR=/Library/Developer/CommandLineTools python3 -c 'pass' >/dev/null 2>&1; then
    export DEVELOPER_DIR=/Library/Developer/CommandLineTools
  else
    print -u2 -- "dtd: python3 is unusable, refusing to launch an empty list: ${_py_err}"
    exit 1
  fi
fi
unset _py_err
# END python3 preflight guard

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
UNDO_FAST="$HOME/i446-monorepo/tools/did/undo-fast.py"
DTD_RESOLVE="$HOME/i446-monorepo/tools/did/dtd_resolve.py"
DTD_OPEN_LINK="$HOME/i446-monorepo/tools/did/dtd-open-link.py"   # ctrl-l: task link -> its Chrome profile
# Task detail (2026-10-06): full title, description, comments, links, fetched
# live per task (the snapshot cache carries none of these). Shown at the top of
# the ctrl-o/ctrl-g edit screen (one screen for both since 2026-10-11).
DTD_DETAIL="$HOME/i446-monorepo/tools/did/task-detail.py"
# Per-item verdict on a did-fast run (ok/already/recorded/agent/unknown) and
# per-item ctrl-z journaling for batched habit runs (2026-10-10).
DTD_OUTCOME="$HOME/i446-monorepo/tools/did/dtd_outcome.py"
TG_FAST="$HOME/i446-monorepo/tools/tg/tg-fast.py"
TOGGL_CLI="$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
# Staleness self-check (mirrors janus.py's _code_is_stale): dtd.sh only reads
# itself once, at launch, to generate the worker/router/hdrgen scripts below —
# a fix landed on disk afterward is invisible to this running session until
# it's relaunched, which has repeatedly masked fixes (the ctrl-c hang, the
# FIFO-loss race) for anyone still on an old session. Capture our own mtime
# now; DTD_HDRGEN compares it against the live file on every header repaint.
DTD_SELF="$HOME/i446-monorepo/tools/did/dtd.sh"
DTD_SRC_MTIME=$(stat -f %m "$DTD_SELF" 2>/dev/null || echo 0)
# Machine-local runtime state (not synced). See lib/state_paths.py + architecture.md
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/jm"
mkdir -p "$STATE_DIR"
CACHE="$STATE_DIR/task-queue.json"
DONE="$STATE_DIR/completed-today.json"

# /travel override (see lib/daytime.py, the Python side of this same
# resolution): every `date`/`datetime.now()` call in this file and its
# Python helpers is naive/system-local, which is correct by default (the
# laptop follows wherever it physically is) but blind to an explicit
# /travel override (e.g. deliberately staying on home time while abroad).
# Exporting TZ once here, before any date-dependent logic runs, makes every
# subsequent `date` call in this script AND its background subshells (they
# inherit exported vars) — plus every Python helper's naive datetime.now()/
# date.today() — honor the override with no per-call plumbing. Absent or
# malformed travel.json is silently a no-op (TZ stays whatever the OS has).
# 2026-10-08: the zone is now daytime's full resolution — /travel override,
# else the nearest device's zone (lib/where.py: imago-c → fuchikoma →
# straylight → ix, never backwards). One python call here; the loops below
# re-read the resolved file with zsh builtins (_dtd_tz_refresh).
_travel_tz=$(python3 "$HOME/i446-monorepo/lib/daytime.py" --tz-env 2>/dev/null)
[[ -n "$_travel_tz" ]] && export TZ="$_travel_tz"
unset _travel_tz
# Re-export TZ from the shared /travel override, else this host's resolved
# zone (~/.local/state/jm/active_tz.json, kept fresh by where.py's cron).
# Pure zsh: called every loop pass, must not spawn processes.
_dtd_tz_refresh() {
  local f j
  for f in "$HOME/vault/z_ibx/where/override.json" "$STATE_DIR/active_tz.json"; do
    [[ -r $f ]] || continue
    j=$(<$f)
    if [[ $j =~ '"(active_tz|tz)": *"([A-Za-z_+-]+/[A-Za-z0-9_+/-]+)"' ]]; then
      [[ "$TZ" != "$match[2]" ]] && export TZ="$match[2]"
      return
    fi
  done
}

# Per-launch id for all temp paths. dtd.sh is *sourced*, so $$ is the
# (long-lived) shell PID and is identical on every re-run. A bare $$ made
# re-entrant launches (suspend an open picker, re-source) collide on the same
# FIFO/temp files, and one run's `exec 3>&-` + cleanup killed another run's
# worker, after which completions written to the now-orphaned FIFO were
# silently dropped. The unique suffix isolates each launch; the $$ prefix lets
# the sweep below reclaim files left by dead shells.
DTD_ID="$$-$(date +%s)-$RANDOM"
# Ctrl-Z can't freeze dtd (2026-10-24, "dtd cli just exited on its own"):
# ^Z is dtd's UNDO key, but any moment the tty is in cooked mode (a value
# prompt, the edit form, every `stty sane` between screens) the kernel turned
# it into SIGTSTP, the whole dtd job stopped and the shell came back: dtd
# looked like it quit. The stopped copy kept its fzf holding /dev/tty, and the
# next launch in that tab ran beside it (found: a dtd stopped since 06:26 under
# the session that died rc=2 at 09:21). So the tty has NO suspend character
# for dtd's lifetime (every `stty sane` below re-applies `susp undef`); inside
# fzf ^Z still arrives as a key and runs undo. A `trap '' TSTP` doesn't work:
# zsh resets job-control signals to default in its children. Restored on exit.
stty susp undef < /dev/tty 2>/dev/null
# Reclaim temp files from dtd launches whose owning shell is gone. mtime >1h so
# a detached did-fast/defer child of a just-exited run is never cut off.
for _f in /tmp/dtd-<->-*(Nmh+1); do
  _pid=${${_f:t}#dtd-}; _pid=${_pid%%-*}
  # -rf: .resolved is a directory; plain rm printed one "is a directory" line
  # per dead launch and they piled up into a scrolling wall at startup (2026-10-22).
  [[ "$_pid" == <-> ]] && ! kill -0 "$_pid" 2>/dev/null && rm -rf "$_f" 2>/dev/null
done
unset _f _pid

DTD_FIFO="/tmp/dtd-$DTD_ID.fifo"
DTD_HDR="/tmp/dtd-$DTD_ID.hdr"
# Stage timing log (2026-10-03): every generated script stamps $EPOCHREALTIME
# at its boundaries so a slow keypress can be attributed. Read with
# tools/did/dtd-timing-report.py. Builtin print only -- no extra exec.
DTD_TIMING="/tmp/dtd-$DTD_ID.timing.log"
# Usage events (2026-10-10): the per-session /tmp logs die with the session,
# so nothing recorded how dtd is actually used. One TSV line per event:
#   epoch \t session \t event \t key \t ids(comma) \t extra
# events: session (start/end), render (top-15 ids, only when they change),
# action (a key on task ids; extra = query/choice), outcome (worker verdict
# per completion; extra = seconds or reason). Appended with zsh's print
# builtin, never an exec, so a keypress pays nothing measurable.
# Read by tools/did/dtd_report.py. Machine-local; host is on the session line.
DTD_EVENTS="${DTD_EVENTS:-${XDG_STATE_HOME:-$HOME/.local/state}/jm/dtd-events.tsv}"
export DTD_EVENTS DTD_ID
# Record log (2026-10-17, "what do the logs tell you about making dtd more
# useful"): one JSON line per pick (which row, under what query, how deep in
# the list) and per completion (estimate vs actual minutes, points, what
# did-fast actually wrote, age and impressions of the task), appended by the
# key routers (zsh print) and dtd_outcome.py. Per host under the synced vault
# so both machines' days read as one; tools/did/dtd-log.py reads it.
# DTD_QUERY holds the live fzf query (the change: script stashes it) for the
# routers that fzf does not hand {q} to. DTD_LIFECYCLE is the list
# generator's first-seen / impressions-per-day store (machine-local).
DTD_HOSTSLUG=$(python3 -c 'import re,socket;print(re.sub(r"[^a-z0-9]+","-",socket.gethostname().lower()).strip("-")[:24])' 2>/dev/null || echo host)
DTD_RECLOG="${DTD_RECLOG:-$HOME/vault/z_ibx/dtd-log/$DTD_HOSTSLUG.jsonl}"
DTD_QUERY="/tmp/dtd-$DTD_ID.query"
DTD_ORDER="/tmp/dtd-$DTD_ID.order"      # every row id in list order, rewritten per render
DTD_LIFECYCLE="${DTD_LIFECYCLE:-${XDG_STATE_HOME:-$HOME/.local/state}/jm/dtd-lifecycle.json}"
mkdir -p "${DTD_RECLOG:h}" 2>/dev/null
export DTD_RECLOG DTD_QUERY DTD_ORDER DTD_LIFECYCLE DTD_HOSTSLUG
# Router -> done.sh handoff of the resolved task content as a FILE (2026-10-03):
# print/$(<f) are zsh builtins, so done.sh needs no base64/sed/tr exec at all
# on the common path (each exec costs 0.07-1.5s under a Defender scan stall).
DTD_RESOLVED_DIR="/tmp/dtd-$DTD_ID.resolved"
zmodload zsh/datetime 2>/dev/null
DTD_LOG="/tmp/dtd-$DTD_ID.log"
# ctrl-z undo state: journal of reversible actions + in-flight counters
DTD_JOURNAL="/tmp/dtd-$DTD_ID.undo.jsonl"
DTD_PUSHED="/tmp/dtd-$DTD_ID.pushed"
DTD_PROCESSED="/tmp/dtd-$DTD_ID.processed"
# FIFO-invariant-check-only bookkeeping (2026-08-01, replacing the naive
# count-diff design from 2026-07-31): $DTD_PUSHED/$DTD_PROCESSED above are
# SHARED with ctrl-d defer's own per-item background workers (see done.sh's
# twin below), which push/process asynchronously and legitimately sit
# "pushed > processed" for seconds while a network round trip is in flight --
# using them for the FIFO-loss check produced false positives (an in-flight
# defer misread as a lost completion). $DTD_PUSHED.log/.processed.ids are
# written ONLY by done.sh and the main worker loop respectively, always in
# "id<TAB>...<TAB>id<TAB>content" / "id-per-line" form, so the invariant
# check can compute a real set difference (which SPECIFIC ids were pushed
# but never processed) instead of guessing from the tail of the log --
# confirmed live (2026-08-01) that the tail-based guess kept re-citing
# already-✓'d completions as "lost" while never once naming the genuinely
# stuck ones once more than one item was actually lost.
DTD_PROCESSED_IDS="/tmp/dtd-$DTD_ID.processed.ids"
# Shutdown signal for the worker loop below. `read -t 2` returns nonzero on
# BOTH a real 2s idle timeout AND real EOF (all FIFO writers closed) -- zsh
# gives no way to tell them apart from the exit status alone, and the
# invariant-check branch used to just `continue` unconditionally in either
# case, so the worker never noticed EOF and looped forever, hanging dtd's
# exit-cleanup wait on $WORKER_PID (2026-08-01: "dtd hangs on ctrl-c").
# Cleanup touches this file before closing fd 3; the worker checks for it
# only on a timeout/EOF tick and breaks instead of continuing.
DTD_STOP="/tmp/dtd-$DTD_ID.stop"
# Durable retry queue for completions did-fast could not land (almost always a
# transient network outage). Format: "next_retry_epoch<TAB>attempts<TAB>id<TAB>content".
# The worker re-drives one eligible item per idle tick once connectivity returns.
DTD_FAILED="/tmp/dtd-$DTD_ID.failed"
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
# Four independent staleness guards follow (time-based, block-aware, today-
# count, due-count), each capable of triggering its OWN synchronous
# `--refresh-cache` call. They're not mutually exclusive on a HEALTHY refresh
# (a successful refresh clears every condition, since each later guard
# re-reads the file/snapshot the previous one just wrote) but on a DEGRADED
# network — slow enough to hit refresh_task_queue's own timeouts/retries
# without failing outright — a refresh can complete without actually fixing
# staleness, and all four guards then fire in sequence, each paying its own
# multi-second-to-tens-of-seconds network cost. That serial pile-up is what
# "dtd is hanging on launch" (2026-09-04) actually was: not one hung call,
# four stacked ones. One refresh attempt per launch is enough — a second
# attempt run milliseconds later against the same degraded network isn't
# going to succeed where the first didn't.
_dtd_refresh_done=""; _dtd_refresh_sync_done=""
# Age/block staleness refreshes in the BACKGROUND (2026-10-22, "anything we can
# do to make startup faster"): the synchronous refresh was ~2.2s of a ~3.7s
# cold start. fzf opens on the slightly stale cache and the auto-reload watcher
# swaps the fresh one in when its mtime moves (baseline = DTD_SNAP_M, taken at
# snapshot time, so a refresh that lands before fzf publishes its port is not
# missed). The count guards below mean the cache is broken, not just old: they
# still block, by waiting on this same refresh rather than starting a second.
_dtd_bg_refresh=""
_dtd_refresh_bg() {
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1 &!
  _dtd_bg_refresh=$!
  _dtd_refresh_done=1
}
_dtd_refresh_sync() {
  if [[ -n "$_dtd_bg_refresh" ]]; then
    while kill -0 "$_dtd_bg_refresh" 2>/dev/null; do sleep 0.1; done
  else
    python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  fi
  _dtd_refresh_done=1
}
cache_age=$(python3 -c "
import json, sys, datetime as dt
try:
    u = json.load(open(sys.argv[1])).get('updated')
    # .astimezone() on BOTH sides makes this safe whether the stored stamp
    # is naive (old cache, pre-daytime.py) or tz-aware (current
    # refresh-cache.py) — a bare subtraction of one naive + one aware
    # datetime raises TypeError, which this except swallowed into 'always
    # stale' rather than crashing, but that meant every dtd launch forced
    # an unnecessary refresh once refresh-cache.py started writing aware
    # timestamps (2026-08-23 travel hardening).
    print(int((dt.datetime.now().astimezone() - dt.datetime.fromisoformat(u).astimezone()).total_seconds()) if u else 10**9)
except Exception:
    print(10**9)
" "$CACHE" 2>/dev/null || echo 1000000000)
if [[ -z "$_dtd_refresh_done" && "$cache_age" -gt "$DTD_CACHE_MAX_AGE" ]]; then
  _dtd_refresh_bg
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
    # .astimezone() normalizes both sides to the current local zone whether
    # the stored stamp is naive or tz-aware (see cache_age's comment above)
    # — .hour on an aware datetime otherwise reflects ITS OWN attached zone,
    # not necessarily the zone 'now' is being read in.
    print('1' if (not u or blk(dt.datetime.fromisoformat(u).astimezone()) != blk(dt.datetime.now().astimezone())) else '0')
except Exception:
    print('1')
" "$CACHE" 2>/dev/null || echo 1)
if [[ -z "$_dtd_refresh_done" && "$stale_block" == "1" ]]; then
  _dtd_refresh_bg
fi

if [[ -z "$_dtd_refresh_sync_done" && $(jq '.today | length // 0' "$CACHE") -lt 5 ]]; then
  echo "Refreshing task cache..."
  _dtd_refresh_sync
  _dtd_refresh_sync_done=1
fi

setopt NO_MONITOR 2>/dev/null
LOCAL_TODAY=$(date +%Y-%m-%d)
DONE_NAMES=$(jq -c --arg today "$LOCAL_TODAY" \
  'if .date == $today then [.names[] | ascii_downcase] else [] end' "$DONE" 2>/dev/null || echo '[]')

# ── SNAPSHOT the cache into a variable. Never read the file again. ──
DTD_SNAP_M=$(stat -f %m "$CACHE" 2>/dev/null)
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
if [[ -z "$_dtd_refresh_sync_done" && $due_today -lt 30 ]]; then
  echo "⚠ Only $due_today tasks due today (expected ~80+). Refreshing..."
  _dtd_refresh_sync
  _dtd_refresh_sync_done=1
  DTD_SNAP_M=$(stat -f %m "$CACHE" 2>/dev/null)
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
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "$DTD_QUERY" "$DTD_ORDER" "/tmp/dtd-$DTD_ID.start.sh" \
      "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PUSHED.log" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" \
      "$DTD_SESSION" "$DTD_TIMER" "$DTD_STOP" "$DTD_FAILED" "$DTD_FAILED.tmp" \
      "/tmp/dtd-$DTD_ID.removed.ids" "/tmp/dtd-$DTD_ID.blockpick"
mkfifo "$DTD_FIFO"
mkdir -p "$DTD_RESOLVED_DIR"
echo "ready" > "$DTD_HDR"
touch "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" "$DTD_SESSION" "$DTD_TIMER" "$DTD_FAILED"

(
  # The FIFO race (2026-08-03): done.sh's FIFO push runs inside a short-lived,
  # KILLABLE fzf `execute` child, and a rapid alt-enter burst tears it down
  # mid-write, so the line never arrives (always the LAST of a cluster) while
  # quick-close.py still closes the card. 2026-08-03..10-09 the worker healed
  # this by diffing $DTD_PUSHED.log against processed ids on idle ticks and
  # re-injecting the lost line; since 2026-10-10 the log simply IS the queue
  # (see QUEUE below), so there is nothing to lose. fd 4 is a persistent
  # read-write handle on the FIFO owned by this subshell: opened <> so the
  # open can't block and so a read never sees EOF while the session lives;
  # shutdown is driven by $DTD_STOP. fd 3 (opened by the parent below) keeps
  # >=1 writer for the whole session.
  exec 4<>"$DTD_FIFO"
  typeset -A stale_alerted
  # Deferred cache refresh (2026-10-02): did-fast skips its in-completion
  # refresh_task_queue() (~4s, blocking) when this is set; the worker fires
  # ONE backgrounded --refresh-cache when the FIFO goes idle (the 2s read
  # timeout below is the debounce), so a burst of ritual closes pays once,
  # after the last one, instead of 4s inside each.
  export DIDFAST_DEFER_REFRESH=1
  _pending_refresh=""
  # QUEUE = $DTD_PUSHED.log (2026-10-10). done.sh appends every completion to
  # that log (atomic O_APPEND) BEFORE its killable FIFO push, so the log, not
  # the FIFO, is the queue: the worker consumes it in order through a line
  # cursor, and a FIFO line is only a wake-up whose content is ignored. A
  # dropped push (the rapid alt-enter race that hit 19 of 54 completions on
  # 2026-10-09/10) now costs at most one 2s idle tick, with no re-inject
  # step and no "auto-recovered" alarm. Cursor by LINE, not by id: a
  # recurring card keeps its id, so the same id can legitimately be queued
  # twice in a day (family 30, later family 20).
  _qcur=0
  # BATCHING (2026-10-10): each 0₦ habit completion is a full did-fast run
  # (~7s, mostly the Neon write over ssh), so a 10-habit morning burst took
  # ~75s serially. Consecutive queued 0neon/夜neon habits now go to ONE
  # did-fast call, which already batches its Neon writes. Habits only: they
  # route by name anyway (did-fast honours --task-id for single items only),
  # and their runs carry none of the timer/0t side work. dtd_outcome.py maps
  # the batch result back to items and journals each one separately, so
  # ctrl-z still undoes one habit at a time.
  _qcache="/tmp/dtd-$DTD_ID.cache.json"
  typeset -A _habit_ids
  _habit_mtime=""
  zmodload -F zsh/stat b:zstat 2>/dev/null
  _dtd_habit_ids_refresh() {
    local m; m=$(zstat +mtime "$_qcache" 2>/dev/null) || return
    [[ "$m" == "$_habit_mtime" ]] && return
    _habit_mtime="$m"; _habit_ids=()
    local i
    for i in ${(f)"$(jq -r '((.["0neon"] // []) + (.["夜neon"] // []))[] | .id // empty' "$_qcache" 2>/dev/null)"}; do
      _habit_ids[$i]=1
    done
  }
  _dtd_batchable() {   # $1 id, $2 content
    [[ -n "$1" && -n "${_habit_ids[$1]}" ]] || return 1
    [[ "$2" != *[,\;]* ]] || return 1        # did-fast splits batches on , and ;
    [[ "${(L)2}" != 0t && "${(L)2}" != "0t "* ]] || return 1   # 0t has its own follow-up run
    return 0
  }
  # Usage event: one "outcome" line per completion (see DTD_EVENTS).
  # $1 status, $2 id(s), $3 extra (seconds in did-fast, or a reason).
  _ev_out() {
    print -r -- "$EPOCHREALTIME"$'\t'"$DTD_ID"$'\t'outcome$'\t'"$1"$'\t'"$2"$'\t'"${3//$'\t'/ }" >> "$DTD_EVENTS" 2>/dev/null
  }
  # Restore the optimistic id-hide (enter.sh/done.sh hid the row the instant
  # the key was pressed) for an item that did not complete.
  _dtd_unhide() {
    [[ -n "$1" ]] || return
    local f="/tmp/dtd-$DTD_ID.removed.ids"
    grep -v -x -F -- "$1" "$f" > "$f.tmp" 2>/dev/null
    mv "$f.tmp" "$f" 2>/dev/null
  }
  _dtd_run_batch() {
    local i n=${#_bids} _pairs=() _rows _r _st _disp _ok=0
    for i in {1..$n}; do
      echo "x" >> "$DTD_PROCESSED"
      echo "${_bids[i]:-${_bcont[i]}}" >> "$DTD_PROCESSED_IDS"
      _pairs+=("${_bids[i]}"$'\t'"${_bcont[i]}")
    done
    echo "⏳ $n habits: ${(j:, :)_bcont}" > "$DTD_HDR"
    local _t0=$EPOCHREALTIME
    print -- "$EPOCHREALTIME\tdidfast-start\tbatch:$n ${(j:,:)_bids}" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
    result=$(python3 "$DID_FAST" "${(j:, :)_bcont}" 2>>"$DTD_LOG.err")
    rc=$?
    print -- "$EPOCHREALTIME\tdidfast-end\tbatch:$n rc=$rc" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
    _rows=$(print -r -- "$result" | python3 "$DTD_OUTCOME" "$DTD_JOURNAL" "$rc" "${_pairs[@]}" 2>>"$DTD_LOG.err")
    for _r in ${(f)_rows}; do
      _f=("${(@ps:\t:)_r}")
      _st="${_f[2]}"; _disp="${_f[3]}"
      _ev_out "$_st" "${_f[1]}" "batch:$n $(( EPOCHREALTIME - _t0 ))"
      case "$_st" in
        ok|already|recorded)
          _ok=1
          echo "✓ $_disp" >> "$DTD_LOG";;
        *)
          # Index of this row's item (rows come back in request order).
          for i in {1..$n}; do [[ "${_bids[i]}" == "${_f[1]}" ]] && break; done
          if (( rc != 0 )); then
            # Transient failure: queue each item for the idle-tick retry,
            # which re-drives it as a single did-fast run.
            printf '%d\t%d\t%s\t%s\n' "$(( $(date +%s) + 10 ))" 1 "${_bids[i]}" "${_bcont[i]}" >> "$DTD_FAILED"
            echo "✗ ${_bcont[i]} (did-fast exit $rc; will retry)" >> "$DTD_LOG"
          else
            _dtd_unhide "${_bids[i]}"
            echo "? ${_disp} (restored to list)" >> "$DTD_LOG"
          fi;;
      esac
    done
    (( _ok )) && _pending_refresh=1
    echo "✓ $n habits: ${(j:, :)_bcont}" > "$DTD_HDR"
  }
  while true; do
    IFS= read -r -t 2 _wake
    _rr=$?
    _qlines=()
    [[ -s "$DTD_PUSHED.log" ]] && _qlines=("${(@f)$(<"$DTD_PUSHED.log")}")
    if (( ${#_qlines} <= _qcur )); then
      # Queue drained. A wake line just means another push is landing; only
      # a real idle tick (read timed out) runs the idle work below.
      (( _rr == 0 )) && continue
      if [[ -n "$_pending_refresh" ]]; then
        _pending_refresh=""
        ( python3 "$DID_FAST" --refresh-cache >/dev/null 2>>"$DTD_LOG.err" ) &
      fi
      # AUTO-RETRY of completions did-fast could not land (2026-08-04): the ✗
      # branch below records each failure in $DTD_FAILED as
      # "next_retry<TAB>attempts<TAB>id<TAB>content". These are almost always
      # transient outages (Todoist API / ssh-to-ix unreachable); did-fast is
      # idempotent, so we re-drive them here once connectivity returns. AT MOST
      # ONE item per idle tick, gated by a fast reachability probe so a still-
      # down network costs ~3s (not a full watchdog window) and never freezes
      # live completions for long. Per-item exponential backoff (30s→300s) and a
      # retry cap (DTD_MAX_RETRIES, default 8) stop a permanently-bad item from
      # looping forever. This runs on idle ticks only; it never touches the main
      # dequeue path, and (rout=/journal-flag-in-var) it deliberately avoids the
      # exact substrings the structural worker tests anchor on.
      if [[ -s "$DTD_FAILED" ]]; then
        rnow=$(date +%s)
        rsel=$(awk -F'\t' -v now="$rnow" '
          $1<=now { if (best=="" || $1<bv) { best=$0; bv=$1 } }
          END { print best }' "$DTD_FAILED" 2>/dev/null)
        if [[ -n "$rsel" ]]; then
          rrest="${rsel#*$'\t'}"; rtry="${rrest%%$'\t'*}"
          rrest="${rrest#*$'\t'}"; rid="${rrest%%$'\t'*}"; rcontent="${rrest#*$'\t'}"
          if python3 -c 'import socket; socket.setdefaulttimeout(3); socket.create_connection(("api.todoist.com",443)).close()' 2>/dev/null; then
            echo "↻ retrying (connectivity back): $rcontent" > "$DTD_HDR"
            if [[ -n "$rid" ]]; then
              rout=$(python3 "$DID_FAST" --task-id "$rid" "$rcontent" 2>>"$DTD_LOG.err")
            else
              rout=$(python3 "$DID_FAST" "$rcontent" 2>>"$DTD_LOG.err")
            fi
            rrc=$?
            if [[ $rrc -eq 0 && -n "$rout" ]]; then
              awk -F'\t' -v id="$rid" -v c="$rcontent" \
                '!($3==id && $4==c)' "$DTD_FAILED" > "$DTD_FAILED.tmp" 2>/dev/null \
                && mv "$DTD_FAILED.tmp" "$DTD_FAILED"
              rjflag="--journal-done"
              echo "$rout" | python3 "$UNDO_FAST" "$rjflag" "$DTD_JOURNAL" "$rid" 2>/dev/null
              rok=$(echo "$rout" | jq -r '.results[]? | "\(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null)
              ( print -r -- "$rout" | python3 "$DTD_OUTCOME" - "$rrc" "$rid"$'\t'"$rcontent" >/dev/null 2>>"$DTD_LOG.err" ) &
              echo "✓ ${rok:-$rcontent} (retry)" > "$DTD_HDR"
              echo "✓ ${rok:-$rcontent} (retry)" >> "$DTD_LOG"
            else
              rtry=$(( rtry + 1 ))
              if [[ $rtry -gt ${DTD_MAX_RETRIES:-8} ]]; then
                awk -F'\t' -v id="$rid" -v c="$rcontent" \
                  '!($3==id && $4==c)' "$DTD_FAILED" > "$DTD_FAILED.tmp" 2>/dev/null \
                  && mv "$DTD_FAILED.tmp" "$DTD_FAILED"
                echo "✗ gave up on $rcontent after $(( rtry - 1 )) retries" >> "$DTD_LOG"
              else
                rback=$(( 30 * (1 << (rtry - 1)) )); (( rback > 300 )) && rback=300
                awk -F'\t' -v id="$rid" -v c="$rcontent" -v nn="$(( rnow + rback ))" -v at="$rtry" \
                  'BEGIN{OFS="\t"} ($3==id && $4==c){ $1=nn; $2=at } {print}' \
                  "$DTD_FAILED" > "$DTD_FAILED.tmp" 2>/dev/null \
                  && mv "$DTD_FAILED.tmp" "$DTD_FAILED"
              fi
            fi
          else
            # Still down: short cooldown only, spend no attempt and no did-fast
            # (watchdog) window on it.
            awk -F'\t' -v id="$rid" -v c="$rcontent" -v nn="$(( rnow + 15 ))" \
              'BEGIN{OFS="\t"} ($3==id && $4==c){ $1=nn } {print}' \
              "$DTD_FAILED" > "$DTD_FAILED.tmp" 2>/dev/null \
              && mv "$DTD_FAILED.tmp" "$DTD_FAILED"
          fi
        fi
      fi
      # Shutdown check: only cleanup sets $DTD_STOP, and only after it has
      # already closed fd 3 -- so by the time this is seen (with nothing left
      # to recover), the FIFO is genuinely drained. Break instead of looping
      # forever on repeated instant-timeout reads.
      [[ -f "$DTD_STOP" ]] && break
      continue
    fi
    # Dequeue "ts<TAB>done<TAB>id<TAB>content" (fields kept even when id is
    # empty: quoted (@ps) preserves empty elements).
    _qcur=$(( _qcur + 1 ))
    _qf=("${(@ps:\t:)_qlines[$_qcur]}")
    task_id="${_qf[3]}"; task_clean="${_qf[4]}"
    if [[ -z "$task_clean" ]]; then
      echo "x" >> "$DTD_PROCESSED"   # keep the undo guard's pushed/processed counts even
      continue
    fi
    # SAME-DAY ONLY (2026-08-10): a session left open across midnight must not
    # run the previous evening's leftovers against the NEW day (did-fast is
    # only idempotent within a day: it would close the new day's recurring
    # cards and credit points to the new day's rows). Alerted once, left for
    # the human. Date-less legacy lines count as stale.
    strftime -s _qtoday '%Y-%m-%d' $EPOCHSECONDS
    if [[ "${_qf[1]}" != "${_qtoday}T"* ]]; then
      if [[ -z "${stale_alerted[${task_id:-$task_clean}]}" ]]; then
        stale_alerted[${task_id:-$task_clean}]=1
        echo "⚠ unprocessed completion from a previous day NOT replayed (would land on today) — $task_clean" >> "$DTD_LOG"
      fi
      continue
    fi
    _bids=("$task_id"); _bcont=("$task_clean")
    _dtd_habit_ids_refresh
    if _dtd_batchable "$task_id" "$task_clean"; then
      while (( _qcur < ${#_qlines} && ${#_bids} < 12 )); do
        _nf=("${(@ps:\t:)_qlines[$(( _qcur + 1 ))]}")
        [[ "${_nf[1]}" == "${_qtoday}T"* ]] || break
        _dtd_batchable "${_nf[3]}" "${_nf[4]}" || break
        _qcur=$(( _qcur + 1 )); _bids+=("${_nf[3]}"); _bcont+=("${_nf[4]}")
      done
    fi
    if (( ${#_bids} > 1 )); then
      _dtd_run_batch
      continue
    fi
    echo "x" >> "$DTD_PROCESSED"
    echo "${task_id:-$task_clean}" >> "$DTD_PROCESSED_IDS"
    echo "⏳ $task_clean" > "$DTD_HDR"
    _t0=$EPOCHREALTIME
    if [[ -n "$task_id" ]]; then
      print -- "$EPOCHREALTIME\tdidfast-start\t${task_id:-$task_clean}" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
      result=$(python3 "$DID_FAST" --task-id "$task_id" "$task_clean" 2>>"$DTD_LOG.err")
    else
      print -- "$EPOCHREALTIME\tdidfast-start\t${task_id:-$task_clean}" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
      result=$(python3 "$DID_FAST" "$task_clean" 2>>"$DTD_LOG.err")
    fi
    rc=$?
    print -- "$EPOCHREALTIME\tdidfast-end\t${task_id:-$task_clean} rc=$rc" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
    if [[ $rc -ne 0 || -z "$result" ]]; then
      echo "✗ $task_clean (did-fast exit $rc, no output)" > "$DTD_HDR"
      echo "✗ $task_clean (did-fast exit $rc, no output)" >> "$DTD_LOG"
      _ev_out fail "$task_id" "rc=$rc $(( EPOCHREALTIME - _t0 ))"
      # Record it for auto-retry. A failed did-fast is almost always a
      # transient outage (Todoist API or the ssh-to-ix stamp writer briefly
      # unreachable, now surfaced fast by the SIGALRM watchdog's exit 124
      # instead of a hang). quick-close.py already closed the card optimistically,
      # but the Neon score/stamp never landed; did-fast is idempotent, so the
      # idle-tick retry loop below re-drives this exact completion once
      # connectivity returns. First retry ~10s out; probe-gated + backed off.
      printf '%d\t%d\t%s\t%s\n' "$(( $(date +%s) + 10 ))" 1 "$task_id" "$task_clean" >> "$DTD_FAILED"
      continue
    fi
    # Journal for ctrl-z undo BEFORE signalling done (the undo guard compares
    # the pushed/processed counters, so the journal entry must land first).
    # $task_id rides along so undo can strip the optimistic id-hide from
    # $REMOVED.ids (completions hide by id since 2026-07-24).
    echo "$result" | python3 "$UNDO_FAST" --journal-done "$DTD_JOURNAL" "$task_id" 2>/dev/null
    ok=$(echo "$result" | jq -r '.results[]? | "\(.name) → \(.step) \(if .todoist.closed then "✓" else "" end)"' 2>/dev/null)
    if [[ -n "$ok" ]]; then
      echo "✓ $ok" > "$DTD_HDR"
      echo "✓ $ok" >> "$DTD_LOG"
      _ev_out ok "$task_id" "$(( EPOCHREALTIME - _t0 ))"
      # Record log (2026-10-17): the batch and fallback paths go through
      # dtd_outcome.py already; this common path never did, so the done
      # record (estimate vs actual, points, writes, age) is written here,
      # detached so the worker never waits on it.
      ( print -r -- "$result" | python3 "$DTD_OUTCOME" - "$rc" "$task_id"$'\t'"$task_clean" >/dev/null 2>>"$DTD_LOG.err" ) &
      # A ritual / d359-met close would have refreshed the cache inline;
      # with DIDFAST_DEFER_REFRESH that is now owed -- pay it on next idle.
      if echo "$result" | jq -e '.results[]? | select(.step == "ritual" or .d359 != null)' >/dev/null 2>&1; then
        _pending_refresh=1
      fi
      # 0t completed in dtd → also run the full /0t review (sleep write,
      # sleep dock, media audit, dashboard cache refresh) in the background,
      # not just the bare habit close this loop already did. 0t-fast.py
      # re-invokes did-fast "0t" itself; the recurring-habit done_today gate
      # (did-fast.py Step 6, "already done today (skipped re-close...)")
      # makes that second call a safe no-op on the Todoist/0n side, so this
      # never double-credits or double-advances the due date — it only adds
      # the sleep/dock/audit/dashboard work dtd's own completion never did.
      if echo "$result" | jq -e '.results[]? | select(.name | ascii_downcase == "0t")' >/dev/null 2>&1; then
        ( python3 "$HOME/i446-monorepo/tools/0t/0t-fast.py" >>"$DTD_LOG.err" 2>&1 ) &
      fi
    else
      # No results entry by the quick jq check. Before calling it a failure,
      # ask dtd_outcome.py (2026-10-10): did-fast may have skipped it as
      # already done today, or the completion landed (completed-today.json
      # has it) and only the report was lost. That false "?" hit 15 of 54
      # completions on 2026-10-09/10, so the failure signal had become noise.
      _vr=$(print -r -- "$result" | python3 "$DTD_OUTCOME" - "$rc" "$task_id"$'\t'"$task_clean" 2>>"$DTD_LOG.err")
      _vf=("${(@ps:\t:)_vr}")
      case "${_vf[2]}" in
        ok|already|recorded)
          _ev_out "${_vf[2]}" "$task_id" "$(( EPOCHREALTIME - _t0 ))"
          echo "✓ ${_vf[3]}" > "$DTD_HDR"
          echo "✓ ${_vf[3]}" >> "$DTD_LOG"
          _pending_refresh=1
          continue;;
      esac
      # Really not completed: restore the optimistic id-hide (enter.sh/done.sh
      # hid $task_id the instant the key was pressed). An empty $ok here means
      # did-fast produced NO results entry (e.g. it fell through to
      # needs_agent, which this synchronous worker can't service), so the task
      # must not vanish from view. Regression 2026-08-18: "ibx i9" stayed in
      # $REMOVED.ids all day though Todoist still showed it open.
      if [[ -n "$task_id" ]]; then
        removed_ids_path="/tmp/dtd-$DTD_ID.removed.ids"
        grep -v -x -F -- "$task_id" "$removed_ids_path" > "$removed_ids_path.tmp" 2>/dev/null
        mv "$removed_ids_path.tmp" "$removed_ids_path" 2>/dev/null
      fi
      # Say WHY when did-fast gave a reason (e.g. "family: how many minutes?").
      _why=$(echo "$result" | jq -r '.agent_needed[0].reason // empty' 2>/dev/null)
      echo "? $task_clean (restored to list)${_why:+ — $_why}" > "$DTD_HDR"
      echo "? $task_clean (restored to list)${_why:+ — $_why}" >> "$DTD_LOG"
      _ev_out "${_vf[2]:-unknown}" "$task_id" "${_why:-$(( EPOCHREALTIME - _t0 ))}"
    fi
  done < "$DTD_FIFO"
  echo "done" > "$DTD_HDR"
) &
WORKER_PID=$!

exec 3>"$DTD_FIFO"

# --- Temp files for list generation (defined before the binding scripts
# below so their heredocs expand to real paths, not empty strings) ---
DTD_CACHE_FILE="/tmp/dtd-$DTD_ID.cache.json"
DTD_REMOVED="/tmp/dtd-$DTD_ID.removed"
# Block-picker state + apply script paths (scripts generated further down;
# defined here so enter.sh/done.sh heredocs expand real paths).
DTD_BLOCKPICK="/tmp/dtd-$DTD_ID.blockpick"
DTD_BLOCKAPPLY="/tmp/dtd-$DTD_ID.blockapply.sh"
# Schedule-screen routers (2026-10-04): enter (picks a row, or N typed days)
# and esc/ctrl-c (back to the list from the picker, else exit).
DTD_PICKENTER="/tmp/dtd-$DTD_ID.pickenter.sh"
DTD_BACK="/tmp/dtd-$DTD_ID.back.sh"
# View mode (ctrl-t toggles): empty = default priority order, 'project' = grouped
# by domain label. Per-session; the list generator reads it as its 8th arg.
DTD_VIEW="/tmp/dtd-$DTD_ID.view"
DTD_VIEWTOGGLE="/tmp/dtd-$DTD_ID.view-toggle.sh"
# Active domain-search scope (2026-10-01): the domain code typed into the
# query box, or empty. Persisted per-session so EVERY reload path (the
# watcher's cache-mtime reload, ctrl-* bindings, resize) re-applies it; the
# list generator reads it as its 11th arg. ctrl-r clears it.
DTD_DOMAIN="/tmp/dtd-$DTD_ID.domain"
# Skips persist across dtd sessions for the duration of one day (stable
# path + date guard), unlike the other per-session temp files. Forward-only:
# reset (and advance the stamp) only when LOCAL_TODAY is strictly newer than
# what's stored — never on a backward move (an OS TZ correction, an
# International Date Line crossing), matching mark-completed.py's date gate.
# Relaunching dtd mid-trip must not wipe a skip list set earlier the same
# subjective day just because the calendar date briefly went backward.
DTD_SKIPPED="$STATE_DIR/dtd-skipped-today.txt"
_dtd_skipped_stored="$(cat "$DTD_SKIPPED.date" 2>/dev/null)"
if [[ -z "$_dtd_skipped_stored" || "$LOCAL_TODAY" > "$_dtd_skipped_stored" ]]; then
  rm -f "$DTD_SKIPPED"
  echo "$LOCAL_TODAY" > "$DTD_SKIPPED.date"
fi
unset _dtd_skipped_stored
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
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}start\${_T}\$1\${_T}" >> "$DTD_EVENTS" 2>/dev/null
TOGGL_CLI="\$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST="\$HOME/i446-monorepo/tools/tg/tg-fast.py"
HDR="$DTD_HDR"
TIMER="$DTD_TIMER"
task="\$1"
# Strip ANSI codes first
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
# Ritual (-1neon) cards carry the 😈 marker; their Toggl project comes from the
# ritual→domain map — the SAME source as their row color (keep in sync with
# RITUAL_DOMAIN in the list generator) — NOT tg-fast, whose shortcodes differ
# (e.g. -1ibx→m5x2 there but i9 here) and which can't resolve the 😈-prefixed
# name. The python also strips 😈 so the Toggl entry reads '-1g', not '😈 -1g'.
# Non-ritual tasks pass through unchanged and fall back to tg-fast below.
_rr=\$(python3 -c "
import sys
RITUAL_DOMAIN = {'-1ibx':'i9','-1g':'g245','-1l':'g245','-1t':'n156','سمش':'hcm'}
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
# 3rd field carries the task id so the list generator can highlight the
# EXACT started row, not every row sharing its annotation-stripped name —
# two Todoist tasks named e.g. "AoS" (a recurring one + an unrelated one-off)
# both matched the old name-only comparison, so starting either one flagged
# BOTH as running (bug 2026-07-19: "I started one AoS task, and it marked
# both as in progress").
# 4th field carries the resolved project code so the footer ticker can color
# the running line in the project's palette color without waiting for (or
# hitting) the Toggl poll (feature 2026-07-24).
printf '%s\t%s\t%s\t%s\n' "\$clean" "\$(date +%s)" "\$1" "\$project" > "\$TIMER"
# Started-today log (2026-10-13): the list keeps every task started today and
# not yet done at the top, even after the timer moves to another task (🧮 is
# the running timer's alone since 2026-10-30).
_sd="\${XDG_STATE_HOME:-\$HOME/.local/state}/jm"
mkdir -p "\$_sd" 2>/dev/null
printf '%s\t%s\t%s\n' "\$(date +%Y-%m-%d)" "\$1" "\$(date +%s)" >> "\$_sd/dtd-started" 2>/dev/null
# DTD_FORM runs this in the BACKGROUND while a prompt_toolkit form owns the
# tty (2026-10-18: Enter on -1g "flashes through a bunch of screens and is
# not fast" -- the two Toggl calls sat on the form's critical path). Skip
# every /dev/tty touch below (the drain loop would eat form keystrokes, the
# mouse reset would kill the form's mouse support) and the HDR write (the
# form's own exit status is the one that matters there).
[[ -n "\$DTD_SKIP_TTY_DRAIN" ]] && exit 0
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the two Toggl
# API calls above — leaked SGR motion sequences type themselves into fzf's
# query as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05,
# ported here from done.sh/defer.sh/edit.sh/split.sh).
# DRAIN_ECHO note (bug 2026-09-24, "input box goes weird when dtd is busy"):
# fzf's execute-silent/transform/reload path calls Pause(false) once the
# command has blocked for 1s (terminal.go blockDuration) -- that RESTORES the
# tty to its original cooked+ECHO state. Click bytes queued while fzf was
# still in raw mode become PENDIN input, and every 'read -k 1' iteration
# below flips the tty raw->cooked->raw, which makes the line discipline
# re-process (and re-ECHO) the still-unread tail each time: 3 clicks (36
# bytes) painted ~100 suffix fragments (;18;19M[<0;18;19M<0;18;19M...)
# straight over the list and the input box. fzf's Resume(false) only
# reprints the prompt line, so the mess stayed. Turning ECHO off before the
# first read makes the re-processing silent; the loop then drains cleanly.
# Reproduced and verified in test_dtd_drain_no_echo.py (pty harness).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
echo "▶ Started: \$clean → \$project" > "\$HDR"
STARTEOF
chmod +x "$DTD_START"

# --- Enter script: ALWAYS starts the selected task's timer, never completes
# it (2026-07-31 user request: "I always have to hit opt+enter to mark a
# task done, not enter twice" -- enter's OTHER job, starting a timer, made a
# second enter press on an already-timing item complete it, which was too
# easy to trigger by accident. alt-enter ($DTD_DONE_ROUTER) is now the ONLY
# way to mark anything done, ritual cards included -- $DTD_START already
# resolves a 😈-prefixed ritual card to its correct Toggl project (see its
# own RITUAL_DOMAIN block below), so pressing enter on one just starts a
# properly-labeled timer instead of completing it. ---
DTD_ENTER="/tmp/dtd-$DTD_ID.enter.sh"
cat > "$DTD_ENTER" << ENTEREOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
START="$DTD_START"
task="\$1"
# Picker mode: enter on a block row applies the snooze (2026-07-27)
if [[ "\$1" == BLOCK:* ]]; then
  "$DTD_BLOCKAPPLY" "\$1"
  exit 0
fi
# Pass the id, not the resolved text: start.sh resolves it itself and logs
# its arg to dtd-started, which the list matches by id (bug 2026-10-15:
# Enter logged the task name, so a started task never got 🧮 or moved up).
"\$START" "\$1"
ENTEREOF
chmod +x "$DTD_ENTER"

# --- Inline survey form wrapper, run by fzf execute() from the Enter router ---
# (2026-10-16: "hitting enter on that sort of task from dtd will open the form
# in that cmd line ... when it escapes I go back to the main panel"). Args:
# <id> <form.py> [form args...]. Starts the task's timer exactly as a plain
# Enter does ($DTD_ENTER), then runs the full-screen prompt_toolkit form on
# this tty; fzf repaints the list when it exits. Same tty hygiene as every
# other interactive execute() script: clear the stale frame first (cmux keeps
# fzf's last frame on screen), and on the way out reset the mouse-tracking
# modes the form enabled and drain queued input so nothing types itself into
# the query. A failing form (non-zero exit: blocked week, write error,
# traceback) holds its output on screen until a key is pressed -- fzf's
# redraw would otherwise erase the only explanation instantly.
DTD_FORM="/tmp/dtd-$DTD_ID.form.sh"
cat > "$DTD_FORM" << FORMEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}form\${_T}\$1\${_T}\${2:t}" >> "$DTD_EVENTS" 2>/dev/null
id="\$1"; form="\$2"; shift 2
HDR="$DTD_HDR"
export DTD_HDR_FILE="\$HDR"   # forms that finish in the background report here
stty sane susp undef < /dev/tty 2>/dev/null
printf "\033[2J\033[H⏳ \${form:t}…" > /dev/tty
# Timer start runs in the BACKGROUND (2026-10-18: "flashes through a bunch of
# screens and is not fast" -- its two serial Toggl HTTP calls held a blank
# screen for seconds before the form appeared). DTD_SKIP_TTY_DRAIN makes
# start.sh exit before its /dev/tty block (drain loop steals form keystrokes
# otherwise); </dev/null so no child inherits the tty as stdin (SIGTTIN);
# &! disowns so a fast Esc exiting this wrapper can't SIGHUP it mid-start.
DTD_SKIP_TTY_DRAIN=1 "$DTD_ENTER" "\$id" </dev/null >/dev/null 2>&1 &!
# The form's stdin must be the REAL pty device, not /dev/tty: on macOS kqueue
# refuses /dev/tty (EINVAL), and prompt_toolkit's event loop add_reader()s
# stdin, so every inline form died with "OSError: [Errno 22] Invalid
# argument" (2026-10-17, -1g form). ttyname() of an fd opened on /dev/tty is
# just "/dev/tty" again, so resolve the controlling terminal through ps.
_tty=/dev/\$(ps -o tty= -p \$\$ 2>/dev/null | tr -d ' ')
[[ "\$_tty" == /dev/ttys* ]] || _tty=/dev/tty
python3 "\$form" "\$@" < "\$_tty" > "\$_tty" 2>&1
rc=\$?
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
if (( rc )); then
  printf '\n[%s exited %d] press any key to return to dtd' "\${form:t}" "\$rc" > /dev/tty
  read -k 1 _discard < /dev/tty 2>/dev/null
  echo "✗ \${form:t} exited \$rc" > "\$HDR"
else
  echo "✓ \${form:t}" > "\$HDR"
fi
exit 0
FORMEOF
chmod +x "$DTD_FORM"

# Variable 1n+ habits must prompt for minutes on completion — their points
# are base + rate×minutes, so a silent complete lands at base/zero (bug
# 2026-07-26: s897 / family / "1 kids nature" never asked). The name list
# lives in did-fast (VARIABLE_1N + the ONENEON_ALIASES that point at it);
# import it at launch so dtd can't drift from the routing source of truth.
# Emits a case-ready alternation of QUOTED, {N}-stripped, lowercase names
# ('"1 kids nature"|"aos"|…' — quoting keeps multi-word names one pattern).
DTD_VAR1N_PAT=$(python3 - "$DID_FAST" <<'VARPY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("df_var", sys.argv[1])
df = importlib.util.module_from_spec(spec)
sys.modules["df_var"] = df
spec.loader.exec_module(df)
norm = {df.header_normalize(n) for n in df.VARIABLE_1N}
names = {n.lower() for n in df.VARIABLE_1N}
names |= {a.lower() for a, t in df.ONENEON_ALIASES.items()
          if df.header_normalize(t) in norm}
names = {re.sub(r"\s*\{\d+\}", "", n).strip() for n in names}
print("|".join('"%s"' % n for n in sorted(names)))
VARPY
)
# A failed import must not write a syntactically-broken `) ;;` case branch.
[[ -z "$DTD_VAR1N_PAT" ]] && DTD_VAR1N_PAT='"__no_variable_1n__"'
# Variable 1n+ habits with NO base points (family, s897, 业写, …: row 5 is a
# bare "1/m" rate). For these a blank answer means nothing to credit, and
# did-fast now refuses to complete them without minutes (bug 2026-10-03:
# blank fell through to a 0分 reference to the rate text, "+'1n+'!AF5"), so
# the prompt re-asks instead of accepting blank.
DTD_VAR1N_NOBASE_PAT=$(python3 - "$DID_FAST" <<'VARPY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("df_var_nb", sys.argv[1])
df = importlib.util.module_from_spec(spec)
sys.modules["df_var_nb"] = df
spec.loader.exec_module(df)
based = {df.header_normalize(n) for n in list(df.VARIABLE_1N_BASES) + list(df.VARIABLE_1N_DEFAULTS)}
nobase = {df.header_normalize(n) for n in df.VARIABLE_1N} - based - {df.header_normalize(n) for n in df.THRESHOLD_1N}
names = set(nobase)
names |= {a.lower() for a, t in df.ONENEON_ALIASES.items() if df.header_normalize(t) in nobase}
names = {re.sub(r"\s*\{\d+\}", "", n).strip() for n in names}
print("|".join('"%s"' % n for n in sorted(names)))
VARPY
)
[[ -z "$DTD_VAR1N_NOBASE_PAT" ]] && DTD_VAR1N_NOBASE_PAT='"__no_variable_1n__"'

# --- Complete-now script used by fzf alt-enter binding (ctrl+enter via the
# Ghostty keybind remap ctrl+enter -> ESC CR). Unlike enter, this never starts
# a timer: it always completes the selected task via the /did worker. ---
DTD_DONE="/tmp/dtd-$DTD_ID.done.sh"
cat > "$DTD_DONE" << DONEEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
HDR="$DTD_HDR"
FIFO="$DTD_FIFO"
SESSION="$DTD_SESSION"
PUSHED="$DTD_PUSHED"
REMOVED="$DTD_REMOVED"
TIMER="$DTD_TIMER"
setopt extendedglob
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\tdone-start\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
task="\$1"
# Picker mode: ⌃⏎ on a block row applies the snooze too (2026-07-27)
if [[ "\$1" == BLOCK:* ]]; then
  "$DTD_BLOCKAPPLY" "\$1"
  exit 0
fi
# Picker mode: enter on a block row applies the snooze (2026-07-27)
if [[ "\$1" == BLOCK:* ]]; then
  "$DTD_BLOCKAPPLY" "\$1"
  exit 0
fi
# Content arrives base64-encoded as \$2 from the done-router (2026-10-02),
# which already looked it up with jq -- decode instead of re-resolving via
# python3. Fall back to the resolver for any caller that passes only the id.
task=""
# Resolved content: file from the router (builtin read), else its base64 arg,
# else the python resolver. Zero execs on the common path (2026-10-03).
[[ -n "\$1" && -r "${DTD_RESOLVED_DIR:-/nonexistent}/\$1" ]] && task="\$(<"${DTD_RESOLVED_DIR:-/nonexistent}/\$1")"
[[ -n "\$task" || -z "\$2" ]] || task=\$(printf '%s' "\$2" | base64 -d 2>/dev/null)
[[ -n "\$task" ]] || task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
print -- "\$EPOCHREALTIME\tdone-resolved\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# Annotation stripping in zsh parameter expansion (extendedglob), replacing
# four echo|sed|tr pipelines (~8 execs). Same rules as the old sed: drop
# " (N)", " [N]", " [x/m]" (and " {N}" for the filter form), squeeze runs
# of spaces, trim the tail; lowercase; strip a dated copy's " M.D" stamp.
clean="\${task//( #\\([0-9]#\\))/}"
clean="\${clean//( #\\[[0-9]#\\])/}"
clean="\${clean//( #\\[[0-9.+]#\\/m\\])/}"
clean="\${clean//  ##/ }"; clean="\${clean%% ##}"
clean_for_filter="\${clean//( #\\{[0-9]#\\})/}"
clean_for_filter="\${clean_for_filter//  ##/ }"; clean_for_filter="\${clean_for_filter%% ##}"
clean_lower="\${(L)clean_for_filter}"
clean_base="\${clean_lower% [0-9](#c1,2).[0-9](#c1,2)}"
_dated=""
[[ "\$clean_base" != "\$clean_lower" ]] && _dated=1
# Tasks that ask for a value on completion (like cpap). The typed number is
# appended so did-fast writes it to the task's own 0n column: cpap = 1-3 sleep
# quality; xk20/xk22/xk26 = minutes with Theo/Ren/Rori, prompted as "分" since
# 0分!X sums 0n!AJ:AO 1:1 with no rate conversion (a minute with a kid IS a
# 分, 2026-09-07 per JM); i444 = count, where an explicit 0 records "none
# needed today" (blank would default to 1 in did-fast). Needs a tty, so the
# router (below) sends these to execute, not execute-silent. Blank input
# just completes with no number.
_ip=""
case "\$clean_base" in
  cpap) _ip="CPAP quality (1-3)";;
  xk20) _ip="xk20 分 (Theo)";;
  xk22) _ip="xk22 分 (Ren)";;
  xk26) _ip="xk26 分 (Rori)";;
  i444) _ip="i444 count (0 = none today)";;
  hiit) _ip="hiit minutes";;
  新闻) _ip="新闻 minutes";;
  "evening hcmc"|"night hcmc") _ip="night hcmc minutes";;
  ${DTD_VAR1N_NOBASE_PAT}) _ip="\$clean_base minutes";;
  ${DTD_VAR1N_PAT}) _ip="\$clean_base minutes (blank = base points)";;
esac
if [[ -n "\$_ip" && -r /dev/tty ]]; then
  # fzf leaves the alternate screen for execute(), but what the terminal shows
  # then is not guaranteed: cmux keeps the stale fzf frame on screen, so the
  # prompt was invisible and the user pressed ⌃⏎ blind (bug 2026-07-21). Clear
  # to home so the question is the only thing visible, and force sane tty modes
  # so Enter always terminates the read.
  stty sane susp undef < /dev/tty 2>/dev/null
  printf '\033[2J\033[H→ %s: ' "\$_ip" > /dev/tty
  read _iv < /dev/tty
  # Digits only: a blind ⌃⏎ (ESC CR) lands a literal ESC byte in the answer,
  # which would ride into the completion name ("CPAP ␛") and break did-fast's
  # task match. Any garbage → empty → completes with no score, as documented.
  _iv=\${_iv//[^0-9]/}
  # No-base rate habits (family: "1/m") need a number — re-ask on blank.
  case "\$clean_base" in
    ${DTD_VAR1N_NOBASE_PAT})
      while [[ -z "\$_iv" ]]; do
        printf '→ %s minutes (needed: points are per minute): ' "\$clean_base" > /dev/tty
        read _iv < /dev/tty || break
        _iv=\${_iv//[^0-9]/}
      done;;
  esac
  if [[ -n "\$_iv" ]]; then
    if [[ -n "\$_dated" ]]; then
      clean="\$clean [\$_iv]"
    else
      clean="\$clean \$_iv"
    fi
  fi
fi
echo "\$clean_for_filter" >> "\$SESSION"
# Optimistic hide by ID, never by name (see enter.sh — bug 2026-07-24:
# name-hide suppressed BOTH same-named "AoS" copies). Name-write only as
# an id-less fallback.
if [[ -n "\$1" ]]; then
  echo "\$1" >> "\$REMOVED.ids"
else
  echo "\$clean_for_filter" >> "\$REMOVED"
fi
# Immediate Todoist close for NON-recurring tasks (2026-07-28): the serial
# FIFO worker runs a full did-fast per completion (Excel over ssh, 5-45s
# each) with the Todoist close LAST, so a completion burst left the later
# cards open in Todoist for minutes ("player retention still in todoist but
# not in dtd"). Fire-and-forget; did-fast's later close is idempotent.
# Recurring cards are skipped inside quick-close (double-close would
# double-advance the recurrence — the 2026-06-27 drift class).
if [[ -n "\$1" ]]; then
  (python3 "$HOME/i446-monorepo/tools/did/quick-close.py" "\$1" "$DTD_CACHE_FILE" >/dev/null 2>&1 &)
fi
echo "x" >> "\$PUSHED"
# Push audit trail — see enter.sh's twin line (2026-07-30 lost -1t/-1l).
strftime -s _now '%Y-%m-%dT%H:%M:%S' \$EPOCHSECONDS 2>/dev/null || _now=\$EPOCHSECONDS
printf '%s\tdone\t%s\t%s\n' "\$_now" "\$1" "\$clean" >> "\$PUSHED.log"
# Only clear the running-timer cache when the task just completed is the one
# it's tracking — matched by id like the list generator's own running-highlight
# (falling back to name when id-less). Clearing it unconditionally blanked the
# footer for ~5-12s (until the next Toggl poll reconciled it) any time an
# UNRELATED task was completed while a different timer kept running (bug
# 2026-08-13: "timer goes blank for 5 seconds ... if I'm not changing the
# timer, it shouldn't flash").
_tline=""; [[ -r "\$TIMER" ]] && _tline="\$(<"\$TIMER")"
_tf=("\${(@ps:\t:)\${_tline%%\$'\n'*}}")   # first line, tab-split (builtin, was cut)
_timer_id="\${_tf[3]}"
if [[ -n "\$1" && -n "\$_timer_id" ]]; then
  [[ "\$_timer_id" == "\$1" ]] && : > "\$TIMER"
elif [[ -z "\$1" ]]; then
  _timer_desc="\${(L)_tf[1]}"
  [[ -n "\$_timer_desc" && "\$_timer_desc" == "\$clean_lower" ]] && : > "\$TIMER"
fi
echo "⏳ completing: \$clean_for_filter" > "\$HDR"
printf '%s\t%s\n' "\$1" "\$clean" > "\$FIFO"
print -- "\$EPOCHREALTIME\tdone-fifo\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# Reset stray mouse-tracking modes AND drain tty input queued during the
# prompt window — this was the ONLY interactive execute() script without the
# defer/edit/split cleanup, so scroll/motion bursts buffered while the value
# prompt was open dumped into fzf's query as literal ^[[<34;x;yM text on
# resume (bug 2026-07-27: "input pane in dtd is a mess").
# Only the value-prompt path handed the tty to a child (stty sane + read); the
# execute-silent path never touched it, so the reset+drain (one stty exec)
# is skipped there (2026-10-03).
if [[ -n "\$_ip" ]]; then
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
fi
print -- "\$EPOCHREALTIME\tdone-end\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
DONEEOF
chmod +x "$DTD_DONE"

# --- Fast-path optimistic hide, split out of done.sh (2026-08-01) ---
# fzf's own man page: "execute-silent... fzf will not be responsive until the
# command is complete. For asynchronous execution, start your command as a
# background process." done.sh was NOT backgrounded, so a rapid second
# alt-enter landing while fzf was still blocked on the FIRST done.sh
# invocation was silently lost -- never even reaching the FIFO (ruled out the
# pipe itself: stress-tested dtd's exact reader construct to 100 concurrent
# writers with zero loss; the loss is fzf not accepting the keypress at all
# while unresponsive). This bug (2026-07-31/08-01: "-1t/-1l marked done but
# no -1n points") is what the FIFO-invariant checker above was built to
# detect -- this is the actual fix, closing the window instead of just
# reporting it.
#
# The router below now runs THIS tiny script synchronously (near-instant --
# no python3, just the id-based hide reload() needs to see immediately),
# then backgrounds the FULL done.sh for everything else (resolve, quick-close,
# FIFO push, tty-drain). done.sh still does its own copy of this same hide
# when it runs a moment later -- a duplicate id line in $REMOVED.ids is a
# harmless no-op (set-membership check, not a counter). The value-prompt
# habits (cpap/xk20/...) are unaffected: the router still sends those through
# execute (foreground, needs a real tty for the prompt), never this path.
DTD_DONE_HIDE="/tmp/dtd-$DTD_ID.done-hide.sh"
cat > "$DTD_DONE_HIDE" << HIDEEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
REMOVED="$DTD_REMOVED"
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\thide\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
if [[ -n "\$1" ]]; then
  echo "\$1" >> "\$REMOVED.ids"
fi
HIDEEOF
chmod +x "$DTD_DONE_HIDE"

# --- Done ROUTER used by the fzf alt-enter (⌃⏎) binding via `transform` ---
# cpap + xk20/xk22/xk26 + i444 prompt for a value on completion and so need a tty —
# route them → execute (which gives the DONE script a terminal) and every other
# task → execute-silent, running the fast hide above then done.sh itself.
#
# done.sh runs WITHOUT a trailing `&` (2026-08-03, reverting the 2026-08-01
# fast-hide/background split's own backgrounding of done.sh — done-hide.sh
# alone still gives the instant-hide UI win). fzf's own man page is explicit
# that execute-silent blocks fzf until the command completes and says the fix
# for that is to background the command yourself — which is exactly what
# invited this bug: `command &` inside execute-silent detaches done.sh as a
# grandchild that isn't part of the action's own tracked lifetime, and lands
# in whatever process-group/session teardown fzf (or the surrounding
# terminal/session) does once the action's own foreground portion returns.
# Confirmed live 2026-08-03: task 6hCHH5g2FG7rw7X2 ("get to a conclusion on
# biowar {10}") got as far as done.sh's $PUSHED.log line (proving done.sh
# itself ran) but its FIFO push never reached the worker (absent from
# $DTD_PROCESSED_IDS) and did-fast never ran (zero ledger entries on ix for
# "biowar") — a clean, total loss, not a slow-tail race. Reproduced the
# general mechanism directly against fzf: a bare `&`-backgrounded child of an
# execute-silent action reliably fails to complete even a first `echo`
# statement, survives neither nohup nor a real new session/process group
# (python3 subprocess.Popen(start_new_session=True)), regardless of whether
# fzf itself exits or stays alive across a later action. Running done.sh
# synchronously (chained by `;`, no `&`) means fzf can't move on until
# done.sh's own process — not a detached grandchild — actually exits, closing
# the loss window entirely; the cpap/xk20/xk22/xk26/i444 value-prompt tasks
# have called done.sh this same synchronous way via bare execute(...) since
# this script existed and have never shown this failure mode. Cost: done.sh's
# own ~65-100ms python3-resolve (dtd_resolve.py) tail is back in the
# synchronous window fzf blocks on — worse for perceived input latency, but a
# bounded, known cost against an unbounded, silent data loss.
#
# The router emits ONLY the execute/execute-silent
# action; the reload/clear-query/transform-header chain stays in the binding
# where $DTD_RELOAD/$DTD_HDRGEN are live. Baking the resolved id into the emitted
# action keeps the transform output free of fzf placeholders; task ids are
# alphanumeric, so no quoting is needed around \$_id.
#
# jq, not python3, for the id->content lookup (2026-08-02): this whole script
# runs as fzf's `transform` action on EVERY alt-enter, synchronously, BEFORE
# fzf is ready to accept the next key -- the one part of the chain the
# 2026-08-01 fast-hide/background split (above) never touched, because that
# fix was about done.sh's OWN tail, not this router's own front. Measured
# live: a python3 interpreter start for dtd_resolve.py's one-id JSON lookup
# costs ~65-100ms; the equivalent jq query costs ~5ms (same output). A
# rapid-fire triple alt-enter 1-2s apart still lost 2 of 3 completions the
# same day as the fast-hide fix (task 6hC77PCj8M3VhGPc "-1l" + 6hC77P8gJ2cgV8m6
# "-1ibx"), so this router-side cost is real, additive latency in the exact
# window that's already been shown to matter -- cutting it doesn't prove the
# race is fully closed, but it's a genuine, measured reduction in it, not a
# refactor for its own sake.
DTD_DONE_ROUTER="/tmp/dtd-$DTD_ID.done-router.sh"
cat > "$DTD_DONE_ROUTER" << ROUTEREOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}done\${_T}\${(j:,:)@}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
_id="\$1"
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\trouter-start\t\$_id" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# Pick record (2026-10-17): action, row id, the query it was found under,
# and where it sat in the UNFILTERED list, 1 = top (\$DTD_ORDER: one id per
# line, written by the list generator). Builtins only: (ie) finds the row.
_rec_pick() {
  local _a="\$1" _i="\$2" _q="\$3" _pos="" _n=0
  if [[ -r "$DTD_ORDER" ]]; then
    local -a _rows; _rows=("\${(@f)\$(<"$DTD_ORDER")}"); _n=\${#_rows}
    _pos=\${_rows[(ie)\$_i]}; (( _pos > _n )) && _pos=""
  fi
  _q="\${_q//\\\\/\\\\\\\\}"; _q="\${_q//\\"/\\\\\\"}"
  print -r -- "{\\"ts\\":\$EPOCHREALTIME,\\"host\\":\\"$DTD_HOSTSLUG\\",\\"sid\\":\\"$DTD_ID\\",\\"kind\\":\\"pick\\",\\"action\\":\\"\$_a\\",\\"id\\":\\"\$_i\\",\\"q\\":\\"\$_q\\",\\"pos\\":\${_pos:-null},\\"n\\":\$_n}" >> "$DTD_RECLOG" 2>/dev/null
}
_qq=""; [[ -r "$DTD_QUERY" ]] && _qq="\$(<"$DTD_QUERY")"
[[ "\$_id" == BLOCK:* ]] || _rec_pick done "\$_id" "\$_qq"
# ONE jq pass (2026-10-02): id lookup + annotation strip + lowercase +
# dated-suffix strip ("xk26 7.21" -> "xk26", see \$DTD_DONE) + base64 of the
# raw content, replacing the old jq/sed/tr/base64 pipeline (7 execs). Every exec
# here runs synchronously before the row can vanish, and each is a chance
# for a Defender scan stall (measured 2026-10-02: ~0.05s typical, 0.4-1.4s
# spikes), so the count matters more than the work.
_out=\$(jq -r --arg id "\$_id" '
  (([.[] | select(type=="array")[] | select(.id == \$id) | .content] | first) // \$id) as \$raw
  | (\$raw
     | gsub(" *\\\\([0-9]*\\\\)"; "") | gsub(" *\\\\[[0-9]*\\\\]"; "")
     | gsub(" *\\\\[[0-9.+]*/m\\\\]"; "") | gsub(" *\\\\{[0-9]*\\\\}"; "")
     | gsub("  +"; " ") | sub(" *\$"; "") | ascii_downcase
     | sub(" [0-9]{1,2}\\\\.[0-9]{1,2}\$"; "")),
    (\$raw | @base64)' "$DTD_CACHE_FILE" 2>/dev/null)
_t_base="\${_out%%\$'\\n'*}"
_b64="\${_out#*\$'\\n'}"
[[ "\$_b64" == "\$_out" ]] && _b64=""   # jq failed: let done.sh resolve itself
# Instant hide (2026-10-02): lead the emitted chain with fzf's own 'exclude'
# so the row vanishes client-side the moment this router returns, instead
# of after done.sh + the list regen (1-3s, longer under load). The hide file
# / reload that follow only confirm it. Picker rows are not excluded (they
# are regenerated by the reload anyway). The resolved content rides along
# base64-encoded (action-string safe) so done.sh skips its own python
# re-resolve of the same id.
# Hand the raw content to done.sh as a file too (builtin read there; the
# base64 arg below stays as the fallback and for the action-string tests).
[[ "\$_id" == BLOCK:* ]] || print -r -- "\$_raw" > "${DTD_RESOLVED_DIR:-/tmp}/\$_id" 2>/dev/null
_ex="exclude+"
[[ "\$_id" == BLOCK:* ]] && _ex=""
print -- "\$EPOCHREALTIME\trouter-resolved\t\$_t_base" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
case "\$_t_base" in
  cpap|xk20|xk22|xk26|i444|hiit|新闻|"evening hcmc"|"night hcmc"|${DTD_VAR1N_PAT})
    printf '%sexecute(%s %s %s)' "\$_ex" "$DTD_DONE" "\$_id" "\$_b64" ;;
  *)
    printf '%sexecute-silent(%s %s; %s %s %s >/dev/null 2>&1)' "\$_ex" "$DTD_DONE_HIDE" "\$_id" "$DTD_DONE" "\$_id" "\$_b64" ;;
esac
print -- "\$EPOCHREALTIME\trouter-end\t\$_id" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
ROUTEREOF
chmod +x "$DTD_DONE_ROUTER"

# --- Defer script used by fzf ctrl-d binding ---
DTD_DEFER="/tmp/dtd-$DTD_ID.defer.sh"
cat > "$DTD_DEFER" << DEFEREOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}defer\${_T}\${(j:,:)@}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
DEFER_FAST="\$HOME/i446-monorepo/tools/did/defer-fast.py"
HDR="$DTD_HDR"
REMOVED="$DTD_REMOVED"
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\tdefer-start\t\$#" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# Multi-select (2026-07-23): the ctrl-d binding passes {+2} — every
# shift-marked row's id, or just the cursor row's id when nothing is marked
# (the single-task path is the 1-element case of the same loop). Resolve
# every id up front, prompt ONCE for the defer target, then fan out to the
# same per-task detached worker as before.
typeset -a ids names _want
for _tid in "\$@"; do
  [[ -n "\$_tid" ]] || continue
  [[ "\$_tid" == BLOCK:* ]] && continue   # picker rows are not tasks
  _want+=("\$_tid")
done
# Pick record (2026-10-17): action, row id, the query it was found under,
# and where it sat in the UNFILTERED list, 1 = top (\$DTD_ORDER: one id per
# line, written by the list generator). Builtins only: (ie) finds the row.
_rec_pick() {
  local _a="\$1" _i="\$2" _q="\$3" _pos="" _n=0
  if [[ -r "$DTD_ORDER" ]]; then
    local -a _rows; _rows=("\${(@f)\$(<"$DTD_ORDER")}"); _n=\${#_rows}
    _pos=\${_rows[(ie)\$_i]}; (( _pos > _n )) && _pos=""
  fi
  _q="\${_q//\\\\/\\\\\\\\}"; _q="\${_q//\\"/\\\\\\"}"
  print -r -- "{\\"ts\\":\$EPOCHREALTIME,\\"host\\":\\"$DTD_HOSTSLUG\\",\\"sid\\":\\"$DTD_ID\\",\\"kind\\":\\"pick\\",\\"action\\":\\"\$_a\\",\\"id\\":\\"\$_i\\",\\"q\\":\\"\$_q\\",\\"pos\\":\${_pos:-null},\\"n\\":\$_n}" >> "$DTD_RECLOG" 2>/dev/null
}
_qq=""; [[ -r "$DTD_QUERY" ]] && _qq="\$(<"$DTD_QUERY")"
for _tid in "\${_want[@]}"; do _rec_pick defer "\$_tid" "\$_qq"; done
# ONE jq pass resolves every id -> cleaned name (2026-10-02). The per-id
# python3 resolve + sed it replaces cost ~0.2s per task (0.6s for three,
# more under a Defender exec-scan stall), all of it before the prompt could
# appear. Unknown ids fall back to themselves, as the resolver did; a
# middle-truncated name keeps only the prefix before the ellipsis.
if (( \${#_want[@]} )); then
  _rows=("\${(f)\$(jq -r --args '
  . as \$d | \$ARGS.positional[] as \$id
  | (([\$d[] | select(type=="array")[] | select(type=="object" and .id == \$id) | .content] | first) // \$id)
  | gsub(" *[(][0-9]*[)]"; "") | gsub(" *[[][0-9]*[]]"; "")
  | gsub(" *[[][0-9.+]*/m[]]"; "") | gsub(" *[{][0-9]*[}]"; "")
  | gsub("  +"; " ") | sub(" *\$"; "") | sub("….*\$"; "")
  | [\$id, .] | @tsv' "\${_want[@]}" < "$DTD_CACHE_FILE" 2>/dev/null)}")
  for _row in "\${_rows[@]}"; do
    [[ -n "\$_row" ]] || continue
    ids+=("\${_row%%\$'\t'*}"); names+=("\${_row#*\$'\t'}")
  done
fi
(( \${#ids[@]} )) || exit 0
label="\${names[1]}"
(( \${#ids[@]} > 1 )) && label="\${#ids[@]} tasks (\${(j:, :)names})"
# Prompt for the defer target — N days or an absolute date; empty/0 = "auto":
# recurring tasks skip to their next occurrence, non-recurring default to +1
# day (0 = today). Gated on DTD_DEFER_PROMPT, which only dtd's fzf session
# exports: the test harness and any scripted caller run the script with the
# flag unset and get the non-interactive default.
days=""
prompted=""
# DTD_DEFER_DAYS (2026-10-04): the unified schedule screen (ctrl-d) already
# collected the target inside fzf, so take it as an explicit human choice
# and never open the tty prompt — this script then runs under fzf's
# execute-silent/transform, where touching /dev/tty would steal fzf's keys.
_preset="\${DTD_DEFER_DAYS:-}"
if [[ -n "\$_preset" ]]; then
  days="\$_preset"
  prompted=1
elif [[ -n "\${DTD_DEFER_PROMPT:-}" && -r /dev/tty ]]; then
  # fzf leaves the alternate screen for execute(), but what the terminal
  # shows then is not guaranteed: cmux keeps the stale fzf frame on screen,
  # so the prompt is invisible and arrow keys land in a blind 'read' that
  # doesn't navigate anything (bug 2026-07-21, fixed for done.sh's
  # value-prompt but never ported here — "dtd is locked on the text input
  # screen, and I can't navigate items in the fzf", 2026-09-03). Clear to
  # home so the question is the only thing visible, and force sane tty
  # modes so Enter always terminates the read.
  stty sane susp undef < /dev/tty 2>/dev/null
  printf "\033[2J\033[H\nDefer '%s' by N days / YYYY-MM-DD (blank or 0 = next occurrence if recurring, no copy created)> " "\$label" > /dev/tty
  print -- "\$EPOCHREALTIME\tdefer-prompt\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
  read days < /dev/tty
  prompted=1
  # Reset any mouse-tracking mode a child enabled, and drain any bytes
  # already queued in the tty buffer from scroll/click events during the
  # prompt above — leaked SGR motion sequences type themselves into fzf's
  # query as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05;
  # drain half of the fix was 2026-07-27 on done.sh/split.sh but never
  # ported here). Runs immediately after the read, before the validation
  # branch below can exit 0 on invalid input and skip cleanup entirely.
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
  stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
  while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
fi
days=\${days// /}
if [[ -n "\$prompted" ]]; then
  # A live human actually saw the prompt and left it blank/0 — that IS an
  # explicit "skip this occurrence, no dated copy" choice. Validate whatever
  # they typed.
  [[ -z "\$days" ]] && days=auto
  case "\$days" in
    auto|<->|[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) echo "✗ invalid defer target: \$days (cancelled)" > "\$HDR"; exit 0 ;;
  esac
else
  # No tty to prompt on (scripted/automated caller — DTD_DEFER_PROMPT unset,
  # e.g. an agent driving dtd non-interactively): never manufacture "auto"
  # (skip this occurrence, no dated copy) on a caller's behalf who never got
  # to choose it. Leave days empty so defer-fast.py's OWN default applies
  # instead — +1 day, WITH a dated one-off copy of the current occurrence
  # (bug fixed 2026-08-09: this unconditional empty->auto rewrite silently
  # skip-deferred weekly 1neon habits — e.g. "1s" vanished for a week with no
  # dated stand-in and no trace beyond a 0-point "deferred: ... → next
  # occurrence" posthoc — any time dtd's ctrl-d binding ran without a live
  # terminal to prompt on).
  days=""
fi
defer_label="+\$days"
if [[ "\$days" == "auto" || "\$days" == "0" ]]; then
  defer_label="auto"
elif [[ -z "\$days" ]]; then
  defer_label="+1 (default)"
fi
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
#
# Each batch member gets its OWN worker + journal entry, so ctrl-z undoes
# them one at a time in reverse. (Two FAILING workers rolling back
# concurrently could race on the .ids rewrite — failure-path only, rare,
# self-corrects at the next day's file.)
for i in {1..\${#ids[@]}}; do
  tid="\${ids[\$i]}"
  clean="\${names[\$i]}"
  echo "\$tid" >> "\$REMOVED.ids"
  echo "⏳ deferring (\$defer_label): \$clean" > "\$HDR"
  echo "x" >> "$DTD_PUSHED"
  (
    result=\$(python3 "\$DEFER_FAST" --id "\$tid" "\$days" 2>/dev/null)
    ok=\$(echo "\$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'→ {d[\"target_date\"]} [{d[\"claimed_points\"]}] today / [{d[\"remaining_points\"]}] later')" 2>/dev/null)
    if [[ -n "\$ok" ]]; then
      # Journal for ctrl-z undo
      echo "\$result" | python3 "$UNDO_FAST" --journal-defer "$DTD_JOURNAL" "\$clean" 2>/dev/null
      echo "⏭ \$clean \$ok" > "\$HDR"
    else
      # Roll back the optimistic hide so the task reappears on next reload
      grep -v -x -F -- "\$tid" "\$REMOVED.ids" > "\$REMOVED.ids.tmp" 2>/dev/null
      mv "\$REMOVED.ids.tmp" "\$REMOVED.ids"
      echo "? defer failed: \$clean (restored to list)" > "\$HDR"
    fi
    echo "x" >> "$DTD_PROCESSED"
  ) >/dev/null 2>&1 &!
done
# Reset any mouse-tracking mode a child enabled — leaked SGR motion
# sequences type themselves into fzf's query (bug 2026-07-05). Also drain
# any bytes already queued in the tty buffer from scroll/click events during
# the "Defer by N days" prompt above — the reset alone only stops FUTURE
# events, it doesn't clear ones already sitting in the buffer, which fzf
# then reads as literal ^[[<0;16;15M text on resume (same class of bug as
# done.sh/split.sh, 2026-07-27, just never ported to this script).
# Skipped for a preset (schedule-screen) defer: no prompt ran, and fzf still
# owns the tty, so draining here would eat the user's next keystrokes.
if [[ -z "\$_preset" ]]; then
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
fi

print -- "\$EPOCHREALTIME\tdefer-end\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
DEFEREOF
chmod +x "$DTD_DEFER"

# --- Block-delay (ctrl-v): fzf-NATIVE picker (rearchitected 2026-07-27) ---
# Same-day delay: HIDE the task until a chosen 地支 block starts today. Ids
# land in $STATE_DIR/dtd-block-snooze.json ({date, snoozes: {id: start_hour}});
# the list generator filters them until the hour arrives and the watcher's
# block-boundary refresh reloads, so snoozed tasks reappear on their own.
#
# Every nested-UI variant of the picker FAILED under cmux (inner fzf never
# painted 2026-07-26 ×2; the printf/read menu was invisible and its blind
# keystrokes skipped tasks 2026-07-27). So the picker now IS the outer fzf:
# ctrl-v "arms" picker mode ($DTD_BLOCKPICK holds the pending ids) and the
# list generator swaps the task rows for block rows (⏰ 申 shen 14:00–15:59,
# searchable by pinyin/汉字). Enter (or ⌃⏎) on a block row applies the snooze
# via $DTD_BLOCKAPPLY and restores the normal list. Nothing ever draws outside
# fzf, so there is nothing cmux can fail to paint.
DTD_BLOCKARM="/tmp/dtd-$DTD_ID.blockarm.sh"
cat > "$DTD_BLOCKARM" << ARMEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}arm:\${DTD_PICK_MODE:-delay}\${_T}\${(j:,:)@}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
HDR="$DTD_HDR"
BLOCKPICK="$DTD_BLOCKPICK"
SNOOZE="$STATE_DIR/dtd-block-snooze.json"
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\tarm-start\t\$#" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# ctrl-v on a picker row (already armed) = close the picker
if [[ "\$1" == BLOCK:* ]]; then
  rm -f "\$BLOCKPICK" "\$BLOCKPICK.mode" "\$BLOCKPICK.days" "\$BLOCKPICK.hint" "\$BLOCKPICK.snoozed"
  echo "↩ back to list" > "\$HDR"
  exit 0
fi
# Nothing to pick after 亥 has begun (20:00) unless un-delay is on offer.
# The +3 accounts for the always-available minute delays (10m/30m/1h),
# which never depend on the hour of day.
# ONE jq pass (2026-10-02): later-block option count + "already snoozed
# today" bonus option + first id -> cleaned name for the header label. It
# replaces a python3 count script plus a python3 resolve + sed, i.e. three
# exec-scan stalls before the picker could appear.
[[ -f "\$SNOOZE" ]] || printf '{}' > "\$SNOOZE"
_out=\$(jq -r --slurpfile sn "\$SNOOZE" --args '
  (now | localtime) as \$lt
  | (if ((\$sn[0] // {}) | (.date >= (\$lt | strftime("%Y-%m-%d")))
            and ((.snoozes // {}) | keys | any(. as \$k | \$ARGS.positional | index(\$k) != null)))
        then 1 else 0 end) as \$snz
  | (([4,6,8,10,12,14,16,18,20] | map(select(. > \$lt[3])) | length) + 3 + \$snz) as \$n
  | \$ARGS.positional[0] as \$id
  | (([.[] | select(type=="array")[] | select(type=="object" and .id == \$id) | .content] | first) // \$id)
  | gsub(" *[(][0-9]*[)]"; "") | gsub(" *[[][0-9]*[]]"; "")
  | gsub(" *[[][0-9.+]*/m[]]"; "") | gsub(" *[{][0-9]*[}]"; "")
  | gsub("  +"; " ") | sub(" *\$"; "") | sub("….*\$"; "")
  | [\$n, ., \$snz] | @tsv' "\$@" < "$DTD_CACHE_FILE" 2>/dev/null)
# Three fields: n, clean name, snoozed-today (0/1). The date gate is
# forward-only (>=), matching the list generator's _sn_all; it was == here.
n="\${_out%%\$'\t'*}"
_rest="\${_out#*\$'\t'}"
clean="\${_rest%\$'\t'*}"
_snz="\${_rest##*\$'\t'}"
[[ "\$_out" == *\$'\t'*\$'\t'* ]] || { n=3; clean="\$1"; _snz=0; }   # jq failed: keep the picker usable
# $BLOCKPICK.snoozed: read by the list generator's zsh fast path for the
# "↩ un-delay" row (2026-10-21). Local snooze file only; python's branch
# also unions the z_ibx cross-host mirrors, so a delay made on another
# host shows that row only via the python path (never taken while armed).
if [[ "\$_snz" == 1 ]]; then : > "\$BLOCKPICK.snoozed"; else rm -f "\$BLOCKPICK.snoozed"; fi
# Reset any mouse-tracking mode a child enabled (bug 2026-07-05). NO tty
# drain here (2026-10-21, "ctrl-d doesn't feel instantaneous ... one
# breath"): the drain's 'read -t 0.05' idled 50 ms on every ctrl-d (two
# thirds of this script's run time) and, worse, DISCARDED any key typed in
# that window, so "ctrl-d 2 enter" typed ahead lost its 2. This script has
# no interactive prompt and its only child is a ~15 ms jq, so a scroll burst
# has no window to leak into the query; keys typed ahead now queue in the tty
# and land in fzf's query once the schedule screen is up.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
if [[ "\$n" == "0" ]]; then
  echo "no later block today — nothing to delay to" > "\$HDR"
  exit 0
fi
lbl="\$clean"
[[ \$# -gt 1 ]] && lbl="\$clean +\$((\$# - 1)) more"
# Mode marker (2026-10-04): ctrl-d opens the unified schedule screen with the
# day-defer rows FIRST (so a bare enter keeps ctrl-d's old blank-prompt
# meaning, "next occurrence"); ctrl-v/k keep minutes/blocks first.
rm -f "\$BLOCKPICK.days"
printf '%s' "\${DTD_PICK_MODE:-delay}" > "\$BLOCKPICK.mode"
printf '%s\n' "\$@" > "\$BLOCKPICK"
echo "📅 schedule \$lbl (enter picks · type N + enter = defer N days · esc back)" > "\$HDR"
print -- "\$EPOCHREALTIME\tarm-end\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
ARMEOF
chmod +x "$DTD_BLOCKARM"

# Applies the picked block (enter/⌃⏎ on a BLOCK:* row) to the armed ids.
cat > "$DTD_BLOCKAPPLY" << APPLYEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}apply\${_T}\${(j:,:)\${(f)\$(<"$DTD_BLOCKPICK" 2>/dev/null)}}\${_T}\${1#BLOCK:}" >> "$DTD_EVENTS" 2>/dev/null
HDR="$DTD_HDR"
BLOCKPICK="$DTD_BLOCKPICK"
SNOOZE="$STATE_DIR/dtd-block-snooze.json"
glyph="\${1#BLOCK:}"
if [[ "\$glyph" == custom ]]; then
  echo "✎ press Enter on the custom row, then type the number of days" > "\$HDR"
  exit 0
fi
ids=(\$(cat "\$BLOCKPICK" 2>/dev/null))
rm -f "\$BLOCKPICK" "\$BLOCKPICK.mode" "\$BLOCKPICK.days" "\$BLOCKPICK.hint" "\$BLOCKPICK.snoozed"
if [[ "\$glyph" == "cancel" || \${#ids[@]} -eq 0 ]]; then
  echo "↩ back to list" > "\$HDR"
  exit 0
fi
# Day rows / typed N (2026-10-04, unified schedule screen): BLOCK:d<N|auto|
# YYYY-MM-DD> defers by days through the same defer script ctrl-d used to
# prompt for. Must branch BEFORE the python writer (HOURS['d1'] would raise).
if [[ "\$glyph" == d* ]]; then
  DTD_DEFER_DAYS="\${glyph#d}" "$DTD_DEFER" "\${ids[@]}" >/dev/null 2>&1
  exit 0
fi
msg=\$(python3 - "\$SNOOZE" "\$glyph" "\${ids[@]}" <<'PYWRITE'
import datetime, json, os, sys
sys.path.insert(0, os.path.expanduser('~/i446-monorepo/lib'))
from blocks import BLOCK_START as HOURS  # canonical 地支 schedule, not a local copy
path, glyph = sys.argv[1], sys.argv[2]
ids = [str(t) for t in sys.argv[3:]]
# TZ already resolves via dtd.sh's exported TZ (/travel override or system
# local) — naive datetime.date.today() picks that up automatically. The
# date gate is forward-only: a stored date equal-or-newer than today is
# kept, matching mark-completed.py's fix (never wipe on a backward date
# move — an OS TZ correction, an International Date Line crossing).
today = datetime.date.today().isoformat()
try:
    data = json.load(open(path))
    if today > data.get('date', ''):
        data = {}
except Exception:
    data = {}
if today > data.get('date', ''):
    data['date'] = today
sn = data.setdefault('snoozes', {})
if glyph == 'now':
    for i in ids:
        sn.pop(i, None)
    print('↩ shown again')
elif glyph.startswith('+') and glyph[-1] in ('m', 'h'):
    # Minute-granularity delay (10m/30m/1h) — stored as an absolute epoch
    # float, distinct from the plain int hour-of-day used by block delays so
    # the reader (dtd.sh's list generator) can tell the two apart on read.
    amount = int(glyph[1:-1])
    minutes = amount if glyph[-1] == 'm' else amount * 60
    until = datetime.datetime.now().timestamp() + minutes * 60
    for i in ids:
        sn[i] = until
    print('⏰ → +' + glyph[1:])
else:
    h = HOURS[glyph]
    for i in ids:
        sn[i] = h
    print('⏰ → ' + glyph + ' ' + str(h).zfill(2) + ':00')
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + '.tmp'
with open(tmp, 'w') as f:
    json.dump(data, f)
os.replace(tmp, path)
# Cross-host mirror: $STATE_DIR/dtd-block-snooze.json is host-local (Syncthing
# does not sync ~/.local/state/jm), so a delay made here in the terminal never
# reached dtd web on another machine (Ix). Also write this host's snooze into
# the Syncthing-synced vault, exactly like completed-today's per-host mirror,
# so dtd web's _snoozed_ids unions it in (2026-09-27 "CLI delay doesn't hit the
# server" fix). Best-effort — never fail the delay on a mirror miss.
try:
    import socket, re as _re
    slug = _re.sub(r'[^a-z0-9]+', '-', socket.gethostname().lower()).strip('-')[:24]
    mdir = os.path.expanduser('~/vault/z_ibx')
    os.makedirs(mdir, exist_ok=True)
    mpath = os.path.join(mdir, 'dtd-block-snooze-' + slug + '.json')
    mtmp = mpath + '.tmp'
    with open(mtmp, 'w') as f:
        json.dump(data, f)
    os.replace(mtmp, mpath)
except Exception:
    pass
PYWRITE
)
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the python call
# above — leaked SGR motion sequences type themselves into fzf's query as
# literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported here
# from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
echo "\${msg:-✗ block delay failed}" > "\$HDR"
APPLYEOF
chmod +x "$DTD_BLOCKAPPLY"

# Enter router (2026-10-04). Bound as transform(...) so it runs even when the
# query matches no row (fzf skips execute() then, and a typed "12" filters
# every schedule row away). Does its work itself and prints NOTHING, so no
# action/quoting layer is emitted; the reload/clear/header tail lives in the
# static bind. Its children's stdout is discarded because transform reads
# stdout as fzf actions.
cat > "$DTD_PICKENTER" << PICKENTEREOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}enter\${_T}\$1\${_T}\${2//\$_T/ }" >> "$DTD_EVENTS" 2>/dev/null
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\tpickenter\t\$1" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
id="\$1"
q="\${2// /}"
# Pick record (2026-10-17): action, row id, the query it was found under,
# and where it sat in the UNFILTERED list, 1 = top (\$DTD_ORDER: one id per
# line, written by the list generator). Builtins only: (ie) finds the row.
_rec_pick() {
  local _a="\$1" _i="\$2" _q="\$3" _pos="" _n=0
  if [[ -r "$DTD_ORDER" ]]; then
    local -a _rows; _rows=("\${(@f)\$(<"$DTD_ORDER")}"); _n=\${#_rows}
    _pos=\${_rows[(ie)\$_i]}; (( _pos > _n )) && _pos=""
  fi
  _q="\${_q//\\\\/\\\\\\\\}"; _q="\${_q//\\"/\\\\\\"}"
  print -r -- "{\\"ts\\":\$EPOCHREALTIME,\\"host\\":\\"$DTD_HOSTSLUG\\",\\"sid\\":\\"$DTD_ID\\",\\"kind\\":\\"pick\\",\\"action\\":\\"\$_a\\",\\"id\\":\\"\$_i\\",\\"q\\":\\"\$_q\\",\\"pos\\":\${_pos:-null},\\"n\\":\$_n}" >> "$DTD_RECLOG" 2>/dev/null
}
[[ -n "\$id" && "\$id" != BLOCK:* && ! -s "$DTD_BLOCKPICK" ]] && _rec_pick enter "\$id" "\$2"
# stdout is read by fzf's transform as ACTIONS: print only change-prompt(...)
# for the custom-days entry (2026-10-04), nothing on every other path.
if [[ -s "$DTD_BLOCKPICK" ]] && [[ "\$q" == <-> || "\$q" == [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] ]]; then
  "$DTD_BLOCKAPPLY" "BLOCK:d\$q" >/dev/null 2>&1
  [[ -e "$DTD_BLOCKPICK.custom" ]] && { rm -f "$DTD_BLOCKPICK.custom"; print -n 'change-prompt(> )'; }
elif [[ "\$id" == BLOCK:custom ]]; then
  # Custom row: same screen, prompt becomes a days field; the next Enter with
  # a number lands in the branch above (works even if the number filters every
  # row out, since it reads the query, not the row).
  : > "$DTD_BLOCKPICK.custom"
  echo "✎ type a whole number of days, then Enter (esc = back)" > "$DTD_HDR"
  print -n 'change-prompt(📅 delay days > )'
elif [[ -n "\$id" ]]; then
  # Survey cards open their form inline (2026-10-16) instead of just starting
  # the timer: one jq pass (same shape as the done router's) turns the row id
  # into its bare lowercase content -- 😈 ritual prefix, (N) [N] {N} and a
  # dated " M.D" suffix stripped -- and a zsh case maps it to a form. The
  # transform only emits the execute(...); \$DTD_FORM starts the timer and
  # runs the form, so this router stays well under fzf's 1s Pause threshold.
  # Everything else is a plain Enter: start the timer, nothing more.
  _clean=\$(jq -r --arg id "\$id" '
    (([.[] | select(type=="array")[] | select(.id == \$id) | .content] | first) // "")
    | gsub("😈"; "")
    | gsub(" *\\\\([0-9]*\\\\)"; "") | gsub(" *\\\\[[0-9]*\\\\]"; "")
    | gsub(" *\\\\[[0-9.+]*/m\\\\]"; "") | gsub(" *\\\\{[0-9]*\\\\}"; "")
    | gsub("  +"; " ") | sub("^ *"; "") | sub(" *\$"; "") | ascii_downcase
    | sub(" [0-9]{1,2}\\\\.[0-9]{1,2}\$"; "")' "${DTD_CACHE_FILE:-/dev/null}" 2>/dev/null)
  _form=""
  case "\$_clean" in
    0l)                               _form="$HOME/i446-monorepo/tools/0s/0s.py --detach" ;;
    1s)                               _form="$HOME/i446-monorepo/tools/1s/1s-survey.py --no-mark" ;;
    "1 xk88"|"1 xk87"|"1 xk87 wknd")  _form="$HOME/i446-monorepo/tools/xk887/xk887-survey.py" ;;
    "moral audit"|moralaudit)         _form="$HOME/i446-monorepo/tools/moralaudit/moralaudit.py" ;;
    "1 hcme")                         _form="$HOME/i446-monorepo/tools/1hcme/1hcme.py" ;;
    "1 i9")                           _form="$HOME/i446-monorepo/tools/1i9/1i9.py" ;;
    "1 hcb")                          _form="$HOME/i446-monorepo/tools/1hcb/1hcb.py" ;;
    "1 f693")                         _form="$HOME/i446-monorepo/tools/f693/1f693.py" ;;
    -1g)                              _form="$HOME/i446-monorepo/tools/-1g/-1g-form.py --detach" ;;
    0g)                               _form="$HOME/i446-monorepo/tools/-1g/-1g-form.py --daily --detach" ;;
  esac
  if [[ -n "\$_form" ]]; then
    print -rn -- "execute($DTD_FORM \$id \$_form)"
  else
    "$DTD_ENTER" "\$id" >/dev/null 2>&1
  fi
  [[ -e "$DTD_BLOCKPICK.custom" ]] && { rm -f "$DTD_BLOCKPICK.custom"; print -n 'change-prompt(> )'; }
fi
exit 0
PICKENTEREOF
chmod +x "$DTD_PICKENTER"

# Esc / ctrl-c router (2026-10-04): on the schedule screen, go back to the
# list; on the list with a query, clear it (the bind tail does that); on the
# bare list, exit. So pressing either twice from the schedule screen exits.
cat > "$DTD_BACK" << BACKEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}back\${_T}\${_T}\${1//\$_T/ }" >> "$DTD_EVENTS" 2>/dev/null
# Prints the WHOLE action list (esc/ctrl-c bind is just transform(this)).
acts=""
if [[ -e "$DTD_BLOCKPICK" ]]; then
  [[ -e "$DTD_BLOCKPICK.custom" ]] && acts="change-prompt(> )+"
  rm -f "$DTD_BLOCKPICK" "$DTD_BLOCKPICK.mode" "$DTD_BLOCKPICK.custom" "$DTD_BLOCKPICK.days" "$DTD_BLOCKPICK.hint" "$DTD_BLOCKPICK.snoozed"
  echo "↩ back to list" > "$DTD_HDR"
elif [[ -z "\$1" ]]; then
  zmodload zsh/datetime zsh/stat 2>/dev/null
  # Within 10 s of leaving the edit form, Esc was meant for the form (see
  # the formexit stamp in the edit script): stay, and let a second Esc quit.
  _fx="/tmp/dtd-$DTD_ID.formexit"
  if [[ -e "\$_fx" ]] && zstat -A _fm +mtime "\$_fx" 2>/dev/null && (( EPOCHSECONDS - _fm[1] < 10 )); then
    rm -f "\$_fx"
    echo "esc again to quit dtd" > "$DTD_HDR"
    print -rn -- "transform-header(\$DTD_HDRGEN)"
    exit 0
  fi
  print -- "\$EPOCHREALTIME\tback-abort\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
  print -n abort
  exit 0
fi
# DTD_RELOAD / DTD_HDRGEN are exported by dtd before fzf starts.
print -rn -- "\${acts}reload(\$DTD_RELOAD)+clear-query+transform-header(\$DTD_HDRGEN)"
exit 0
BACKEOF
chmod +x "$DTD_BACK"

# --- Unified edit script used by fzf ctrl-g binding ---
# One prompt edits name + domain + points from a single line (needs a tty, so
# the binding uses execute(), not execute-silent): @code → domain, a standalone
# number → points, the rest → new name. Patches the snapshot cache
# ($DTD_CACHE_FILE) so the row updates on reload.
DTD_EDIT="/tmp/dtd-$DTD_ID.edit.sh"
cat > "$DTD_EDIT" << EDITEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}edit\${_T}\$1\${_T}" >> "$DTD_EVENTS" 2>/dev/null
# ctrl-c on this screen = cancel the edit and go back to the list, NOT quit
# dtd (2026-10-17: "ctrl-c from the details dialog ... goes to the main
# screen, not exit all the way"). fzf's Pause() hands us the tty in cooked
# mode with ISIG on, and the tty raises SIGINT for the WHOLE foreground
# process group -- fzf included -- so a ^C anywhere in this script killed
# dtd outright. Undefine the intr (and quit) chars FIRST, so ^C/^\ arrive as
# plain bytes; the curses form (raw mode) then sees ^C as a key and cancels
# (exit 2). The stty sane below would reset intr, so it repeats the undef.
# Restored explicitly after the drain.
stty intr undef quit undef < /dev/tty 2>/dev/null
EDIT_FAST="\$HOME/i446-monorepo/tools/did/edit-fast.py"
EDIT_FORM="\$HOME/i446-monorepo/tools/did/edit-form.py"
HDR="$DTD_HDR"
TIMING="$DTD_TIMING"
print -- "\$EPOCHREALTIME\tedit-start\t\$1" >> "\$TIMING" 2>/dev/null
# fzf leaves the alternate screen for execute(), but what the terminal shows
# then is not guaranteed: cmux keeps the stale fzf frame on screen, so the
# prompt is invisible and arrow keys land in a blind 'read' that doesn't
# navigate anything (bug 2026-07-21, fixed for done.sh's value-prompt but
# never ported here — "dtd is locked on the text input screen, and I can't
# navigate items in the fzf", 2026-09-03). Clear to home so the question is
# the only thing visible, and force sane tty modes so Enter always
# terminates the read.
stty sane susp undef < /dev/tty 2>/dev/null
stty intr undef quit undef < /dev/tty 2>/dev/null  # sane reset intr; keep ^C a byte (see top)
printf "\033[2J\033[H⏳" > /dev/tty
# ctrl-o and ctrl-p both open this screen (2026-10-11: "ideally ctrl+g and
# ctrl+o go to the same place"; 2026-10-21: "I don't like the new ctrl+o /
# ctrl+p page" → a curses FORM, edit-form.py: [N] pts · (N) min, haiku name,
# full name, notes, project, then the breakoff "done so far" block (pts done
# · min done · done as). One interpreter, cache read in-process, ~50 ms to
# paint (budget: < 200, aim 80–100). ctrl-p from the list lands on "pts
# done" (DTD_SPLIT_SEED=1); ctrl-o on the task fields; ^O/^P jump inside.
_out="/tmp/dtd-$DTD_ID.edit.\$1.json"
rm -f "\$_out" "/tmp/dtd-$DTD_ID.editsaved"
python3 "\$EDIT_FORM" --id "\$1" "$DTD_CACHE_FILE" --out "\$_out" --timing "\$TIMING" \\
  \${DTD_SPLIT_SEED:+--focus split} < /dev/tty > /dev/tty 2>/tmp/dtd-$DTD_ID.edit.err
_rc=\$?
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the prompt above
# — leaked SGR motion sequences type themselves into fzf's query as literal
# ^[[<0;16;15M text on resume otherwise (bug 2026-07-05; drain half of the
# fix was 2026-07-27 on done.sh/split.sh but never ported here). Runs
# unconditionally, BEFORE the cancel check below, so a cancelled edit
# still cleans up instead of exiting past it.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
# intr/quit stay UNDEFINED until fzf exits (restored after fzf in the UI
# loop). Restoring them here (2026-10-17 to 10-22) opened a ~15 ms window
# between this line and fzf's Resume() re-raw: a second ^C, or a held key's
# repeat, landed in it as SIGINT for the whole process group and fzf exited
# 130 straight after a clean cancel (session 63658, 2026-10-22: edit-end
# rc=2, fzf-exit rc=130 12 ms later, no back event).
# Form-exit stamp (2026-10-22, "tried to change the project of a -1g and
# dtd died"): Enter on the last field (project) saves and closes the form,
# and an Esc meant for the form then landed on the bare list, which quits
# dtd. The back router reads this stamp and makes that Esc a no-op.
: > "/tmp/dtd-$DTD_ID.formexit"
if [[ ! -s "\$_out" ]]; then
  if (( _rc == 2 )); then echo "edit cancelled" > "\$HDR"
  else echo "✗ edit form exited \$_rc: \$(tail -c 120 /tmp/dtd-$DTD_ID.edit.err 2>/dev/null | tr '\\n' ' ')" > "\$HDR"; fi
  print -- "\$EPOCHREALTIME\tedit-end\t\$1 rc=\$_rc" >> "\$TIMING" 2>/dev/null
  exit 0
fi
echo "⏳ saving…" > "\$HDR"
: > "/tmp/dtd-$DTD_ID.editsaved"   # ctrl-p clears the query only after a save (cancel keeps the search)
print -- "\$EPOCHREALTIME\tedit-end\t\$1" >> "\$TIMING" 2>/dev/null
# The save runs in the BACKGROUND (2026-10-21: "call haiku for the short name
# after exit and async with UI"): edit-fast's Todoist calls and the Haiku
# short-name regeneration took 1-3 s with fzf blocked. Same shape as the
# split/alt-enter writers: hand the tty back now, let the writer flip the
# header, and poke fzf's listen port to reload the list once the cache is
# patched so the row shows the new name without a keypress.
{
trap '' HUP
out=\$(python3 "\$EDIT_FAST" --fields --id "\$1" "$DTD_CACHE_FILE" --json "\$_out" 2>&1)
rm -f "\$_out"
# Drop task-detail's 10-min cache so ctrl-o / a re-edit shows the new title.
rm -f "\${XDG_STATE_HOME:-\$HOME/.local/state}/jm/task-detail/\$1.json" 2>/dev/null
# Split: when the form's done card held pts done, edit-fast has applied the
# remainder card's name/haiku/notes/project and prints ONE line of shell
# assignments prefixed SPLIT (pts=, done_name=, done_min=, rem_pts=,
# rem_min=, done_short=, rem_short=, done_bonus=, rem_bonus=, rem_due=,
# keep=; newlines sanitised). The remainder's [N]/(N)/{N} and do-date travel
# as overrides: the split script writes them; keep=0 closes the task instead
# (partial credit, 2026-10-22). It owns the header from here.
_split_line=\$(print -r -- "\$out" | grep '^SPLIT ' | tail -n 1)
if [[ -n "\$_split_line" ]]; then
  pts=""; done_name=""; done_min=""; rem_pts=""; rem_min=""; done_short=""; rem_short=""
  done_bonus=""; rem_bonus=""; rem_due=""; keep="1"
  eval "\${_split_line#SPLIT }"
  "/tmp/dtd-$DTD_ID.split.sh" "\$1" "\$pts" "\$done_name" "" "\$rem_pts" "\$done_min" "\$rem_min" "\$done_short" "\$rem_short" "\$done_bonus" "\$rem_bonus" "\$rem_due" "\$keep"
else
  echo "\${out:-✗ edit failed}" > "\$HDR"
  _port="\$(cat "$DTD_PORT" 2>/dev/null)"
  if [[ -n "\$_port" ]]; then
    _today="\$(date +%Y-%m-%d)"
    _reload="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '\$_today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
    if [[ -n "\$FZF_API_KEY" ]]; then
      curl -s -H "X-API-Key: \$FZF_API_KEY" -XPOST "localhost:\$_port" --data "reload(\$_reload)+transform-header($DTD_HDRGEN)" >/dev/null 2>&1
    else
      curl -s -XPOST "localhost:\$_port" --data "reload(\$_reload)+transform-header($DTD_HDRGEN)" >/dev/null 2>&1
    fi
  fi
  "/tmp/dtd-$DTD_ID.bgrefresh.sh"   # then the ctrl-r pull, so Todoist-side changes show (2026-10-22)
fi
print -- "\$EPOCHREALTIME\tedit-saved\t\$1" >> "\$TIMING" 2>/dev/null
} </dev/null >> "/tmp/dtd-edit.log" 2>&1 &!

EDITEOF
chmod +x "$DTD_EDIT"

# --- List generation script (reloadable by fzf) ---
DTD_LIST="/tmp/dtd-$DTD_ID.list.sh"
cat > "$DTD_LIST" << 'LISTEOF'
#!/bin/zsh
[[ -n "${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
zmodload zsh/datetime 2>/dev/null; _tl="${1%.cache.json}.timing.log"
print -- "$EPOCHREALTIME\tlist-start\t" >> "$_tl" 2>/dev/null
# Args: $1=cache_file $2=done_file_path $3=removed_file $4=today $5=columns $6=skipped_file $7=timer_file $8=view $9=blockpick_file $10=domain_filter (optional, exact domain code -> scope the list)
# Live width: fzf exports FZF_COLUMNS to every bound/reload command — prefer
# it over the launch-time $5 so rows re-truncate to the CURRENT window width
# (bug 2026-07-23: the width was baked into the reload command string, so an
# expanded window kept launch-width "…" truncation forever). $5 stays as the
# cold-start fallback for the first pipe into fzf and scripted callers.
[[ -n "$FZF_COLUMNS" ]] && argv[5]="$FZF_COLUMNS"
# ── Schedule screen (ctrl-d/v/k armed): zsh builtins only (2026-10-21) ──
# The picker rows are a pure function of the arm files + the clock, and the
# python below spends ~34 ms starting up and ~28 ms loading the cache before
# reaching its BLOCK-PICKER branch -- the single largest slice of the ~210 ms
# from ctrl-d to the screen painting. So render them here, byte-identical
# to python's branch (which stays as the reference; test_dtd_picker_fastpath
# diffs the two across a 24-TZ hour sweep), and exit before python.
# Inputs: $9 (armed ids, non-empty), $9.mode ('days' = day rows first),
# $9.days (typed digit query: that one day row only), $9.snoozed (touched by
# the arm script when an armed id is already delayed today: un-delay row).
if [[ -n "$9" && -s "$9" ]]; then
  _bp="$9"
  unset LC_ALL; LC_TIME=C   # python's strftime is C-locale; zsh's honours LC_*
  _G=$'\x1b[38;2;152;195;121m'; _O=$'\x1b[38;2;255;138;61m'; _C=$'\x1b[38;2;97;175;239m'
  _Y=$'\x1b[38;2;139;150;163m'; _R=$'\x1b[0m'; _T=$'\t'
  _mode=""; [[ -r "$_bp.mode" ]] && _mode="$(<"$_bp.mode")"; _mode="${_mode//[[:space:]]/}"
  _dq="";   [[ -r "$_bp.days" ]] && _dq="$(<"$_bp.days")";   _dq="${_dq//[[:space:]]/}"
  if [[ "$_dq" == <-> ]]; then
    if (( _dq == 0 )); then
      print -r -- "${_G}↻ 0 / next occurrence / one day${_R}${_T}BLOCK:d0"
    else
      # Calendar days, anchored at local noon, so a DST change inside the
      # span can't shift the date (epoch + n*86400 can: Nov 1 00:30 + 1 day
      # lands on Nov 1 23:30).
      strftime -s _today '%Y-%m-%d' $EPOCHSECONDS
      strftime -r -s _noon '%Y-%m-%d %H' "$_today 12"
      strftime -s _when '%a %b %-d' $(( _noon + _dq * 86400 ))
      print -r -- "${_G}📅 ${_dq}天 → ${_when}${_R}${_T}BLOCK:d${_dq}"
    fi
    print -- "$EPOCHREALTIME\tlist-end\t" >> "$_tl" 2>/dev/null
    exit 0
  fi
  _days=("${_G}↻ 0 / next occurrence / one day${_R}${_T}BLOCK:dauto"
         "${_G}📅 1天${_R}${_T}BLOCK:d1"
         "${_G}📅 2天${_R}${_T}BLOCK:d2"
         "${_G}📅 7天${_R}${_T}BLOCK:d7"
         "${_G}✎ delay N days: type a number…${_R}${_T}BLOCK:custom")
  [[ "$_mode" == days ]] && print -rl -- "${_days[@]}"
  print -r -- "${_C}⏱ delay 10 minutes${_R}${_T}BLOCK:+10m"
  print -r -- "${_C}⏱ delay 30 minutes${_R}${_T}BLOCK:+30m"
  print -r -- "${_C}⏱ delay 1 hour${_R}${_T}BLOCK:+1h"
  strftime -s _hr '%H' $EPOCHSECONDS; _hr=${_hr#0}
  for _row in "卯 mao 4" "辰 chen 6" "巳 si 8" "午 wu 10" "未 wei 12" "申 shen 14" "酉 you 16" "戌 xu 18" "亥 hai 20"; do
    _f=(${=_row})
    (( _f[3] > _hr )) && printf '%s⏰ %s  %-5s %02d:00–%02d:59%s\tBLOCK:%s\n' "$_O" "$_f[1]" "$_f[2]" "$_f[3]" "$(( _f[3] + 1 ))" "$_R" "$_f[1]"
  done
  [[ "$_mode" != days ]] && print -rl -- "${_days[@]}"
  [[ -e "$_bp.snoozed" ]] && print -r -- "${_Y}↩ un-delay — show again now${_R}${_T}BLOCK:now"
  print -r -- "${_Y}✗ cancel${_R}${_T}BLOCK:cancel"
  print -- "$EPOCHREALTIME\tlist-end\t" >> "$_tl" 2>/dev/null
  exit 0
fi
python3 -c "
import json, sys, re, time

cache_file, done_file, removed_file, today, cols = sys.argv[1:6]
skipped_file = sys.argv[6] if len(sys.argv) > 6 else ''
timer_file = sys.argv[7] if len(sys.argv) > 7 else ''
cols = int(cols)
# Row numbers (2026-10-24: 'add a display number to every line in dtd so I
# can reference it from elsewhere'): every row is printed as 'NNN ' + row, so
# rows are right-justified 4 columns narrower to keep the estimates aligned.
ROWNUM_W = 4
cols = max(20, cols - ROWNUM_W)

with open(cache_file) as f:
    d = json.load(f)
# Stable render order across refreshes (2026-08-06 bug report: every time a
# task is finished, the whole list reorders for a few seconds). A ritual/
# d359-met completion triggers did-fast --refresh-cache, which the auto-
# reload watcher (below, polling the live task-queue file's mtime) picks up
# within ~2s and swaps wholesale into this session's cache file, then
# reloads fzf. But
# --refresh-cache re-fetches from Todoist (a today|overdue filter query
# plus several separate per-label queries, unioned) whose return order for
# same-priority-tier tasks is NOT guaranteed stable call-to-call -- none of
# today_tasks/_sec()'s buckets below apply their own sort, they just take
# whatever order the cache array happens to be in. So the SAME set of tasks
# can render in a different relative order after a refresh even though
# nothing the user cares about changed, which reads as a full reorder. Sort
# every task bucket by id right after load so rendering only ever depends
# on WHICH tasks exist, never on the order Todoist's API happened to return
# them in on any given fetch.
for _k, _v in d.items():
    if isinstance(_v, list):
        d[_k] = sorted(_v, key=lambda t: str(t.get('id', '')) if isinstance(t, dict) else '')
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
# 3rd field (id) is preferred when present: two tasks can share the same
# annotation-stripped name (e.g. a recurring 'AoS' + an unrelated one-off
# 'AoS'), and matching by name alone flagged BOTH as running when only one
# was started (bug 2026-07-19). Fall back to the name-only match for a timer
# file written before this fix (2 fields, no id) until it's next overwritten.
running_clean = ''
running_started = 0
running_id = ''
try:
    timer_raw = open(timer_file).read().strip()
    if timer_raw:
        parts = timer_raw.split('\t')
        running_clean = parts[0].strip().lower()
        running_started = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        running_id = parts[2].strip() if len(parts) > 2 else ''
except: pass
# Shared running hint (2026-10-06): janus /d creates a task and starts its
# timer, but this dtd's own timer file never hears of it, so the new task sat
# mid-list. Same 3-field format; the newer start wins; ignored after 4h.
try:
    _hint = open(__import__('os').path.expanduser('~/.local/state/jm/dtd-running-hint')).read().strip().split('\t')
    _hs = int(_hint[1]) if len(_hint) > 2 and _hint[1].isdigit() else 0
    if _hs > running_started and time.time() - _hs < 4 * 3600:
        running_clean, running_started, running_id = _hint[0].strip().lower(), _hs, _hint[2].strip()
except Exception: pass
# Shared running timer (2026-10-19). Everything above is THIS pane's own
# state: the timer file is per-instance, so a pane that did not itself start
# the current task kept showing its own stale row while janus had it right.
# lib/running_timer.py reads the one observation of Toggl that every writer
# here already shares (~/.cache/toggl-current.json), so janus, the ticker and
# every dtd pane answer from the same place instead of janus pushing state
# into dtd's files. A reading taken AFTER our own start wins -- including one
# that says nothing is running, which is what clears a stale row. A cold or
# stale cache returns None and changes nothing.
try:
    _rt_sys = __import__('sys')
    _rt_sys.path.insert(0, __import__('os').path.expanduser('~/i446-monorepo/lib'))
    _obs = __import__('running_timer').observed()
    if _obs and _obs['ts'] > running_started:
        running_clean = _obs['desc'].strip().lower()
        running_started = _obs['started']
        running_id = _obs['task_id']
except Exception: pass
# Started-today set (2026-10-13: Enter means working on it, so the task gets
# 🧮 and goes to the top; when the timer moves on and it isn't done, it stays
# at the top, without the 🧮 since 2026-10-30). start.sh appends date/id/epoch to dtd-started on every Enter
# and ctrl-s; id -> latest start today. Completion hides the row as usual.
started_at = {}
try:
    import os as _os_s
    _sp = _os_s.path.join(_os_s.environ.get('XDG_STATE_HOME') or _os_s.path.expanduser('~/.local/state'), 'jm', 'dtd-started')
    for _l in open(_sp):
        _p = _l.strip().split('\t')
        if len(_p) >= 3 and _p[0] == today and _p[2].isdigit():
            started_at[_p[1]] = max(started_at.get(_p[1], 0), int(_p[2]))
except Exception: pass

# Neon color palette (label → ANSI 256-color)
COLORS = {
    'g245': '\033[38;2;0;230;118m',    'epcn': '\033[38;2;0;191;165m',
    'h335': '\033[38;2;0;230;118m',    # same as g245 (JM 2026-10-26)
    's897': '\033[38;2;27;94;32m',     'hcmc2': '\033[38;2;255;214;0m',
    'xk87': '\033[38;2;253;108;29m',   'xk88': '\033[38;2;230;81;0m',
    'xk20': '\033[38;2;255;145;0m',    'xk22': '\033[38;2;255;171;64m',
    'hci':  '\033[38;2;99;237;224m',   'i9':   '\033[38;2;41;121;255m',
    'n156': '\033[38;2;18;73;180m',    'hcmc': '\033[38;2;13;59;102m',
    'm5x2': '\033[38;2;213;0;50m',     'm828': '\033[38;2;155;0;35m',
    'hcb':  '\033[38;2;248;29;120m',
    'hcbp': '\033[38;2;255;64;129m',   'infra':'\033[38;2;158;158;158m',
    'i444': '\033[38;2;97;97;97m',     'i447': '\033[38;2;168;156;138m',
    'hcm':  '\033[38;2;170;0;255m',    'hcmp': '\033[38;2;124;77;255m',
    'hcmr': '\033[38;2;189;166;255m',  '家':   '\033[38;2;0;184;212m',
    '睡觉': '\033[38;2;102;102;102m',
}
RESET = '\033[0m'

# -1neon ritual cards carry only the '-1neon' label (no domain), so they render
# colorless. Map each ritual tag to its natural domain color (user 2026-07-07).
RITUAL_DOMAIN = {'-1ibx': 'i9', '-1g': 'g245', '-1l': 'g245', '-1t': 'n156', 'سمش': 'hcm'}

def prank(p):
    return -(p or 1)

def strip_ann(s):
    # [1/m]-style rate annotations strip like numeric [N] (variable 1n+ cards)
    return re.sub(r'  +', ' ', re.sub(r' *\(\d*\)| *\[\d*\]| *\{\d*\}| *\[[0-9.+]*/m\]', '', s)).strip()

# Right-justify trailing (N)/[N]/{N} estimates into a column. target = cols - 8
# pulls the estimate column ~5 cols in from the edge (vs the old cols - 3): it
# keeps fzf's pointer/gutter (2) + scrollbar (1) clear AND adds a 5-col right
# margin so estimates stay visible in a narrow pane and the name→estimate gap
# shrinks. If there is no room (long/truncated rows), leave inline.
# [1/m]-style rate markers on variable 1n+ cards count as estimates too, so
# they right-justify into the same column as numeric [N] (2026-07-25).
import unicodedata as _ud
# Terminal display width: East Asian Wide/Fullwidth cells count 2, combining
# marks 0, everything else 1. len() undercounts CJK by half, so rows like
# 大孩子文学时间 pushed their (N)/[N] column past the edge (bug 2026-09-28,
# 'naive character counter for chinese'). Emoji presentation is left at 1:
# fzf and terminals disagree on it, and the ritual cards carry no estimate.
# NB: this whole generator is embedded in a double-quoted -c string, so no
# double quotes or backticks may appear in it (test_dtd_listgen_no_shell_meta).
def _dwidth(s):
    w = 0
    for ch in s:
        if _ud.combining(ch):
            continue
        w += 2 if _ud.east_asian_width(ch) in ('W', 'F') else 1
    return w

# Leftmost run of s that fits in width display cells (width-aware truncation).
def _dtrunc(s, width):
    out, w = [], 0
    for ch in s:
        cw = 0 if _ud.combining(ch) else (2 if _ud.east_asian_width(ch) in ('W', 'F') else 1)
        if w + cw > width:
            break
        out.append(ch); w += cw
    return ''.join(out)

_EST_TOK = r'(?:\(\(?\d+\)?\)|\[\d*G?\]|\[[0-9.+]*/m\]|\{\d+\})'
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
    pad = (cols - 8) - _dwidth(head) - _dwidth(est)
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
# The tomorrow bound is ONLY for recurring cards (due-drift guard, 2026-06-27).
# Non-recurring 0neon tasks are deferred one-off copies ('xk22 7.21') — they
# must stay hidden until actually due, else a just-deferred habit pops right
# back into today's queue (bug 2026-07-21).
# (missing 'recurring' defaults to True so a partial cache entry keeps the
# drift guard; copies always carry an explicit recurring: false.)
#
# habits-deferred-<date>.ids: parent ids of daily habits DEFERRED today
# (written by defer-fast, removed by undo-fast on ctrl-z). A deferred parent
# advanced to tomorrow is cache-identical to the 2026-06-27 drift case the
# tomorrow bound exists to rescue, so intent has to come from this marker.
# Hidden unconditionally (no due predicate): right after a defer the cache
# may still hold the parent at due=today until the next refresh, and a dtd
# restart in that window would resurface it. Path uses the CURRENT date, not
# the session-start 'today' arg, so a session crossing midnight stops
# honoring yesterday's marker.
import os as _os
_deferred_ids = set()
try:
    with open(_os.path.expanduser('~/.cache/jm/habits-deferred-%s.ids'
                                  % _dt.date.today().isoformat())) as _df:
        _deferred_ids = {_l.strip() for _l in _df if _l.strip()}
except OSError:
    pass
# Plus every host's synced mirror in z_ibx (2026-10-22): a defer made on the
# phone runs defer-fast on Ix, whose local marker this host never sees.
import glob as _glob
for _mp in _glob.glob(_os.path.expanduser('~/vault/z_ibx/habits-deferred-*-%s.ids'
                                          % _dt.date.today().isoformat())):
    try:
        with open(_mp) as _df:
            _deferred_ids |= {_l.strip() for _l in _df if _l.strip()}
    except OSError:
        pass
zeroneon = [t for t in _sec('0neon', _tomorrow) + _sec('夜neon', _tomorrow)
            if t.get('id') not in _deferred_ids
            and t['due'] <= today]
# Block-snooze (ctrl-v, 2026-07-24): ids hidden until their chosen 地支 block
# starts. File is {date, snoozes: {id: start_hour}}; a stale date voids it.
# Uses the CURRENT clock (not the session-start today arg) so an idle-open
# dtd un-hides the task on the first reload after the block hour arrives
# (the watcher refreshes at every block boundary).
_snoozed = set()
_sn_all = set()
# Cross-host (2026-10-08): union this host's file with every host's synced
# mirror (~/vault/z_ibx/dtd-block-snooze-<host>.json), same as dtd web's
# _snoozed_ids: a delay made on Ix (phone or Ix dtd) now hides the task here
# too, so a network switch never resurfaces snoozed cards.
import glob as _glob
_nw = _dt.datetime.now()
for _sp in [_os.path.expanduser('~/.local/state/jm/dtd-block-snooze.json')] + \
           _glob.glob(_os.path.expanduser('~/vault/z_ibx/dtd-block-snooze-*.json')):
    try:
        with open(_sp) as _sf:
            _sn = json.load(_sf)
    except Exception:
        continue
    # Forward-only: a stored date equal-or-newer than today is still valid,
    # matching the writer's fix (DTD_BLOCKAPPLY, above) — a plain equality
    # check here would silently un-hide every snoozed task the moment the
    # writer started preserving them across a backward date move instead of
    # wiping them, since a backward move means this reader's freshly-
    # computed 'today' no longer equals the (correctly preserved) newer
    # stored date.
    if not isinstance(_sn, dict) or _nw.date().isoformat() > _sn.get('date', ''):
        continue
    # Minute-delays (ctrl-v +10m/+30m/+1h) store an absolute epoch float;
    # block delays store a plain int hour-of-day. json round-trips a
    # python float with a decimal point, so isinstance(v, float)
    # unambiguously distinguishes the two on read.
    def _still_snoozed(v):
        if isinstance(v, float):
            return _nw.timestamp() < v
        return _nw.hour < int(v)
    for _k, _v in (_sn.get('snoozes') or {}).items():
        _sn_all.add(str(_k))
        try:
            if _still_snoozed(_v):
                _snoozed.add(str(_k))
        except (TypeError, ValueError):
            pass
# Block LABELS (feature 2026-07-27): a task carrying a 地支 glyph label
# (/todo ... 戌) hides until that block starts — the durable, task-level
# analog of the ctrl-v snooze. Uses the current clock, same as above.
# Canonical schedule import (was a 3rd independent copy of this table,
# consolidated 2026-08-23 — see lib/blocks.py).
import sys as _sys
_sys.path.insert(0, _os.path.expanduser('~/i446-monorepo/lib'))
from blocks import BLOCK_START as _BLOCK_LABEL_HOURS
_now_hour = _dt.datetime.now().hour
# Woken (2026-10-05): a task whose delay or block label has passed TODAY
# floats to the top of its tier, so 'hide until 酉' means 'and then put it
# first'. Snooze entries expire by value but stay in the file for the day,
# so _sn_all minus _snoozed is exactly the delays that already fired.
def _woke(t):
    tid = t.get('id')
    if tid is not None and str(tid) in _sn_all and str(tid) not in _snoozed:
        return True
    _bh = next((_BLOCK_LABEL_HOURS[l] for l in t.get('labels', [])
                if l in _BLOCK_LABEL_HOURS), None)
    return _bh is not None and _now_hour >= _bh
def _float(tier):
    return sorted(tier, key=lambda t: not _woke(t))   # stable
# Agent claims (/claim, 2026-10-05): tasks a Claude session is mid-turn on
# render grey with a 😈 prefix, in place. Store + staleness rules live in
# tools/did/agent_claims.py; the watcher reloads when the claims dir changes.
# The claiming session's cmux number (cs numbering: dtd 1, janus 2, Claude
# panes 3+) sits right before the glyph, '3😈' / '3🕐', so JM can see which
# tab has the task (2026-10-21); blank when cs hasn't numbered the session.
_agent_ids = set()
_claim_ids = set()
_done_ids = set()
_claim_pane = {}
try:
    _sys.path.insert(0, _os.path.expanduser('~/i446-monorepo/tools/did'))
    import agent_claims as _ac
    _claims = _ac.load()
    # A repeating card keeps its id across days, so a claim set before today's
    # local midnight belongs to an earlier occurrence (2026-10-24: notes showed
    # yesterday's released claim as 🧮 🕐). did-fast clears claims on close;
    # this covers cards closed elsewhere (the Todoist app).
    _midnight = time.mktime(time.strptime(today, '%Y-%m-%d'))
    _recur_ids = {str(_t.get('id')) for _sec in d.values() if isinstance(_sec, list)
                  for _t in _sec if isinstance(_t, dict) and _t.get('recurring')}
    _claims = {i: r for i, r in _claims.items()
               if not (i in _recur_ids and r.get('state_ts', 0) < _midnight)}
    _agent_ids = {i for i, r in _claims.items() if r['working']}
    _claim_ids = set(_claims.keys())
    _done_ids = {i for i, r in _claims.items() if r.get('done')}
    _claim_pane = {i: str(r['pane']) for i, r in _claims.items() if r.get('pane') is not None}
except Exception:
    pass
AGENT_GREY = '\x1b[38;2;110;110;110m'
# Claimed tasks sort to the TOP of the list (2026-10-13); a
# working one carries Claude's own spinner
# glyph, advanced on every reload (the watcher reloads twice a second while
# any claim is working), so the row itself moves.
_AGENT_SPIN = '·✢✳✶✻✽✻✶✳✢'
_agent_frame = _AGENT_SPIN[int(time.time() * 2) % len(_AGENT_SPIN)]

# ── BLOCK-PICKER MODE (ctrl-v, 2026-07-27): when the arm file holds pending
# ids, the list IS the picker — block rows instead of tasks. Rendered by the
# outer fzf itself, so cmux cannot fail to paint it (every nested-UI variant
# did). Row id field carries BLOCK:<glyph>; enter.sh/done.sh route it to
# blockapply. Searchable by pinyin or 汉字.
_bp = sys.argv[9] if len(sys.argv) > 9 else ''
_armed = []
try:
    with open(_bp) as _bf:
        _armed = [l.strip() for l in _bf if l.strip()]
except Exception:
    pass
if _armed:
    _nw2 = _dt.datetime.now()
    ORANGE = '\x1b[38;2;255;138;61m'
    CYAN = '\x1b[38;2;97;175;239m'
    GREY = '\x1b[38;2;139;150;163m'
    _R = '\x1b[0m'
    # Short, always-available delays (not tied to the 地支 block schedule) —
    # come before the block rows since they're the more common quick-snooze.
    GREEN = '\x1b[38;2;152;195;121m'
    _mode = ''
    try:
        _mode = open(_bp + '.mode').read().strip()
    except Exception:
        pass
    # Day-defer rows (2026-10-04, unified schedule screen). Typing a bare
    # number + enter also defers N days (handled by the enter router).
    # Labels lead with the number you can also type + enter (2026-10-04).
    _day_rows = [(f'{GREEN}↻ 0 / next occurrence / one day{_R}', 'dauto'),
                 (f'{GREEN}📅 1天{_R}', 'd1'),
                 (f'{GREEN}📅 2天{_R}', 'd2'),
                 (f'{GREEN}📅 7天{_R}', 'd7'),
                 (f'{GREEN}✎ delay N days: type a number…{_R}', 'custom')]
    _days_q = ''
    try:
        _days_q = open(_bp + '.days').read().strip()
    except Exception:
        pass
    if _days_q.isdigit():
        # Typed bare number = N days, the only row shown (see domainsearch).
        _n = int(_days_q)
        if _n == 0:
            print(f'{GREEN}↻ 0 / next occurrence / one day{_R}\tBLOCK:d0')
        else:
            _when = (_dt.date.today() + _dt.timedelta(days=_n)).strftime('%a %b %-d')
            print(f'{GREEN}📅 {_n}天 → {_when}{_R}\tBLOCK:d{_n}')
        sys.exit(0)
    if _mode == 'days':
        for _t, _g in _day_rows:
            print(f'{_t}\tBLOCK:{_g}')
    for _lbl, _g in (('10 minutes','+10m'),('30 minutes','+30m'),('1 hour','+1h')):
        print(f'{CYAN}⏱ delay {_lbl}{_R}\tBLOCK:{_g}')
    for g, py, h in (('卯','mao',4),('辰','chen',6),('巳','si',8),('午','wu',10),
                     ('未','wei',12),('申','shen',14),('酉','you',16),
                     ('戌','xu',18),('亥','hai',20)):
        if h > _nw2.hour:
            # End-inclusive label (14:00–15:59, not 14:00–16:00) so typing a
            # start time matches exactly one row (JM, 2026-10-20).
            print(f'{ORANGE}⏰ {g}  {py:<5} {h:02d}:00–{h+1:02d}:59{_R}\tBLOCK:{g}')
    if _mode != 'days':
        for _t, _g in _day_rows:
            print(f'{_t}\tBLOCK:{_g}')
    if any(i in _sn_all for i in _armed):
        print(f'{GREY}↩ un-delay — show again now{_R}\tBLOCK:now')
    print(f'{GREY}✗ cancel{_R}\tBLOCK:cancel')
    sys.exit(0)

oneneon = _sec('1neon', today)
# 0g: today's daily goals.
zerog = [t for t in today_tasks if _has(t, '#0g') and not _has(t, '-1neon') and not _has(t, '#-1g')]
# critical-path + any other uncategorized today task, by priority, at the end.
_placed = lambda t: _has(t, '-1neon') or _has(t, '#-1g') or _has(t, '#0g')
critical = _sec('关键路径', today)
# Within a priority tier, highest 分 per minute first (2026-10-10): every
# task already carries its value [N] and estimate (N), and a 79-task today
# list ordered only by priority and id buried a [60]-for-60-minutes task
# under quick low-value ones. No (N) counts as 30 minutes; no [N] sorts last.
# Stable, so equal ratios keep the by-id order (test_dtd_stable_render_order).
def _fen_per_min(t):
    c = t.get('content', '')
    pv = re.search(r'\[(\d+)\]', c)
    if not pv:
        return 0.0
    mv = re.search(r'\((\d+)\)', c)
    mins = int(mv.group(1)) if mv and int(mv.group(1)) > 0 else 30
    return int(pv.group(1)) / mins
rest = sorted([t for t in today_tasks if not _placed(t)],
              key=lambda t: (prank(t.get('priority')), -_fen_per_min(t)))
# Plain tasks (critical path + everything uncategorized): p1 (API priority 4)
# rises to the top of this area, so dtd's alt-up bump actually moves a task
# (2026-10-04). Stable sort, so everything else keeps its order.
plain = sorted(critical + rest, key=lambda t: (not _woke(t), t.get('priority') != 4))
all_tasks = (_float(rituals) + _float(neg1g) + _float(zeroneon)
             + _float(oneneon) + _float(zerog) + plain)

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
    # Ritual cards carry only '-1neon' — resolve their domain the same way the
    # color pass does (RITUAL_DOMAIN), so ctrl-t project view groups -1ibx
    # with i9, -1g with g245, etc.
    if '-1neon' in t.get('labels', []):
        bare = strip_ann(t.get('content') or '').lower().replace('😈', '').strip()
        for tag, dom in RITUAL_DOMAIN.items():
            if bare == tag or tag in bare.split():
                return dom
    for lbl in t.get('labels', []):
        if lbl in COLORS:
            return lbl
    return 'zzz'   # unlabelled tasks sort to the end

def time_of(t):
    m = re.search(r'\((\d+)\)', t.get('short') or t.get('content') or '')
    return int(m.group(1)) if m else 10**9   # no (N) estimate -> sort to the end

# Domain search (11th arg, 2026-09-16): typing a domain code exactly (e.g.
# 'm5x2') into the query box scopes the list to that domain -- fzf's own
# fuzzy matcher can't do this on its own, because domain is conveyed PURELY
# via row color (deliberately, see COLORS/no-project-prefix above), never as
# text in the searched field. $DTD_DOMAINSEARCH (bound to change:) detects
# an exact match and reloads through here with the domain as this arg,
# +clear-query -- so this filter, unlike view/sort, actually REMOVES
# non-matching rows rather than just reordering them.
DTD_HIDE_NAMES = ('一起饭',)   # see the hide check in the row loop
# The 11th arg is a FILE holding the active domain code (like the view file
# in arg 8), not the code itself (2026-10-01 fix): a literal code was only
# ever sent by the domain-search POST, so the next reload from any other
# path (the cache-mtime watcher, ctrl-* bindings, resize) silently dropped
# the filter and the full list came back. Empty/missing file = no filter.
domain_filter = ''
try:
    domain_filter = open(sys.argv[10]).read().strip() if len(sys.argv) > 10 else ''
except: domain_filter = ''
if domain_filter:
    unique = [t for t in unique if domain_of(t) == domain_filter]

if view == 'project':
    unique.sort(key=lambda t: (domain_of(t), not _woke(t), prank(t.get('priority'))))
elif view == 'time':
    unique.sort(key=lambda t: (time_of(t), prank(t.get('priority'))))

DIM = '\033[2m'
claimed_lines = []   # B: agent waiting on JM
working_lines = []   # C: agent mid-turn
running_lines = []   # A: JM's own timer
started_lines = []   # A2: JM started it today, timer moved on, not done
normal_lines = []
skipped_lines = []

# One running row per timer (2026-10-23: 'when recurring task in dtd gets
# marked with an abacus, the recurrance should not also have an abacus').
# With no task id on the timer, the name match lit every same-named card:
# the recurring parent and its one-off copy ('give kids allowance' x2). The
# card Entered today wins; otherwise the first visible match does.
_run_pref = None
_run_taken = [False]
if not running_id and running_clean:
    _run_pref = next((str(t.get('id', '')) for t in unique
                      if strip_ann(t['content']).lower() == running_clean
                      and str(t.get('id', '')) in started_at), None)

for t in unique:
    raw = t['content']
    clean = strip_ann(raw).lower()
    prefix = clean.split(' - ')[0]
    # Hide by id first: definitive, and immune to same-name collisions.
    if t.get('id') is not None and str(t['id']) in completed_ids:
        continue
    # Cross-machine done-today (2026-09-07 fix, mirrors tools/dtd/dtd.py's
    # build_tasks): a recurring habit whose due date has advanced past today
    # was completed today -- each close bumps a daily/weekly recurrence by
    # one occurrence. completed-today.json (and its id/name overlays above)
    # is machine-local and lags a remote host's completion until the ~2s
    # z_ibx mirror watcher folds it in (or forever, if the id/name never made
    # it into the local record at all -- e.g. a Todoist word-overlap match
    # whose recorded name still carries a (N) annotation the name-hide below
    # strips before comparing, so it never matches). The due date itself is
    # durable Todoist state, unlike the local completed-today overlay, so it
    # survives regardless of which machine's did-fast call advanced it.
    # Bug report 2026-09-07: 1st hci done via dtd mobile (Ix) never hid in
    # dtd cli (Straylight) -- mobile's dtd.py already had this exact check;
    # dtd.sh never did. (NOTE: this whole block sits inside dtd.sh's
    # python3 -c zsh double-quoted string -- a literal double-quote char in
    # a comment here silently truncates the script; avoid it.)
    # Cards done elsewhere (2026-10-03): never shown in dtd. Matches the bare
    # name and dated catch-up copies (NAME M.D). Still tracked by neon and
    # completable via /did, janus or inbound -- only hidden from this picker.
    if any(clean == h or clean.startswith(h + ' ') for h in DTD_HIDE_NAMES):
        continue
    if t.get('recurring', True) and t.get('due') and t['due'] > today:
        continue
    # Block-snoozed (ctrl-v): hidden until the chosen block's hour arrives.
    if t.get('id') is not None and str(t['id']) in _snoozed:
        continue
    # Block-labeled (地支 glyph label from /todo): hidden until its block.
    _blk_h = next((_BLOCK_LABEL_HOURS[l] for l in t.get('labels', [])
                   if l in _BLOCK_LABEL_HOURS), None)
    if _blk_h is not None and _now_hour < _blk_h:
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

    # Markdown links (/todo stores URLs as '[(link)](https://…)') collapse to
    # their visible text for layout; the first one becomes an OSC 8 terminal
    # hyperlink after padding so it stays clickable in cmux (feature
    # 2026-07-28). Raw URLs would blow out truncation and read as noise.
    link_url = None
    _mdlink = re.search(r'\[([^\]]*)\]\((https?://[^)\s]+)\)', display)
    if _mdlink:
        link_url = _mdlink.group(2)
        link_text = _mdlink.group(1) or '(link)'
        display = re.sub(r'\[([^\]]*)\]\((https?://[^)\s]+)\)',
                         lambda mm: mm.group(1) or '(link)', display)
    # Every task with a link shows (link) (2026-10-13), opened with ctrl-l in
    # the task's Chrome profile (dtd-open-link.py). Bare URLs collapse to
    # (link) too, and a link the Haiku short name dropped, or a named
    # markdown link, still gets a (link) ahead of the trailing estimates.
    _bare = re.search(r'https?://[^\s)\]]+', display)
    if _bare:
        link_url = link_url or _bare.group(0)
        display = re.sub(r'https?://[^\s)\]]+', '(link)', display)
    if not link_url:
        _rl = re.search(r'https?://[^\s)\]]+', raw)
        link_url = _rl.group(0) if _rl else None
    # Shown as a chain emoji rather than (link) (2026-10-30, shorter).
    # Escaped, not literal: this body must stay ASCII-safe under any locale.
    _chain = '\U0001F517'
    display = display.replace('(link)', _chain)
    if link_url:
        link_text = _chain
        if _chain not in display:
            _em = re.search(r'(\s*(?:\(\d*\)|\[[\d.+]*(?:/m)?\]|\{\d*\}))+\s*$', display)
            _cut = _em.start() if _em else len(display)
            display = display[:_cut].rstrip() + ' ' + _chain + display[_cut:]

    # Middle-truncate if needed (fallback; short names usually fit). cols - 7
    # keeps the whole row ~5 cols thinner, matching the estimate margin above.
    line = display
    if _dwidth(line) > cols - 7:
        # Find trailing annotations
        tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][ ]*[\(\[\{]\d*[\)\]\}].*$', line)
        if not tail_m:
            tail_m = re.search(r'[ ]*[\(\[\{]\d*[\)\]\}][^()\[\]{}]*$', line)
        tail = tail_m.group() if tail_m else line[-15:]
        head_len = max(10, cols - _dwidth(tail) - 7)
        line = _dtrunc(line, head_len) + '…' + tail

    # Hidden field 2 carries the task id so bindings resolve the real task.
    # fzf shows field 1 only (--with-nth=1); search therefore matches the
    # visible short name (Haiku keeps key codes/names, so this stays usable).
    sfx = '\t' + str(t.get('id', ''))

    repeat = '↻ ' if recurring else ''
    # Project/time views group by color alone — no project-name prefix (the user
    # knows the domain from the color; the names just add clutter).
    dom_tag = ''
    if running_id:
        is_running = str(t.get('id', '')) == running_id
    elif _run_pref:
        is_running = str(t.get('id', '')) == _run_pref
    else:
        is_running = bool(running_clean and clean == running_clean and not _run_taken[0])
        _run_taken[0] = _run_taken[0] or is_running
    # In-progress tiers (2026-10-11), all printed at the TOP of the list in
    # this order (2026-10-13); see the print loop below:
    #   A  JM's own running timer: spinner Nm, domain color (2026-10-16: 'when
    #      my time is on a certain task, it should be a clock ticker or
    #      something like the claude snowflake that shows movement'; the
    #      watcher reloads once a second while the timer runs so it moves)
    #      plus 🧮 after the minutes (2026-10-22: 'what happened to the abacus
    #      for tasks that I'm doing myself'; the spinner was meant to add
    #      motion, not to take the abacus away)
    #   A2 JM started it today (Enter/ctrl-s), timer moved on, not done: keeps
    #      its top slot, no glyph (2026-10-30: 'make sure that if I start
    #      another task ... abacus is only ever on one task at a time'; the
    #      2026-10-16 'it can just show the abacus' left one per started task)
    #   C  agent claim working: spinner + 😈, grey
    #   B  agent claim waiting on JM (idle): 🕐, domain color (2026-10-28: no
    #      🧮: 'it should only have abacus if it's my current task in janus')
    # A working claim beats A (2026-10-16: 'when agent started working on
    # /0t it didn't replace the abacus with a demon icon'): the row keeps its
    # elapsed minutes and top slot, but the abacus becomes spinner + 😈 and it
    # greys. An idle claim loses to A (JM's timer wins over 🕐); an agent
    # claim beats A2; a skip beats A2. 🕐, not ⏰: ⏰ is delay.
    is_working = str(t.get('id', '')) in _agent_ids
    is_claimed = is_working or (str(t.get('id', '')) in _claim_ids and not is_running)
    is_started = (str(t.get('id', '')) in started_at and not is_running
                  and not is_claimed and not is_skipped)
    if is_running:
        elapsed = max(0, int((time.time() - running_started) // 60)) if running_started else 0
        prefix = f'{_agent_frame} {elapsed}m · 🧮 {dom_tag}'
    elif is_started:
        prefix = repeat + dom_tag
    else:
        prefix = repeat + dom_tag
    _pane = _claim_pane.get(str(t.get('id', '')), '')
    if is_working:
        # Drop the tier-A/A2 lead glyph (spinner or abacus) so it isn't doubled.
        _lead = prefix.split(' ', 1)
        _rest = _lead[1] if len(_lead) == 2 and _lead[0] in ('🧮', _agent_frame) else prefix
        # A running row keeps its 🧮 after the minutes (2026-10-22). A2 +
        # working no longer adds one (2026-10-30: one abacus, on the live
        # timer only; the 10-21 started-task abacus made a second one).
        prefix = _agent_frame + ' ' + _pane + '😈 ' + _rest
        color = AGENT_GREY
    elif str(t.get('id', '')) in _done_ids:
        # Agent thinks it's finished (2026-10-22): ✅ on the far left, still
        # in the claimed tier until JM completes it with ⌥↵. The session's
        # pane number leads it, '6✅', like '6😈' while it worked.
        prefix = _pane + '✅ ' + prefix
    elif is_claimed:
        prefix = _pane + '🕐 ' + prefix
    # Build the full visible row, then right-justify its trailing estimates so
    # they align in a column regardless of the prefix. ANSI is added after.
    body = rjust_est(prefix + line, cols)
    if link_url and link_text in body:
        # OSC 8 wrap AFTER layout so the escape bytes never skew the padding.
        # ST spelled with chr(92): this python lives in a zsh double-quoted
        # string where a backslash pair would collapse and break the escape.
        # Target is the hammerspoon:// handler (dtd_link.click_url), so a
        # click opens the task's Chrome profile in a new window (2026-10-22);
        # a bare URL target opened whichever profile Chrome used last.
        _st = '\x1b' + chr(92)
        try:
            _sys.path.insert(0, _os.path.expanduser('~/i446-monorepo/tools/did'))
            import dtd_link as _dl
            _target = _dl.click_url(link_url, t.get('labels'))
        except Exception:
            _target = link_url
        body = body.replace(
            link_text,
            '\x1b]8;;' + _target + _st + link_text + '\x1b]8;;' + _st, 1)
    if is_running:
        # NB: this python lives inside a zsh double-quoted string — never use
        # double quotes in here, they terminate the -c argument.
        running_lines.append(f'{color}{body}{RESET}{sfx}')
    elif is_started:
        started_lines.append((started_at[str(t.get('id', ''))], f'{color}{body}{RESET}{sfx}'))
    elif is_working:
        working_lines.append(f'{color}{body}{RESET}{sfx}')
    elif is_claimed:
        claimed_lines.append(f'{color}{body}{RESET}{sfx}')
    elif is_skipped:
        skipped_lines.append(f'{color}{body}{RESET}{sfx}')
    elif color:
        normal_lines.append(f'{color}{body}{RESET}{sfx}')
    else:
        normal_lines.append(f'{body}{sfx}')

# JM's own work prints FIRST (2026-10-13: 'when I hit enter on a task that
# means I am working on it ... it should be at the top'): the running timer,
# then tasks started today and not done, most recent start first. Agent
# claims follow (2026-10-13: a task an AI /d's goes to the top, not the
# bottom), working above waiting. The running timer used to print LAST for
# fzf's --cycle up-wrap (2026-10-12); both are gone. The footer line that
# mirrored them is gone (2026-10-12); the footer is now the plain dtd /
# N done / N left header.
_active = running_lines + [l for _, l in sorted(started_lines, key=lambda x: -x[0])]
_claims = working_lines + claimed_lines
# Display position, not anything about the task: 1 = top of the unfiltered
# list. The same order goes to ~/.local/state/jm/dtd-order.json below, so a
# Claude session can say '/d 5' and mean row 5 (agent_claims.candidates).
for _rn, l in enumerate(_active + _claims + normal_lines + skipped_lines, 1):
    print(f'\x1b[2m{_rn:>3}\x1b[0m ' + l)
# Usage event (2026-10-10, see DTD_EVENTS in dtd.sh): the top 15 ids in screen
# order, written only when that order changes, so the report can tell what
# was on screen when each key was pressed and how far down it was.
try:
    _evp = _os.environ.get('DTD_EVENTS', ''); _sid = _os.environ.get('DTD_ID', '')
    if _evp and _sid:
        _order = [l.rsplit(chr(9), 1)[-1] for l in _active + _claims + normal_lines + skipped_lines]
        _op = _os.environ.get('DTD_ORDER', '')
        if _op:
            open(_op + '.tmp', 'w').write(chr(10).join(_order) + chr(10)); _os.replace(_op + '.tmp', _op)
            # Shared, stable copy for Claude sessions: row N = ids[N-1].
            _so = _os.path.join(_os.environ.get('XDG_STATE_HOME') or _os.path.expanduser('~/.local/state'), 'jm', 'dtd-order.json')
            open(_so + '.tmp', 'w').write(json.dumps({'ts': time.time(), 'dtd_id': _sid, 'ids': _order}))
            _os.replace(_so + '.tmp', _so)
        _top = ','.join(_order[:15])
        _lr = '/tmp/dtd-' + _sid + '.lastrender'
        try:
            _prev = open(_lr).read()
        except Exception:
            _prev = None
        if _top != _prev:
            open(_lr, 'w').write(_top)
            with open(_evp, 'a') as _ef:
                _ef.write(chr(9).join(['%.3f' % time.time(), _sid, 'render', 'list', _top, str(len(_order))]) + chr(10))
except Exception:
    pass
# Lifecycle store (2026-10-17): first day each id was rendered and how many
# list regenerations showed it per day, so a completion can carry its age
# and impression count (dtd_outcome.py reads this) and the backlog tail is
# measurable. Pruned to 60 days; tmp + rename so a crash never truncates it.
try:
    _lcp = _os.environ.get('DTD_LIFECYCLE', '')
    if _lcp and _order:
        import json as _lj
        try:
            _lc = _lj.load(open(_lcp))
        except Exception:
            _lc = {}
        _fs = _lc.setdefault('first_seen', {}); _dd = _lc.setdefault('days', {})
        _dk = time.strftime('%Y-%m-%d'); _day = _dd.setdefault(_dk, {})
        for _oid in _order:
            if _oid:
                _fs.setdefault(_oid, _dk); _day[_oid] = _day.get(_oid, 0) + 1
        for _k in [k for k in _dd if k < time.strftime('%Y-%m-%d', time.localtime(time.time() - 60 * 86400))]:
            _dd.pop(_k, None)
        _tmp = _lcp + '.tmp'
        open(_tmp, 'w').write(_lj.dumps(_lc, separators=(',', ':')))
        _os.replace(_tmp, _lcp)
except Exception:
    pass
" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}"
print -- "$EPOCHREALTIME\tlist-end\t" >> "$_tl" 2>/dev/null
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer while this script ran — leaked SGR motion
# sequences type themselves into fzf's query as literal ^[[<34;x;yM text on
# resume otherwise (bug 2026-07-05, ported here from done.sh/defer.sh/edit.sh/
# split.sh). This is the ONE reload script that was missing the fix: fzf's
# reload(...) binding blocks its own tty read while this runs, exactly like
# an execute() script does, but LISTEOF was never added to that family. Since
# reload($DTD_LIST ...) is what the auto-reload watcher fires on every single
# FIFO-worker completion (dtd.sh's background did-fast.py loop touches
# $CACHE per item), this window recurs constantly WHILE dtd is "processing" —
# matching the reported symptom exactly ("input box interprets scrolling as
# keystrokes" only during processing, bug 2026-09-11). Written to /dev/tty,
# never stdout, so it can't corrupt the row data reload() reads from this
# script's stdout.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
# No tty drain here any more (2026-10-21). fzf runs reload() AND reload-sync()
# commands in a BACKGROUND process group (verified against fzf 0.72 with
# tcgetpgrp: reload-sync -> bg, execute-silent -> fg), where stty / `read -k`
# raise SIGTTOU/SIGTTIN and freeze the script (the 2026-10-04 picker hang).
# The guard added then -- a python3 tcgetpgrp check -- therefore ALWAYS said
# no, so the drain was dead code that still cost a ~30 ms python spawn on
# every reload: every ctrl-d/v/k picker open and every watcher reload
# (1-2 x/s while a timer runs). Measured list-end -> list-drained 30-39 ms
# in the timing log with the drain itself never firing. And because fzf
# keeps reading the tty itself during a reload, a drain here could only
# ever have stolen keys typed ahead ("ctrl-d 2 enter" in one breath), never
# helped. The mouse-mode printf above is a plain write and stays.
print -- "$EPOCHREALTIME\tlist-drained\t" >> "$_tl" 2>/dev/null
LISTEOF
chmod +x "$DTD_LIST"

# Self-test the generated list script before fzf takes the screen. The python
# payload above lives inside a zsh double-quoted string: one stray " in it
# splits the -c argument, shifts argv, and the picker opens empty with a
# traceback bleeding into the prompt (bug 2026-07-21: int('2026-07-21')).
# {} is a valid-but-empty cache; /dev/null feeds the overlay args.
DTD_SMOKE="/tmp/dtd-$DTD_ID.smoke.json"
printf '{}' > "$DTD_SMOKE"
if "$DTD_LIST" "$DTD_SMOKE" /dev/null /dev/null "$(date +%Y-%m-%d)" 80 /dev/null /dev/null /dev/null >/dev/null 2>&1; then
  rm -f "$DTD_SMOKE"
else
  echo "⚠ dtd: list.sh self-test FAILED — likely a stray double-quote in the list heredoc in dtd.sh."
  echo "  Debug: zsh -x $DTD_LIST $DTD_SMOKE /dev/null /dev/null $(date +%Y-%m-%d) 80"
  sleep 3
fi

# View-cycle script (ctrl-t): advance the view-state file to the next view,
# wrapping around. Add a view by appending to `views` here and handling its
# name in the list generator. The subsequent reload re-runs the generator.
cat > "$DTD_VIEWTOGGLE" << 'VTEOF'
#!/bin/zsh
[[ -n "${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
VIEW="PLACEHOLDER_VIEW"
HDR="PLACEHOLDER_HDR"
views=(default project time)     # cycle order; append new views here
typeset -A labels
labels=(default "default" project "by project" time "by time (short first)")
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
: > "$DTD_DOMAIN"            # no domain-search scope at session start

# --- Domain-search script used by fzf's change: binding (2026-09-16) ---
# Fires on every keystroke. A domain code is conveyed PURELY via row color
# (see COLORS in the list generator, and its own "no project-name prefix"
# comment) -- never as searchable text -- so fzf's own fuzzy matcher can
# never find it, no matter how --with-nth/--nth are combined (verified: once
# --with-nth restricts the searched+displayed text to field 1, --nth cannot
# reach a hidden later field to widen search scope back out -- there is no
# flag combination that displays field 1 while also searching a different,
# hidden field). So this bypasses fzf's matcher entirely for the one case
# that needs it: reload the list PRE-FILTERED to the named domain (the list
# generator already computes each task's true domain via domain_of() for
# ctrl-t project view; step 5a below just reuses it as a hard filter instead
# of a sort key), then clear the query so fzf's own matcher — now filtering
# an already-scoped list against an empty string — shows all of it.
#
# Runs via execute-silent on EVERY keystroke, so the domain-code check here
# must stay a cheap in-process string comparison, not a re-invocation of the
# (much heavier) list generator, or normal typing would lag. Only an EXACT
# match pays for the reload; every other keystroke is a no-op (no POST, no
# subprocess beyond this check) — ordinary fuzzy search is completely
# unaffected. Keep this domain-code set in sync with COLORS above (see
# test_dtd_domain_search.py, mirroring test_dtd_colors.py's existing
# cross-file sync check).
DTD_DOMAINSEARCH="/tmp/dtd-$DTD_ID.domainsearch.sh"
cat > "$DTD_DOMAINSEARCH" << EOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
q="\$1"
print -rn -- "\$q" > "$DTD_QUERY" 2>/dev/null   # live query for the done/defer routers' pick records
# On the delay screen a typed number means "delay N days" (Enter applies it,
# see the enter router): say so in the header as you type (2026-10-04).
if [[ -s "$DTD_BLOCKPICK" ]]; then
  _n="\${q// /}"
  # Header push (2026-10-21): there is no result: transform any more (it was
  # a synchronous spawn on every keystroke), so this script, which already
  # runs per keystroke, pushes the hint over the listen port itself in the
  # same shape hdrgen prints (text, three spaces, the key legend). The arm
  # script's '📅 schedule …' line is saved to .hint on the first digit and
  # restored (one POST, not one per keystroke) when the query stops being
  # digits. This block sits ABOVE the .days early exit below on purpose.
  _hport="\$(cat "$DTD_PORT" 2>/dev/null)"
  _push_hdr() {   # \$1 = header text (no newlines)
    [[ -z "\$_hport" ]] && return 0
    if [[ -n "\$FZF_API_KEY" ]]; then
      curl -s -H "X-API-Key: \$FZF_API_KEY" -XPOST "localhost:\$_hport" --data-binary "change-header:\$1   \$DTD_KEYS" >/dev/null 2>&1
    else
      curl -s -XPOST "localhost:\$_hport" --data-binary "change-header:\$1   \$DTD_KEYS" >/dev/null 2>&1
    fi
  }
  if [[ "\$_n" == <-> ]]; then
    zmodload zsh/datetime 2>/dev/null
    [[ -e "$DTD_BLOCKPICK.hint" ]] || cp "$DTD_HDR" "$DTD_BLOCKPICK.hint" 2>/dev/null || : > "$DTD_BLOCKPICK.hint"
    if (( _n == 0 )); then
      _hint="↵ delay 0 days → next occurrence (recurring) / one day (one-off)"
    else
      # Calendar days anchored at local noon (DST-safe; was EPOCHSECONDS + n*86400).
      strftime -s _today '%Y-%m-%d' \$EPOCHSECONDS
      strftime -r -s _noon '%Y-%m-%d %H' "\$_today 12"
      LC_TIME=C strftime -s _d '%a %b %-d' \$(( _noon + _n * 86400 ))
      _hint="↵ delay \$_n day\$( (( _n == 1 )) || print -n s) → \$_d"
    fi
    print -r -- "\$_hint" > "$DTD_HDR"
    _push_hdr "\$_hint"
  elif [[ -e "$DTD_BLOCKPICK.hint" ]]; then
    cat "$DTD_BLOCKPICK.hint" > "$DTD_HDR" 2>/dev/null; rm -f "$DTD_BLOCKPICK.hint"
    _push_hdr "\$(<"$DTD_HDR")"
  fi
  # A bare number means DAYS, never a time (2026-10-05: "4" fuzzy-matched the
  # 14:00 申 row and Enter on it looked like the default). While the query is
  # all digits the list generator shows ONLY the day row (.days flag); times
  # take a colon ("16:") or the block name ("申"/"shen"). Reload only when the
  # flag actually changes, so ordinary typing costs nothing.
  _want=""
  [[ "\$_n" == <-> ]] && _want="\$_n"
  _have="\$(cat "$DTD_BLOCKPICK.days" 2>/dev/null)"
  [[ "\$_want" == "\$_have" ]] && exit 0
  if [[ -n "\$_want" ]]; then printf '%s' "\$_want" > "$DTD_BLOCKPICK.days"
  else rm -f "$DTD_BLOCKPICK.days"; fi
  port="\$(cat "$DTD_PORT" 2>/dev/null)"
  [[ -z "\$port" ]] && exit 0
  today="\$(date +%Y-%m-%d)"
  reload_cmd="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '\$today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
  if [[ -n "\$FZF_API_KEY" ]]; then
    curl -s -H "X-API-Key: \$FZF_API_KEY" -XPOST "localhost:\$port" \\
      --data "reload(\$reload_cmd)" >/dev/null 2>&1
  else
    curl -s -XPOST "localhost:\$port" --data "reload(\$reload_cmd)" >/dev/null 2>&1
  fi
  exit 0
fi
case "\$q" in
  g245|h335|epcn|s897|hcmc2|xk87|xk88|xk20|xk22|hci|i9|n156|hcmc|m5x2|m828|hcb|hcbp|infra|i444|i447|hcm|hcmp|hcmr|家|睡觉)
    ;;
  *)
    exit 0 ;;
esac
port="\$(cat "$DTD_PORT" 2>/dev/null)"
[[ -z "\$port" ]] && exit 0
# Resolved directly (not deferred like DTD_RELOAD's \$(date...) trick) --
# this script is spawned fresh on every keystroke, not baked into a
# long-lived --bind string at fzf startup, so there's no stale-date risk
# to defer against; resolving now also sidesteps stacking a third layer of
# shell-escaping on top of the curl-POST -> fzf-reload hop.
today="\$(date +%Y-%m-%d)"
# Persist the scope so every later reload (watcher, ctrl-*, resize) keeps it;
# the list generator reads this file as its 11th arg (2026-10-01 fix).
printf '%s' "\$q" > "$DTD_DOMAIN"
reload_cmd="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '\$today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
echo "🔍 domain: \$q (ctrl-r to clear)" > "$DTD_HDR"
if [[ -n "\$FZF_API_KEY" ]]; then
  curl -s -H "X-API-Key: \$FZF_API_KEY" -XPOST "localhost:\$port" \\
    --data "reload(\$reload_cmd)+clear-query" >/dev/null 2>&1
else
  curl -s -XPOST "localhost:\$port" \\
    --data "reload(\$reload_cmd)+clear-query" >/dev/null 2>&1
fi
EOF
chmod +x "$DTD_DOMAINSEARCH"

# --- Bump script used by fzf alt-up binding (2026-10-04) ---
# Sets Todoist priority p1 on the cursor row (or every marked row). The list
# snapshot is patched first, so the reload shows the task at the top of the
# plain-task area at once; the API call runs detached (same &! pattern as
# defer's workers) and the next cache refresh carries the real value.
DTD_BUMP="/tmp/dtd-$DTD_ID.bump.sh"
cat > "$DTD_BUMP" << BUMPEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}bump\${_T}\${(j:,:)@}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\tbump-start\t\$#" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
typeset -a ids
for _t in "\$@"; do [[ -n "\$_t" && "\$_t" != BLOCK:* ]] && ids+=("\$_t"); done
(( \${#ids[@]} )) || exit 0
jq --args '(.[] | arrays | .[] | objects | select(.id as \$i | \$ARGS.positional | index(\$i) != null)).priority = 4' "\${ids[@]}" \\
  < "$DTD_CACHE_FILE" > "$DTD_CACHE_FILE.bump" 2>/dev/null && mv "$DTD_CACHE_FILE.bump" "$DTD_CACHE_FILE"
echo "⬆ p1: \${#ids[@]} task(s)" > "$DTD_HDR"
( python3 "\$HOME/i446-monorepo/tools/did/bump-fast.py" "\${ids[@]}" >/dev/null 2>&1 \\
    || echo "✗ p1 failed for \${#ids[@]} task(s) — will revert on next refresh" > "$DTD_HDR" ) >/dev/null 2>&1 &!
print -- "\$EPOCHREALTIME\tbump-end\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
BUMPEOF
chmod +x "$DTD_BUMP"

# --- Skip script used by fzf ctrl-k binding ---
DTD_SKIP="/tmp/dtd-$DTD_ID.skip.sh"
cat > "$DTD_SKIP" << SKIPEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
SKIPPED="$DTD_SKIPPED"
HDR="$DTD_HDR"
[[ "\$1" == BLOCK:* ]] && exit 0   # picker rows are not tasks
# Multi-select fan-out (2026-07-23): ctrl-k passes {+2} (all marked ids, or
# the cursor row's). Re-run self per id so the single-id body stays untouched.
if (( \$# > 1 )); then
  for _tid in "\$@"; do "\$0" "\$_tid"; done
  exit 0
fi
task="\$1"
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
echo "\$clean" | tr '[:upper:]' '[:lower:]' >> "\$SKIPPED"
echo "⏭ \$clean" > "\$HDR"
SKIPEOF
chmod +x "$DTD_SKIP"

# --- Delete script used by fzf ctrl-x binding ---
DTD_DELETE="/tmp/dtd-$DTD_ID.delete.sh"
cat > "$DTD_DELETE" << DELETEEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; (( \$# == 1 )) && print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}delete\${_T}\$1\${_T}" >> "$DTD_EVENTS" 2>/dev/null
HDR="$DTD_HDR"
CACHE_FILE="$DTD_CACHE_FILE"
REMOVED="$DTD_REMOVED"
[[ "\$1" == BLOCK:* ]] && exit 0   # picker rows are not tasks
# Multi-select fan-out (2026-07-23): ctrl-x passes {+2} (all marked ids, or
# the cursor row's). Re-run self per id so the single-id body stays untouched.
if (( \$# > 1 )); then
  for _tid in "\$@"; do "\$0" "\$_tid"; done
  exit 0
fi
task="\$1"
# Strip ANSI codes and recurring indicator
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
# fzf field 2 (\$1) IS the Todoist id (id-based since 2026-07-12; the old
# name-match lookup that ran here was dead code, overwritten on the next line).
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
  # Optimistic (2026-10-07, "ctrl-x is a bit slow"): hide the row by id NOW
  # and let fzf reload at once; the two Todoist round trips (pre-image GET +
  # DELETE, 0.3-1s each from Ix) ran inline under execute-silent and froze
  # dtd 1-2s per task. A failed DELETE rolls the hide back, same as defer.
  # Hide by id, not by name: two identically-named tasks must not both
  # vanish (2026-08-17).
  echo "\$tid" >> "\$REMOVED.ids"
  echo "🗑 deleting: \$clean" > "\$HDR"
  (
  # Pre-image for ctrl-z undo — fetched before the DELETE, journaled only
  # after a successful DELETE (a failed delete must not be undoable, or
  # ctrl-z would recreate a task that still exists)
  pre=\$(curl -s "https://api.todoist.com/api/v1/tasks/\$tid" \\
    -H "Authorization: Bearer \$(cat ~/.config/todoist/token)" 2>/dev/null)
  code=\$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "https://api.todoist.com/api/v1/tasks/\$tid" \\
    -H "Authorization: Bearer \$(cat ~/.config/todoist/token)" 2>/dev/null)
  if [[ "\$code" == 2* ]]; then
    printf '%s' "\$pre" | python3 -c "
import json, sys
name, fallback, tid = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    task = json.load(sys.stdin)
except Exception:
    task = {}
if not isinstance(task, dict) or not task.get('content'):
    task = {'content': fallback}
print(json.dumps({'type': 'delete', 'names': [name], 'task': task, 'task_id': tid},
                 ensure_ascii=False))
" "\${fullname:-\$clean}" "\$clean" "\$tid" | python3 "$UNDO_FAST" --append "$DTD_JOURNAL"
    # Daily habit (0neon/夜neon) deleted = N/A for today: explicit 0 in its
    # 0n column + the day's NA file, so validate-daily-habits --fix doesn't
    # resurrect the card the same day (ctrl-z recreates the card, keeps the 0).
    python3 - "\${fullname:-\$clean}" "\$pre" << 'NAEOF'
import datetime, json, pathlib, subprocess, sys
name = sys.argv[1].strip()
try:
    task = json.loads(sys.argv[2])
except Exception:
    task = {}
labels = task.get("labels") or []
if not ("0neon" in labels or "夜neon" in labels):
    sys.exit(0)
na = (pathlib.Path.home() / ".cache/jm"
      / f"habits-na-{datetime.date.today():%Y-%m-%d}.json")
na.parent.mkdir(parents=True, exist_ok=True)
try:
    names = json.loads(na.read_text())
except Exception:
    names = []
if name not in names:
    names.append(name)
    na.write_text(json.dumps(names, ensure_ascii=False) + "\n")
subprocess.run(
    ["python3", str(pathlib.Path.home() / "i446-monorepo/tools/did/did-fast.py"),
     f"{name} 0"], capture_output=True, timeout=120)
NAEOF
    echo "🗑 Deleted: \$clean" > "\$HDR"
  else
    grep -v -x -F -- "\$tid" "\$REMOVED.ids" > "\$REMOVED.ids.tmp" 2>/dev/null
    mv "\$REMOVED.ids.tmp" "\$REMOVED.ids"
    echo "? delete failed (HTTP \$code): \$clean (restored to list)" > "\$HDR"
  fi
  ) >/dev/null 2>&1 &!
  # Reset any mouse-tracking mode a child enabled, and drain bytes already
  # queued in the tty buffer (bug 2026-07-05; echo off first, 2026-09-24) —
  # cheap now that the Todoist calls no longer run inline.
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
  stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
  while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
else
  echo "? delete: task not found" > "\$HDR"
fi
DELETEEOF
chmod +x "$DTD_DELETE"

# --- Split script used by fzf ctrl-p binding ---
DTD_SPLIT="/tmp/dtd-$DTD_ID.split.sh"
cat > "$DTD_SPLIT" << 'SPLITEOF'
#!/bin/zsh
[[ -n "${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "$EPOCHREALTIME${_T}PLACEHOLDER_SID${_T}action${_T}split${_T}$1${_T}" >> "PLACEHOLDER_EVENTS" 2>/dev/null
# Split a task: claim partial points today; the rest stays on today unless a do-date moves it.
# Args: $1 task id, $2 [N] points done today, $3 done record name (blank =
# the task's own name), $4 remainder name (blank = keep), $5 points remaining
# (blank = total - done), $6 min done, $7 min remaining, $8/$9 haiku names,
# $10 {N} bonus done, $11 bonus remaining (blank = total - done), $12 the
# remainder's do-date (YYYY-MM-DD, blank = today), $13 keep remainder
# (0 = close the task: partial credit, nothing deferred; 2026-10-22). Since 2026-10-21 the questions are asked on the
# ctrl-o/ctrl-g edit screen (" ✓ <pts> [what I did]" on the edit line) and
# dtd's edit script calls this with the answers; there is no prompt here.
# All Todoist/Neon writes done inline via Python.

HDR="PLACEHOLDER_HDR"
REMOVED="PLACEHOLDER_REMOVED"
CACHE_FILE="PLACEHOLDER_CACHE"
DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"

task_id="$1"
TIMING="PLACEHOLDER_TIMING"
print -- "$EPOCHREALTIME\tsplit-start\t$task_id" >> "$TIMING" 2>/dev/null
task=$(python3 "$HOME/i446-monorepo/tools/did/dtd_resolve.py" "$CACHE_FILE" "$1")  # id -> canonical content (display/clean only)

# Extract points and (N) duration from task. Points come in TWO mutually
# exclusive styles (did-fast.py: "SQUARE brackets only. {N} is the 0g bonus"):
# [N] = domain points (routed via Todoist label), {N} = 0g bonus (routed to
# 0分 col Q regardless of label). Splitting a {N} task must preserve the
# curly style throughout — treating it as [N] silently loses the total
# (regression 2026-07-28: a {50} task's total came back "?", forcing
# remaining_pts to 0 and wiping the {N} marker off both halves entirely).
# Since 2026-10-22 a task may carry BOTH ([N] task points, {N} 0g bonus,
# common on -1g goals): each has its own done/remaining pair and its own
# did-fast write, so neither style is lost.
total=$(echo "$task" | grep -oE '\[[0-9]+\]' | head -1 | tr -d '[]')
bonus_total=$(echo "$task" | grep -oE '\{[0-9]+\}' | head -1 | tr -d '{}')
duration=$(echo "$task" | grep -oE '\([0-9]+\)' | head -1 | tr -d '()')
[[ -z "$total" ]] && total="?"

# Was three GUI dialogs (2026-06), three sequential prompts, then one curses
# form (split-form.py, 2026-10-04); now the answers arrive as arguments.
pts_today="${2:-}"; done_desc="${3:-}"; remaining_desc="${4:-}"; remaining_override="${5:-}"; minutes_done="${6:-}"; remaining_min_override="${7:-}"; done_short="${8:-}"; rem_short="${9:-}"
bonus_today="${10:-}"; rem_bonus_override="${11:-}"; rem_due="${12:-}"; keep_rem="${13:-1}"
[[ "$pts_today" =~ ^[0-9]+$ || "$bonus_today" =~ ^[0-9]+$ ]] || { echo "split cancelled" > "$HDR"; exit 0; }

clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\[[0-9.+]*\/m\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')
# Strip truncation: if fzf middle-truncated the name with …, search by the
# prefix before it — otherwise the Todoist substring match fails with
# "task not found" after the user already answered all three dialogs
# (regression 2026-06-06; same fix as the defer script)
if [[ "$clean" == *"…"* ]]; then
  clean="${clean%%…*}"
  clean=$(echo "$clean" | sed 's/ *$//')
fi
echo "⏳ splitting: $clean" > "$HDR"

# The writes run in the BACKGROUND (2026-10-19: "slow to get back to dtd once
# complete"). Four sequential Todoist calls + a did-fast Neon write (3-4 s on
# its own) + the undo journal held fzf blocked for 5-8 s after the form.
# Same shape as alt-enter: hide the row now (REMOVED), hand the tty back,
# and let the writer flip the header from ⏳ to ✂ (or ⚠) when it lands.
# </dev/null so no child inherits the tty; &! disowns so fzf's reload can't
# SIGHUP it; stdout/err to a log so a traceback is still findable.
# Only when the task LEAVES today (closed, or remainder due later): a
# remainder kept on today shares the name, and a name-hide here kept it out
# of dtd for the rest of the session (bug 2026-10-23: split task vanished
# though it was still in Todoist). Same rule as the writer's own hide below.
if [[ "$keep_rem" == 0 || ( "$rem_due" == [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] && "$rem_due" > "$(date +%Y-%m-%d)" ) ]]; then
  echo "$clean" | tr '[:upper:]' '[:lower:]' >> "$REMOVED"
fi
print -- "$EPOCHREALTIME\tsplit-bg\t$task_id" >> "$TIMING" 2>/dev/null
{
trap '' HUP   # outlive the tty: a dtd exit (or a closing pane) mid-write must not kill the writer
# Find the original Todoist task and get its labels/project
python3 -c "
import json, re, sys, urllib.request

TOKEN = open(__import__('os').path.expanduser('~/.config/todoist/token')).read().strip()
BASE = 'https://api.todoist.com/api/v1'
HDR = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}

clean = sys.argv[1]
has_pts = sys.argv[2].strip().isdigit()
pts_today = int(sys.argv[2]) if has_pts else 0
total = int(sys.argv[3]) if sys.argv[3] != '?' else 0
done_desc = sys.argv[4]
remaining_desc = sys.argv[5]
duration = sys.argv[6]
hdr_file = sys.argv[7]
removed_file = sys.argv[8]
task_id = sys.argv[9]
# {N} bonus: 10th done, 11th the task total, 17th typed remaining
has_bonus = sys.argv[10].strip().isdigit()
bonus_today = int(sys.argv[10]) if has_bonus else 0
bonus_total = int(sys.argv[11]) if sys.argv[11].strip().isdigit() else 0
remaining_bonus = max(0, bonus_total - bonus_today) if bonus_total > 0 else 0
if len(sys.argv) > 17 and sys.argv[17].strip().isdigit():
    remaining_bonus = int(sys.argv[17].strip())
rem_due = sys.argv[18].strip() if len(sys.argv) > 18 else ''
keep_rem = (sys.argv[19].strip() if len(sys.argv) > 19 else '1') != '0'

remaining_pts = max(0, total - pts_today) if total > 0 else 0
# Points remaining typed in the form (12th arg) wins over total - done.
if len(sys.argv) > 12 and sys.argv[12].strip().isdigit():
    remaining_pts = int(sys.argv[12].strip())
# Minutes done (13th arg, edit form 2026-10-21): the done record takes them
# and the remainder keeps duration - done; blank = the old behaviour (the
# done record shows the whole duration, the remainder keeps it too).
minutes_done = sys.argv[13].strip() if len(sys.argv) > 13 else ''
done_dur = duration
remaining_dur = duration
if minutes_done.isdigit():
    done_dur = minutes_done
    remaining_dur = str(max(0, int(duration) - int(minutes_done))) if duration.isdigit() else ''
# Minutes remaining typed on the form (14th arg) wins, like the 12th for points.
if len(sys.argv) > 14 and sys.argv[14].strip().isdigit():
    remaining_dur = sys.argv[14].strip()
# Haiku names typed on the form (15th: done record, 16th: remainder).
done_short = sys.argv[15].strip() if len(sys.argv) > 15 else ''
rem_short = sys.argv[16].strip() if len(sys.argv) > 16 else ''

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
_done_tags = [f'({done_dur or pts_today or bonus_today})']
if pts_today > 0:
    _done_tags.append(f'[{pts_today}]')
if bonus_today > 0:
    _done_tags.append(f'{{{bonus_today}}}')
posthoc_content = ' '.join([today_label] + _done_tags)
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
    if done_short:
        # the done record's own short name, where shorten.py would look
        try:
            import hashlib as _hl
            _h = _hl.sha1(f'37:{posthoc_content}'.encode('utf-8')).hexdigest()[:8]
            api('POST', '/comments', {'task_id': posthoc['id'], 'content': f'dtd-short:{_h}:{done_short}'})
        except Exception:
            pass

# 2. Update original task: new content with remaining description + reschedule
#    to the do-date (blank = today: 2026-10-22, the remainder of a split kept
#    vanishing from dtd because it defaulted to tomorrow). keep_rem off =
#    partial credit: the task is closed as it stands, nothing deferred.
rem_date = date.today().isoformat()
if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', rem_due):
    rem_date = rem_due
# Tags are emitted independently: time may run out while points remain and
# vice versa; an empty duration never writes a bare '()'.
_tags = []
if remaining_dur and remaining_dur != '0':
    _tags.append(f'({remaining_dur})')
if remaining_pts > 0:
    _tags.append(f'[{remaining_pts}]')
if remaining_bonus > 0:
    _tags.append(f'{{{remaining_bonus}}}')
new_content = ' '.join([remaining_desc or clean] + _tags)
if keep_rem:
    api('POST', f'/tasks/{tid}', {
        'content': new_content,
        'due_date': rem_date,
    })
else:
    api('POST', f'/tasks/{tid}/close')
if rem_short and keep_rem:
    # stored against the FINAL content (tags just changed), sidecar + comment
    import subprocess as _sp
    _sp.run(['python3', '$HOME/i446-monorepo/tools/did/edit-fast.py', '--set-short', '--id', tid,
             '$CACHE_FILE', '--content', new_content, '--short', rem_short],
            capture_output=True, text=True, timeout=30)

# 3. Log points to 0分 via did-fast. {N} (0g bonus) routes to column Q from
#    the curly marker alone, regardless of label -- did-fast.py: 'SQUARE
#    brackets only. {N} is the 0g bonus'; giving it a domain label too would
#    double-credit (bug 2026-07-27). [N] (domain points) needs the label for
#    column mapping, so only look one up for the square-bracket case.
#    --points-only skips Todoist matching: without it did-fast re-finds the
#    just-renamed remainder task and closes it.
#    Domains come from did-fast's own LABEL_TO_0FEN: a hardcoded copy here
#    went stale and lacked m828, so an @m828 split logged nothing while the
#    header still claimed +N today (bug 2026-10-09).
import subprocess, importlib.util, sys as _sys
try:
    _spec = importlib.util.spec_from_file_location('did_fast_split', '$HOME/i446-monorepo/tools/did/did-fast.py')
    _df = importlib.util.module_from_spec(_spec); _sys.modules['did_fast_split'] = _df; _spec.loader.exec_module(_df)
    domains = set(_df.LABEL_TO_0FEN) - {'0g'}
except Exception:
    domains = {'i9','i447','f693','f694','m5x2','m828','g245','h335','infra','cc','hcmc','hcm','hci','hcb','hcbp','xk87','xk88','xk20','xk22','s897'}
label_arg = ''
for l in labels:
    if l in domains:
        label_arg = f'@{l}'
        break
# No label with a 0分 column (qz12, n156, untagged...): credit 个, the same
# catch-all did-fast's own Todoist-match path uses (_project_fen_col). Without
# it the bare name fell to needs_agent and the [N] done today was never
# logged (bug 2026-10-22: a qz12 split lost its [20]).
if not label_arg:
    try:
        _ge = _df.neon_cols.domain_col('0分', '个')
        label_arg = next((f'@{k}' for k, v in _df.LABEL_TO_0FEN.items() if v == _ge and k in domains), '')
    except Exception:
        label_arg = ''
    label_arg = label_arg or '@g245'
# did-fast splits its input on commas/semicolons — a task name containing
# one would be parsed as multiple items, detaching [pts]/@label from the
# name and scattering the points (regression 2026-06-06: a name like
# 'Rev on ground transit. Buy nightshade, 2' logged its 10 points as '2').
# NB: NO bare double quotes in this comment — they terminate the enclosing
# python -c string and silently break the whole split (see line ~551).
safe_name = re.sub(r'[,;]+', ' ', clean)
def _credit(item):
    df = subprocess.run(['python3', '$HOME/i446-monorepo/tools/did/did-fast.py', '--points-only', item],
                        capture_output=True, text=True, timeout=30)
    try:
        return json.loads(df.stdout)
    except Exception:
        return None
# [N] with its domain label; {N} alone (a label beside it double-credits)
didfast_out = _credit(f'{safe_name} [{pts_today}] {label_arg}') if pts_today > 0 else None
didfast_bonus = _credit(f'{safe_name} {{{bonus_today}}}') if bonus_today > 0 else None
# The credit above records the done name in completed-today with NO id, and
# dtd hides a name-only completion by NAME, which hid the remainder sharing
# that name (bug 2026-10-22: a split remainder never showed up). Tie the name
# to the completed done record's id: id-backed names hide by id only.
if posthoc and keep_rem and (didfast_out or didfast_bonus):
    try:
        _mspec = importlib.util.spec_from_file_location('mc_split', '$HOME/i446-monorepo/tools/did/mark-completed.py')
        _mc = importlib.util.module_from_spec(_mspec); _mspec.loader.exec_module(_mc)
        _mc.append_names([safe_name.strip()], ids={safe_name.strip(): posthoc['id']})
    except Exception as _e:
        print(f'split: completed-today id tie failed: {_e}', file=sys.stderr)

# 4. Journal for ctrl-z undo
record = {
    'type': 'split',
    'names': [clean],
    'task_id': tid,
    'prev_content': prev_content,
    'prev_due': prev_due,
    'posthoc_id': posthoc['id'] if posthoc else None,
    'didfast': didfast_out,
    'didfast_bonus': didfast_bonus,
    'closed': not keep_rem,
}
subprocess.run(['python3', '$HOME/i446-monorepo/tools/did/undo-fast.py',
                '--append', 'PLACEHOLDER_JOURNAL'],
               input=json.dumps(record, ensure_ascii=False), text=True,
               capture_output=True, timeout=10)

# Write results
# Hide the name only when the task leaves today (closed or deferred); a
# remainder kept on today shares the name and must stay listed.
if not keep_rem or rem_date > date.today().isoformat():
    with open(removed_file, 'a') as f: f.write(clean.lower() + '\n')
_got = ' '.join(t for t in ([f'[{pts_today}]'] if pts_today > 0 else []) + ([f'{{{bonus_today}}}'] if bonus_today > 0 else [])) or '0'
_left = ' '.join(_tags[1:] if _tags and _tags[0].startswith('(') else _tags) or 'nothing'
_where = 'kept for today' if rem_date <= date.today().isoformat() else f'deferred to {rem_date}'
msg = f'✂ +{_got} today / {_left} {_where}' if keep_rem else f'✂ +{_got} today · rest dropped, task closed'
_lost = []
if pts_today > 0 and not (didfast_out or {}).get('results'):
    _lost.append(f'[{pts_today}] (did-fast: no domain)')
if bonus_today > 0 and not (didfast_bonus or {}).get('results'):
    _lost.append(f'{{{bonus_today}}}')
if _lost:
    msg = '⚠ split: ' + ' '.join(_lost) + ' NOT logged / ' + msg[2:]
with open(hdr_file, 'w') as f: f.write(msg)
" "$clean" "$pts_today" "${total:-?}" "${done_desc:-}" "${remaining_desc:-}" "${duration:-}" "$HDR" "$REMOVED" "$task_id" "${bonus_today:-}" "${bonus_total:-}" "${remaining_override:-}" "${minutes_done:-}" "${remaining_min_override:-}" "${done_short:-}" "${rem_short:-}" "${rem_bonus_override:-}" "${rem_due:-}" "${keep_rem:-1}"
print -- "$EPOCHREALTIME\tsplit-bg-end\t$task_id rc=$?" >> "$TIMING" 2>/dev/null
"/tmp/dtd-PLACEHOLDER_SID.bgrefresh.sh"   # pull Todoist + reload so the split shows (2026-10-22)
} </dev/null >> "/tmp/dtd-split.log" 2>&1 &!
print -- "$EPOCHREALTIME\tsplit-end\t$task_id" >> "$TIMING" 2>/dev/null

# Flush tty input buffered while the edit screen held the tty. With the
# terminal idle behind the prompt, two-finger touchpad scroll emits ESC[A/ESC[B
# arrow bursts that queue in the tty input buffer; left unread, fzf dumps the
# whole burst into its query as literal ^[[A^[[B text on return (bug 2026-07-14).
# Draining here consumes them before fzf reads. Also reset mouse modes, matching
# the defer/points/edit action scripts.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
SPLITEOF
# Substitute placeholder paths
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_REMOVED|$DTD_REMOVED|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g; s|PLACEHOLDER_JOURNAL|$DTD_JOURNAL|g; s|PLACEHOLDER_EVENTS|$DTD_EVENTS|g; s|PLACEHOLDER_SID|$DTD_ID|g; s|PLACEHOLDER_TIMING|$DTD_TIMING|g" "$DTD_SPLIT"
chmod +x "$DTD_SPLIT"

# --- Agent script used by fzf ctrl-a binding ---
DTD_AGENT="/tmp/dtd-$DTD_ID.agent.sh"
cat > "$DTD_AGENT" << 'AGENTEOF'
#!/bin/zsh
[[ -n "${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "$EPOCHREALTIME${_T}PLACEHOLDER_SID${_T}action${_T}agent${_T}$1${_T}" >> "PLACEHOLDER_EVENTS" 2>/dev/null
# Spawn a Claude agent in a new cmux tab to work on the selected task.
# Starts a Toggl timer, fetches task context, launches claude interactively.

TOGGL_CLI="$HOME/i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST="$HOME/i446-monorepo/tools/tg/tg-fast.py"
HDR="PLACEHOLDER_HDR"
CACHE_FILE="PLACEHOLDER_CACHE"

task="$1"
task=$(python3 "$HOME/i446-monorepo/tools/did/dtd_resolve.py" "$CACHE_FILE" "$1")  # id -> canonical content
clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\[[0-9.+]*\/m\]//g; s/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

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
TOKEN = open(__import__('os').path.expanduser('~/.config/todoist/token')).read().strip()
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
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the cmux/osascript
# spawn above — leaked SGR motion sequences type themselves into fzf's query
# as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported
# here from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
AGENTEOF
sed -i '' "s|PLACEHOLDER_HDR|$DTD_HDR|g; s|PLACEHOLDER_CACHE|$DTD_CACHE_FILE|g; s|PLACEHOLDER_EVENTS|$DTD_EVENTS|g; s|PLACEHOLDER_SID|$DTD_ID|g" "$DTD_AGENT"
chmod +x "$DTD_AGENT"

# --- Undo script used by fzf ctrl-z binding ---
# Pops the last journaled action (done/split/defer) and reverses it via
# undo-fast.py, which also removes the task from the session/removed/done
# filter files so it reappears in the list on reload.
DTD_UNDO="/tmp/dtd-$DTD_ID.undo.sh"
cat > "$DTD_UNDO" << UNDOEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}undo\${_T}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
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
  # Reset/drain here too: this is the longest blocking window (up to 5s of
  # sleep-polling above), so it's the path most likely to trigger the leak —
  # see the fuller comment below for the bug this guards against.
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
  stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
  while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
  exit 0
fi
result=\$(python3 "$UNDO_FAST" --undo "$DTD_JOURNAL" \\
  --session "$DTD_SESSION" --removed "$DTD_REMOVED" --done-json "$DTD_DONE_FILE" 2>&1)
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the poll loop and
# undo-fast call above — leaked SGR motion sequences type themselves into
# fzf's query as literal ^[[<0;16;15M text on resume otherwise (bug
# 2026-07-05, ported here from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
summary=\$(echo "\$result" | jq -r '.summary // .error // "undo failed"' 2>/dev/null)
if [[ \$(echo "\$result" | jq -r '.ok // empty' 2>/dev/null) == "true" ]]; then
  echo "↩ \$summary" > "\$HDR"
else
  echo "? \${summary:-undo failed}" > "\$HDR"
fi
UNDOEOF
chmod +x "$DTD_UNDO"

# --- Background refresh after an edit-form save (2026-10-22) ---
# "after running ctrl+p in dtd, can you run ctrl+r so that the change to that
# task is visible when I return to dtd?" The edit/split writes run detached
# AFTER fzf's own reload, so the list kept the pre-save row until a manual
# ctrl-r. Once the writer finishes it calls this: the same Todoist pull as
# ctrl-r, then a reload poked through fzf's listen port. No tty I/O (it runs
# in the background while fzf owns the terminal) and no header write, so the
# save's own result line stays visible.
DTD_BGREFRESH="/tmp/dtd-$DTD_ID.bgrefresh.sh"
cat > "$DTD_BGREFRESH" << BGREFRESHEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0
python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1 && cp "$CACHE" "$DTD_CACHE_FILE"
_port="\$(cat "$DTD_PORT" 2>/dev/null)"
[[ -n "\$_port" ]] || exit 0
_reload="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '\$(date +%Y-%m-%d)' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
if [[ -n "\$FZF_API_KEY" ]]; then
  curl -s -H "X-API-Key: \$FZF_API_KEY" -XPOST "localhost:\$_port" --data "reload(\$_reload)+transform-header($DTD_HDRGEN)" >/dev/null 2>&1
else
  curl -s -XPOST "localhost:\$_port" --data "reload(\$_reload)+transform-header($DTD_HDRGEN)" >/dev/null 2>&1
fi
BGREFRESHEOF
chmod +x "$DTD_BGREFRESH"

# --- Refresh script used by fzf ctrl-r binding ---
# Was an inline execute-silent(...) command in the --bind string itself until
# 2026-09-01, but the multi-second did-fast.py --refresh-cache network call
# it runs is a real blocking window — same leak class as the other scripts
# below, and a plain inline string has no room for the reset/drain fix (no
# clean way to embed a `while read` loop inside execute-silent(...)'s parens).
# Pulled out into its own generated script so it can carry the fix like its
# siblings.
DTD_REFRESH="/tmp/dtd-$DTD_ID.refresh.sh"
cat > "$DTD_REFRESH" << REFRESHEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
_T=\$'\t'; zmodload zsh/datetime 2>/dev/null; print -r -- "\$EPOCHREALTIME\${_T}$DTD_ID\${_T}action\${_T}refresh\${_T}\${_T}" >> "$DTD_EVENTS" 2>/dev/null
python3 "$DID_FAST" --refresh-cache
cp "$CACHE" "$DTD_CACHE_FILE"
: > "$DTD_DOMAIN"   # ctrl-r also clears the domain-search scope
echo "🔄 refreshed" > "$DTD_HDR"
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the refresh above
# — leaked SGR motion sequences type themselves into fzf's query as literal
# ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported here from
# done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
stty -echo < /dev/tty 2>/dev/null || true  # echo OFF before draining (bug 2026-09-24, see DRAIN_ECHO note)
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
REFRESHEOF
chmod +x "$DTD_REFRESH"

# Clear leftover terminal scrollback so the picker starts on a clean screen
# (fzf --height renders inline below whatever was already on the terminal).
clear

# Keybinding hints shown on the status line. Exported so the transform-header
# bindings (which run in fzf's child shell) can read it. With --header-first the
# header renders BELOW the prompt (Claude-style status line): the live match
# count ($FZF_MATCH_COUNT), any worker status ($DTD_HDR), and these keys.
export DTD_KEYS="enter: start (0l 1s xk887 -1g 0g 1hcme 1i9 1hcb: form) | ⌥⏎: done | ctrl-s: timer | ctrl-d: 📅schedule | ctrl-p: ✂done-so-far | ctrl-v/k: ⏰delay | esc: back | ⌥↑: p1 top | ctrl-o/g: details+edit | ctrl-l: open link | ctrl-x: del | ctrl-z: undo | ctrl-r: refresh | ctrl-t: view | ⇧↑↓: mark multi"

# Status-line generator (the header, below the prompt): "<N left>   <worker
# status>   <keys>". fzf exports $FZF_MATCH_COUNT to this child; $DTD_KEYS is
# exported above; the worker-status file path is baked in here. Used by the
# load/result binds and after every action so worker confirmations persist.
cat > "$DTD_HDRGEN" <<HDRGENEOF
#!/bin/zsh
[[ -n "\${DTD_WARM:-}" ]] && exit 0   # DTD_WARM pre-exec (Defender first-exec scan), see the warm loop before the UI loop
# Stale-code check first, same as janus.py's render_header: if it wins, it
# replaces the WHOLE header line (in red) so a fix that shipped after this
# session launched can't be missed or mistaken for the normal status line.
zmodload zsh/stat 2>/dev/null
zstat -A _st +mtime "$DTD_SELF" 2>/dev/null; live_mtime=\${_st[1]:-0}   # builtin stat (was an exec)
if (( live_mtime > $DTD_SRC_MTIME + 1 )); then
  printf '\033[1;91m⚠ RESTART DTD — code updated on disk\033[0m'
  exit 0
fi
zmodload zsh/datetime 2>/dev/null
print -- "\$EPOCHREALTIME\thdr-start\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
# Builtin reads + expansion (was cat|tr twice = 4 execs; this script runs on
# every keystroke's result event, so under a Defender exec stall it cost
# 0.8-1.6s per keypress -- measured 2026-10-03).
# "footer" mode (2026-10-12): the top line is a plain header,
# "dtd   N done   N left". done = today's count from the Ix tally
# ("<pts> 分 · <N> done"), left = every row in the list (not the filtered
# match count, so typing a query doesn't change it).
if [[ "\$1" == footer ]]; then
  tally=""; [[ -r "$DTD_TALLY" ]] && tally="\$(<"$DTD_TALLY")"
  done_n="\${\${tally##*· }%% done*}"; [[ "\$done_n" == <-> ]] || done_n="…"
  printf '\033[1mdtd\033[0m   %s done   %s left' "\$done_n" "\${FZF_TOTAL_COUNT:-0}"
  exit 0
fi
ws=""; [[ -r "$DTD_HDR" ]] && ws="\$(<"$DTD_HDR")"; ws="\${ws//\$'\n'/ }"
# Stale-list warning (2026-10-25: "I thought I marked lock down #data channel
# done but I see it today"): dtd opens instantly on the saved task cache and
# refreshes in the background, so after a laptop wake it showed last night's
# list, with a task completed in the Todoist app since, for ~3 minutes. The
# refresh daemon keeps the live cache under ~3 min old; older than
# DTD_CACHE_MAX_AGE means the list on screen may be out of date. Builtins only
# (zstat/strftime): this runs on every keystroke.
zstat -A _cs +mtime "$CACHE" 2>/dev/null; cache_m=\${_cs[1]:-0}
if (( cache_m > 0 && EPOCHSECONDS - cache_m > $DTD_CACHE_MAX_AGE )); then
  strftime -s _ct '%H:%M' \$cache_m
  printf '\033[33m⏳ list from %s, refreshing (ctrl-r)\033[0m   ' "\$_ct"
fi
printf '%s   %s' "\$ws" "\$DTD_KEYS"
print -- "\$EPOCHREALTIME\thdr-end\t" >> "${DTD_TIMING:-/dev/null}" 2>/dev/null
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
# `3>&-` closes this job's inherited copy of the FIFO write-end (fd 3, opened
# by `exec 3>"$DTD_FIFO"` above) BEFORE exec'ing python3 — otherwise the
# ticker process holds fd 3 open for its whole life, same bug class as the
# watcher below (regression 2026-07-11): the main loop's `exec 3>&-` on exit
# no longer drops the FIFO's writer count to zero, the worker's `read` never
# sees EOF, and dtd hangs silently on exit forever (2026-07-15: `lsof` showed
# a live dtd-ticker.py holding fd 3w on the FIFO).
rm -f "$DTD_PORT"
# The ticker (dtd-ticker.py: live ▶ timer + active-task lines in the footer) is
# retired (2026-10-12, "forget about the line at the top"): the footer is the
# static "dtd   N done   N left" header from $DTD_HDRGEN footer.
TICKER_PID=""

# Day-tally refresher: pull the single Ix computation (points 分 + tasks done)
# every 15s into $DTD_TALLY so the header shows today's real totals. It reads
# from the always-on Ix mobile server (ix:5560/api/summary) rather than
# recomputing here — the Excel daemon is on Ix and this machine's cache lags.
# Best-effort; self-exits when the session sentinel is gone.
: > "$DTD_TALLY"
(
  # Close this subshell's inherited copy of fd 3 immediately — same fix as
  # the ticker above and the watcher below (regression 2026-07-11 bug class).
  exec 3>&-
  while [[ -f "$DTD_SESSION" ]]; do
    t=$(curl -fsS --max-time 3 http://ix:5560/api/summary 2>/dev/null \
        | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    if d.get("ok"): print("%s 分 · %s done" % (d.get("points",0), d.get("done",0)))
except Exception: pass' 2>/dev/null)
    if [[ -n "$t" && "$t" != "$(<"$DTD_TALLY")" ]]; then
      printf '%s' "$t" > "$DTD_TALLY"
      p=$(<"$DTD_PORT" 2>/dev/null)
      [[ -n "$p" ]] && curl -fsS --max-time 2 -X POST "localhost:$p" \
        -d "transform-footer($DTD_HDRGEN footer)" &>/dev/null
    fi
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
  last_m=${DTD_SNAP_M:-$(stat -f %m "$CACHE" 2>/dev/null)}
  last_blk="$(date +%Y%m%d)-$(( ( $(date +%H) - 4 ) / 2 ))"
  last_day="$(date +%Y-%m-%d)"
  while [[ -f "$DTD_PORT" ]]; do
    # Claims live in the root and in remote/<host>/ (each other host's mirror,
    # 2026-10-25), so both the mtime watch and the working glob cover both.
    # Wake within 0.5s of a claim change (fractional mtime: two writes in one
    # second still differ), and keep cycling every 0.5s while any claim is
    # working so its spinner glyph animates; otherwise the usual 2s cadence.
    for _acw in 1 2 3 4; do
      sleep 0.5
      [[ "$(stat -f %Fm "$HOME"/vault/z_ibx/agent-claims{,/remote/*(N/)} 2>/dev/null)" != "${last_ac:-}" ]] && break
      _acs=("$HOME"/vault/z_ibx/agent-claims/{,remote/*/}*.state(N))
      (( ${#_acs} )) && grep -qs '^working' "${_acs[@]}" </dev/null && break
      # JM's own timer running: 1s cadence so the top row's glyph moves (2026-10-16).
      (( _acw >= 2 )) && [[ -s "$DTD_TIMER" ]] && break
    done
    _dtd_tz_refresh
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
    # Cross-machine completions (2026-07-30): another host's did-fast (e.g. a
    # janus-mobile swipe on ix) mirrors its completed-today record to the
    # synced vault as z_ibx/completed-today-<host>.json. When any remote
    # mirror's mtime advances, fold it into the local completed-today
    # ($DONE) and touch $CACHE so the reload branch below rebuilds the
    # overlay and the just-completed card disappears from this dtd too.
    cur_rm=$(stat -f %m "$HOME"/vault/z_ibx/completed-today-*.json 2>/dev/null | sort -rn | head -1)
    if [[ -n "$cur_rm" && "$cur_rm" != "${last_rm:-}" ]]; then
      last_rm="$cur_rm"
      python3 "$HOME/i446-monorepo/tools/did/mark-completed.py" --absorb-remote >/dev/null 2>&1
      touch "$CACHE" 2>/dev/null
    fi
    # Agent claims (/claim, 2026-10-05): a session going working/idle rewrites
    # its state file via rename, which advances the claims dir mtime (here or
    # via Syncthing from another host). Force the reload below so the task
    # re-greys / un-greys. (Not `touch $CACHE`: that would make a stale task
    # cache look fresh to did-fast.)
    ac_reload=""
    cur_ac=$(stat -f %Fm "$HOME"/vault/z_ibx/agent-claims{,/remote/*(N/)} 2>/dev/null)
    if [[ -n "$cur_ac" && "$cur_ac" != "${last_ac:-}" ]]; then
      [[ -n "${last_ac:-}" ]] && ac_reload=1
      last_ac="$cur_ac"
      # Renumber cmux tabs on a claim change (2026-10-22): an ix-hosted session's
      # hooks run on ix, where there is no cmux, so its pane number (the N in
      # "N😈") reached panes.json only when some LOCAL session's hook happened
      # to run. cs writes panes.json only when the map changed, so the mtime
      # bump it causes settles after one more pass. Detached, at most every 10s.
      if command -v cmux >/dev/null 2>&1 && (( EPOCHSECONDS - ${last_retitle:-0} >= 10 )); then
        last_retitle=$EPOCHSECONDS
        ( "$HOME/.local/bin/cs" retitle </dev/null >/dev/null 2>&1 & )
      fi
    fi
    _acs=("$HOME"/vault/z_ibx/agent-claims/{,remote/*/}*.state(N))
    (( ${#_acs} )) && grep -qs '^working' "${_acs[@]}" </dev/null && ac_reload=1
    [[ -s "$DTD_TIMER" ]] && ac_reload=1   # running timer: re-render so its spinner advances
    # Schedule screen open (ctrl-d/v/k armed): hold every reload. The spinner/
    # timer cadence above otherwise re-ran the list generator every 0.5-1 s
    # UNDER the picker (2026-10-21: three regens between ctrl-d and Enter in
    # the timing log), each one a python spawn plus an fzf list swap that can
    # move the cursor off the row being chosen. last_m is left alone, so a
    # cache change that lands meanwhile is picked up on the first cycle after
    # the picker closes.
    [[ -s "$DTD_BLOCKPICK" ]] && continue
    cur_m=$(stat -f %m "$CACHE" 2>/dev/null)
    [[ -z "$cur_m" ]] && continue
    [[ "$cur_m" == "$last_m" && -z "$ac_reload" ]] && continue
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
    watch_reload="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$watch_today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
    # reload-sync, not reload (bug 2026-10-22, "I wasn't able to delay a
    # currently running task"): a running timer reloads every ~1 s for its
    # spinner, and an async reload empties fzf's match list while it
    # re-streams, so a ctrl-d/ctrl-v pressed under a search query in that
    # window had no current row and fzf silently dropped it (5/40 presses
    # landed in a headless repro; reload-sync 40/40). reload-sync keeps the
    # old rows live until the new list is complete.
    if [[ -n "$FZF_API_KEY" ]]; then
      curl -s -H "X-API-Key: $FZF_API_KEY" -XPOST "localhost:$port" --data "reload-sync($watch_reload)" >/dev/null 2>&1
    else
      curl -s -XPOST "localhost:$port" --data "reload-sync($watch_reload)" >/dev/null 2>&1
    fi
  done
) &>/dev/null &
WATCHER_PID=$!

# Pre-warm every generated action script (2026-10-21, "ctrl+d is still
# feeling a little bit delayed"). Microsoft Defender (wdavdaemon) scans a
# freshly written file on its FIRST direct exec: measured 150-300 ms for a
# two-line script and 450-600 ms for these (the 457 ms pickenter -> defer
# gap on the first Enter of a session; 614 ms in an earlier log; later
# presses 27 ms). Every script here is new per session, so the first use of
# each key paid it. `zsh file` reads the file without exec and skips the
# scan, but fzf bindings and the scripts' calls to each other all exec
# directly. So exec each one once now, in the background, with DTD_WARM=1
# (line 2 of every script exits on it before any side effect), hot path
# first. Two lanes so it finishes in ~2-3 s; $DTD_WARM_DONE marks the end
# (the launch smoke test waits for it before timing the scripts).
# fd 3 (the FIFO write end) is closed first, same as the watcher/ticker.
DTD_WARM_DONE="/tmp/dtd-$DTD_ID.warm"
rm -f "$DTD_WARM_DONE"
_dtd_warm() { local _s; for _s in "$@"; do [[ -x "$_s" ]] && DTD_WARM=1 "$_s" </dev/null >/dev/null 2>&1; done; }
( exec 3>&-
  _dtd_warm "$DTD_LIST" "$DTD_HDRGEN" "$DTD_BLOCKARM" "$DTD_PICKENTER" "$DTD_BLOCKAPPLY" \
            "$DTD_DEFER" "$DTD_DONE_ROUTER" "$DTD_DONE_HIDE" "$DTD_DONE" "$DTD_START" "$DTD_ENTER" "$DTD_BACK" &
  _dtd_warm "$DTD_DOMAINSEARCH" "$DTD_EDIT" "$DTD_SPLIT" "$DTD_UNDO" "$DTD_BUMP" "$DTD_SKIP" \
            "$DTD_DELETE" "$DTD_REFRESH" "$DTD_BGREFRESH" "$DTD_VIEWTOGGLE" "$DTD_AGENT" "$DTD_FORM" &
  wait
  : > "$DTD_WARM_DONE"
) &>/dev/null &!

# --- UI loop (reads from CACHE_SNAPSHOT variable, never the file) ---
# Usage event: session start (host + terminal size) / end (see DTD_EVENTS).
print -r -- "$EPOCHREALTIME"$'\t'"$DTD_ID"$'\t'session$'\t'start$'\t'$'\t'"${HOST%%.*} ${COLUMNS:-?}x${LINES:-?}" >> "$DTD_EVENTS" 2>/dev/null
while true; do
  # Refresh date and completed-today on each iteration (handles midnight rollover)
  _dtd_tz_refresh
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
  # Date arg is a LIVE, unevaluated \$(date ...) substitution, not \$LOCAL_TODAY
  # baked in as a literal (2026-08-08 fix): this string becomes DTD_RELOAD,
  # which fzf's --bind flags below capture as STATIC text for fzf's entire
  # (often many-hours-long) process lifetime. A literal date here means every
  # keypress-triggered reload keeps re-sending the date from whenever fzf
  # happened to launch -- past midnight, today_tasks' due<=today bound then
  # excludes every card genuinely due today (rituals included -- "the -1n
  # cards aren't rendering" bug report) on every single reload, not just a
  # transient window. A literal \$(...) here is re-evaluated by the shell fzf
  # spawns to run each reload/execute action, so it tracks the real clock for
  # as long as the fzf process stays open, no relaunch required.
  DTD_LIST_CMD="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' \"\$(date +%Y-%m-%d)\" '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'"
  DTD_RELOAD="${DTD_LIST_CMD}"
  export DTD_RELOAD DTD_HDRGEN  # back.sh prints its own reload (esc/ctrl-c)
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
  # Mouse ON = fzf's DEFAULT — this fzf build has no --mouse flag (passing it is
  # an "unknown option" error that kills fzf and breaks the list pipe, bug
  # 2026-07-15), so we simply DON'T pass --no-mouse. With mouse on, fzf consumes
  # scroll-wheel / two-finger touchpad scroll as list navigation (like Claude
  # Code) — the scroll events are parsed by fzf and never reach the query. fzf
  # subscribes to click + SGR scroll (1000/1006) only, NOT motion (1002/1003),
  # so the motion-leak that once forced --no-mouse (bug 2026-07-05: ESC[<34;x;yM
  # motion events dumped into the input) doesn't apply to fzf's own subscription.
  # The reset below still strips any stray MOTION mode a child left enabled from
  # an execute() binding. (Supersedes the --no-mouse + alt-scroll-off workaround,
  # which stopped the ^[[A^[[B flood but also killed scrolling — bugs 07-14/15.)
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
  rm -f "$DTD_BLOCKPICK" "$DTD_BLOCKPICK.mode" "$DTD_BLOCKPICK.days"   # never relaunch into a stale schedule screen
  # fzf's own error text (rc=2 exits: two unexplained ones on 2026-10-22, no
  # keypress, mid background reload) went to the tty and was wiped by the
  # exit's clear. fzf draws on /dev/tty, so stderr can go to a file.
  DTD_FZF_ERR="$STATE_DIR/dtd-fzf.err"
  print -r -- "=== $(date '+%F %T') $DTD_ID" >> "$DTD_FZF_ERR" 2>/dev/null
  fzf_output=$(eval "$DTD_LIST_CMD" | fzf --prompt="> " --layout=reverse-list --no-sort --ansi \
      --info=inline-right \
      --input-border=horizontal \
      --listen --header-first \
      --header="$DTD_KEYS" \
      --bind "start:execute-silent(echo \$FZF_PORT > $DTD_PORT)" \
      --bind "load:transform-header($DTD_HDRGEN)+transform-footer($DTD_HDRGEN footer)" \
      $( : "no result: binding (2026-10-21). It re-ran the header script on every keystroke and after every reload, a synchronous spawn each time; the only header that depends on the query is the schedule screen's '↵ delay N days' hint, which domainsearch now pushes itself over the listen port. Header writes from action scripts still repaint via their binding's transform-header or the load event after a reload" ) \
      --delimiter=$'\t' --with-nth=1 \
      --bind "change:first+execute-silent($DTD_DOMAINSEARCH {q})" \
      --bind "resize:reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --multi \
      --bind "shift-down:toggle+down" --bind "shift-up:toggle+up" \
      $( : "ctrl-d/v/k end at clear-query (2026-10-21): reload-sync is followed by the load event, whose transform-header already repaints the arm script's '📅 schedule' line; the explicit one was a second spawn for the same text" ) \
      --bind "ctrl-o:execute($DTD_EDIT {2})+clear-screen+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "enter:transform($DTD_PICKENTER {2} {q})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "esc:transform($DTD_BACK {q})" \
      --bind "ctrl-c:transform($DTD_BACK {q})" \
      --bind "ctrl-q:transform($DTD_BACK {q})" \
      --bind "alt-enter:transform($DTD_DONE_ROUTER {2})+deselect-all+reload-sync($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-s:execute-silent($DTD_START {2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-d:select+execute-silent(DTD_PICK_MODE=days $DTD_BLOCKARM {+2})+deselect-all+reload-sync($DTD_RELOAD)+clear-query" \
      --bind "ctrl-x:select+execute-silent($DTD_DELETE {+2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      $( : "ctrl-p clears the query only after a SAVE (2026-10-22, 'cancelling out of the split leads to the task disappearing'): an unconditional clear-query wiped the search on a cancel too, so a task found by typing snapped back to row 64 of 70 and read as deleted. A cancel keeps query and cursor (fzf preserves both across reload)." ) \
      --bind "ctrl-p:execute(DTD_SPLIT_SEED=1 $DTD_EDIT {2})+clear-screen+deselect-all+reload($DTD_RELOAD)+transform([[ -e /tmp/dtd-$DTD_ID.editsaved ]] && echo clear-query)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-v:select+execute-silent($DTD_BLOCKARM {+2})+deselect-all+reload-sync($DTD_RELOAD)+clear-query" \
      --bind "alt-up:select+execute-silent($DTD_BUMP {+2})+deselect-all+reload-sync($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-g:execute($DTD_EDIT {2})+clear-screen+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-k:select+execute-silent($DTD_BLOCKARM {+2})+deselect-all+reload-sync($DTD_RELOAD)+clear-query" \
      --bind "ctrl-z:execute-silent($DTD_UNDO)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-r:execute-silent($DTD_REFRESH)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-l:execute-silent(python3 $DTD_OPEN_LINK {2} $DTD_CACHE_FILE $DTD_HDR)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-t:execute-silent($DTD_VIEWTOGGLE)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      2>>"$DTD_FZF_ERR")

  _fzf_rc=$?
  stty intr '^C' quit '^\' < /dev/tty 2>/dev/null   # the edit script leaves them undefined for fzf's lifetime
  task="$fzf_output"

  if [[ -z "$task" ]]; then
    # Exit forensics (2026-10-06, "dtd is randomly exiting"): 130 = esc/ctrl-c
    # abort (back-abort line just before it), 2 = fzf error, other = unexpected.
    print -- "$EPOCHREALTIME\tfzf-exit\trc=$_fzf_rc" >> "$DTD_TIMING" 2>/dev/null
    print -r -- "$EPOCHREALTIME"$'\t'"$DTD_ID"$'\t'session$'\t'end$'\t'$'\t'"rc=$_fzf_rc" >> "$DTD_EVENTS" 2>/dev/null
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
  clean=$(echo "$task" | sed -E 's/ *\([0-9]*\)//g; s/ *\[[0-9]*\]//g; s/ *\[[0-9.+]*\/m\]//g; s/  +/ /g; s/ *$//')

  # --- DONE MODE (existing behavior) ---
  # Track original name for list filtering (strip {N} too for matching)
  clean_for_filter=$(echo "$clean" | sed -E 's/ *\{[0-9]*\}//g; s/  +/ /g; s/ *$//')

  # Tasks that need args (e.g. cpap needs a score; xk20/xk22/xk26 need
  # minutes; i444 needs a count, 0 meaning "none needed today")
  clean_lower=$(echo "$clean" | tr '[:upper:]' '[:lower:]')
  # Strip a deferred/catch-up copy's stamped origin date ("xk26 7.21" ->
  # "xk26") before matching -- see the matching comment in $DTD_DONE. $_dated
  # remembers whether one was present so the timer-detected minutes route as
  # an explicit [N] points override instead of a bare number, same reasoning
  # as $DTD_DONE (dated copies fall through to the generic Todoist path, not
  # the habit's own variable-0n/1n+ handling).
  clean_base=$(echo "$clean_lower" | sed -E 's/ [0-9]{1,2}\.[0-9]{1,2}$//')
  _dated=""
  [[ "$clean_base" != "$clean_lower" ]] && _dated=1
  case "$clean_base" in
    cpap|ibx\ s897|ibx\ i9|ibx\ m5x2|xk20|xk22|xk26|i444|hiit|新闻)
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
        if [[ -n "$_dated" ]]; then
          clean="$clean [$timer_mins]"
        else
          clean="$clean $timer_mins"
        fi
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

# Signal the worker to stop BEFORE closing fd 3, so by the time it next sees
# EOF on the FIFO (immediately after the close below) $DTD_STOP already
# exists and it breaks instead of spinning forever (see $DTD_STOP's
# declaration comment).
touch "$DTD_STOP"
exec 3>&-

session_count=$(grep -c . "$DTD_SESSION" 2>/dev/null)
session_count=${session_count:-0}
if [[ $session_count -gt 0 ]]; then
  # Only the still-unprocessed backlog needs waiting on. The worker drains while
  # you read/scroll the list, so most of the session is usually already done by
  # the time you close — show the honest remaining count (pushed - processed),
  # NOT the whole session, and stay silent + exit fast when the queue already
  # drained (the loop still runs so the worker fully exits before cleanup).
  pushed=$(wc -l < "$DTD_PUSHED" 2>/dev/null || echo 0); pushed=${pushed// /}
  processed=$(wc -l < "$DTD_PROCESSED" 2>/dev/null || echo 0); processed=${processed// /}
  remaining=$(( pushed - processed ))
  (( remaining < 0 )) && remaining=0
  if (( remaining > 0 )); then
    echo ""
    echo "Waiting for $remaining task(s)..."
  fi
  while kill -0 $WORKER_PID 2>/dev/null; do
    sleep 0.2
    (( remaining > 0 )) && printf "."
  done
  (( remaining > 0 )) && echo ""

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
# $DTD_PUSHED.log deliberately NOT removed here (matches $DTD_SKIPPED's
# precedent) -- it's the only postmortem record of what a session pushed,
# and is what made the 2026-08-01 false-positive diagnosis possible.
stty susp '^Z' < /dev/tty 2>/dev/null   # give ^Z back to the shell (see DTD_ID)
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "$DTD_START" "$DTD_ENTER" "$DTD_DONE" "$DTD_DONE_HIDE" "$DTD_DONE_ROUTER" "$DTD_DEFER" "$DTD_DELETE" "$DTD_SPLIT" "$DTD_AGENT" "$DTD_SKIP" "$DTD_BUMP" "$DTD_UNDO" "$DTD_REFRESH" "$DTD_BGREFRESH" "$DTD_CACHE_FILE" "$DTD_REMOVED" "$DTD_REMOVED.ids" "$DTD_LIST" "$DTD_DONE_FILE" "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" "$DTD_STOP" "$DTD_SESSION" "$DTD_TIMER" "$DTD_FAILED" "$DTD_FAILED.tmp" "$DTD_PORT" "$DTD_HDRGEN" "$DTD_TALLY" "$DTD_VIEW" "$DTD_VIEWTOGGLE" "$DTD_BLOCKPICK" "$DTD_BLOCKPICK.mode" "$DTD_BLOCKPICK.custom" "$DTD_BLOCKPICK.days" "$DTD_BLOCKPICK.hint" "$DTD_BLOCKPICK.snoozed" "$DTD_BLOCKARM" "$DTD_BLOCKAPPLY" "$DTD_PICKENTER" "$DTD_BACK" "$DTD_EDIT" "$DTD_FORM" "$DTD_WARM_DONE"
rm -rf "$DTD_RESOLVED_DIR" 2>/dev/null
