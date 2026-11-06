"""time_checksum.py — /0t's check that a day's Toggl adds up (JM 2026-11-06).

A hole over 5m is an error. A non-sleep task running over 2h without a review is
a warning that comes back until JM overrides it (/0t ok <date>). Holes of 5m or
less are fine, however many there are (JM 2026-11-06). It never writes to Neon
and never stops 0t being marked done; in a Claude session (0t-fast.py --session)
a hole exits 3 and an open warning exits 4. Gap finding and fills are /2n's
(tools/2n/2n-coverage.py); this owns the threshold, the long-entry check,
overrides and render().
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import socket
from pathlib import Path

MIN_GAP_MIN = 6            # "over 5m"; /2n's minutes are rounded and its filter is >=
MAX_ENTRY_MIN = 120
SEAM_MIN = 1               # same task with a seam this small is one sitting
LIVE_REACH_DAYS = 85       # v9 stops at ~90 days; older days stay on the nightly store
STATE_PATH = Path.home() / ".local/state/jm/0t-gaps.json"   # NOT /2n's: an unattended
# run must never renumber a list JM is working from (fills can delete placeholders)
# One writer per file, merged on read: Syncthing last-writer-wins clobbers shared
# state files (see mark-completed.py's mirror). Losing one costs a repeat warning.
OVERRIDE_DIR = Path.home() / "vault/g245"
_COV_PATH = Path(__file__).resolve().parent.parent / "2n" / "2n-coverage.py"


def load_coverage(tz):
    spec = importlib.util.spec_from_file_location("coverage_2n", _COV_PATH)
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)
    cov.TZ = tz       # a /travel day is cut at local midnights, like the rest of 0t
    return cov


def _override_path() -> Path:
    host = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")
    return OVERRIDE_DIR / f"0t-overrides-{host}.json"


def load_overrides(day: dt.date) -> tuple[set, list[str]]:
    """(override keys for the day, unreadable files). An unreadable file only
    brings its warnings back; it must never stop the hole check."""
    keys: set = set()
    bad: list[str] = []
    for p in sorted(OVERRIDE_DIR.glob("0t-overrides-*.json")):
        try:
            keys.update(json.loads(p.read_text(encoding="utf-8")).get(day.isoformat(), []))
        except (OSError, ValueError, AttributeError, TypeError):
            bad.append(p.name)
    return keys, bad


def record_overrides(day: dt.date, keys: list[str]) -> None:
    """Raises on a malformed file rather than treat it as empty: that would
    wipe this host's history."""
    path = _override_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    data.setdefault(day.isoformat(), []).extend(keys)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
            # Minutes in the key: a task that grows warns again. UTC: a /travel
            # zone mustn't undo an override.
            key = "|".join((r["start"].astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                            r["desc"], r["code"], str(minutes)))
            out.append({"desc": r["desc"], "code": r["code"], "minutes": minutes, "key": key,
                        "start": r["start"].strftime("%H:%M"), "end": r["end"].strftime("%H:%M")})
    return out


def check(day: dt.date, entries: list[dict], cov, *, use_calendar: bool,
          overrides: set | frozenset = frozenset()) -> tuple[dict, dict | None]:
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
             "confidence": g["suggest"]["confidence"],
             "after": (g.get("prev") or {}).get("desc", ""),
             "before": (g.get("next") or {}).get("desc", "")} for g in d["gaps"]]
    long_ = long_entries(day, entries, cov)
    for x in long_:
        x["overridden"] = x["key"] in overrides
    return {"date": day.isoformat(), "status": "checked", "ok": not gaps,
            "needs_override": any(not x["overridden"] for x in long_),
            "missing_min": missing, "gaps": gaps, "long_entries": long_}, a


def _usable(g: dict) -> bool:
    return not g["fill"].startswith(("?", "multiple:"))


def render(report: dict | str) -> str:
    """What JM reads: the holes, the long tasks, and the exact commands."""
    if isinstance(report, str):
        return f"Time checksum did not run: {report}"
    day = report["date"]
    if report.get("status") == "no data":
        return f"Time checksum {day}: Toggl returned nothing, so the day is unchecked"
    gaps = report["gaps"]
    all_long = report["long_entries"]
    long_ = [x for x in all_long if not x["overridden"]]
    done = [x for x in all_long if x["overridden"]]
    L = [f"Overridden: {x['start']}–{x['end']} {x['minutes']}m {x['desc']} @{x['code']}"
         for x in report.get("recorded", [])]
    for name in report.get("unreadable_overrides", []):
        L.append(f"⚠️ {name} is unreadable, so its overrides are ignored")
    if not gaps and not long_:
        note = f", {len(done)} over 2h overridden" if done else ", no task over 2h"
        return "\n".join(L + [f"Time checksum {day}: ✅ no hole over 5m{note}"])
    head = []
    if gaps:
        head.append(f"❌ {len(gaps)} hole{'s'[:len(gaps) != 1]} over 5m")
    if long_:
        head.append(f"⚠️ {len(long_)} task{'s'[:len(long_) != 1]} over 2h need{'s'[:len(long_) == 1]} your override")
    L.append(f"Time checksum {day}: " + ", ".join(head))
    if done:
        L.append(f"({len(done)} over 2h already overridden)")
    if gaps:
        L += ["", "Missing blocks:"]
        for g in gaps:
            side = " · ".join(x for x in (g["after"] and f"after {g['after']}",
                                          g["before"] and f"before {g['before']}") if x)
            what = f'{g["fill"]} @{g["code"]} ({g["confidence"]})' if _usable(g) else "no suggestion"
            L.append(f"  #{g['id']} {g['start']}–{g['end']} {g['minutes']}m → {what}" + (f"  [{side}]" if side else ""))
    if long_:
        L += ["", "Warnings, over 2h with no review:"]
        for x in long_:
            L.append(f"  {x['start']}–{x['end']} {x['minutes']}m {x['desc']} @{x['code']} → "
                     "split it where you stopped or switched, or relabel what it was")
    L += ["", "Fix:"]
    ready = [str(g["id"]) for g in gaps if _usable(g)]
    if ready:
        L.append(f"  /0t fill {','.join(ready)}    create the suggested entries")
    for g in gaps:
        if not _usable(g):
            L.append(f"  /0t fill {g['id']}=<what it was> @<code>")
    if ready:
        L.append("  /0t fill N=<what it was> @<code>    override any suggestion")
    if long_:
        L.append(f"  /0t ok {day}    keep all {len(long_)} as they are (yours to say, not Claude's)")
    L.append(f"  /0t --check {day}    recheck when done")
    return "\n".join(L)


def _apply_override(day: dt.date, report: dict) -> None:
    pending = [x for x in report.get("long_entries", []) if not x["overridden"]]
    if not pending:
        return
    record_overrides(day, [x["key"] for x in pending])
    for x in pending:
        x["overridden"] = True
    report["needs_override"] = False
    report["recorded"] = pending


def run(day: dt.date, tz, *, interactive: bool, ok_long: bool = False) -> dict:
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
    overrides, unreadable = load_overrides(day)
    report, audit = check(day, entries, cov, use_calendar=interactive, overrides=overrides)
    if unreadable:
        report["unreadable_overrides"] = unreadable
    if ok_long:
        _apply_override(day, report)
    if interactive and audit is not None:
        cov.STATE_PATH = STATE_PATH
        cov.save_state(audit)
    return report
