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
import json, os, sys, time, datetime as dt
from pathlib import Path
import urllib.request, urllib.parse, base64

THROTTLE = 1.2   # seconds between AppFolio calls (be a good citizen)

DIR = Path(__file__).parent
DATA = DIR / "data"; DATA.mkdir(exist_ok=True)

def env():
    e = {}
    for ln in (DIR / ".env").read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1); e[k.strip()] = v.strip()
    return e
E = env()
VHOST = E.get("APPFOLIO_VHOST", "mckay")
BASE = f"https://{VHOST}.appfolio.com"
AUTH = base64.b64encode(
    f"{E['APPFOLIO_USERNAME']}:{E['APPFOLIO_PASSWORD']}".encode()).decode()

def report(name, body):
    """AppFolio reports are v2 POST with a JSON body; follow next_page if present."""
    url = f"{BASE}/api/v2/reports/{name}.json"
    data = json.dumps({"unit_visibility": "active", **body}).encode()
    hdr = {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}
    rows = []
    while url:
        if data is not None:   # first call = POST with body
            req = urllib.request.Request(url, data=data, method="POST", headers=hdr)
        else:                  # subsequent next_page links are GETs
            req = urllib.request.Request(url, headers={"Authorization": f"Basic {AUTH}"})
        for attempt in range(6):        # retry on 429 with exponential backoff
            try:
                d = json.load(urllib.request.urlopen(req, timeout=120))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    time.sleep(2 ** attempt * 3)   # 3,6,12,24,48s
                    continue
                raise
        rows += d.get("results", [])
        url = d.get("next_page_url") or d.get("next_page"); data = None
        time.sleep(THROTTLE)
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

def pdate(s):
    try: return dt.date.fromisoformat(s[:10]) if s else None
    except Exception: return None

# ── Per-unit archive + event log (the tenant-tickler engine) ─────────────────
# AppFolio exposes no NTV-received date, so we detect changes ourselves: snapshot
# each non-occupied unit daily, diff vs the prior snapshot, and stamp the change
# date as `known` (when we learned) while `effective` stays the move date.
ARCH = DATA / "unit-archive"

def archive_units(vac, today):
    ARCH.mkdir(exist_ok=True)
    rec = {}
    for r in vac:
        uid = r.get("unit_id")
        if uid is None: continue
        rec[str(uid)] = {"status": r.get("unit_status"), "mo": r.get("last_move_out"),
                         "mi": r.get("next_move_in"), "prop": r.get("property_name"),
                         "unit": r.get("unit")}
    (ARCH / f"{today.isoformat()}.json").write_text(json.dumps(rec))
    return rec

def prev_archive(today):
    if not ARCH.exists(): return None
    prior = sorted(p.stem for p in ARCH.glob("*.json") if p.stem < today.isoformat())
    return json.loads((ARCH / f"{prior[-1]}.json").read_text()) if prior else None

def update_events(vac, le, today):
    cur = archive_units(vac, today)
    prev = prev_archive(today)
    ev_path = DATA / "events.json"
    events = json.loads(ev_path.read_text()) if ev_path.exists() else []
    seen = {(e["unit_id"], e["kind"], e.get("effective")) for e in events}
    def add(kind, uid, eff, prop, unit, tenant=None, known=None):
        key = (uid, kind, eff)
        if key in seen: return
        seen.add(key)
        events.append({"known": known or today.isoformat(), "effective": eff, "kind": kind,
                       "unit_id": uid, "prop": prop, "unit": unit, "tenant": tenant,
                       "baseline": known is None and prev is None})
    noticed = lambda s: (s or "").startswith("Notice")
    rented = lambda s: (s or "").endswith("Rented")
    vacant = lambda s: (s or "").startswith("Vacant")
    if prev is None:                       # first run: seed baseline + real sign dates
        cur_ids = set(cur)
        for uid, r in cur.items():
            s = r["status"]
            if noticed(s): add("ntv", uid, r["mo"], r["prop"], r["unit"])
            if rented(s):  add("leased", uid, r["mi"], r["prop"], r["unit"])
            if vacant(s) and not rented(s): add("vacant", uid, r["mo"], r["prop"], r["unit"])
        for r in le:                       # recent lease signings on now-occupied units
            uid = str(r.get("unit_id")); lsd = pdate(r.get("lease_sign_date"))
            if lsd and (today - lsd).days <= 150 and uid not in cur_ids:
                mi = pdate(r.get("move_in"))
                if mi and (lsd - mi).days > 45:        # signed long after move-in → renewal
                    add("renewal", uid, r.get("renewal_start_date"), r.get("property_name"),
                        r.get("unit"), r.get("tenant_name"), known=lsd.isoformat())
                else:                                  # fresh lease / new move-in
                    add("leased", uid, r.get("move_in"), r.get("property_name"),
                        r.get("unit"), r.get("tenant_name"), known=lsd.isoformat())
    else:                                  # diff: stamp change date as `known`
        for uid in set(prev) | set(cur):
            p = prev.get(uid); c = cur.get(uid)
            ps = p["status"] if p else "Occupied"
            cs = c["status"] if c else "Occupied"
            ref = c or p; prop = ref["prop"]; unit = ref["unit"]
            if not noticed(ps) and noticed(cs):
                add("ntv", uid, c["mo"], prop, unit)
            if not rented(ps) and rented(cs):
                add("leased", uid, (c or {}).get("mi"), prop, unit)
            if noticed(ps) and vacant(cs):
                add("moveout", uid, (c["mo"] or today.isoformat()), prop, unit)
            if p and not c:                # left the vacancy table → moved in / occupied
                add("movein", uid, today.isoformat(), prop, unit)
    ev_path.write_text(json.dumps(events, indent=2))
    return events

def daterange(start, end, step):
    d = start
    while d <= end:
        yield d; d += dt.timedelta(days=step)

def main():
    today = dt.date.today()
    path = DATA / "occupancy-history.json"
    have = {h["date"] for h in (json.loads(path.read_text()) if path.exists() else [])}
    want = {today}
    if "--daily" in sys.argv:                 # daily back N days (the 90-day view)
        nd = int(sys.argv[sys.argv.index("--daily") + 1])
        want |= set(daterange(today - dt.timedelta(days=nd), today, 1))
    if "--weekly-since" in sys.argv:          # weekly back to a date (long-term view)
        since = dt.date.fromisoformat(sys.argv[sys.argv.index("--weekly-since") + 1])
        want |= set(daterange(since, today, 7))
    if "--resume" in sys.argv:                # skip dates already captured
        want -= have
    targets = sorted(want)
    snaps = []
    for i, d in enumerate(targets):
        try:
            snaps.append(snapshot(d))
        except Exception as e:
            print(f"  {d}: ERR {e}")
        if i and i % 20 == 0:
            upsert_history(snaps); print(f"  ...{i+1}/{len(targets)} ({d})")
    total = upsert_history(snaps)
    # current vacancy detail (unit-level move dates) — report id is 'unit_vacancy'
    keep = ["property_name", "unit", "unit_status", "last_move_in", "last_move_out",
            "next_move_in", "available_on", "days_vacant", "schd_rent",
            "advertised_rent", "rent_ready"]
    vac = report("unit_vacancy", {})
    (DATA / "vacancy.json").write_text(
        json.dumps([{k: r.get(k) for k in keep} for r in vac], indent=2))
    # lease expiration detail → real lease_sign_date (known date for signings)
    lkeep = ["unit_id", "property_name", "unit", "tenant_name", "status",
             "lease_sign_date", "move_in", "renewal_start_date", "last_lease_renewal", "rent"]
    le = report("lease_expiration_detail", {})
    (DATA / "lease-expiration.json").write_text(
        json.dumps([{k: r.get(k) for k in lkeep} for r in le], indent=2))
    # diff per-unit state → events.json (known vs effective dates)
    events = update_events(vac, le, today)
    print(f"history now {total} snapshots; pulled {len(snaps)}; "
          f"vacancy units {len(vac)}; events {len(events)}")

if __name__ == "__main__":
    main()
