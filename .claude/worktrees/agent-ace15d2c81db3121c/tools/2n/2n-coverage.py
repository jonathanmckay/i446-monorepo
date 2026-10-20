#!/usr/bin/env python3
"""2n-coverage.py — monthly Toggl coverage audit (/2n).

For the most recent finished month (or the month given), find every local
day whose Toggl entries cover less than 23:45 of the 24h, list each gap,
and suggest what the gap probably was:

  * a calendar event overlapping the gap  → that meeting (@i9 / @m5x2 / …)
  * night-time gap next to a 睡觉 entry     → 睡觉
  * same project on both sides of the gap  → continue that entry
  * otherwise                               → ? (needs a human)

Read-only against Toggl and Google Calendar. `--write` also drops a
markdown report into the vault.

Every listed gap gets a sequential number; `--fill 1,3,5` then creates the
suggested Toggl entries for those numbers (from the last audit's saved
state), and `--fill "2=family time @xk87"` overrides one.

Usage:
    2n-coverage.py [YYYY-MM] [--floor-min 1425] [--min-gap 5]
                   [--no-calendar] [--json] [--write]
    2n-coverage.py --fill "1,3,5=睡觉,7=family time @xk87"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

MONOREPO = Path.home() / "i446-monorepo"
sys.path.insert(0, str(MONOREPO))
sys.path.insert(0, str(MONOREPO / "tools/tg"))

TZ = ZoneInfo("America/Los_Angeles")
DAY_MIN = 24 * 60
DEFAULT_FLOOR_MIN = 23 * 60 + 45        # 23:45 of coverage per day
DAY_END_SLACK_MIN = 1                   # the day-barrier rule ends entries at 23:59,
                                        # so the last minute is never a real gap
DEFAULT_MIN_GAP_MIN = 5                 # seams shorter than this are noise, not gaps
LONG_GAP_MIN = 240                      # ≥ this: list the events inside, don't pick one
SLEEP_CODE = "睡觉"
NIGHT_START, NIGHT_END = 21, 7          # local hours treated as sleep-plausible
REPORT_DIR = Path.home() / "vault/g245/reviews"
STATE_PATH = Path.home() / ".local/state/jm/2n-last.json"   # last audit's numbered gaps, for --fill

# Calendar → project resolution, mirroring tools/janus/mobile.py's copy
# (which itself mirrors tools/tg/janus.py). Not imported: both modules pull
# in Flask / prompt_toolkit at import time.
CALENDAR_PROJECT_MAP = {
    "m5x2 Cal": "m5x2",
    "3494 House": "m5x2",
    "CAIS School": "xk87",
    "Habits": "hcm",
    "lx@m5c7.com": "xk88",
    "lxu888": "xk88",
    "Calendar": "infra",
    "jonathan.b.mckay@gmail.com": "infra",
    "Outlook": "i9",
    "MSFT (Slow Sync)": "i9",
}
EVENT_KEYWORDS = [
    (["1:1", "1|1", "standup", "sprint", "retro", "slt", "metrics"], "i9"),
    (["m5x2", "property", "tenant", "lease", "appfolio"], "m5x2"),
    (["school", "cais", "pta", "ptc"], "xk87"),
    (["bball", "basketball", "gym", "hiit"], "hcbp"),
]


# ── Toggl ──────────────────────────────────────────────────────────────────────

def _ensure_toggl_key() -> None:
    """toggl_server.config reads TOGGL_API_KEY at import; a plain shell
    doesn't export it, so fall back to the MCP server's env block."""
    if os.environ.get("TOGGL_API_KEY"):
        return
    try:
        cfg = json.loads((Path.home() / ".claude.json").read_text())
        env = cfg["mcpServers"]["toggl_server"].get("env", {})
        for k in ("TOGGL_API_KEY", "TOGGL_WORKSPACE_ID"):
            if env.get(k):
                os.environ.setdefault(k, str(env[k]))
    except (OSError, KeyError, ValueError):
        pass


def fetch_entries(first: _dt.date, last: _dt.date) -> list[dict]:
    _ensure_toggl_key()
    from mcp.toggl_server import toggl_api  # noqa: E402
    from mcp.toggl_server.config import PROJECT_NAMES  # noqa: E402
    # /me/time_entries caps the entries per request (a whole month blows
    # past it and silently drops the oldest days), so fetch in 7-day chunks
    # and dedup by id.
    raw_by_id: dict = {}
    chunk_start = first - _dt.timedelta(days=1)
    stop = last + _dt.timedelta(days=2)
    while chunk_start < stop:
        chunk_end = min(chunk_start + _dt.timedelta(days=7), stop)
        for e in toggl_api.get_entries(chunk_start.isoformat(), chunk_end.isoformat()) or []:
            raw_by_id[e.get("id")] = e
        chunk_start = chunk_end
    now = _dt.datetime.now(TZ)
    out = []
    for e in raw_by_id.values():
        s = e.get("start")
        if not s:
            continue
        start = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
        dur = e.get("duration", 0)
        if dur is None or dur < 0:          # running timer
            end = now
        else:
            end = start + _dt.timedelta(seconds=dur)
        if end <= start:
            continue
        pid = e.get("project_id")
        out.append({
            "start": start, "end": end,
            "desc": (e.get("description") or "").strip(),
            "code": PROJECT_NAMES.get(pid, "") if pid else "",
        })
    out.sort(key=lambda x: x["start"])
    return out


# ── Coverage (pure) ────────────────────────────────────────────────────────────

def month_range(arg: str | None, today: _dt.date | None = None) -> tuple[_dt.date, _dt.date]:
    """(first, last) of the month given as YYYY-MM, or of the most recent
    finished month when omitted."""
    today = today or _dt.date.today()
    if arg:
        y, m = (int(x) for x in arg.split("-")[:2])
        first = _dt.date(y, m, 1)
    else:
        first = _dt.date(today.year, today.month, 1)
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    nxt = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return first, nxt - _dt.timedelta(days=1)


def day_gaps(day: _dt.date, entries: list[dict], min_gap_min: int = 1) -> list[dict]:
    """Uncovered intervals within the local day, after merging overlaps.
    Each gap: {start, end, minutes, prev, next} where prev/next are the
    nearest entries on either side (or None)."""
    d0 = _dt.datetime.combine(day, _dt.time(0, 0), TZ)
    d1 = d0 + _dt.timedelta(days=1) - _dt.timedelta(minutes=DAY_END_SLACK_MIN)
    clipped = []
    for e in entries:
        s, t = max(e["start"], d0), min(e["end"], d1)
        if t > s:
            clipped.append((s, t, e))
    clipped.sort(key=lambda x: x[0])
    merged: list[list] = []                      # [start, end, last_entry]
    for s, t, e in clipped:
        if merged and s <= merged[-1][1]:
            if t > merged[-1][1]:
                merged[-1][1], merged[-1][2] = t, e
        else:
            merged.append([s, t, e])
    gaps = []
    cursor, prev = d0, None
    for s, t, e in merged:
        if s > cursor:
            gaps.append({"start": cursor, "end": s, "prev": prev, "next": _first_at(clipped, s)})
        cursor, prev = max(cursor, t), e
    if cursor < d1:
        gaps.append({"start": cursor, "end": d1, "prev": prev, "next": None})
    out = []
    for g in gaps:
        g["minutes"] = round((g["end"] - g["start"]).total_seconds() / 60)
        if g["minutes"] >= min_gap_min:
            out.append(g)
    return out


def _first_at(clipped, when):
    for s, _, e in clipped:
        if s == when:
            return e
    return None


def day_coverage_min(day: _dt.date, entries: list[dict]) -> int:
    """Covered minutes out of DAY_MIN; the DAY_END_SLACK_MIN tail counts as
    covered (see the constant)."""
    return DAY_MIN - sum(g["minutes"] for g in day_gaps(day, entries, min_gap_min=0))


# ── Suggestions ────────────────────────────────────────────────────────────────

def gcal_project_code(event: dict) -> str:
    title_lower = (event.get("title") or "").lower()
    if "m5x2" in title_lower:
        return "m5x2"
    code = CALENDAR_PROJECT_MAP.get(event.get("calendar", ""))
    if code:
        return code
    for keywords, kw_code in EVENT_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return kw_code
    return ""


def _overlap_min(a0, a1, b0, b1) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 60)


def _is_night(g: dict) -> bool:
    """Gap lies wholly inside the sleep-plausible band (21:00 → 07:00)."""
    s, e = g["start"], g["end"] - _dt.timedelta(seconds=1)
    in_band = lambda t: t.hour >= NIGHT_START or t.hour < NIGHT_END  # noqa: E731
    return in_band(s) and in_band(e) and (e - s) <= _dt.timedelta(hours=10)


def suggest(g: dict, events: list[dict]) -> dict:
    """{'fill': str, 'code': str, 'reason': str, 'confidence': 'high'|'med'|'low'}"""
    prev, nxt = g.get("prev"), g.get("next")
    # 1. calendar: the event that covers the most of the gap
    best, best_ov, inside = None, 0.0, []
    for ev in events:
        if ev.get("all_day") or ev.get("transparency") == "transparent":
            continue
        ov = _overlap_min(g["start"], g["end"], ev["start_dt"], ev["end_dt"])
        if ov > 0:
            inside.append((ev["start_dt"], ev["title"], gcal_project_code(ev)))
        if ov > best_ov:
            best, best_ov = ev, ov
    if g["minutes"] >= LONG_GAP_MIN:
        # A multi-hour hole is not one thing. Name what the calendar shows
        # inside it (and sleep at the edges) so it can be filled by hand.
        parts = [f"{t.strftime('%H:%M')} {title}" + (f" @{c}" if c else "")
                 for t, title, c in sorted(inside)[:6]]
        if _is_night(g) or (prev is None and nxt is None):
            parts.insert(0, "睡觉 overnight")
        return {"fill": "multiple: " + ("; ".join(parts) if parts else "no calendar events"),
                "code": "", "reason": f"{g['minutes'] // 60}h hole", "confidence": "low"}
    if best is not None and best_ov >= min(g["minutes"], 10) * 0.5:
        code = gcal_project_code(best) or "i9"
        return {"fill": best["title"], "code": code,
                "reason": f"calendar ({best['calendar']}, {int(best_ov)}m overlap)",
                "confidence": "high" if best_ov >= 0.8 * g["minutes"] else "med"}
    # 2. sleep
    touches_sleep = any(e and e.get("code") == SLEEP_CODE for e in (prev, nxt))
    if _is_night(g) and (touches_sleep or prev is None or nxt is None):
        return {"fill": SLEEP_CODE, "code": SLEEP_CODE,
                "reason": "night gap next to 睡觉" if touches_sleep else "night gap at day edge",
                "confidence": "high" if touches_sleep else "med"}
    # 3. same project on both sides
    if prev and nxt and prev.get("code") and prev.get("code") == nxt.get("code"):
        same_desc = prev.get("desc") == nxt.get("desc")
        return {"fill": prev.get("desc") or prev["code"], "code": prev["code"],
                "reason": "same entry both sides" if same_desc else f"@{prev['code']} both sides",
                "confidence": "high" if same_desc else "med"}
    # 4. short gap: extend the neighbour
    if g["minutes"] <= 10 and (prev or nxt):
        n = prev or nxt
        return {"fill": n.get("desc") or n.get("code") or "?", "code": n.get("code", ""),
                "reason": "short gap; extend " + ("previous" if prev else "next") + " entry",
                "confidence": "low"}
    return {"fill": "?", "code": "", "reason": "no signal", "confidence": "low"}


# ── Calendar ───────────────────────────────────────────────────────────────────

def fetch_day_events(day: _dt.date) -> list[dict]:
    """gcal_client caches per start-day, so one call per gap-day is cheap
    on reruns. Best-effort: any failure → no calendar suggestions."""
    try:
        import gcal_client  # noqa: E402
    except Exception as e:  # noqa: BLE001
        print(f"WARN gcal_client unavailable: {e}", file=sys.stderr)
        return []
    d0 = _dt.datetime.combine(day, _dt.time(0, 0), TZ)
    try:
        return gcal_client.list_events(d0, d0 + _dt.timedelta(days=1))
    except Exception as e:  # noqa: BLE001
        print(f"WARN calendar fetch failed for {day}: {e}", file=sys.stderr)
        return []


# ── Report ─────────────────────────────────────────────────────────────────────

def audit(first: _dt.date, last: _dt.date, entries: list[dict], *,
          floor_min: int = DEFAULT_FLOOR_MIN, min_gap_min: int = DEFAULT_MIN_GAP_MIN,
          use_calendar: bool = True, event_fetcher=fetch_day_events) -> dict:
    days = []
    d = first
    gap_no = 0
    while d <= last:
        cov = day_coverage_min(d, entries)
        gaps = day_gaps(d, entries, min_gap_min) if cov < floor_min else []
        events = event_fetcher(d) if (use_calendar and gaps) else []
        numbered = []
        for g in gaps:
            gap_no += 1
            numbered.append({**g, "id": gap_no, "suggest": suggest(g, events)})
        days.append({
            "date": d.isoformat(), "coverage_min": cov, "short": cov < floor_min,
            # A day under the floor only because of sub-threshold seams is
            # fine (JM 2026-09-16); it is reported in one line, not listed.
            "seams_only": cov < floor_min and not gaps,
            "n_seams": len(day_gaps(d, entries, min_gap_min=1)) - len(gaps) if cov < floor_min else 0,
            "gaps": numbered,
        })
        d += _dt.timedelta(days=1)
    with_gaps = [x for x in days if x["gaps"]]
    return {
        "month": first.strftime("%Y-%m"), "first": first.isoformat(), "last": last.isoformat(),
        "floor_min": floor_min, "min_gap_min": min_gap_min, "n_days": len(days),
        "n_short": len(with_gaps), "n_seams_only": sum(1 for x in days if x["seams_only"]),
        "n_gaps": gap_no,
        "uncovered_min": sum(DAY_MIN - x["coverage_min"] for x in days),
        "days": days,
    }


def save_state(a: dict) -> None:
    """Persist the numbered gaps so `--fill 1,3,5` can act on them later."""
    gaps = []
    for day in a["days"]:
        for g in day["gaps"]:
            gaps.append({"id": g["id"], "date": day["date"],
                         "start": g["start"].isoformat(), "end": g["end"].isoformat(),
                         "minutes": g["minutes"], "fill": g["suggest"]["fill"],
                         "code": g["suggest"]["code"], "confidence": g["suggest"]["confidence"]})
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"month": a["month"], "gaps": gaps}, ensure_ascii=False, indent=1))


def parse_fill_spec(spec: str) -> dict[int, tuple[str | None, str | None]]:
    """'1,3,5' → use each gap's suggestion; '2=family time @xk87' overrides
    the description (and project, if an @code is given); '2=@睡觉' keeps the
    suggested text but forces the project. Items are comma-separated, so an
    override description cannot itself contain a comma."""
    out: dict[int, tuple[str | None, str | None]] = {}
    for item in filter(None, (x.strip() for x in spec.split(","))):
        if "=" in item:
            n, rest = item.split("=", 1)
            desc, code = rest.strip(), None
            if "@" in desc:
                desc, code = desc.rsplit("@", 1)
                desc, code = desc.strip(), code.strip()
            out[int(n)] = (desc or None, code or None)
        else:
            out[int(item)] = (None, None)
    return out


def fill_gaps(spec: str) -> int:
    """Create Toggl entries for the numbered gaps of the last audit."""
    if not STATE_PATH.exists():
        print("ERROR: no saved audit; run the report first", file=sys.stderr)
        return 1
    state = json.loads(STATE_PATH.read_text())
    by_id = {g["id"]: g for g in state["gaps"]}
    _ensure_toggl_key()
    from mcp.toggl_server import toggl_api  # noqa: E402
    from mcp.toggl_server.config import PROJECT_MAP  # noqa: E402
    wanted = parse_fill_spec(spec)
    created, skipped = [], []
    for n, (desc_override, code_override) in sorted(wanted.items()):
        g = by_id.get(n)
        if g is None:
            skipped.append(f"#{n}: not in last audit ({state['month']})")
            continue
        desc = desc_override or g["fill"]
        code = code_override or g["code"]
        if desc.startswith("?") or desc.startswith("multiple:"):
            skipped.append(f"#{n} {g['date']} {g['start'][11:16]}–{g['end'][11:16]}: no usable "
                           f"suggestion — pass '{n}=<description> @<code>'")
            continue
        if code == SLEEP_CODE or not desc.strip():
            desc = desc or SLEEP_CODE
        pid = PROJECT_MAP.get(code) if code else None
        if code and pid is None:
            skipped.append(f"#{n}: unknown project code @{code}")
            continue
        start = _dt.datetime.fromisoformat(g["start"])
        end = _dt.datetime.fromisoformat(g["end"])
        dur = int((end - start).total_seconds())
        try:
            toggl_api.create_entry(desc, start.isoformat(), end.isoformat(), dur, project_id=pid)
        except Exception as e:  # noqa: BLE001
            skipped.append(f"#{n}: Toggl error {e}")
            continue
        created.append(f"#{n} {g['date']} {g['start'][11:16]}–{g['end'][11:16]} {desc}"
                       + (f" @{code}" if code else ""))
    for line in created:
        print("created", line)
    for line in skipped:
        print("skipped", line)
    print(f"{len(created)} created, {len(skipped)} skipped")
    return 0 if not skipped else 2


def _hm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _t(dtv: _dt.datetime) -> str:
    return dtv.strftime("%H:%M")


def render_md(a: dict) -> str:
    first = _dt.date.fromisoformat(a["first"])
    lines = [f"## {first.strftime('%B %Y')} Toggl coverage (floor {_hm(a['floor_min'])})", ""]
    seams = [d for d in a["days"] if d["seams_only"]]
    lines.append(f"**{a['n_short']} of {a['n_days']} days have gaps ≥ {a['min_gap_min']}m** "
                 f"({a['n_gaps']} gaps) · {_hm(a['uncovered_min'])} uncovered in total")
    if seams:
        lines.append(f"Under the floor from sub-{a['min_gap_min']}m seams only (fine, not listed): "
                     + ", ".join(_dt.date.fromisoformat(d["date"]).strftime("%-m/%-d") for d in seams))
    lines.append("")
    if not a["n_short"]:
        lines.append("Nothing to fill.")
        return "\n".join(lines)
    lines.append("| # | Day | Gap | Min | Suggested fill | Why | Conf |")
    lines.append("|---|---|---|---|---|---|---|")
    for day in a["days"]:
        d = _dt.date.fromisoformat(day["date"])
        for g in day["gaps"]:
            s = g["suggest"]
            tag = f" @{s['code']}" if s["code"] else ""
            lines.append(f"| {g['id']} | {d.strftime('%a %-m/%-d')} | {_t(g['start'])}–{_t(g['end'])} | "
                         f"{g['minutes']} | {s['fill']}{tag} | {s['reason']} | {s['confidence']} |")
    lines.append("")
    lines.append("### Context")
    for day in a["days"]:
        if not day["gaps"]:
            continue
        d = _dt.date.fromisoformat(day["date"])
        lines.append(f"\n**{d.strftime('%a %-m/%-d')}** · covered {_hm(day['coverage_min'])}")
        for g in day["gaps"]:
            prev = f"after `{g['prev']['desc'] or g['prev']['code']}`" if g["prev"] else "day start"
            nxt = f"before `{g['next']['desc'] or g['next']['code']}`" if g["next"] else "day end"
            lines.append(f"- #{g['id']} {_t(g['start'])}–{_t(g['end'])}: {prev}, {nxt}")
    lines.append("")
    lines.append("Batch fill: `/2n fill 1,3,5` uses the suggestions; `/2n fill 2=family time @xk87` "
                 "overrides one. `?` and `multiple:` rows need an override.")
    return "\n".join(lines)


def _jsonable(a: dict) -> dict:
    def conv(o):
        if isinstance(o, _dt.datetime):
            return o.isoformat()
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items() if k not in ("prev", "next")}
        if isinstance(o, list):
            return [conv(x) for x in o]
        return o
    return conv(a)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("month", nargs="?", help="YYYY-MM (default: last finished month)")
    ap.add_argument("--floor-min", type=int, default=DEFAULT_FLOOR_MIN)
    ap.add_argument("--min-gap", type=int, default=DEFAULT_MIN_GAP_MIN,
                    help="list only gaps at least this long (min); coverage totals count everything")
    ap.add_argument("--no-calendar", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help="also write vault/g245/reviews/YYYY-MM-2n.md")
    ap.add_argument("--fill", metavar="SPEC",
                    help="create Toggl entries for numbered gaps of the LAST audit, e.g. "
                         "'1,3,5' or '2=family time @xk87' (no fetch/report)")
    args = ap.parse_args()

    if args.fill:
        return fill_gaps(args.fill)

    first, last = month_range(args.month)
    try:
        entries = fetch_entries(first, last)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Toggl fetch failed: {e}", file=sys.stderr)
        return 1
    if not entries:
        print(f"ERROR: no Toggl entries returned for {first}..{last} "
              "(v9 only reaches back ~90 days)", file=sys.stderr)
        return 1
    a = audit(first, last, entries, floor_min=args.floor_min, min_gap_min=args.min_gap,
              use_calendar=not args.no_calendar)
    save_state(a)
    if args.json:
        print(json.dumps(_jsonable(a), ensure_ascii=False, indent=1))
    else:
        md = render_md(a)
        print(md)
        if args.write:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = REPORT_DIR / f"{a['month']}-2n.md"
            fm = (f"---\ntitle: \"{first.strftime('%B %Y')} Toggl Coverage\"\n"
                  f"date: {last.isoformat()}\ntype: review\ntags: [g245, 2n, toggl]\n"
                  f"source: /2n\n---\n\n")
            path.write_text(fm + md + "\n", encoding="utf-8")
            print(f"\nwritten → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
