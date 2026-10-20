#!/bin/bash
# dream-alert.sh — Emit a high-priority alert when Dream fails before producing
# a morning brief. Two sinks: append-to-alerts-jsonl (consumed by ai-dashboard)
# and a macOS notification with Basso sound (audible when the laptop is open).
#
# Usage: dream-alert.sh <reason> <detail>
#   reason  — short slug (e.g. "keychain_locked", "claude_auth_401")
#   detail  — human-readable detail
#
# Side effects:
#   - appends JSON line to ~/vault/z_ibx/alerts.jsonl (durable)
#   - writes ~/vault/i447/i446/dream-runs/<latest>/FAILED marker (so the
#     personal dashboard can show a red banner instead of a stale READY)
#   - fires osascript notification (best-effort; silent in headless cron)
#
# Pattern mirrors git-autopush-healthcheck.sh. Intentionally does NOT call
# Pushover/ntfy — JM has not configured those yet. If/when configured, add
# the curl call here behind an env-var gate.

set -u
REASON="${1:?reason required}"
DETAIL="${2:?detail required}"
HOST="$(hostname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ALERTS="$HOME/vault/z_ibx/alerts.jsonl"
mkdir -p "$(dirname "$ALERTS")"

# 1. Durable JSONL sink — picked up by personal dashboard alert rail.
# Use python3 for JSON encoding so quotes/backslashes/newlines in DETAIL can't
# corrupt the JSONL file (a malformed line breaks the dashboard's line-by-line
# parser for every alert after it).
python3 -c '
import json, sys
line = json.dumps({
    "ts":       sys.argv[1],
    "host":     sys.argv[2],
    "tool":     "dream-launch",
    "severity": "critical",
    "reason":   sys.argv[3],
    "detail":   sys.argv[4],
}, ensure_ascii=False)
print(line)
' "$NOW" "$HOST" "$REASON" "$DETAIL" >> "$ALERTS"

# 2. FAILED marker in the current run dir, if we can find one.
DREAM_RUNS="$HOME/vault/i447/i446/dream-runs"
LATEST_RUN=$(ls -dt "$DREAM_RUNS"/2*-v* 2>/dev/null | head -1 || true)
if [[ -n "$LATEST_RUN" && -d "$LATEST_RUN" ]]; then
  cat > "$LATEST_RUN/FAILED" <<EOF
$NOW
reason: $REASON
detail: $DETAIL
EOF
fi

# 3. Best-effort macOS notification (silent when no GUI session).
# Escape backslashes and double-quotes so a detail like `use "security find..."`
# doesn't syntax-error the osascript literal (which would swallow the alert).
_esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
_DETAIL_ESC="$(_esc "$DETAIL")"
_REASON_ESC="$(_esc "$REASON")"
osascript -e "display notification \"$_DETAIL_ESC\" with title \"Dream failed: $_REASON_ESC\" sound name \"Basso\"" 2>/dev/null || true

# 4. Stub the morning brief so JM at least sees the failure in his usual surface
#    instead of opening yesterday's brief by reflex.
if [[ -n "$LATEST_RUN" && -d "$LATEST_RUN" && ! -f "$LATEST_RUN/morning-brief.md" ]]; then
  cat > "$LATEST_RUN/morning-brief.md" <<EOF
# Dream RUN FAILED — $NOW

Reason: \`$REASON\`
Detail: $DETAIL

No cards were produced. See \`logs/agent-run.log\` in this run dir.

Likely manual fix: log in via macOS GUI to unlock the login keychain,
or check that \`security find-generic-password -s 'Claude Code-credentials' -w\`
returns a value. If repeatedly broken, see drafts/self-improvement-v51.md
for the keychain-pass-file remediation path.
EOF
fi

exit 0
