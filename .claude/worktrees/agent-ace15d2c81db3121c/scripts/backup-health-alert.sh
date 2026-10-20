#!/bin/bash
# backup-health-alert — Claude UserPromptSubmit hook (Straylight side).
#
# Reads the verdict backup-health.py writes on ix (synced here via the vault).
# Emits hook context JSON ONLY when something is wrong, so healthy days cost
# nothing. Two alarm modes:
#   1. ok=false            → the checks found a failure; surface the list.
#   2. verdict file stale  → the checker itself (or Syncthing) died; silence
#                            must alarm too, else a dead checker looks healthy.
# Rate-limited to once per 4h per condition so it nags without spamming.

HEALTH="${BACKUP_HEALTH_JSON:-$HOME/vault/i447/backup-health.json}"
STAMP="${BACKUP_ALERT_STAMP:-/tmp/claude-backup-alert-stamp}"
NOW=$(date +%s)

# rate limit: skip if we alerted in the last 4h
if [ -f "$STAMP" ]; then
    LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
    [ $((NOW - LAST)) -lt 14400 ] && exit 0
fi

emit() {
    echo "$NOW" > "$STAMP"
    ~/i446-monorepo/scripts/term-color.sh orange >/dev/null 2>&1
    python3 -c "import json,sys; print(json.dumps({'context': sys.argv[1]}))" "$1"
}

if [ ! -f "$HEALTH" ]; then
    # never alarmed before the system's first run; only alarm if the vault
    # itself is present (i.e. this isn't a fresh machine)
    [ -d "$HOME/vault/i447" ] && emit "⚠️ BACKUP ALERT: backup-health.json missing — backup-health checker has never run or vault sync is broken. Investigate (see g245/CLAUDE.md ledger + i447 backup notes) and tell the user."
    exit 0
fi

python3 - "$HEALTH" <<'EOF' | { read -r line && [ -n "$line" ] && emit "$line"; true; }
import json, sys, time, datetime
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("⚠️ BACKUP ALERT: backup-health.json unreadable — checker on ix is writing garbage. Investigate and tell the user.")
    raise SystemExit
ts = d.get("ts", "")
try:
    age_h = (time.time() - datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").timestamp()) / 3600
except ValueError:
    age_h = 999
if not d.get("ok", False):
    print("⚠️ BACKUP ALERT (from ix backup-health): " + "; ".join(d.get("failures", ["unknown"]))
          + " — investigate and tell the user.")
elif age_h > 48:
    print(f"⚠️ BACKUP ALERT: backup-health verdict is {age_h:.0f}h stale — the daily checker on ix "
          "stopped running or Syncthing stopped syncing. Investigate and tell the user.")
EOF
exit 0
