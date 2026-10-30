#!/bin/bash
# Nightly Neon rule-engine parallel run (Neon off Excel, step 3) — Ix cron 04:05,
# after neon-reconcile's 03:50 snapshot pass. Sibling of run_0t.sh.
#
#   run   : tools/neon-ledger/neon.py diff <yesterday> --write
#           → ~/vault/g245/neon-ledger/rules-diff/<date>.json + rules-diff-latest.json
#   strict: tools/neon-ledger/neon.py strict <yesterday> --write → strict-streak.json (/0t reads it)
#   alert : non-zero exit -> ~/vault/z_ibx/alerts.jsonl via bin/cron-alert.sh
#   read  : the Saturday /1-i446 vault-health card (check_neon_rules) reports
#           mismatched columns and job liveness, so JM never has to remember.
export PATH="/opt/homebrew/bin:/usr/bin:/bin"
MONO="$HOME/i446-monorepo"
LOGDIR="$HOME/.cache/neon-rules"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(date +%Y-%m-%d).log"
LOCK="$LOGDIR/run.lock"
cd "$HOME" || exit 1
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
fail() { log "FAILED: $2"; "$HOME/bin/cron-alert.sh" neon_rules_diff "$1" "$2 (see $LOG)"; }

if ! shlock -f "$LOCK" -p $$; then
    log "another run holds $LOCK; exiting"; exit 0
fi
trap 'rm -f "$LOCK"' EXIT

YDAY="$(date -v-1d +%Y-%m-%d)"
log "=== neon rules diff $YDAY ==="
if /usr/bin/python3 "$MONO/tools/neon-ledger/neon.py" diff "$YDAY" --write >> "$LOG" 2>&1; then
    log "ok"
else
    fail "diff_failed" "neon.py diff $YDAY exited non-zero"
    exit 1
fi
# Strict cutover gate (ledger alone vs Excel) → g245/neon-ledger/strict-streak.json,
# reported every morning by /0t until JM holds a 5-day streak.
if /usr/bin/python3 "$MONO/tools/neon-ledger/neon.py" strict "$YDAY" --write >> "$LOG" 2>&1; then
    log "strict ok"
else
    fail "strict_failed" "neon.py strict $YDAY exited non-zero"
    exit 1
fi
