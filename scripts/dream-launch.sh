#!/bin/bash
# dream-launch.sh — Reusable Dream overnight launcher for ix
# Creates a dated run dir, templates the prompt, runs claude detached.
# Usage: dream-launch.sh [--dry-run] [--budget N] [--floor N]
set -euo pipefail

# --- Ensure full PATH for MCP servers (npx, uvx, python3) ---
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# --- Config ---
DREAM_RUNS="$HOME/vault/i447/i446/dream-runs"
PROMPT_BASE="$HOME/vault/i447/i446/dream-prompt-base.md"
CLAUDE="/opt/homebrew/bin/claude"
DEFAULT_BUDGET=180
DEFAULT_FLOOR=90

# --- Parse args ---
DRY_RUN=""
BUDGET="$DEFAULT_BUDGET"
FLOOR="$DEFAULT_FLOOR"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN="-dry-run"; shift ;;
    --budget)  BUDGET="$2"; shift 2 ;;
    --floor)   FLOOR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# --- Date strings ---
DATE_ISO=$(date '+%Y-%m-%d')
DATE_DOT=$(date '+%Y.%m.%d')

# --- Auto-increment version ---
# Find latest run for today, extract version number, increment
LATEST=$(ls -d "$DREAM_RUNS/$DATE_DOT"* 2>/dev/null | sort -V | tail -1 || true)
if [[ -n "$LATEST" && "$LATEST" =~ -v([0-9]+)$ ]]; then
  PREV_VER="${BASH_REMATCH[1]}"
  VER=$((PREV_VER + 1))
elif [[ -n "$LATEST" ]]; then
  VER=2
else
  VER=1
fi

# Global version: count all runs ever (for v8, v9, etc. lineage)
GLOBAL_VER=$(ls -d "$DREAM_RUNS"/*-v* 2>/dev/null | sed 's/.*-v//' | sort -n | tail -1 || echo 0)
GLOBAL_VER=$((GLOBAL_VER + 1))

VERSION="v${GLOBAL_VER}"
VERSION_SHORT="v${GLOBAL_VER}"
RUN_DIR="$DREAM_RUNS/${DATE_DOT}${DRY_RUN}-${VERSION}"

# --- Prevent double-launch (acquire lock BEFORE creating run dir) ---
LOCK="/tmp/dream-launch.lock"
MAX_LOCK_AGE_SEC=14400  # 4 hours; any dream run beyond this is stuck
if [[ -f "$LOCK" ]]; then
  LOCK_PID=$(cat "$LOCK")
  if kill -0 "$LOCK_PID" 2>/dev/null; then
    # Check lock age — kill stale runs that exceeded the budget
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK") ))
    if [[ $LOCK_AGE -gt $MAX_LOCK_AGE_SEC ]]; then
      echo "[$(date)] Dream PID $LOCK_PID stale (${LOCK_AGE}s > ${MAX_LOCK_AGE_SEC}s), killing" >&2
      kill "$LOCK_PID" 2>/dev/null
      # Also kill any child claude processes
      pkill -P "$LOCK_PID" 2>/dev/null || true
      sleep 2
      kill -9 "$LOCK_PID" 2>/dev/null || true
      rm -f "$LOCK"
    else
      echo "[$(date)] Dream already running (PID $LOCK_PID, age ${LOCK_AGE}s), skipping" >&2
      exit 0
    fi
  else
    # Stale lock from dead process — remove it
    rm -f "$LOCK"
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# --- Create run dir ---
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/staged" "$RUN_DIR/drafts" "$RUN_DIR/approvals" "$RUN_DIR/branches" "$RUN_DIR/tmp"

# --- Backfill date tracking ---
# Each night, Dream also scans one historical day's conversation logs.
# State file tracks the backfill pointer; advances one day back each run.
BACKFILL_STATE="$DREAM_RUNS/.backfill-pointer"
BACKFILL_STOP="2026-03-01"  # stop when we reach March 1
if [[ -f "$BACKFILL_STATE" ]]; then
  BACKFILL_DATE=$(cat "$BACKFILL_STATE")
else
  # Start from yesterday
  BACKFILL_DATE=$(date -v-1d '+%Y-%m-%d')
fi
# Check if backfill is complete
if [[ "$BACKFILL_DATE" < "$BACKFILL_STOP" ]]; then
  BACKFILL_DATE=""
  BACKFILL_MSG="Backfill complete (reached $BACKFILL_STOP)."
else
  BACKFILL_MSG="Backfill date: $BACKFILL_DATE. Also scan AI transcripts from this date for loose threads."
  # Advance pointer one day back for next run
  NEXT_BACKFILL=$(date -j -f '%Y-%m-%d' "$BACKFILL_DATE" -v-1d '+%Y-%m-%d')
  echo "$NEXT_BACKFILL" > "$BACKFILL_STATE"
fi

# --- Template the prompt ---
sed \
  -e "s|{{VERSION}}|$VERSION|g" \
  -e "s|{{VERSION_SHORT}}|$VERSION_SHORT|g" \
  -e "s|{{RUN_DIR}}|$RUN_DIR|g" \
  -e "s|{{DATE}}|$DATE_ISO|g" \
  -e "s|{{DATE_DOT}}|$DATE_DOT|g" \
  -e "s|{{COMPUTE_FLOOR}}|$FLOOR|g" \
  -e "s|{{BUDGET_USD}}|$BUDGET|g" \
  -e "s|{{BACKFILL_DATE}}|$BACKFILL_DATE|g" \
  -e "s|{{BACKFILL_MSG}}|$BACKFILL_MSG|g" \
  "$PROMPT_BASE" > "$RUN_DIR/PROMPT.md"

LOG="$RUN_DIR/logs/agent-run.log"

# --- Run dream-intake before claude ---
INTAKE_SCRIPT="$HOME/i446-monorepo/scripts/dream-intake.py"
INTAKE_OUT="$RUN_DIR/dream-intake.json"
if [[ -f "$INTAKE_SCRIPT" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running dream-intake.py..." >> "$LOG"
  python3 "$INTAKE_SCRIPT" --output "$INTAKE_OUT" --run-dir "$RUN_DIR" >> "$LOG" 2>&1 || true
  # Symlink to latest
  ln -sf "$INTAKE_OUT" "$DREAM_RUNS/dream-intake-latest.json"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Intake complete: $INTAKE_OUT" >> "$LOG"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dream $VERSION launcher starting" >> "$LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] run_dir=$RUN_DIR budget=$BUDGET floor=$FLOOR" >> "$LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] claude=$CLAUDE ($(${CLAUDE} --version 2>/dev/null || echo 'unknown'))" >> "$LOG"

# --- Run claude with activity watchdog ---
# Watchdog checks every 3 min: if no new file writes in RUN_DIR for 15 min,
# kill claude (stalled). Launcher then checks what pass completed and retries
# the remaining pass with a fresh session.
cd "$HOME/vault"
STALL_THRESHOLD=900  # 15 min with no new file or log output = stalled
WATCHDOG_INTERVAL=180  # check every 3 min
DEADLINE=$(($(date +%s) + 10800))  # hard stop at 3h regardless

_run_claude() {
  local prompt_file="$1"
  local label="$2"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting claude ($label)" >> "$LOG"

  caffeinate -s "$CLAUDE" \
    --print \
    --model opus \
    --fallback-model sonnet \
    --max-budget-usd "$BUDGET" \
    --dangerously-skip-permissions \
    --add-dir "$HOME/vault" \
    --add-dir "$HOME/i446-monorepo" \
    < "$prompt_file" \
    >> "$LOG" 2>&1 &
  CLAUDE_PID=$!

  # Watchdog loop
  while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    sleep "$WATCHDOG_INTERVAL"

    # Hard deadline check
    if [[ $(date +%s) -ge $DEADLINE ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] DEADLINE reached, killing claude ($label)" >> "$LOG"
      kill "$CLAUDE_PID" 2>/dev/null; sleep 2; kill -9 "$CLAUDE_PID" 2>/dev/null
      return 124
    fi

    # Activity check: any file in RUN_DIR modified in last STALL_THRESHOLD seconds?
    NEWEST=$(find "$RUN_DIR" -type f -not -name "*.log" -newer "$RUN_DIR/PROMPT.md" -print -quit 2>/dev/null)
    if [[ -z "$NEWEST" ]]; then
      # No files newer than PROMPT.md at all — check log growth instead
      NEWEST="$LOG"
    fi
    FILE_AGE=$(( $(date +%s) - $(stat -f %m "$NEWEST" 2>/dev/null || echo 0) ))
    if [[ $FILE_AGE -gt $STALL_THRESHOLD ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] STALL detected ($label): no activity for ${FILE_AGE}s, killing" >> "$LOG"
      kill "$CLAUDE_PID" 2>/dev/null; sleep 2; kill -9 "$CLAUDE_PID" 2>/dev/null
      return 1
    fi
  done
  wait "$CLAUDE_PID"
  return $?
}

# --- Main run ---
_run_claude "$RUN_DIR/PROMPT.md" "full-run"
EXIT_CODE=$?

# --- If stalled, check what's missing and retry the remaining pass ---
if [[ $EXIT_CODE -ne 0 && $(date +%s) -lt $DEADLINE ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Checking for incomplete passes..." >> "$LOG"

  HAS_DRAFTS=$(ls "$RUN_DIR/drafts/"*.md 2>/dev/null | wc -l)
  HAS_BRIEF=$(test -f "$RUN_DIR/morning-brief.md" && echo 1 || echo 0)

  if [[ $HAS_DRAFTS -gt 0 && $HAS_BRIEF -eq 0 ]]; then
    # Pass 1+2 done, Pass 3 missing — retry just Pass 3
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pass 1+2 complete ($HAS_DRAFTS drafts), retrying Pass 3" >> "$LOG"
    PASS3_PROMPT="$RUN_DIR/tmp/pass3-retry.md"
    cat > "$PASS3_PROMPT" << PASS3EOF
Complete Pass 3 of Dream $VERSION. Run dir: $RUN_DIR
Today: $(date '+%Y-%m-%d')

Read $RUN_DIR/PROMPT.md for the Pass 3 instructions, then:
1. Read all files in $RUN_DIR/drafts/ and $RUN_DIR/approvals/
2. Read $RUN_DIR/loose-threads.md
3. Run the 5-filter and rubric on every card, drop C or worse
4. Write morning-brief.md with ranked cards and multiple-choice responses
5. Write cards.json, manifest.json, staged/changelog.md
6. Write READY marker
PASS3EOF
    _run_claude "$PASS3_PROMPT" "pass3-retry"
    EXIT_CODE=$?
  fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dream $VERSION finished (exit=$EXIT_CODE)" >> "$LOG"

# --- Write READY marker ---
date '+%Y-%m-%d %H:%M:%S' > "$RUN_DIR/READY"

# --- Generate morning context ---
if [[ -f "$HOME/i446-monorepo/scripts/dream-morning-context.py" ]]; then
  python3 "$HOME/i446-monorepo/scripts/dream-morning-context.py" "$RUN_DIR" >> "$LOG" 2>&1 || true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dream $VERSION complete. Run dir: $RUN_DIR" >> "$LOG"
