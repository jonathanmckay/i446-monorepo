#!/usr/bin/env python3
"""Refresh the occupancy dashboard data straight from AppFolio (no Claude/MCP).

- occupancy_summary (as-of today) → append a real snapshot to occupancy-history.json
  (idempotent per date). Run daily and the 90-day history densifies on its own.
- unit_vacancy_detail → vacancy.json (current non-occupied units + move dates).

Usage:
  python3 fetch.py                 # today's snapshot + vacancy
  python3 fetch.py --backfill 90   # also pull weekly as-of snapshots back N days

Then: python3 build.py
Creds: .env (APPFOLIO_CLIENT_ID / _SECRET / _BASE_URL).
"""
import json, os, sys, datetime as dt
from pathlib import Path
import urllib.request, urllib.parse, base64

DIR = Path(__file__).parent
DATA = DIR / "data"; DATA.mkdir(exist_ok=True)

def env():
    e = {}
    for ln in (DIR / ".env").read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1); e[k.strip()] = v.strip()
    return e
E = env()
BASE = E.get("APPFOLIO_BASE_URL", "https://mckay.appfolio.com").rstrip("/")
if "@" in BASE:  # creds-in-url form → strip, use basic auth header instead
    BASE = "https://" + BASE.split("@", 1)[1]
AUTH = base64.b64encode(f"{E['APPFOLIO_CLIENT_ID']}:{E['APPFOLIO_CLIENT_SECRET']}".encode()).decode()

def report(name, params, ver="v1"):
    """GET an AppFolio report, following next_page pagination. Returns all rows."""
    url = f"{BASE}/api/{ver}/reports/{name}.json?" + urllib.parse.urlencode(
        {**params, "paginate_results": "true"})
    rows = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {AUTH}"})
        d = json.load(urllib.request.urlopen(req, timeout=120))
        rows += d.get("results", [])
        url = d.get("next_page_url") or d.get("next_page")
    return rows

def n(x):
    try: return int(float(str(x).replace(",", "")))
    except Exception: return 0

def totals(rows):
    t = dict(units=0, occ=0, vr=0, vu=0, nr=0, nu=0)
    for r in rows:
        t["units"] += n(r["number_of_units"]); t["occ"] += n(r["occupied"])
        t["vr"] += n(r["vacant_rented"]); t["vu"] += n(r["vacant_unrented"])
        t["nr"] += n(r["notice_rented"]); t["nu"] += n(r["notice_unrented"])
    t["occ_stable"] = t["occ"] - t["nr"] - t["nu"]
    return t

def snapshot(date):
    t = totals(report("occupancy_summary", {"as_of_to": date.isoformat()}))
    t["date"] = date.isoformat()
    return t

def upsert_history(snaps):
    path = DATA / "occupancy-history.json"
    hist = json.loads(path.read_text()) if path.exists() else []
    by = {h["date"]: h for h in hist}
    for s in snaps: by[s["date"]] = s
    hist = sorted(by.values(), key=lambda x: x["date"])
    path.write_text(json.dumps(hist, indent=2))
    return len(hist)

def main():
    today = dt.date.today()
    snaps = [snapshot(today)]
    if "--backfill" in sys.argv:
        days = int(sys.argv[sys.argv.index("--backfill") + 1])
        d = today - dt.timedelta(days=7)
        while d > today - dt.timedelta(days=days + 1):
            snaps.append(snapshot(d)); d -= dt.timedelta(days=14)
    total = upsert_history(snaps)
    # vacancy detail
    keep = ["property_name", "unit", "unit_status", "last_move_in", "last_move_out",
            "next_move_in", "available_on", "days_vacant", "schd_rent",
            "advertised_rent", "rent_ready"]
    vac = report("unit_vacancy_detail", {})
    (DATA / "vacancy.json").write_text(
        json.dumps([{k: r.get(k) for k in keep} for r in vac], indent=2))
    print(f"history now {total} snapshots; today occ%={snaps[0]['occ']/snaps[0]['units']*100:.1f}; "
          f"vacancy units {len(vac)}")

if __name__ == "__main__":
    main()
