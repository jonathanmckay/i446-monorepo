#!/usr/bin/env python3
"""One-time/periodic backfill for the dashboard's weekly/monthly Time/Entries
charts.

The live v9 /me/time_entries endpoint (used for the daily view and the ~90-day
tail refresh here) is hard-capped to ~90 days back from today. The Reports v3
endpoint can reach back up to 366 days per request, but for this account
(Toggl descriptions are highly varied, not just per-project, so there are many
distinct groups) a single 365-day request needs 20+ paginated pages at ~16s
each — confirmed live, too slow for a dashboard request. Chunking into ~60-day
windows keeps each request in the sub-2s range (also confirmed live), so this
script sweeps the desired backfill window in small chunks and writes results
to a persistent cache (.toggl-daily-cache.json) that the dashboard reads
directly for anything older than its live tail.

Usage:
  python3 backfill_toggl_cache.py [--days N] [--force]

--days N   how far back to backfill (default 730 = ~2 years)
--force    refetch windows even if every date in them is already cached
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
CACHE_PATH = HERE / ".toggl-daily-cache.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
WORKSPACE_ID = int(os.environ.get("TOGGL_WORKSPACE_ID", "2092616"))
WINDOW_DAYS = 60  # keeps each Reports v3 request fast (~1-2s, confirmed live)

# Mirrors dashboard.py's PROJECT_ID_TO_CODE — kept as a plain data copy here
# rather than importing dashboard.py, since that module pulls in Flask/GA4 and
# a host-enforcement before_request hook not relevant to this standalone script.
PROJECT_ID_TO_CODE = {
    108537163: "g245", 153212856: "h335", 154064792: "hcb", 108359995: "hcm",
    109932707: "hcmc", 109216950: "hci", 158134455: "i447", 209635316: "i9",
    108359987: "m5x2", 112310620: "m828", 152057340: "qz12", 109719141: "s897",
    163129781: "xk87", 108433670: "xk88", 108547409: "家", 108358083: "睡觉",
    150114323: "epcn", 120844877: "infra", 108357451: "n156", 174372636: "q5n7",
    185952786: "i444", 160959920: "h5c7", 45122191: "f8", 108360024: "hcbp",
    108359992: "hcmc2",
}


def _load_env():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # Fallback: the toggl_server MCP config in ~/.claude.json (same source
    # toggl_cli.py uses) — lets the daily cron run without a secret in the
    # crontab or a .env file.
    if not os.environ.get("TOGGL_API_KEY"):
        try:
            with open(os.path.expanduser("~/.claude.json")) as f:
                key = (json.load(f).get("mcpServers", {})
                       .get("toggl_server", {}).get("env", {})
                       .get("TOGGL_API_KEY", ""))
            if key:
                os.environ["TOGGL_API_KEY"] = key
        except Exception:
            pass


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache))


def fetch_window(creds: str, start: date, end: date) -> dict:
    """Paginate one (start, end) window via Reports v3.
    Returns {date_str: {"minutes": {code: n}, "entries": {code: n}}}.
    """
    url = f"https://api.track.toggl.com/reports/api/v3/workspace/{WORKSPACE_ID}/search/time_entries"
    out = defaultdict(lambda: {"minutes": defaultdict(int), "entries": defaultdict(int)})
    payload = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    for page_num in range(10):  # generous for a 60-day window
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Authorization", f"Basic {creds}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Toggl sends lowercase header names (x-next-id, not X-Next-Id) —
                # confirmed live; normalize before lookup or pagination silently
                # truncates to page 1.
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                rows = json.loads(resp.read())
        except Exception as e:
            print(f"    page {page_num + 1}: ERROR {e!r}", file=sys.stderr, flush=True)
            break
        for row in rows:
            proj_id = row.get("project_id")
            code = PROJECT_ID_TO_CODE.get(proj_id, "no project") if proj_id else "no project"
            for te in row.get("time_entries", []):
                secs = te.get("seconds", 0)
                if not secs or secs <= 0:
                    continue
                start_str = te.get("start", "")
                if not start_str:
                    continue
                try:
                    dt = datetime.fromisoformat(start_str).astimezone(LOCAL_TZ)
                except (ValueError, TypeError):
                    continue
                d = dt.date()
                if d < start or d > end:
                    continue
                out[d.isoformat()]["minutes"][code] += secs // 60
                out[d.isoformat()]["entries"][code] += 1
        quota_remaining = hdrs.get("x-toggl-quota-remaining")
        if quota_remaining is not None and int(quota_remaining) < 20:
            print(f"    quota low ({quota_remaining} remaining), stopping this window early", flush=True)
            break
        next_id = hdrs.get("x-next-id")
        next_row = hdrs.get("x-next-row-number")
        if not next_id or not next_row or len(rows) < 50:
            break
        payload = {**payload, "first_id": int(next_id), "first_row_number": int(next_row)}
        time.sleep(0.3)
    return {k: {"minutes": dict(v["minutes"]), "entries": dict(v["entries"])} for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730, help="how far back to backfill (default: 730 = ~2 years)")
    ap.add_argument("--force", action="store_true", help="refetch windows even if fully cached already")
    args = ap.parse_args()

    _load_env()
    api_key = os.environ.get("TOGGL_API_KEY", "")
    if not api_key:
        print("ERROR: TOGGL_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)
    creds = base64.b64encode(f"{api_key}:api_token".encode()).decode()

    cache = load_cache()
    today = date.today()
    floor = today - timedelta(days=args.days)

    # Sweep newest-to-oldest so the most useful/recent history is saved first
    # if the run gets interrupted partway through.
    windows = []
    window_end = today
    while window_end >= floor:
        window_start = max(floor, window_end - timedelta(days=WINDOW_DAYS - 1))
        windows.append((window_start, window_end))
        window_end = window_start - timedelta(days=1)

    print(f"backfilling {len(windows)} window(s), {floor} .. {today}", flush=True)
    for i, (w_start, w_end) in enumerate(windows):
        expected_days = {(w_start + timedelta(days=n)).isoformat() for n in range((w_end - w_start).days + 1)}
        if not args.force and expected_days <= cache.keys():
            print(f"[{i + 1}/{len(windows)}] {w_start}..{w_end}: fully cached, skip", flush=True)
            continue
        t0 = time.time()
        result = fetch_window(creds, w_start, w_end)
        cache.update(result)
        save_cache(cache)
        print(f"[{i + 1}/{len(windows)}] {w_start}..{w_end}: {len(result)} days fetched, "
              f"{round(time.time() - t0, 1)}s", flush=True)
        time.sleep(0.5)

    print(f"done — cache now covers {len(cache)} days total.", flush=True)


if __name__ == "__main__":
    main()
