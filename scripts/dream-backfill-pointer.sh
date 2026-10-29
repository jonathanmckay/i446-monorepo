#!/bin/bash
# dream-backfill-pointer.sh — read and advance Dream's backfill pointer.
# Usage: dream-backfill-pointer.sh STATE_FILE [STOP_DATE]
# Prints "START OLDEST" (the inclusive range of days this run should backfill,
# START newest) or nothing when backfill has reached STOP_DATE. Advances the
# pointer BACKFILL_DAYS_PER_RUN (default 1) days back.
#
# Regression (2026-10-27): the advance used `date -j -f FMT "$D" -v-1d +FMT`.
# BSD date stops option parsing at the first operand, so -v-1d was ignored and
# the default format was written ("Mon Oct 26 03:00:00 PDT 2026"). The next run
# saw a corrupted pointer and reset to yesterday: from v83 to v170 the backfill
# never got further back than yesterday. Options must precede operands, and
# nothing is written unless it is a YYYY-MM-DD date.
set -euo pipefail

STATE="${1:?usage: dream-backfill-pointer.sh STATE_FILE [STOP_DATE]}"
STOP="${2:-2026-03-01}"
DAYS="${BACKFILL_DAYS_PER_RUN:-1}"
RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2; }

if ! [[ "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
  log "WARNING: bad BACKFILL_DAYS_PER_RUN '$DAYS', using 1"
  DAYS=1
fi

shift_days() {  # shift_days YYYY-MM-DD -N  -> YYYY-MM-DD
  date -j -v"${2}"d -f '%Y-%m-%d' "$1" '+%Y-%m-%d'
}

mkdir -p "$(dirname "$STATE")"
START=""
[[ -f "$STATE" ]] && START=$(cat "$STATE")
if ! [[ "$START" =~ $RE ]]; then
  [[ -n "$START" ]] && log "WARNING: corrupted backfill pointer '$START', resetting"
  START=$(date -v-1d '+%Y-%m-%d')
fi

if [[ "$START" < "$STOP" ]]; then
  echo "$START" > "$STATE"   # keep a valid value; nothing to do
  exit 0
fi

OLDEST=$(shift_days "$START" "-$((DAYS - 1))")
[[ "$OLDEST" < "$STOP" ]] && OLDEST="$STOP"
NEXT=$(shift_days "$OLDEST" -1)
if ! [[ "$NEXT" =~ $RE && "$OLDEST" =~ $RE ]]; then
  log "ERROR: computed backfill pointer '$NEXT' is not YYYY-MM-DD; leaving pointer unchanged"
  echo "$START $START"
  exit 0
fi
echo "$NEXT" > "$STATE"
echo "$START $OLDEST"
