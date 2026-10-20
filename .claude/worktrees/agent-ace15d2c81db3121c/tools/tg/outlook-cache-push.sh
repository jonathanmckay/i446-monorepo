#!/bin/bash
# outlook-cache-push.sh — refresh the Outlook calendar cache on Straylight
# (the only Mac with Agency/work auth) and push it to Ix, where Janus runs
# with JANUS_OUTLOOK_CACHE_ONLY=1. Manual one-shot only; the unattended
# feed is outlook-cache-daemon.py (launchd com.mckay.outlook-cache-daemon).
set -u
# INTERACTIVE ONLY (2026-10-04). Unattended runs (this was a */5 cron job)
# start a fresh Agency calendar server every time -- Agency servers die with
# the process that started them -- and each fresh server re-runs Microsoft
# interactive sign-in, so a lapsed token meant a new sign-in prompt every 5
# minutes. Run it by hand from a Straylight terminal; it refuses without a
# terminal unless OUTLOOK_PUSH_UNATTENDED=1 is set deliberately.
if [[ ! -t 0 && "${OUTLOOK_PUSH_UNATTENDED:-0}" != "1" ]]; then
  echo "outlook-cache-push: refusing to run without a terminal (would trigger Microsoft sign-in prompts)" >&2
  exit 0
fi
cd "$HOME/i446-monorepo/tools/tg" || exit 1
python3 - <<'PY'
import datetime as dt, sys
sys.path.insert(0, ".")
import outlook_client as oc
tz = oc._tz()
d0 = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
for off in (0, 1):  # today + tomorrow (Janus's late-evening lookahead)
    s = d0 + dt.timedelta(days=off)
    try:
        oc.list_events(s, s + dt.timedelta(days=1), force=True)
    except Exception as e:
        print(f"{s:%F}: {e}", file=sys.stderr)
PY
rsync -q -t "$HOME"/.cache/janus/outlook-*.json ix:.cache/janus/ 2>/dev/null \
  || { ssh ix 'mkdir -p ~/.cache/janus' && rsync -q -t "$HOME"/.cache/janus/outlook-*.json ix:.cache/janus/; }
