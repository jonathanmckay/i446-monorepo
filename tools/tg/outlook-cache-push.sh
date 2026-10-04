#!/bin/bash
# outlook-cache-push.sh — refresh the Outlook calendar cache on Straylight
# (the only Mac with Agency/work auth) and push it to Ix, where Janus runs
# with JANUS_OUTLOOK_CACHE_ONLY=1. Straylight cron, every 5 min. 2026-10-04.
set -u
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
