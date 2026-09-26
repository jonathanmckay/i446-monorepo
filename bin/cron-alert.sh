#!/bin/bash
# cron-alert.sh — durable failure signal for headless cron wrappers on Ix.
# Appends one JSON line to ~/vault/z_ibx/alerts.jsonl (the sink the personal
# dashboard alert rail and dream-alert.sh already use). Silent failures are how
# /0r ran dead for a month (Claude logged out on Ix since 2026-08-28, nobody
# read ~/.cache/0r/*.log) — every claude/cron wrapper should call this on a
# non-zero exit.
#
# Usage: cron-alert.sh <tool> <reason> <detail>
set -u
TOOL="${1:?tool required}"; REASON="${2:?reason required}"; DETAIL="${3:?detail required}"
ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
mkdir -p "$(dirname "$ALERTS")"
python3 -c '
import json, sys
print(json.dumps({"ts": sys.argv[1], "host": sys.argv[2], "tool": sys.argv[3],
                  "severity": "critical", "reason": sys.argv[4],
                  "detail": sys.argv[5]}, ensure_ascii=False))
' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname -s)" "$TOOL" "$REASON" "$DETAIL" >> "$ALERTS"
