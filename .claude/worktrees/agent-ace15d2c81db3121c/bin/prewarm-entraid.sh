#!/usr/bin/env bash
# prewarm-entraid.sh — keep Agency MCP EntraID tokens warm so that
# unattended cron jobs (mtg.py poll, sync_outlook_responses.py) don't have
# to trigger an interactive Microsoft login popup mid-day.
#
# Strategy: run the same tiny MCP calls that the cron jobs use, on a
# schedule that aligns with the start of the user's workday. If a popup
# is needed, it'll happen once when the user is at the keyboard rather
# than later when they're heads-down or AFK.
#
# Routes through ms-auth-wrap.sh so the popup (if any) is labeled.

set -u
PYBIN="${PYBIN:-/usr/bin/python3}"
LOG="$HOME/.prewarm-entraid.log"
WRAP="$HOME/bin/ms-auth-wrap.sh"

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { printf "[%s] %s\n" "$(ts)" "$*" >> "$LOG"; }

log "begin prewarm"

# Calendar (used by mtg.py poll). Tiny window — just enough to refresh the token.
"$WRAP" prewarm-calendar "$PYBIN" - <<'PY' >>"$LOG" 2>&1
import sys, datetime as dt, os
sys.path.insert(0, os.path.expanduser("~/i446-monorepo/tools/ibx"))
import agency_mcp
now = dt.datetime.utcnow()
end = now + dt.timedelta(minutes=15)
try:
    agency_mcp.call_tool("calendar", "ListCalendarView", {
        "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, timeout=60)
    print("calendar ok")
except Exception as e:
    print(f"calendar prewarm err: {e}")
PY

# Mail auth has historically timed out and opened repeated interactive sign-in
# windows. Keep it opt-in; real mail jobs will authenticate when they run.
if [ "${PREWARM_MAIL:-0}" = "1" ]; then
"$WRAP" prewarm-mail "$PYBIN" - <<'PY' >>"$LOG" 2>&1
import sys, os
sys.path.insert(0, os.path.expanduser("~/i446-monorepo/tools/ibx"))
import agency_mcp
try:
    agency_mcp.call_tool("mail", "ListMailFolders", {}, timeout=60)
    print("mail ok")
except Exception as e:
    # ListMailFolders may not exist; fall back to a no-op-ish search.
    try:
        agency_mcp.call_tool("mail", "SearchMessagesQueryParameters",
                             {"query": "subject:__prewarm__", "top": 1}, timeout=60)
        print("mail ok (search fallback)")
    except Exception as e2:
        print(f"mail prewarm err: {e} / {e2}")
PY
else
    log "mail prewarm skipped (set PREWARM_MAIL=1 to enable)"
fi

log "end prewarm"
