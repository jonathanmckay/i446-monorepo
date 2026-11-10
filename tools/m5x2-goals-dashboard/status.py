#!/usr/bin/env python3
"""Live status for the Q4 m5x2 goals (goals.json) from AppFolio.

Computes every goal whose source is an AppFolio report, with the same
definitions Dream used for the 2026-11-03 baselines (vac.py / tours.py in
.dream-runs/2026.11.03-v177/tmp), and marks the rest `manual` with who owns
the number. Prints a markdown table, P1 before P2 within each section.

Usage: status.py [--json] [--week YYYY-MM-DD   # Sunday of the leasing week to read]
Credentials: ~/files/i447/secrets/appfolio.env (APPFOLIO_VHOST/USERNAME/PASSWORD).
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
GOALS = ROOT / "goals.json"
ENV_FILES = [Path.home() / "files/i447/secrets/appfolio.env",
             Path.home() / ".config/m5x2/appfolio.env",
             ROOT.parent / "m5x2-occupancy-dashboard" / ".env"]
THROTTLE = 1.2


def _creds():
    env = {}
    for f in ENV_FILES:
        if f.exists():
            for ln in f.read_text().splitlines():
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.split("=", 1); env.setdefault(k.strip(), v.strip())
            break
    if not env.get("APPFOLIO_USERNAME"):
        sys.exit("ERROR: AppFolio credentials not found (see ENV_FILES)")
    return env.get("APPFOLIO_VHOST", "mckay"), env["APPFOLIO_USERNAME"], env["APPFOLIO_PASSWORD"]


def report(name: str, body: dict | None = None) -> list[dict]:
    vhost, user, pw = _creds()
    base = f"https://{vhost}.appfolio.com"
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    url = f"{base}/api/v2/reports/{name}.json"
    data = json.dumps({"unit_visibility": "active", **(body or {})}).encode()
    rows: list[dict] = []
    while url:
        if url.startswith("/"):
            url = base + url
        hdr = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
        req = (urllib.request.Request(url, data=data, method="POST", headers=hdr) if data is not None
               else urllib.request.Request(url, headers=hdr))
        for attempt in range(6):
            try:
                d = json.load(urllib.request.urlopen(req, timeout=120)); break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    time.sleep(2 ** attempt * 3); continue
                raise
        rows += d.get("results", [])
        url = d.get("next_page_url") or d.get("next_page"); data = None
        time.sleep(THROTTLE)
    return rows


def _d(s):
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


# ── metric computations ─────────────────────────────────────────────────────

def turns(today: dt.date) -> dict:
    r = report("unit_vacancy")  # the API report name; goals.json calls it unit_vacancy_detail
    nr = [x for x in r if x.get("unit_status") == "Vacant-Unrented" and x.get("rent_ready") == "No"]
    dv = [x.get("days_vacant") or 0 for x in nr]
    past = [x for x in nr if _d(x.get("unit_turn_target_date")) and _d(x["unit_turn_target_date"]) < today]
    idle = sum(float(x.get("computed_market_rent") or x.get("advertised_rent") or 0) for x in nr)
    by_prop = collections.Counter(x.get("property_name") for x in nr).most_common(4)
    return {
        "backlog": len(nr),
        "backlog_by_property": ", ".join(f"{k} {v}" for k, v in by_prop),
        "median_days_vacant": st.median(dv) if dv else None,
        "over_60d": sum(1 for d in dv if d > 60),
        "past_target": len(past),
        "no_target": sum(1 for x in nr if not x.get("unit_turn_target_date")),
        "idle_rent": round(idle),
    }


def trs(today: dt.date) -> dict:
    le = report("lease_expiration_detail")
    dec = [x for x in le if (_d(x.get("lease_expires")) or dt.date.min).replace(day=1) == dt.date(2026, 12, 1)]
    status = collections.Counter(x.get("status") for x in dec)
    vacated = [x for x in dec if _d(x.get("move_out"))]
    notice = [x for x in dec if x.get("notice_given_date") and not _d(x.get("move_out"))]
    eligible_untouched = [x for x in dec if x.get("status") == "Eligible" and not x.get("notice_given_date") and not _d(x.get("move_out"))]
    rs = report("renewal_summary")
    dec_ren = [x for x in rs if (_d(x.get("previous_lease_end")) or dt.date.min).replace(day=1) == dt.date(2026, 12, 1)]
    renewed = sum(1 for x in dec_ren if x.get("status") == "Renewed")
    cohort = len(dec) + renewed - len(vacated)   # still-listed + already renewed, minus moved out
    resolved = len(dec) - len(eligible_untouched) + renewed
    win = [x for x in rs if _d(x.get("lease_start")) and today <= _d(x["lease_start"]) <= today + dt.timedelta(days=90)]
    rn = sum(1 for x in win if x.get("status") == "Renewed"); dnr = sum(1 for x in win if x.get("status") == "Did Not Renew")
    return {
        "dec_total": len(dec) + renewed, "dec_resolved": resolved, "dec_status": dict(status),
        "dec_notice": len(notice), "dec_vacated": len(vacated), "dec_eligible_untouched": len(eligible_untouched),
        "dec_renewed": renewed, "dec_cohort": cohort,
        "legacy_renewed": rn, "legacy_dnr": dnr,
        "legacy_rate": round(100 * rn / (rn + dnr), 1) if (rn + dnr) else None,
    }


def leasing(week_sunday: dt.date, today: dt.date) -> dict:
    frm = week_sunday - dt.timedelta(days=7)
    to = week_sunday + dt.timedelta(days=6)
    # Parameter names per the AppFolio report schema: from_date/to_date are ignored
    # (the API then returns a default window), and Inactive cards are excluded unless
    # asked for. The roadmap definition counts every status.
    gc = report("guest_card_inquiries", {
        "received_on_from": frm.isoformat(), "received_on_to": to.isoformat(),
        "guest_card_statuses": ["Active", "Inactive", "Waitlisted", "Pre-Qualified", "Prospect", "Applicant"],
    })
    weeks: dict[dt.date, list] = collections.defaultdict(list)
    for x in gc:
        t = dt.datetime.fromisoformat(x["received"].replace("Z", "+00:00")) - dt.timedelta(hours=8)
        d = t.date(); weeks[d - dt.timedelta(days=(d.weekday() + 1) % 7)].append(x)
    out = {"statuses_seen": dict(collections.Counter(x.get("status") for x in gc))}
    for w in (frm, week_sunday):
        xs = weeks.get(w, [])
        cards: dict = {}
        for x in xs:
            cards[x["guest_card_id"]] = max(cards.get(x["guest_card_id"], 0), x.get("showings") or 0)
        with_show = sum(1 for v in cards.values() if v > 0)
        out[w.isoformat()] = {
            "cards": len(cards), "cards_with_showing": with_show,
            "tour_rate": round(100 * with_show / len(cards), 1) if cards else None,
            "showings": sum(x.get("showings") or 0 for x in xs),
            "age_days": (today - w).days, "mature_14d": (today - w).days >= 14 + 6,
        }
    # Q4 new leases: rent roll occupancies with move_in >= 10-01 (proxy; signed-not-yet-moved-in are missed)
    rr = report("rent_roll_itemized")
    out["q4_new_leases_signed_approx"] = sum(1 for x in rr if (_d(x.get("move_in")) or dt.date.min) >= dt.date(2026, 10, 1))
    return out


# ── render ──────────────────────────────────────────────────────────────────

def build_rows(today: dt.date, week_sunday: dt.date) -> list[dict]:
    T = turns(today); R = trs(today); L = leasing(week_sunday, today)
    wk = L[week_sunday.isoformat()]; prev = L[(week_sunday - dt.timedelta(days=7)).isoformat()]
    def wkfmt(w, k):
        v = w.get(k); return "–" if v is None else (f"{v}%" if k == "tour_rate" else str(v))
    status = {
        ("r203 Construction / Turns", "Turn backlog cap"): (f"{T['backlog']} not-ready vacant", f"{T['backlog_by_property']}"),
        ("r203 Construction / Turns", "Rent-ready turn days"): (f"proxy: median {T['median_days_vacant']} d vacant, {T['over_60d']} units > 60 d", "true metric needs Meld (Ian)"),
        ("r203 Construction / Turns", "Backlog past turn target"): (f"{T['past_target']} / {T['backlog']} past target", f"{T['no_target']} with no target date"),
        ("r203 Construction / Turns", "Idle rent in not-ready units"): (f"${T['idle_rent']/1000:.1f}K/mo", ""),
        ("r203 Construction / Turns", "Turn spend vs estimate"): ("manual", "LeeRoy turn tracker"),
        ("r203 Construction / Turns", "P1 projects fully specified"): ("manual", "Lawrence weekly; was due 11-08"),
        ("r202 TRS", "Dec expirations resolved"): (f"{R['dec_resolved']} / {R['dec_total']}", f"status {R['dec_status']}; {R['dec_notice']} notice, {R['dec_vacated']} vacated, {R['dec_eligible_untouched']} Eligible untouched; was due 11-06"),
        ("r202 TRS", "Expiry-cohort retention"): (f"{R['dec_renewed']} renewed / {R['dec_cohort']} in cohort ({100*R['dec_renewed']/max(1,R['dec_cohort']):.0f}%)", f"{R['dec_status'].get('Pending',0)} pending"),
        ("r202 TRS", "Offer-conditional renewal rate (legacy)"): (f"{R['legacy_rate']}% ({R['legacy_renewed']} / {R['legacy_renewed']+R['legacy_dnr']})", "renewal starts next 90 d"),
        ("r204 Special Projects", "Priority list with NOI"): ("manual", "Ian; was due 11-08"),
        ("r204 Special Projects", "Tools state chart"): ("manual", "Ian; due 11-15"),
        ("r204 Special Projects", "Move-out recorded before turn"): ("manual", "needs Meld turn WOs"),
        ("r204 Special Projects", "Canonical funnel metric live"): ("manual", "Jesus; due 11-15"),
        ("r202 Leasing", "Tour-scheduled rate"): (f"wk {week_sunday.month}/{week_sunday.day}: {wkfmt(wk,'tour_rate')} ({wk['cards_with_showing']}/{wk['cards']} cards)" + ("" if wk["mature_14d"] else " · immature, <14 d"), f"prev wk {wkfmt(prev,'tour_rate')} ({prev['cards_with_showing']}/{prev['cards']})" + ("" if prev["mature_14d"] else " · immature")),
        ("r202 Leasing", "Tours scheduled per week"): (f"wk {week_sunday.month}/{week_sunday.day}: {wk['showings']}" + ("" if wk["mature_14d"] else " · immature"), f"prev wk {prev['showings']}"),
        ("r202 Leasing", "Q4 leases signed"): (f"≈{L['q4_new_leases_signed_approx']} / 110 (AppFolio new leases signed since 10-01)", "Andie's weekly is the source of record"),
    }
    data = json.loads(GOALS.read_text())
    rows = []
    for s in data["sections"]:
        for g in sorted(s["goals"], key=lambda g: (g.get("p", "P9"), )):
            st_, note = status.get((s["name"], g["goal"]), ("?", "no rule"))
            rows.append({"section": s["name"], "p": g.get("p"), "goal": g["goal"], "dri": g.get("dri"),
                         "baseline": g.get("baseline"), "target": g.get("target"), "due": g.get("due"),
                         "status": st_, "note": note})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--week", help="Sunday of the leasing week to read (default: last complete week)")
    ap.add_argument("--write", action="store_true", help="also save status.json for dashboard.py's Now column")
    a = ap.parse_args()
    today = dt.date.today()
    if a.week:
        week = dt.date.fromisoformat(a.week)
    else:
        last_sat = today - dt.timedelta(days=(today.weekday() + 2) % 7 or 7)
        week = last_sat - dt.timedelta(days=6)
    rows = build_rows(today, week)
    if a.write:
        (ROOT / "status.json").write_text(json.dumps(
            {"as_of": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "week": week.isoformat(), "rows": rows},
            ensure_ascii=False, indent=1))
    if a.json:
        print(json.dumps({"as_of": today.isoformat(), "week": week.isoformat(), "rows": rows}, ensure_ascii=False, indent=1)); return
    print(f"as of {today.isoformat()} · leasing week starting {week.isoformat()}\n")
    sec = None
    for r in rows:
        if r["section"] != sec:
            sec = r["section"]; print(f"\n## {sec}\n| P | Goal | Baseline (11-03) | Target | Status now | Note |\n|---|---|---|---|---|---|")
        print(f"| {r['p']} | {r['goal']} | {str(r['baseline'])[:60]} | {r['target']} | **{r['status']}** | {r['note']} |")


if __name__ == "__main__":
    main()
