#!/usr/bin/env python3
"""Refresh the occupancy dashboard data straight from AppFolio (no Claude/MCP).

- occupancy_summary (as-of today) → append a real snapshot to occupancy-history.json
  (idempotent per date). Run daily and the 90-day history densifies on its own.
- unit_vacancy_detail → vacancy.json (current non-occupied units + move dates).
- twelve_month_income_statement (GL 41150 Concessions / 40110 Gross Potential
  Rent) → concessions.json, monthly $ figures feeding the dashboard's
  "concessions % of rent" band; plus general_ledger lines on 41150 → per-month
  count of distinct units that received a concession (the concessions bar chart).

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

def report(name, body, single=False):
    """AppFolio reports are v2 POST with a JSON body; follow next_page if present.
    single=True returns just the first page (5000 rows) and warns if truncated —
    general_ledger's next_page link is relative and 404s when followed
    (probed 2026-09-16), so GL callers keep each request under one page."""
    url = f"{BASE}/api/v2/reports/{name}.json"
    data = json.dumps({"unit_visibility": "active", **body}).encode()
    hdr = {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}
    rows = []
    while url:
        if url.startswith("/"): url = BASE + url
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
        if single:
            if url: print(f"  WARN {name}: truncated at {len(rows)} rows (next page ignored)")
            break
    return rows

def income_statement_12mo(frm, to):
    """twelve_month_income_statement is also a v2 POST report, but — unlike
    every other report() caller here — it returns a bare JSON array (one row
    per GL account, each with a `months` sub-array), not the {results:[...]}
    envelope report() expects. Confirmed 2026-09-15 by probing both
    'twelve_month_income_statement' and 'income_statement' directly; only the
    former returns this month-bucketed shape. Capped at 12 months per call —
    callers chunk by year."""
    url = f"{BASE}/api/v2/reports/twelve_month_income_statement.json"
    body = json.dumps({"unit_visibility": "active", "posted_on_from": frm,
                       "posted_on_to": to, "level_of_detail": "detail_view"}).encode()
    hdr = {"Authorization": f"Basic {AUTH}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, method="POST", headers=hdr)
    for attempt in range(6):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=120)); break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(2 ** attempt * 3); continue
            raise
    time.sleep(THROTTLE)
    return d

def fetch_concessions(start_year=2024):
    """Monthly Concessions $ (GL 41150) and Gross Potential Rent $ (GL 40110),
    start_year-01 through the current month, chunked into ≤12-month calls.
    Concessions posts as a GL credit (negative); stored here as a positive
    dollar amount for readability. Real ledger data on this AppFolio instance
    only starts ~2026-06 (portfolio consolidation) — confirmed via spot-check
    back to 2024, every earlier month is a genuine $0, not a missing value."""
    today = dt.date.today()
    months = {}
    y = start_year
    while dt.date(y, 1, 1) <= today:
        frm, to = f"{y}-01", (f"{y}-12" if y < today.year else f"{today.year}-{today.month:02d}")
        accounts = income_statement_12mo(frm, to)
        gpr = next((a for a in accounts if a.get("account_code") == "40110"), None)
        cx = next((a for a in accounts if a.get("account_code") == "41150"), None)
        for m in (gpr or {}).get("months", []):
            months.setdefault(m["id"], {})["gpr"] = float(m["value"])
        for m in (cx or {}).get("months", []):
            months.setdefault(m["id"], {})["concessions"] = -float(m["value"])
        y += 1
    return months

def gl_account_id(number):
    """Resolve a GL account number ('41150') → gl_account_id via chart_of_accounts;
    general_ledger's gl_account_ids filter wants the id, not the number."""
    for r in report("chart_of_accounts", {}):
        if str(r.get("number")) == str(number):
            return r["gl_account_id"]
    return None

def concession_units(months):
    """Per-month count of distinct units that received a concession, from
    general_ledger lines on GL 41150. A concession posts as a DEBIT to the income
    account (AppFolio auto-generates a 'Receipt' from the tenant credit); credits
    are reversals, so a unit only counts when its month net is a debit > 0.
    ~320 lines/month (2026-09), well under the 5000-row single page.
    2026-06 is the consolidation month: one bulk JE, 2 units — not comparable."""
    import calendar
    aid = gl_account_id("41150")
    if aid is None:
        print("  WARN: GL 41150 not found in chart_of_accounts; unit counts skipped"); return
    for key, m in months.items():
        if not m.get("concessions"):
            continue
        y, mo = int(key[:4]), int(key[5:7])
        rows = report("general_ledger", {"posted_on_from": f"{key}-01",
                                          "posted_on_to": f"{key}-{calendar.monthrange(y, mo)[1]:02d}",
                                          "gl_account_ids": [aid]}, single=True)
        net = {}
        for r in rows:
            uid = r.get("unit_id")
            if uid is None: continue
            net[uid] = net.get(uid, 0.0) + float(r.get("debit") or 0) - float(r.get("credit") or 0)
        m["units"] = sum(1 for v in net.values() if v > 0.005)
        m["lines"] = len(rows)

def tickler(frm, to):
    """tenant_tickler is a v1 GET report (not v2 POST). Returns move/notice
    events with OccurredDate (MM/DD/YYYY). Used to build the daily occupancy
    timeline as ±1 deltas off the reliable point-in-time occupied anchors."""
    url = f"{BASE}/api/v1/reports/tenant_tickler.json?from_date={frm}&to_date={to}"
    rows = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {AUTH}"})
        for attempt in range(6):
            try:
                d = json.load(urllib.request.urlopen(req, timeout=120)); break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    time.sleep(2 ** attempt * 3); continue
                raise
        rows += d.get("results", [])
        url = d.get("next_page_url") or d.get("next_page")
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
    if prev is None:                       # first run: seed baseline with real anchor dates
        cur_ids = set(cur)
        sign = {str(r.get("unit_id")): r.get("lease_sign_date")
                for r in le if r.get("lease_sign_date")}
        def cap_today(*cands):
            # earliest real date among candidates, but never in the future (we can't
            # have learned something before it exists); falls back to today.
            for c in cands:
                d = pdate(c)
                if d: return min(d, today).isoformat()
            return today.isoformat()
        for uid, r in cur.items():
            s = r["status"]
            # a vacated unit became vacant on its move-out; a signed lease on its
            # sign date — real past dates, not today. Only genuinely-future events
            # (a current notice's scheduled move-out) cap to today ("aware as of now").
            if noticed(s): add("ntv", uid, r["mo"], r["prop"], r["unit"],
                               known=cap_today(sign.get(uid), r["mo"]))
            if rented(s):  add("leased", uid, r["mi"], r["prop"], r["unit"],
                               known=cap_today(sign.get(uid), r["mi"]))
            if vacant(s) and not rented(s): add("vacant", uid, r["mo"], r["prop"], r["unit"],
                               known=cap_today(r["mo"]))
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
    # tenant tickler → move/notice events for the occupancy timeline + feed
    tkeep = ["OccurredDate", "Event", "PropertyName", "Unit", "UnitId", "Tenant",
             "MoveInDate", "MoveOutDate", "LeaseSignDate", "Rent", "MoveOutReason"]
    tk = tickler("2023-01-01", "2027-12-31")
    (DATA / "tickler.json").write_text(
        json.dumps([{k: r.get(k) for k in tkeep} for r in tk]))
    mv = sum(1 for r in tk if r.get("Event") in ("Move-in", "Move-out"))
    # monthly concessions $ + Gross Potential Rent $ → concessions.json
    conc = fetch_concessions()
    concession_units(conc)
    (DATA / "concessions.json").write_text(json.dumps(conc, indent=2, sort_keys=True))
    print(f"history now {total} snapshots; pulled {len(snaps)}; "
          f"vacancy units {len(vac)}; events {len(events)}; tickler {len(tk)} ({mv} moves); "
          f"concessions {len(conc)} months")

if __name__ == "__main__":
    main()
