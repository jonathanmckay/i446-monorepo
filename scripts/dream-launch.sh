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
DEFAULT_BUDGET=120
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

# --- Prevent double-launch ---
LOCK="/tmp/dream-launch.lock"
if [[ -f "$LOCK" ]]; then
  LOCK_PID=$(cat "$LOCK")
  if kill -0 "$LOCK_PID" 2>/dev/null; then
    echo "[$(date)] Dream already running (PID $LOCK_PID), skipping" >&2
    exit 0
  fi
fi

# --- Create run dir ---
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/staged" "$RUN_DIR/drafts" "$RUN_DIR/approvals" "$RUN_DIR/branches" "$RUN_DIR/tmp"

# --- Template the prompt ---
sed \
  -e "s|{{VERSION}}|$VERSION|g" \
  -e "s|{{VERSION_SHORT}}|$VERSION_SHORT|g" \
  -e "s|{{RUN_DIR}}|$RUN_DIR|g" \
  -e "s|{{DATE}}|$DATE_ISO|g" \
  -e "s|{{DATE_DOT}}|$DATE_DOT|g" \
  -e "s|{{COMPUTE_FLOOR}}|$FLOOR|g" \
  -e "s|{{BUDGET_USD}}|$BUDGET|g" \
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

# --- Write PID lock ---
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# --- Run claude ---
cd "$HOME/vault"

caffeinate -s "$CLAUDE" \
  --print \
  --model opus \
  --fallback-model sonnet \
  --max-budget-usd "$BUDGET" \
  --dangerously-skip-permissions \
  --add-dir "$HOME/vault" \
  --add-dir "$HOME/i446-monorepo" \
  < "$RUN_DIR/PROMPT.md" \
  >> "$LOG" 2>&1

EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dream $VERSION claude exited with code $EXIT_CODE" >> "$LOG"

# --- Write READY marker ---
date '+%Y-%m-%d %H:%M:%S' > "$RUN_DIR/READY"

# --- Generate morning context ---
if [[ -f "$HOME/i446-monorepo/scripts/dream-morning-context.py" ]]; then
  python3 "$HOME/i446-monorepo/scripts/dream-morning-context.py" "$RUN_DIR" >> "$LOG" 2>&1 || true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dream $VERSION complete. Run dir: $RUN_DIR" >> "$LOG"
