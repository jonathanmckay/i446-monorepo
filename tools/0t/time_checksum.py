"""time_checksum.py — /0t's check that a day's Toggl adds up (JM 2026-11-06).

Flags gaps over 5m, more than ~15m missing from the day, and any non-sleep task
running over 2h without a review. Report only: it never writes to Neon and
never stops 0t being marked done. Gap finding and fills are /2n's
(tools/2n/2n-coverage.py); this owns only the thresholds and the long-entry check.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
from pathlib import Path

MIN_GAP_MIN = 6            # "over 5m"; /2n's minutes are rounded and its filter is >=
MAX_MISSING_MIN = 15
MAX_ENTRY_MIN = 120
SEAM_MIN = 1               # same task with a seam this small is one sitting
LIVE_REACH_DAYS = 85       # v9 stops at ~90 days; older days stay on the nightly store
STATE_PATH = Path.home() / ".local/state/jm/0t-gaps.json"   # NOT /2n's: an unattended
# run must never renumber a list JM is working from (fills can delete placeholders)
_COV_PATH = Path(__file__).resolve().parent.parent / "2n" / "2n-coverage.py"


def load_coverage(tz):
    spec = importlib.util.spec_from_file_location("coverage_2n", _COV_PATH)
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)
    cov.TZ = tz       # a /travel day is cut at local midnights, like the rest of 0t
    return cov


def long_entries(day: dt.date, entries: list[dict], cov) -> list[dict]:
    """Non-sleep tasks over MAX_ENTRY_MIN within the day. Back-to-back entries
    with the same description and project count as one: janus splits a long
    entry per block, so `interview prep` 120+120+60 is 5h, not three clean rows."""
    d0 = dt.datetime.combine(day, dt.time(0, 0), cov.TZ)
    d1 = d0 + dt.timedelta(days=1)
    clipped = sorted(
        ({**e, "start": max(e["start"], d0), "end": min(e["end"], d1)} for e in entries
         if e["end"] > d0 and e["start"] < d1
         and e.get("code") != cov.SLEEP_CODE and not cov.is_placeholder(e)),
        key=lambda e: e["start"])
    runs: list[dict] = []
    for e in clipped:
        last = runs[-1] if runs else None
        if (last and (last["desc"], last["code"]) == (e["desc"], e["code"])
                and e["start"] - last["end"] <= dt.timedelta(minutes=SEAM_MIN)):
            last["end"] = max(last["end"], e["end"])
        else:
            runs.append(dict(e))
    out = []
    for r in runs:
        minutes = round((r["end"] - r["start"]).total_seconds() / 60)
        if minutes > MAX_ENTRY_MIN:
            out.append({"desc": r["desc"], "code": r["code"], "minutes": minutes,
                        "start": r["start"].strftime("%H:%M"), "end": r["end"].strftime("%H:%M")})
    return out


def check(day: dt.date, entries: list[dict], cov, *, use_calendar: bool) -> tuple[dict, dict | None]:
    """(report, /2n audit). The audit is None when Toggl has nothing for the
    day: an outage or a day past the store must not read as 24h missing."""
    d0 = dt.datetime.combine(day, dt.time(0, 0), cov.TZ)
    d1 = d0 + dt.timedelta(days=1)
    if not any(e["end"] > d0 and e["start"] < d1 for e in entries):
        return {"date": day.isoformat(), "status": "no data", "ok": None}, None

    def run(calendar: bool) -> dict:
        # floor above 24h: /2n hides gaps on a day over its floor; here every one counts
        return cov.audit(day, day, entries, floor_min=cov.DAY_MIN + 1,
                         min_gap_min=MIN_GAP_MIN, use_calendar=calendar)
    try:
        a = run(use_calendar)
    except Exception:  # noqa: BLE001 — suggestions are a nicety, the numbers aren't
        a = run(False)
    d = a["days"][0]
    missing = cov.DAY_MIN - d["coverage_min"]
    gaps = [{"id": g["id"], "start": g["start"].strftime("%H:%M"), "end": g["end"].strftime("%H:%M"),
             "minutes": g["minutes"], "fill": g["suggest"]["fill"], "code": g["suggest"]["code"],
             "confidence": g["suggest"]["confidence"]} for g in d["gaps"]]
    long_ = long_entries(day, entries, cov)
    coverage_ok = missing <= MAX_MISSING_MIN and not gaps
    return {"date": day.isoformat(), "status": "checked", "ok": coverage_ok and not long_,
            "coverage_ok": coverage_ok, "long_ok": not long_, "missing_min": missing,
            "max_missing_min": MAX_MISSING_MIN, "gaps": gaps, "seams": d["n_seams"],
            "long_entries": long_}, a


def run(day: dt.date, tz, *, interactive: bool) -> dict:
    """Checksum for `day`. Reads live Toggl within v9's reach: settled days come
    from the nightly store, so a recheck after a fill would show the filled gap
    and invite a second fill. interactive = someone is reading: calendar
    suggestions and a fill list (always written, so stale numbers can't survive
    a clean recheck). Unattended runs do neither."""
    cov = load_coverage(tz)
    live = (dt.datetime.now(tz).date() - day).days <= LIVE_REACH_DAYS
    prior = os.environ.get("TOGGL_LIVE")
    if live:
        os.environ["TOGGL_LIVE"] = "1"
    try:
        entries = cov.fetch_entries(day, day)
    finally:
        if live:
            if prior is None:
                os.environ.pop("TOGGL_LIVE", None)
            else:
                os.environ["TOGGL_LIVE"] = prior
    report, audit = check(day, entries, cov, use_calendar=interactive)
    if interactive and audit is not None:
        cov.STATE_PATH = STATE_PATH
        cov.save_state(audit)
    return report
