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

DID_FAST="$HOME/i446-monorepo/tools/did/did-fast.py"
UNDO_FAST="$HOME/i446-monorepo/tools/did/undo-fast.py"
DTD_RESOLVE="$HOME/i446-monorepo/tools/did/dtd_resolve.py"
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
_travel_tz=$(jq -r '.active_tz // empty' "$STATE_DIR/travel.json" 2>/dev/null)
[[ -n "$_travel_tz" ]] && export TZ="$_travel_tz"
unset _travel_tz

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
_dtd_refresh_done=""
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
  echo "Task cache is ${cache_age}s old (>${DTD_CACHE_MAX_AGE}s). Refreshing..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  _dtd_refresh_done=1
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
  echo "Cache built in a previous block. Refreshing for new-block rituals..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  _dtd_refresh_done=1
fi

if [[ -z "$_dtd_refresh_done" && $(jq '.today | length // 0' "$CACHE") -lt 5 ]]; then
  echo "Refreshing task cache..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  _dtd_refresh_done=1
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
if [[ -z "$_dtd_refresh_done" && $due_today -lt 30 ]]; then
  echo "⚠ Only $due_today tasks due today (expected ~80+). Refreshing..."
  python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1
  _dtd_refresh_done=1
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
      "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PUSHED.log" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" \
      "$DTD_SESSION" "$DTD_TIMER" "$DTD_STOP" "$DTD_FAILED" "$DTD_FAILED.tmp" \
      "/tmp/dtd-$DTD_ID.removed.ids" "/tmp/dtd-$DTD_ID.blockpick"
mkfifo "$DTD_FIFO"
echo "ready" > "$DTD_HDR"
touch "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" "$DTD_SESSION" "$DTD_TIMER" "$DTD_FAILED"

(
  # RECOVERY, not just detection (2026-08-03): "completed all -1n in a block,
  # points still short" is a genuine FIFO race. The done keybinding's FIFO
  # push (done.sh: `printf ... > "$FIFO"`) runs inside a short-lived, KILLABLE
  # fzf `execute` child; a rapid-fire alt-enter burst (sub-1s apart) tears that
  # child down mid-write, so the line NEVER reaches this loop -- always the
  # LAST item in the cluster. Meanwhile quick-close.py (forked detached, not
  # gated on this worker) still closes the Todoist card, so it vanishes looking
  # "done" while its stamp and -1₦ credit silently never happen. Every prior
  # fix (2026-07-30..08-02) only DETECTED the loss via a set-difference alert;
  # the work still had to be redone by hand. This loop now RECOVERS it.
  #
  # done.sh appends every requested completion to $DTD_PUSHED.log (atomic
  # O_APPEND, "ts<TAB>done<TAB>id<TAB>content") BEFORE the racy FIFO push, so
  # that log -- not the ephemeral FIFO -- is the durable source of truth for
  # "this work was requested." fd 4 below is a persistent read-write handle on
  # the FIFO that THIS subshell owns (in-process, never a killable child); we
  # open it <> so the open can't block waiting for a reader (we are the reader,
  # via `done < "$DTD_FIFO"`) and so shutdown stays driven by $DTD_STOP, never
  # by EOF. `read -t 2` polls every idle 2s; each tick reconciles the durable
  # log against $DTD_PROCESSED_IDS and re-injects any lost id back onto the
  # FIFO through fd 4, healing the loss through the exact same processing path
  # within ~2s. fd 3 (opened by the parent below) keeps ≥1 writer for the whole
  # session, so a failed read here is always this timeout, never real EOF.
  exec 4<>"$DTD_FIFO"
  typeset -A reinjected stale_alerted
  while true; do
    if ! IFS= read -r -t 2 line; then
      # Durable-log reconcile + recover: any id done.sh recorded in
      # $DTD_PUSHED.log (field 3) that this loop has not yet marked in
      # $DTD_PROCESSED_IDS was lost before reaching us (killable-child FIFO
      # race). Re-inject the FIRST such item onto the FIFO through fd 4 and let
      # it flow through the exact same processing path below -- do NOT merely
      # alert. ONE item per tick keeps the self-write far under the pipe buffer
      # (no capacity deadlock draining our own FIFO); the next idle tick picks
      # up the next one. Safe to replay: did-fast's Todoist close + Neon write
      # are idempotent, and the id is marked processed the instant it is
      # dequeued (below), so a recovered item is attempted exactly once.
      #
      # SAME-DAY ONLY (2026-08-10): replay is gated on the push carrying
      # TODAY's date. A session left open across midnight replayed the whole
      # previous evening's batch against the NEW day when processed-ids came
      # up short at the next reconcile -- did-fast is only idempotent within
      # a day, so the replay closed the new day's recurring cards (advancing
      # their due dates, hiding them from dtd) and re-credited points into
      # the new day's rows ("why isn't 1st hci appearing today?", plus a
      # phantom relax +40). A stale loss is alerted once, calmly, and left
      # for the human: yesterday's row can't be safely written by a replay
      # that only knows how to target today. Push timestamps carry a full
      # date for this gate (date-less legacy lines count as stale).
      rec_today=$(date +%Y-%m-%d)
      lost=$(awk -F'\t' -v idsfile="$DTD_PROCESSED_IDS" -v today="$rec_today" '
        BEGIN { while ((getline id < idsfile) > 0) seen[id] = 1 }
        !($3 in seen) {
          print (index($1, today "T") == 1 ? "live" : "stale") "\t" $3 "\t" $4
        }
      ' "$DTD_PUSHED.log" 2>/dev/null)
      recovered=""
      if [[ -n "$lost" ]]; then
        while IFS=$'\t' read -r rkind rid rcontent; do
          [[ -z "$rid" ]] && continue
          if [[ "$rkind" == "stale" ]]; then
            if [[ -z "${stale_alerted[$rid]}" ]]; then
              stale_alerted[$rid]=1
              echo "⚠ unprocessed completion from a previous day NOT replayed (would land on today) — $rcontent" >> "$DTD_LOG"
            fi
            continue
          fi
          [[ -n "${reinjected[$rid]}" ]] && continue
          printf '%s\t%s\n' "$rid" "$rcontent" >&4 || break
          reinjected[$rid]=1
          recovered="$rcontent"
          break
        done <<< "$lost"
      fi
      if [[ -n "$recovered" ]]; then
        # A recovered completion is a SUCCESS, not a failure: the FIFO race is
        # auto-healed right here and the points still land (the "✓ ..." line
        # that follows confirms it). So log it CALMLY and do NOT flash the pane
        # orange -- that alarm signals "a tool call failed and needs you", which
        # is exactly the wrong message for a loss the worker just fixed by
        # itself. It was mis-classifying self-heals as failures that made a
        # working recovery look like "the invariant fired again". A genuine
        # problem (did-fast erroring on the reprocessed item) still surfaces via
        # the "✗ ... (did-fast exit N)" branch below.
        msg="↻ auto-recovered a completion the FIFO dropped, reprocessing — $recovered"
        echo "$msg" > "$DTD_HDR"
        echo "$msg" >> "$DTD_LOG"
        # Drain the reinjected item through the loop before honoring shutdown,
        # so a completion recovered at the last second is never dropped.
        continue
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
    # Mark processed the INSTANT this line is dequeued from the FIFO --
    # before calling did-fast at all, not just "before the undo/jq pipeline"
    # (2026-08-02, second incident: task 6hC5fV8W3qJxwm3R "finish 1 g245
    # before m5x2" proved did-fast ran its ENTIRE pipeline successfully --
    # real Todoist close, real Neon ledger entry "T did 1g" at 10:01:19 --
    # yet nothing after it in this loop ran: no log line, no processed-id
    # record, even with the earlier fix (2026-08-02, first incident, task
    # 6gHVV7fjPwqfvq76 "i447") that moved this write to right after
    # capturing did-fast's exit code. That proves the interruption strikes
    # somewhere in or around the `result=$(...)` capture itself, EARLIER
    # than "after did-fast returns" -- so this write can no longer live
    # after the did-fast call at all. Once a line is dequeued, this loop
    # WILL attempt it exactly once; that attempt is what "processed" means
    # here, matching what the invariant check (below) is actually meant to
    # detect: a message the FIFO never delivered, not one that hit trouble
    # somewhere downstream. A silent post-did-fast gap (this exact class,
    # twice now, unreproducible via component testing both times) is a
    # separate, lower-severity failure mode: the real work still lands
    # (proven both times), only this shell's own confirmation log line is
    # missing. See test_dtd_processed_before_pipeline.py.
    echo "x" >> "$DTD_PROCESSED"
    echo "${task_id:-$task_clean}" >> "$DTD_PROCESSED_IDS"
    echo "⏳ $task_clean" > "$DTD_HDR"
    if [[ -n "$task_id" ]]; then
      result=$(python3 "$DID_FAST" --task-id "$task_id" "$task_clean" 2>>"$DTD_LOG.err")
    else
      result=$(python3 "$DID_FAST" "$task_clean" 2>>"$DTD_LOG.err")
    fi
    rc=$?
    if [[ $rc -ne 0 || -z "$result" ]]; then
      echo "✗ $task_clean (did-fast exit $rc, no output)" > "$DTD_HDR"
      echo "✗ $task_clean (did-fast exit $rc, no output)" >> "$DTD_LOG"
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
    else
      # Restore the optimistic id-hide (enter.sh/done.sh hid $task_id from
      # the list the instant Enter was pressed, before this call ran) — an
      # empty $ok means did-fast produced NO results entry with
      # todoist.closed:true (e.g. it fell through to needs_agent, which dtd's
      # synchronous worker can't service), so nothing was actually completed
      # and the task must not vanish from view. Regression 2026-08-18: "ibx
      # i9" hit exactly this path, its id stayed in $REMOVED.ids all day even
      # though Todoist still showed it open — the same class of bug already
      # fixed for delete/defer failures (which DO restore on failure), just
      # never applied to this ambiguous-completion branch.
      if [[ -n "$task_id" ]]; then
        removed_ids_path="/tmp/dtd-$DTD_ID.removed.ids"
        grep -v -x -F -- "$task_id" "$removed_ids_path" > "$removed_ids_path.tmp" 2>/dev/null
        mv "$removed_ids_path.tmp" "$removed_ids_path" 2>/dev/null
      fi
      echo "? $task_clean (restored to list)" > "$DTD_HDR"
      echo "? $task_clean (restored to list)" >> "$DTD_LOG"
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
# View mode (ctrl-t toggles): empty = default priority order, 'project' = grouped
# by domain label. Per-session; the list generator reads it as its 8th arg.
DTD_VIEW="/tmp/dtd-$DTD_ID.view"
DTD_VIEWTOGGLE="/tmp/dtd-$DTD_ID.view-toggle.sh"
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
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the two Toggl
# API calls above — leaked SGR motion sequences type themselves into fzf's
# query as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05,
# ported here from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
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
START="$DTD_START"
task="\$1"
# Picker mode: enter on a block row applies the snooze (2026-07-27)
if [[ "\$1" == BLOCK:* ]]; then
  "$DTD_BLOCKAPPLY" "\$1"
  exit 0
fi
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
"\$START" "\$task"
ENTEREOF
chmod +x "$DTD_ENTER"

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
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/  +/ /g; s/ *\$//')
clean_for_filter=\$(echo "\$clean" | sed -E 's/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
# Reinstated (2026-07-03): cpap asks for a 1-3 sleep-quality score on completion.
# The number is appended so did-fast writes it to cpap's 0n column. Needs a tty,
# so alt-enter is bound with execute (not execute-silent). Blank input just
# completes with no score.
clean_lower=\$(echo "\$clean_for_filter" | tr '[:upper:]' '[:lower:]')
# Deferred/catch-up copies of these habits get their origin date stamped into
# the name (defer-fast.py's _dated_copy_content, e.g. "xk26 7.21") so they
# don't silently re-claim the habit's own 0n/1n+ column on completion -- but
# the stamp also hid them from the case match below entirely, so completing a
# delayed xk20/xk22/xk26/... card silently used the card's static default
# points instead of asking (bug 2026-08-20: delayed number-input habits
# didn't prompt). Match on the name with any trailing "M.D" stamp stripped;
# \$_dated remembers whether one was present so the typed value routes as an
# explicit [N] points override below instead of a bare number -- dated copies
# fall through to the generic Todoist path (Step 5), not the habit's own
# variable-0n/1n+ handling, so a bare trailing number would silently be
# discarded as an unused time_value instead of becoming points.
clean_base=\$(echo "\$clean_lower" | sed -E 's/ [0-9]{1,2}\.[0-9]{1,2}\$//')
_dated=""
[[ "\$clean_base" != "\$clean_lower" ]] && _dated=1
# Tasks that ask for a value on completion (like cpap). The typed number is
# appended so did-fast writes it to the task's own 0n column: cpap = 1-3 sleep
# quality; xk20/xk22/xk26 = minutes with Theo/Ren/Rori; i444 = count, where an
# explicit 0 records "none needed today" (blank would default to 1 in did-fast).
# Needs a tty, so the router (below) sends these to execute, not
# execute-silent. Blank input just completes with no number.
_ip=""
case "\$clean_base" in
  cpap) _ip="CPAP quality (1-3)";;
  xk20) _ip="xk20 minutes (Theo)";;
  xk22) _ip="xk22 minutes (Ren)";;
  xk26) _ip="xk26 minutes (Rori)";;
  i444) _ip="i444 count (0 = none today)";;
  hiit) _ip="hiit minutes";;
  新闻) _ip="新闻 minutes";;
  "evening hcmc"|"night hcmc") _ip="night hcmc minutes";;
  ${DTD_VAR1N_PAT}) _ip="\$clean_base minutes (blank = base points)";;
esac
if [[ -n "\$_ip" && -r /dev/tty ]]; then
  # fzf leaves the alternate screen for execute(), but what the terminal shows
  # then is not guaranteed: cmux keeps the stale fzf frame on screen, so the
  # prompt was invisible and the user pressed ⌃⏎ blind (bug 2026-07-21). Clear
  # to home so the question is the only thing visible, and force sane tty modes
  # so Enter always terminates the read.
  stty sane < /dev/tty 2>/dev/null
  printf '\033[2J\033[H→ %s: ' "\$_ip" > /dev/tty
  read _iv < /dev/tty
  # Digits only: a blind ⌃⏎ (ESC CR) lands a literal ESC byte in the answer,
  # which would ride into the completion name ("CPAP ␛") and break did-fast's
  # task match. Any garbage → empty → completes with no score, as documented.
  _iv=\${_iv//[^0-9]/}
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
printf '%s\tdone\t%s\t%s\n' "\$(date +%Y-%m-%dT%H:%M:%S)" "\$1" "\$clean" >> "\$PUSHED.log"
# Only clear the running-timer cache when the task just completed is the one
# it's tracking — matched by id like the list generator's own running-highlight
# (falling back to name when id-less). Clearing it unconditionally blanked the
# footer for ~5-12s (until the next Toggl poll reconciled it) any time an
# UNRELATED task was completed while a different timer kept running (bug
# 2026-08-13: "timer goes blank for 5 seconds ... if I'm not changing the
# timer, it shouldn't flash").
_timer_id=\$(cut -f3 "\$TIMER" 2>/dev/null)
if [[ -n "\$1" && -n "\$_timer_id" ]]; then
  [[ "\$_timer_id" == "\$1" ]] && : > "\$TIMER"
elif [[ -z "\$1" ]]; then
  _timer_desc=\$(cut -f1 "\$TIMER" 2>/dev/null | tr '[:upper:]' '[:lower:]')
  [[ -n "\$_timer_desc" && "\$_timer_desc" == "\$clean_lower" ]] && : > "\$TIMER"
fi
echo "⏳ completing: \$clean_for_filter" > "\$HDR"
printf '%s\t%s\n' "\$1" "\$clean" > "\$FIFO"
# Reset stray mouse-tracking modes AND drain tty input queued during the
# prompt window — this was the ONLY interactive execute() script without the
# defer/edit/split cleanup, so scroll/motion bursts buffered while the value
# prompt was open dumped into fzf's query as literal ^[[<34;x;yM text on
# resume (bug 2026-07-27: "input pane in dtd is a mess").
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
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
REMOVED="$DTD_REMOVED"
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
_id="\$1"
_t=\$(jq -r --arg id "\$_id" '([.[] | select(type=="array")[] | select(.id == \$id) | .content] | first) // \$id' "$DTD_CACHE_FILE" 2>/dev/null | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//' | tr '[:upper:]' '[:lower:]')
# Strip a deferred/catch-up copy's stamped origin date ("xk26 7.21" -> "xk26")
# before matching -- see the matching comment in \$DTD_DONE above. Without
# this the router sent dated copies through execute-silent (no tty), so even
# after \$DTD_DONE learned to prompt for these, there was never a terminal to
# prompt on.
_t_base=\$(echo "\$_t" | sed -E 's/ [0-9]{1,2}\.[0-9]{1,2}\$//')
case "\$_t_base" in
  cpap|xk20|xk22|xk26|i444|hiit|新闻|"evening hcmc"|"night hcmc"|${DTD_VAR1N_PAT})
    printf 'execute(%s %s)' "$DTD_DONE" "\$_id" ;;
  *)
    printf 'execute-silent(%s %s; %s %s >/dev/null 2>&1)' "$DTD_DONE_HIDE" "\$_id" "$DTD_DONE" "\$_id" ;;
esac
ROUTEREOF
chmod +x "$DTD_DONE_ROUTER"

# --- Defer script used by fzf ctrl-d binding ---
DTD_DEFER="/tmp/dtd-$DTD_ID.defer.sh"
cat > "$DTD_DEFER" << DEFEREOF
#!/bin/zsh
DEFER_FAST="\$HOME/i446-monorepo/tools/did/defer-fast.py"
HDR="$DTD_HDR"
REMOVED="$DTD_REMOVED"
# Multi-select (2026-07-23): the ctrl-d binding passes {+2} — every
# shift-marked row's id, or just the cursor row's id when nothing is marked
# (the single-task path is the 1-element case of the same loop). Resolve
# every id up front, prompt ONCE for the defer target, then fan out to the
# same per-task detached worker as before.
typeset -a ids names
for _tid in "\$@"; do
  [[ -n "\$_tid" ]] || continue
  [[ "\$_tid" == BLOCK:* ]] && continue   # picker rows are not tasks
  task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$_tid")  # id (field 2) -> canonical content
  clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
  # fzf middle-truncates long rows; keep the prefix before the ellipsis
  # (regression 2026-06-06: "defer failed: call dad" with two call-dad tasks)
  [[ "\$clean" == *"…"* ]] && clean="\${clean%%…*}"
  ids+=("\$_tid")
  names+=("\$clean")
done
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
if [[ -n "\${DTD_DEFER_PROMPT:-}" && -r /dev/tty ]]; then
  # fzf leaves the alternate screen for execute(), but what the terminal
  # shows then is not guaranteed: cmux keeps the stale fzf frame on screen,
  # so the prompt is invisible and arrow keys land in a blind `read` that
  # doesn't navigate anything (bug 2026-07-21, fixed for done.sh's
  # value-prompt but never ported here — "dtd is locked on the text input
  # screen, and I can't navigate items in the fzf", 2026-09-03). Clear to
  # home so the question is the only thing visible, and force sane tty
  # modes so Enter always terminates the read.
  stty sane < /dev/tty 2>/dev/null
  printf "\033[2J\033[H\nDefer '%s' by N days / YYYY-MM-DD (blank or 0 = next occurrence if recurring, no copy created)> " "\$label" > /dev/tty
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
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty

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
# list generator swaps the task rows for block rows (⏰ 申 shen 14:00–16:00,
# searchable by pinyin/汉字). Enter (or ⌃⏎) on a block row applies the snooze
# via $DTD_BLOCKAPPLY and restores the normal list. Nothing ever draws outside
# fzf, so there is nothing cmux can fail to paint.
DTD_BLOCKARM="/tmp/dtd-$DTD_ID.blockarm.sh"
cat > "$DTD_BLOCKARM" << ARMEOF
#!/bin/zsh
HDR="$DTD_HDR"
BLOCKPICK="$DTD_BLOCKPICK"
SNOOZE="$STATE_DIR/dtd-block-snooze.json"
# ctrl-v on a picker row (already armed) = close the picker
if [[ "\$1" == BLOCK:* ]]; then
  rm -f "\$BLOCKPICK"
  echo "↩ block picker closed" > "\$HDR"
  exit 0
fi
# Nothing to pick after 亥 has begun (20:00) unless un-delay is on offer
n=\$(python3 - "\$SNOOZE" "\$@" <<'PYCOUNT'
import datetime, json, sys
now = datetime.datetime.now()
n = sum(1 for h in (4, 6, 8, 10, 12, 14, 16, 18, 20) if h > now.hour)
try:
    data = json.load(open(sys.argv[1]))
    sn = data.get('snoozes') or {}
    if data.get('date') == now.date().isoformat() and any(str(t) in sn for t in sys.argv[2:]):
        n += 1
except Exception:
    pass
print(n)
PYCOUNT
)
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the python call
# above — leaked SGR motion sequences type themselves into fzf's query as
# literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported here
# from done.sh/defer.sh/edit.sh/split.sh). Runs before the early exit below
# too, not just the end of the script.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
if [[ "\$n" == "0" ]]; then
  echo "no later block today — nothing to delay to" > "\$HDR"
  exit 0
fi
task=\$(python3 "$DTD_RESOLVE" "$DTD_CACHE_FILE" "\$1")  # id (field 2) -> canonical content
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
[[ "\$clean" == *"…"* ]] && clean="\${clean%%…*}"
lbl="\$clean"
[[ \$# -gt 1 ]] && lbl="\$clean +\$((\$# - 1)) more"
printf '%s\n' "\$@" > "\$BLOCKPICK"
echo "⏰ delay \$lbl until… (enter picks · ctrl-v or ↩-row cancels)" > "\$HDR"
ARMEOF
chmod +x "$DTD_BLOCKARM"

# Applies the picked block (enter/⌃⏎ on a BLOCK:* row) to the armed ids.
cat > "$DTD_BLOCKAPPLY" << APPLYEOF
#!/bin/zsh
HDR="$DTD_HDR"
BLOCKPICK="$DTD_BLOCKPICK"
SNOOZE="$STATE_DIR/dtd-block-snooze.json"
glyph="\${1#BLOCK:}"
ids=(\$(cat "\$BLOCKPICK" 2>/dev/null))
rm -f "\$BLOCKPICK"
if [[ "\$glyph" == "cancel" || \${#ids[@]} -eq 0 ]]; then
  echo "block delay cancelled" > "\$HDR"
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
PYWRITE
)
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the python call
# above — leaked SGR motion sequences type themselves into fzf's query as
# literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported here
# from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
echo "\${msg:-✗ block delay failed}" > "\$HDR"
APPLYEOF
chmod +x "$DTD_BLOCKAPPLY"

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
clean=\$(echo "\$task" | sed -E 's/ *\\([0-9]*\\)//g; s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g; s/ *\\{[0-9]*\\}//g; s/  +/ /g; s/ *\$//')
query="\$task"
if [[ "\$clean" == *"…"* ]]; then
  clean="\${clean%%…*}"
  query="\$clean"
fi
# fzf leaves the alternate screen for execute(), but what the terminal shows
# then is not guaranteed: cmux keeps the stale fzf frame on screen, so the
# prompt is invisible and arrow keys land in a blind `read` that doesn't
# navigate anything (bug 2026-07-21, fixed for done.sh's value-prompt but
# never ported here — "dtd is locked on the text input screen, and I can't
# navigate items in the fzf", 2026-09-03). Clear to home so the question is
# the only thing visible, and force sane tty modes so Enter always
# terminates the read.
stty sane < /dev/tty 2>/dev/null
printf "\033[2J\033[H\nEdit: %s\n(text=rename · @code=domain · N=points)> " "\$clean" > /dev/tty
read edits < /dev/tty
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the prompt above
# — leaked SGR motion sequences type themselves into fzf's query as literal
# ^[[<0;16;15M text on resume otherwise (bug 2026-07-05; drain half of the
# fix was 2026-07-27 on done.sh/split.sh but never ported here). Runs
# unconditionally, BEFORE the cancel check below, so a cancelled edit
# (blank input) still cleans up instead of exiting past it.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
if [[ -z "\${edits// /}" ]]; then
  echo "edit cancelled" > "\$HDR"
  exit 0
fi
out=\$(python3 "\$EDIT_FAST" --id "\$1" "\$edits" "$DTD_CACHE_FILE" 2>/dev/null)
echo "\${out:-✗ edit failed}" > "\$HDR"

EDITEOF
chmod +x "$DTD_EDIT"

# --- List generation script (reloadable by fzf) ---
DTD_LIST="/tmp/dtd-$DTD_ID.list.sh"
cat > "$DTD_LIST" << 'LISTEOF'
#!/bin/zsh
# Args: $1=cache_file $2=done_file_path $3=removed_file $4=today $5=columns $6=skipped_file $7=timer_file
# Live width: fzf exports FZF_COLUMNS to every bound/reload command — prefer
# it over the launch-time $5 so rows re-truncate to the CURRENT window width
# (bug 2026-07-23: the width was baked into the reload command string, so an
# expanded window kept launch-width "…" truncation forever). $5 stays as the
# cold-start fallback for the first pipe into fzf and scripted callers.
[[ -n "$FZF_COLUMNS" ]] && argv[5]="$FZF_COLUMNS"
python3 -c "
import json, sys, re, time

cache_file, done_file, removed_file, today, cols = sys.argv[1:6]
skipped_file = sys.argv[6] if len(sys.argv) > 6 else ''
timer_file = sys.argv[7] if len(sys.argv) > 7 else ''
cols = int(cols)

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

# Neon color palette (label → ANSI 256-color)
COLORS = {
    'g245': '\033[38;2;0;230;118m',    'epcn': '\033[38;2;0;191;165m',
    's897': '\033[38;2;27;94;32m',     'hcmc2': '\033[38;2;255;214;0m',
    'xk87': '\033[38;2;253;108;29m',   'xk88': '\033[38;2;230;81;0m',
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
zeroneon = [t for t in _sec('0neon', _tomorrow) + _sec('夜neon', _tomorrow)
            if t.get('id') not in _deferred_ids
            and t['due'] <= today]
# Block-snooze (ctrl-v, 2026-07-24): ids hidden until their chosen 地支 block
# starts. File is {date, snoozes: {id: start_hour}}; a stale date voids it.
# Uses the CURRENT clock (not the session-start today arg) so an idle-open
# dtd un-hides the task on the first reload after the block hour arrives
# (the watcher refreshes at every block boundary).
_snoozed = set()
_sn_all = {}
try:
    with open(_os.path.expanduser('~/.local/state/jm/dtd-block-snooze.json')) as _sf:
        _sn = json.load(_sf)
    _nw = _dt.datetime.now()
    # Forward-only: a stored date equal-or-newer than today is still valid,
    # matching the writer's fix (DTD_BLOCKAPPLY, above) — a plain equality
    # check here would silently un-hide every snoozed task the moment the
    # writer started preserving them across a backward date move instead of
    # wiping them, since a backward move means this reader's freshly-
    # computed 'today' no longer equals the (correctly preserved) newer
    # stored date.
    if _nw.date().isoformat() <= _sn.get('date', ''):
        _sn_all = {str(k) for k in (_sn.get('snoozes') or {})}
        _snoozed = {str(k) for k, v in (_sn.get('snoozes') or {}).items()
                    if _nw.hour < int(v)}
except Exception:
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
    GREY = '\x1b[38;2;139;150;163m'
    _R = '\x1b[0m'
    for g, py, h in (('卯','mao',4),('辰','chen',6),('巳','si',8),('午','wu',10),
                     ('未','wei',12),('申','shen',14),('酉','you',16),
                     ('戌','xu',18),('亥','hai',20)):
        if h > _nw2.hour:
            print(f'{ORANGE}⏰ {g}  {py:<5} {h:02d}:00–{h+2:02d}:00{_R}\tBLOCK:{g}')
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

if view == 'project':
    unique.sort(key=lambda t: (domain_of(t), prank(t.get('priority'))))
elif view == 'time':
    unique.sort(key=lambda t: (time_of(t), prank(t.get('priority'))))

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
    # Project/time views group by color alone — no project-name prefix (the user
    # knows the domain from the color; the names just add clutter).
    dom_tag = ''
    if running_id:
        is_running = str(t.get('id', '')) == running_id
    else:
        is_running = bool(running_clean and clean == running_clean)
    if is_running:
        elapsed = max(0, int((time.time() - running_started) // 60)) if running_started else 0
        prefix = f'▶ {elapsed}m · {dom_tag}'
    else:
        prefix = repeat + dom_tag
    # Build the full visible row, then right-justify its trailing estimates so
    # they align in a column regardless of the prefix. ANSI is added after.
    body = rjust_est(prefix + line, cols)
    if link_url and link_text in body:
        # OSC 8 wrap AFTER layout so the escape bytes never skew the padding.
        # ST spelled with chr(92): this python lives in a zsh double-quoted
        # string where a backslash pair would collapse and break the escape.
        _st = '\x1b' + chr(92)
        body = body.replace(
            link_text,
            '\x1b]8;;' + link_url + _st + link_text + '\x1b]8;;' + _st, 1)
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
" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"
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

# --- Skip script used by fzf ctrl-k binding ---
DTD_SKIP="/tmp/dtd-$DTD_ID.skip.sh"
cat > "$DTD_SKIP" << SKIPEOF
#!/bin/zsh
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
  # Reset any mouse-tracking mode a child enabled, and drain any bytes already
  # queued in the tty buffer from scroll/click events during the two curl
  # calls above — leaked SGR motion sequences type themselves into fzf's
  # query as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05,
  # ported here from done.sh/defer.sh/edit.sh/split.sh). Runs before the
  # success/failure branch below so both outcomes get the cleanup.
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
  while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
  if [[ "\$code" == 2* ]]; then
    # Hide by id (\$REMOVED.ids), NOT by name (\$REMOVED): delete already
    # resolves the exact task via \$tid (collision-proof, 2026-07-12), but
    # hiding by its annotation-stripped name suppressed EVERY task sharing
    # that name — two identically-named open tasks, delete one, both vanish
    # from the list until the next full cache refresh (2026-08-17). Same
    # mechanism enter.sh/done.sh/defer already use for this exact reason.
    echo "\$tid" >> "\$REMOVED.ids"
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
    # Daily habit (0neon/夜neon) deleted = N/A for today: write an explicit 0
    # to its 0n Neon column (blank = not done yet; 0 = didn't apply/happen —
    # Janus hides explicit-0 habits from its strip) and record the name in the
    # day's NA file so validate-daily-habits --fix doesn't resurrect the card
    # the same day. The recurring card returns on the next day's validation.
    # (ctrl-z undo recreates the card but leaves the 0; re-completing the
    # habit overwrites it.)
    python3 - "\${fullname:-\$clean}" "\$pre" << 'NAEOF' &
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

# Extract points and (N) duration from task. Points come in TWO mutually
# exclusive styles (did-fast.py: "SQUARE brackets only. {N} is the 0g bonus"):
# [N] = domain points (routed via Todoist label), {N} = 0g bonus (routed to
# 0分 col Q regardless of label). Splitting a {N} task must preserve the
# curly style throughout — treating it as [N] silently loses the total
# (regression 2026-07-28: a {50} task's total came back "?", forcing
# remaining_pts to 0 and wiping the {N} marker off both halves entirely).
bracket='['
close=']'
total=$(echo "$task" | grep -oE '\[[0-9]+\]' | head -1 | tr -d '[]')
if [[ -z "$total" ]]; then
  total=$(echo "$task" | grep -oE '\{[0-9]+\}' | head -1 | tr -d '{}')
  [[ -n "$total" ]] && { bracket='{'; close='}'; }
fi
duration=$(echo "$task" | grep -oE '\([0-9]+\)' | head -1 | tr -d '()')
[[ -z "$total" ]] && total="?"

# Dialog 1: points
pts_today=$(/usr/bin/osascript -e 'display dialog "Split: points done today? (total: '"$bracket$total$close"')" default answer "" buttons {"Cancel","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)
[[ -z "$pts_today" || ! "$pts_today" =~ ^[0-9]+$ ]] && { echo "cancelled" > "$HDR"; exit 0; }

# Dialog 2: what you did
done_desc=$(/usr/bin/osascript -e 'display dialog "What did you do?" default answer "" buttons {"Skip","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)

# Dialog 3: what remains
remaining_desc=$(/usr/bin/osascript -e 'display dialog "What remains?" default answer "" buttons {"Skip","OK"} default button "OK"' -e 'text returned of result' 2>/dev/null)

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
open_b, close_b = sys.argv[10], sys.argv[11]  # '[' ']' or '{' '}' -- preserve the original's point style

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
posthoc_content = f'{today_label} ({duration or pts_today}) {open_b}{pts_today}{close_b}'
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
new_content = f'{remaining_desc or clean} ({duration}) {open_b}{remaining_pts}{close_b}' if remaining_pts > 0 else f'{remaining_desc or clean}'
api('POST', f'/tasks/{tid}', {
    'content': new_content,
    'due_date': tomorrow,
})

# 3. Log points to 0分 via did-fast. {N} (0g bonus) routes to column Q from
#    the curly marker alone, regardless of label -- did-fast.py: 'SQUARE
#    brackets only. {N} is the 0g bonus'; giving it a domain label too would
#    double-credit (bug 2026-07-27). [N] (domain points) needs the label for
#    column mapping, so only look one up for the square-bracket case.
#    --points-only skips Todoist matching: without it did-fast re-finds the
#    just-renamed remainder task and closes it.
import subprocess
label_arg = ''
if open_b == '[':
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
                '--points-only', f'{safe_name} {open_b}{pts_today}{close_b} {label_arg}'],
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
msg = f'✂ +{pts_today} today / {open_b}{remaining_pts}{close_b} deferred to {tomorrow}'
with open(hdr_file, 'w') as f: f.write(msg)
" "$clean" "$pts_today" "${total:-?}" "${done_desc:-}" "${remaining_desc:-}" "${duration:-}" "$HDR" "$REMOVED" "$task_id" "$bracket" "$close"

# Flush tty input buffered while the osascript GUI dialogs held focus. With the
# terminal idle behind the dialogs, two-finger touchpad scroll emits ESC[A/ESC[B
# arrow bursts that queue in the tty input buffer; left unread, fzf dumps the
# whole burst into its query as literal ^[[A^[[B text on return (bug 2026-07-14).
# Draining here consumes them before fzf reads. Also reset mouse modes, matching
# the defer/points/edit action scripts.
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
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
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the cmux/osascript
# spawn above — leaked SGR motion sequences type themselves into fzf's query
# as literal ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported
# here from done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
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
  # Reset/drain here too: this is the longest blocking window (up to 5s of
  # sleep-polling above), so it's the path most likely to trigger the leak —
  # see the fuller comment below for the bug this guards against.
  printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
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
while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty
summary=\$(echo "\$result" | jq -r '.summary // .error // "undo failed"' 2>/dev/null)
if [[ \$(echo "\$result" | jq -r '.ok // empty' 2>/dev/null) == "true" ]]; then
  echo "↩ \$summary" > "\$HDR"
else
  echo "? \${summary:-undo failed}" > "\$HDR"
fi
UNDOEOF
chmod +x "$DTD_UNDO"

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
python3 "$DID_FAST" --refresh-cache
cp "$CACHE" "$DTD_CACHE_FILE"
echo "🔄 refreshed" > "$DTD_HDR"
# Reset any mouse-tracking mode a child enabled, and drain any bytes already
# queued in the tty buffer from scroll/click events during the refresh above
# — leaked SGR motion sequences type themselves into fzf's query as literal
# ^[[<0;16;15M text on resume otherwise (bug 2026-07-05, ported here from
# done.sh/defer.sh/edit.sh/split.sh).
printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty 2>/dev/null || true
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
export DTD_KEYS="enter: start | ⌥⏎: done | ctrl-s: timer | ctrl-d: defer | ctrl-p: split | ctrl-v/k: ⏰block | ctrl-g: edit | ctrl-a: agent | ctrl-x: del | ctrl-z: undo | ctrl-r: refresh | ctrl-t: view | ⇧↑↓: mark multi"

# Status-line generator (the header, below the prompt): "<N left>   <worker
# status>   <keys>". fzf exports $FZF_MATCH_COUNT to this child; $DTD_KEYS is
# exported above; the worker-status file path is baked in here. Used by the
# load/result binds and after every action so worker confirmations persist.
cat > "$DTD_HDRGEN" <<HDRGENEOF
#!/bin/zsh
# Stale-code check first, same as janus.py's render_header: if it wins, it
# replaces the WHOLE header line (in red) so a fix that shipped after this
# session launched can't be missed or mistaken for the normal status line.
live_mtime=\$(stat -f %m "$DTD_SELF" 2>/dev/null || echo 0)
if (( live_mtime > $DTD_SRC_MTIME + 1 )); then
  printf '\033[1;91m⚠ RESTART DTD — code updated on disk\033[0m'
  exit 0
fi
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
# `3>&-` closes this job's inherited copy of the FIFO write-end (fd 3, opened
# by `exec 3>"$DTD_FIFO"` above) BEFORE exec'ing python3 — otherwise the
# ticker process holds fd 3 open for its whole life, same bug class as the
# watcher below (regression 2026-07-11): the main loop's `exec 3>&-` on exit
# no longer drops the FIFO's writer count to zero, the worker's `read` never
# sees EOF, and dtd hangs silently on exit forever (2026-07-15: `lsof` showed
# a live dtd-ticker.py holding fd 3w on the FIFO).
rm -f "$DTD_PORT"
python3 "$DTD_TICKER" "$DTD_PORT" "$DTD_TIMER" 3>&- &>/dev/null &
TICKER_PID=$!

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
    watch_reload="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' '$watch_today' '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK'"
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
  DTD_LIST_CMD="$DTD_LIST '$DTD_CACHE_FILE' '$DTD_DONE_FILE' '$DTD_REMOVED' \"\$(date +%Y-%m-%d)\" '${COLUMNS:-80}' '$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK'"
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
  fzf_output=$(eval "$DTD_LIST_CMD" | fzf --prompt="> " --layout=reverse-list --no-sort --ansi \
      --info=inline-right \
      --input-border=horizontal \
      --listen --header-first \
      --header="$DTD_KEYS" \
      --bind "start:execute-silent(echo \$FZF_PORT > $DTD_PORT)" \
      --bind "load:transform-header($DTD_HDRGEN)" \
      --bind "result:transform-header($DTD_HDRGEN)" \
      --delimiter=$'\t' --with-nth=1 \
      --bind "change:first" \
      --bind "resize:reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --multi \
      --bind "shift-down:toggle+down" --bind "shift-up:toggle+up" \
      --bind "enter:execute-silent($DTD_ENTER {2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "alt-enter:transform($DTD_DONE_ROUTER {2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-s:execute-silent($DTD_START {2})+deselect-all+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-d:execute($DTD_DEFER {+2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-x:execute-silent($DTD_DELETE {+2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-p:execute-silent($DTD_SPLIT {2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-v:execute-silent($DTD_BLOCKARM {+2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-g:execute($DTD_EDIT {2})+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-a:execute-silent($DTD_AGENT {2})+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-k:execute-silent($DTD_BLOCKARM {+2})+deselect-all+reload($DTD_RELOAD)+clear-query+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-z:execute-silent($DTD_UNDO)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
      --bind "ctrl-r:execute-silent($DTD_REFRESH)+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)" \
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
rm -f "$DTD_FIFO" "$DTD_HDR" "$DTD_LOG" "$DTD_LOG.err" "$DTD_START" "$DTD_ENTER" "$DTD_DONE" "$DTD_DONE_HIDE" "$DTD_DONE_ROUTER" "$DTD_DEFER" "$DTD_DELETE" "$DTD_SPLIT" "$DTD_AGENT" "$DTD_SKIP" "$DTD_UNDO" "$DTD_REFRESH" "$DTD_CACHE_FILE" "$DTD_REMOVED" "$DTD_REMOVED.ids" "$DTD_LIST" "$DTD_DONE_FILE" "$DTD_JOURNAL" "$DTD_PUSHED" "$DTD_PROCESSED" "$DTD_PROCESSED_IDS" "$DTD_STOP" "$DTD_SESSION" "$DTD_TIMER" "$DTD_FAILED" "$DTD_FAILED.tmp" "$DTD_PORT" "$DTD_HDRGEN" "$DTD_TALLY" "$DTD_VIEW" "$DTD_VIEWTOGGLE" "$DTD_BLOCKPICK" "$DTD_BLOCKARM" "$DTD_BLOCKAPPLY" "$DTD_EDIT"
