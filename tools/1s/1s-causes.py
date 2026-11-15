#!/usr/bin/env python3
"""1s-causes.py — evidence for the "Causes in 分" section of /1s.

Prints, for the review week against the trailing N weeks, the facts a causal
read of the week needs: where points came from (and which sources look
one-off), the weekday and 2-hour-block profile, 分 per hour by domain and by
activity, time that earned nothing, ritual overhead, and zero-hcb days. It
states evidence only; ranking causes and naming fixes is the skill's job.

Read-only, three local sources, no Excel:
  - points cache  tools/personal-dashboard/.points-cache.json  (per-day domain
    and per-block totals; what Excel shows, habits included)
  - neon.db       lib/neon/ledger events (credits with a `src`; habit-formula
    points never pass through it, so cache − ledger = habit/unattributed)
  - Toggl store   ~/vault/i156/toggl.sqlite (entries with project names)

Usage:
    1s-causes.py [YYYY-MM-DD] [--weeks N] [--json]

No date → the most recent completed Sun–Sat week (same rule as 1s-survey.py).
Python 3.9-clean (Ix /usr/bin/python3).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MONO = HERE.parent.parent
sys.path.insert(0, str(MONO / "lib"))

POINTS_CACHE = MONO / "tools" / "personal-dashboard" / ".points-cache.json"

_spec = importlib.util.spec_from_file_location("survey", HERE / "1s-survey.py")
_survey = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_survey)  # type: ignore[union-attr]
week_range = _survey.week_range
week_row_label = _survey.week_row_label

DOMAINS = ["-1₦", "0₲", "i9", "m5", "个", "媒", "思", "hcb", "xk", "社"]
BLOCKS = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

# Points-cache domain → the Toggl projects whose time earns it.
DOMAIN_PROJECTS = {
    "i9": ["i9"], "m5": ["m5x2"], "个": ["g245", "h335", "i447", "n156", "hci"],
    "媒": ["hcmc"], "思": ["hcm"], "hcb": ["hcb", "hcbp"],
    "xk": ["xk87", "xk88"], "社": ["s897", "家"],
}
# neon.db `field` for the same domains (0₲ is "0g" there).
LEDGER_FIELD = {"0₲": "0g"}
SLEEP = "睡觉"
# Time in these projects is expected to earn nothing; listed so it can be cut.
ZERO_PROJECTS = ["i444", "infra", "hcmc", "no project"]
# Planning and ritual overhead, by Toggl description.
OVERHEAD_RE = re.compile(r"^(-1g|-1l|-1t|0l|0g|0t|0n|1 cal|ibx\b.*|slack\b.*|teams)\b", re.I)
CORRECTION_RE = re.compile(r"^(fix|revert|undo|move|jm:)", re.I)
MIN_ACTIVITY_MIN = 20


# ── pure helpers ─────────────────────────────────────────────────────────────

def week_days(sunday: _dt.date) -> list:
    return [sunday + _dt.timedelta(days=i) for i in range(7)]


def day_total(day: dict) -> float:
    return sum(day.get(d, 0) or 0 for d in DOMAINS)


def norm_name(s: str) -> str:
    """Task/Toggl name reduced to a join key: no (N) [N] {N}, no link, no
    block arrow, no `did ` prefix, lowercased, 40 chars."""
    s = re.sub(r"^did\s+", "", s or "")
    s = re.sub(r"\[\(link\)\].*", "", s)
    s = re.sub(r"[\(\[\{]\d+[\)\]\}]", "", s)
    s = re.sub(r"→.*", "", s)
    return re.sub(r"\s+", " ", s.strip().lower())[:40]


def domain_totals(cache: dict, days: list) -> dict:
    out = {d: 0.0 for d in DOMAINS}
    for day in days:
        row = cache.get(day.isoformat(), {})
        for d in DOMAINS:
            out[d] += row.get(d, 0) or 0
    return out


def trailing_weeks(sunday: _dt.date, n: int) -> list:
    return [sunday - _dt.timedelta(days=7 * i) for i in range(n, 0, -1)]


def weekday_profile(cache: dict, sunday: _dt.date, n: int) -> list:
    """[(dow, this_week_total, trailing_mean)] Sun..Sat."""
    out = []
    for i, name in enumerate(DOW):
        this = day_total(cache.get((sunday + _dt.timedelta(days=i)).isoformat(), {}))
        prior = [day_total(cache[(w + _dt.timedelta(days=i)).isoformat()])
                 for w in trailing_weeks(sunday, n)
                 if (w + _dt.timedelta(days=i)).isoformat() in cache]
        out.append((name, this, statistics.mean(prior) if prior else 0.0))
    return out


def block_profile(cache: dict, sunday: _dt.date, n: int) -> list:
    """[(block, this_week_total, trailing_weekly_mean)]."""
    def wk(s):
        t = {b: 0.0 for b in BLOCKS}
        for day in week_days(s):
            for b, p in (cache.get(day.isoformat(), {}).get("__block__") or {}).items():
                if b in t:
                    t[b] += p or 0
        return t
    this = wk(sunday)
    prior = [wk(w) for w in trailing_weeks(sunday, n)]
    return [(b, this[b], statistics.mean(p[b] for p in prior) if prior else 0.0) for b in BLOCKS]


def hours_by_project(toggl: list) -> dict:
    out = defaultdict(float)
    for _day, project, _desc, minutes in toggl:
        out[project] += minutes / 60
    return dict(out)


def domain_yield(points: dict, hours: dict) -> list:
    """[(domain, 分, hours, 分/hr)] for domains that map to Toggl projects."""
    out = []
    for d, projects in DOMAIN_PROJECTS.items():
        h = sum(hours.get(p, 0) for p in projects)
        out.append((d, points.get(d, 0), h, points.get(d, 0) / h if h else 0.0))
    return out


def activity_yield(toggl: list, credits: list) -> tuple:
    """Join Toggl time to ledger credits by normalized name.
    Returns (rows, unmatched): rows = [(分/hr, minutes, 分, project, name)] for
    activities >= MIN_ACTIVITY_MIN, lowest yield first; unmatched = [(分, name)]
    credited with no Toggl entry of that name. The join is by name, so 0 分
    means "no credit under this name", not "earned nothing"."""
    mins, proj = defaultdict(float), {}
    for _day, project, desc, minutes in toggl:
        if project == SLEEP:
            continue
        k = norm_name(desc)
        mins[k] += minutes
        proj[k] = project
    pts = defaultdict(float)
    for c in credits:
        pts[norm_name(c["src"])] += c["points"]
    rows = sorted(((pts.get(k, 0) / m * 60, round(m), round(pts.get(k, 0)), proj[k], k)
                   for k, m in mins.items() if m >= MIN_ACTIVITY_MIN), key=lambda r: (r[0], -r[1]))
    unmatched = sorted(((round(v), k) for k, v in pts.items() if k not in mins and v), reverse=True)
    return rows, unmatched


def zero_point_time(toggl: list) -> dict:
    """{project: [(minutes, description, n_entries)]} for ZERO_PROJECTS."""
    agg, cnt = defaultdict(float), defaultdict(int)
    for _day, project, desc, minutes in toggl:
        if project in ZERO_PROJECTS:
            k = (project, (desc or "").strip()[:40] or "∅")
            agg[k] += minutes
            cnt[k] += 1
    out = defaultdict(list)
    for (project, desc), m in agg.items():
        out[project].append((round(m), desc, cnt[(project, desc)]))
    return {p: sorted(v, reverse=True) for p, v in out.items()}


def overhead(toggl: list, credits: list) -> list:
    """[(name, minutes, n_entries, net 分 credited under that name)]."""
    mins, cnt = defaultdict(float), defaultdict(int)
    for _day, _project, desc, minutes in toggl:
        k = norm_name(desc)
        if OVERHEAD_RE.match(k):
            mins[k] += minutes
            cnt[k] += 1
    pts = defaultdict(float)
    for c in credits:
        pts[norm_name(c["src"])] += c["points"]
    return sorted(((k, round(m), cnt[k], round(pts.get(k, 0))) for k, m in mins.items()),
                  key=lambda r: -r[1])


def split_corrections(credits: list) -> tuple:
    """(real, corrected): a credit is 'corrected' when it is itself a
    fix/revert/undo/move row, or a same-day same-field negative row cancels at
    least 80% of it (a write error and its repair)."""
    negs = defaultdict(list)
    for c in credits:
        if c["points"] < 0:
            negs[(c["date"], c["field"])].append(-c["points"])
    real, corrected = [], []
    for c in credits:
        is_fix = bool(CORRECTION_RE.match(c["src"] or ""))
        cancelled = c["points"] >= 100 and any(n >= 0.8 * c["points"] for n in negs[(c["date"], c["field"])])
        (corrected if is_fix or cancelled or c["points"] < 0 else real).append(c)
    return real, corrected


def ledger_by_domain(credits: list) -> dict:
    out = defaultdict(float)
    for c in credits:
        out[c["field"]] += c["points"]
    return dict(out)


# ── data sources ─────────────────────────────────────────────────────────────

def load_points() -> dict:
    if socket.gethostname().lower().startswith("ix"):
        return json.loads(POINTS_CACHE.read_text())
    out = subprocess.run(["ssh", "ix", f"cat {POINTS_CACHE}"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise SystemExit(f"points cache unreadable on ix: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def load_toggl(start: _dt.date, end: _dt.date) -> tuple:
    """([(day, project, description, minutes)], settled_until or None)."""
    import toggl_store
    conn = sqlite3.connect(f"file:{toggl_store.STORE_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT day, project, description, seconds FROM entries "
                            "WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())).fetchall()
    finally:
        conn.close()
    return [(d, p or "no project", desc or "", s / 60) for d, p, desc, s in rows], toggl_store.settled_until()


def load_credits(start: _dt.date, end: _dt.date) -> list:
    """Ledger credits on the 0分 domain columns for [start, end], by resolved
    date (a row written later for a day in the week still counts)."""
    from neon import ledger
    conn = ledger.connect()
    try:
        ledger.catch_up(conn)
        fields = [LEDGER_FIELD.get(d, d) for d in DOMAINS]
        q = ("SELECT date, field, points, src FROM events WHERE sheet='0分' AND kind IN ('credit','credit_ref') "
             "AND points IS NOT NULL AND date BETWEEN ? AND ? AND field IN (%s)" % ",".join("?" * len(fields)))
        rows = conn.execute(q, [start.isoformat(), end.isoformat()] + fields).fetchall()
    finally:
        conn.close()
    back = {v: k for k, v in LEDGER_FIELD.items()}
    return [{"date": d, "field": back.get(f, f), "points": p, "src": s or ""} for d, f, p, s in rows]


# ── assemble + render ────────────────────────────────────────────────────────

def build(sunday: _dt.date, n: int, cache: dict, toggl: list, settled, credits: list) -> dict:
    days = week_days(sunday)
    points = domain_totals(cache, days)
    prior = [domain_totals(cache, week_days(w)) for w in trailing_weeks(sunday, n)]
    prior_mean = {d: statistics.mean(p[d] for p in prior) if prior else 0.0 for d in DOMAINS}
    hours = hours_by_project(toggl)
    waking = sum(h for p, h in hours.items() if p != SLEEP)
    real, corrected = split_corrections(credits)
    led = ledger_by_domain(credits)
    rows, unmatched = activity_yield(toggl, real)
    total = sum(points.values())
    tracked_by_day = defaultdict(float)
    for d, _p, _desc, m in toggl:
        tracked_by_day[d] += m / 60
    return {
        "week": week_row_label(sunday), "start": sunday.isoformat(), "end": days[-1].isoformat(),
        "weeks": n, "total": total, "prior_total_mean": sum(prior_mean.values()),
        "domains": [(d, points[d], prior_mean[d], led.get(d, 0), points[d] - led.get(d, 0)) for d in DOMAINS],
        "top_items": sorted(((round(c["points"]), c["field"], c["date"][5:], c["src"][:60]) for c in real),
                            reverse=True)[:15],
        "corrected": sorted(((round(c["points"]), c["field"], c["date"][5:], c["src"][:60]) for c in corrected),
                            key=lambda r: -abs(r[0]))[:12],
        "weekday": weekday_profile(cache, sunday, n),
        "blocks": block_profile(cache, sunday, n),
        "waking_hours": waking, "avg_per_waking_hour": total / waking if waking else 0.0,
        "domain_yield": domain_yield(points, hours),
        "other_hours": sorted(((p, h) for p, h in hours.items()
                               if p != SLEEP and not any(p in v for v in DOMAIN_PROJECTS.values())),
                              key=lambda r: -r[1]),
        "activity": rows, "unmatched": unmatched[:25],
        "zero_time": zero_point_time(toggl), "overhead": overhead(toggl, real),
        "zero_hcb_days": [d.isoformat()[5:] for d in days if (cache.get(d.isoformat(), {}).get("hcb", 0) or 0) <= 0],
        "daily_0g": [(DOW[i], round(cache.get(d.isoformat(), {}).get("0₲", 0) or 0)) for i, d in enumerate(days)],
        "tracked_by_day": [(d.isoformat()[5:], round(tracked_by_day.get(d.isoformat(), 0), 1)) for d in days],
        "toggl_settled_until": settled.isoformat() if settled else None,
    }


def _pct(this: float, base: float) -> str:
    return f"{(this - base) / base * 100:+.0f}%" if base else "n/a"


def render(r: dict) -> str:
    L = [f"### Cause evidence ({r['week']}, {r['start']} to {r['end']}, vs trailing {r['weeks']} weeks)", ""]
    if r["toggl_settled_until"] and r["toggl_settled_until"] < r["end"]:
        short = ", ".join(f"{d} {h}h" for d, h in r["tracked_by_day"])
        L += [f"> Toggl store is settled only through {r['toggl_settled_until']}; later days may be incomplete "
              f"(tracked per day: {short}). Prefer Step 3's live hours where they differ.", ""]
    L += [f"**Total** {r['total']:,.0f}分 vs trailing mean {r['prior_total_mean']:,.0f} "
          f"({_pct(r['total'], r['prior_total_mean'])}). Average {r['avg_per_waking_hour']:.0f}分 per waking hour "
          f"({r['waking_hours']:.1f}h tracked awake).", "",
          "| Domain | 分 | trailing x̄ | Δ | ledger credits | habit/unattributed |", "|---|---|---|---|---|---|"]
    for d, p, m, led, gap in r["domains"]:
        L.append(f"| {d} | {p:,.0f} | {m:,.0f} | {_pct(p, m)} | {led:,.0f} | {gap:,.0f} |")
    L += ["", "**Largest credited items** (candidates for one-off income):"]
    L += [f"- {p}分 {f} {d}: {s}" for p, f, d, s in r["top_items"]]
    if r["corrected"]:
        L += ["", "**Corrections and negative rows** (exclude from causes):"]
        L += [f"- {p}分 {f} {d}: {s}" for p, f, d, s in r["corrected"]]
    L += ["", "**Weekday** (this week / trailing mean): " +
          " · ".join(f"{n} {t:,.0f}/{m:,.0f}" for n, t, m in r["weekday"]),
          "**Block** (week total / trailing weekly mean): " +
          " · ".join(f"{b} {t:,.0f}/{m:,.0f}" for b, t, m in r["blocks"]),
          "**0₲ per day:** " + " · ".join(f"{n} {v}" for n, v in r["daily_0g"]),
          "**Zero-hcb days:** " + (", ".join(r["zero_hcb_days"]) or "none"), "",
          "| Domain | 分 | hours | 分/hr |", "|---|---|---|---|"]
    for d, p, h, y in r["domain_yield"]:
        L.append(f"| {d} | {p:,.0f} | {h:.1f} | {y:.0f} |")
    L += ["", "Hours outside the point domains: " + ", ".join(f"{p} {h:.1f}h" for p, h in r["other_hours"]), "",
          "**Activity yield** (Toggl ≥20m joined to credits by name; 0 = no credit under that name):", "",
          "| 分/hr | min | 分 | project | activity |", "|---|---|---|---|---|"]
    L += [f"| {y:.0f} | {m} | {p} | {pr} | {k} |" for y, m, p, pr, k in r["activity"]]
    L += ["", "**Credited with no Toggl entry of that name:** " +
          "; ".join(f"{k} {p}" for p, k in r["unmatched"]), "", "**Zero-point time:**"]
    for proj, items in r["zero_time"].items():
        tot = sum(m for m, _d, _c in items)
        L.append(f"- {proj} {tot / 60:.1f}h: " + "; ".join(f"{d} {m}m" + (f"×{c}" if c > 1 else "")
                                                         for m, d, c in items[:12]))
    L += ["", "**Ritual and planning overhead** (minutes, entries, net 分 under that name):"]
    L += [f"- {k}: {m}m ×{c}, {p}分" for k, m, c, p in r["overhead"]]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("date", nargs="?")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sunday, saturday = week_range(a.date)
    toggl, settled = load_toggl(sunday, saturday)
    r = build(sunday, a.weeks, load_points(), toggl, settled, load_credits(sunday, saturday))
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
