#!/usr/bin/env python3
"""prof_score.py — score meetings against the professionalism rules.

Reads:
  - ~/.config/prof/cal-YYYY-MM-DD.json  (snapshot from prof_snapshot.py)
  - Live Outlook calendar for the same day (via Agency MCP)
  - ~/.config/prof/arrivals.jsonl       (from /d357 lifecycle)

Writes nothing by default — emits a structured report to stdout.

Usage:
  prof_score.py                 # score today
  prof_score.py --date 2026-05-28
  prof_score.py --no-no-show    # skip the -10 penalty for missing /d357
                                # (useful while rolling out)
  prof_score.py --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prof_snapshot import fetch_events, normalize_event  # type: ignore

SNAPSHOT_DIR = Path.home() / ".config/prof"
ARRIVALS_LOG = SNAPSHOT_DIR / "arrivals.jsonl"
MY_EMAIL_DEFAULT = "jomckay@microsoft.com"

# Body length thresholds. Teams auto-invites are ~250 chars in bodyPreview.
# A "real agenda" requires more than just the join link boilerplate.
AGENDA_MIN_PREVIEW = 400        # R6
PREREAD_MIN_PREVIEW = 600       # R3a — higher bar than agenda since preread is content


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # Graph returns 7-digit fractional seconds; Python's fromisoformat (3.9)
    # only accepts up to 6. Truncate.
    m = re.match(r"^(.*\.\d{6})\d+(.*)$", s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Graph timestamps from ListCalendarView often lack tz info but are UTC
    # by default. Treat naive as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_arrivals(day: date) -> list[dict]:
    if not ARRIVALS_LOG.exists():
        return []
    out = []
    for line in ARRIVALS_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_iso(rec.get("ts"))
        if ts and ts.astimezone().date() == day:
            out.append(rec)
    return out


def _load_snapshot(day: date) -> dict | None:
    f = SNAPSHOT_DIR / f"cal-{day.isoformat()}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def _should_score(ev: dict) -> tuple[bool, str]:
    """Filter out events that don't belong in the scoring set."""
    if ev["is_cancelled"]:
        return False, "cancelled"
    resp = (ev.get("response_status") or "").lower()
    if resp == "declined":
        return False, "declined"
    if not ev.get("start") or not ev.get("end"):
        return False, "no time"
    start = _parse_iso(ev["start"])
    end = _parse_iso(ev["end"])
    if not start or not end:
        return False, "bad time"
    duration_min = (end - start).total_seconds() / 60
    if duration_min >= 8 * 60:
        return False, "all-day"
    if duration_min < 10:
        return False, "<10 min"
    # OOF-style placeholders: low attendee count + shows-as-OOF subject patterns
    subject = ev.get("subject", "").lower()
    if any(k in subject for k in ("oof", "out of office", "holiday", "ooo", "blocked")):
        return False, "OOF/blocked"
    return True, ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _match_arrival(event: dict, arrivals: list[dict]) -> tuple[dict | None, dict | None]:
    """Return (start_record, stop_record) matched to this event, or (None, None)."""
    ev_start = _parse_iso(event["start"])
    ev_end = _parse_iso(event["end"])
    if not ev_start or not ev_end:
        return None, None
    ev_subj_n = _norm(event["subject"])

    # Find start arrivals within [-15min, +30min] of event start, score by name+time
    candidates = []
    for r in arrivals:
        if r.get("kind") != "start":
            continue
        ts = _parse_iso(r["ts"])
        if not ts:
            continue
        delta_min = abs((ts - ev_start).total_seconds()) / 60
        if delta_min > 30:
            continue
        name_sim = SequenceMatcher(None, _norm(r["name"]), ev_subj_n).ratio()
        # combined score: name similarity dominates, time as tiebreaker
        score = name_sim - (delta_min / 200)
        candidates.append((score, name_sim, r))
    candidates.sort(key=lambda x: -x[0])
    start_rec = None
    for score, name_sim, r in candidates:
        if name_sim >= 0.45 or score >= 0.6:
            start_rec = r
            break

    if not start_rec:
        return None, None

    # Find matching stop: same name, after start_rec.ts
    start_ts = _parse_iso(start_rec["ts"])
    stop_rec = None
    best = 0.0
    for r in arrivals:
        if r.get("kind") != "stop":
            continue
        r_ts = _parse_iso(r["ts"])
        if not r_ts or not start_ts or r_ts < start_ts:
            continue
        sim = SequenceMatcher(None, _norm(r["name"]), _norm(start_rec["name"])).ratio()
        if sim > best and sim >= 0.6:
            best = sim
            stop_rec = r
    return start_rec, stop_rec


@dataclass
class RuleHit:
    rule: str
    points: int
    note: str


@dataclass
class EventScore:
    subject: str
    start: str
    end: str
    is_organizer: bool
    attendee_count: int
    hits: list[RuleHit] = field(default_factory=list)
    net: int = 0
    success: bool = True
    skipped: str = ""

    def add(self, rule: str, points: int, note: str) -> None:
        self.hits.append(RuleHit(rule, points, note))
        self.net += points


def score_event(
    ev: dict,
    arrivals: list[dict],
    snapshot_events: list[dict] | None,
    *,
    no_show_penalty: bool = True,
) -> EventScore:
    es = EventScore(
        subject=ev["subject"],
        start=ev["start"] or "",
        end=ev["end"] or "",
        is_organizer=ev["is_organizer"],
        attendee_count=ev["attendee_count"],
    )

    keep, reason = _should_score(ev)
    if not keep:
        es.skipped = reason
        return es

    start_dt = _parse_iso(ev["start"])
    end_dt = _parse_iso(ev["end"])

    # ── R1: same-day reschedule ────────────────────────────────────────────────
    if snapshot_events is not None:
        snap_match = next((s for s in snapshot_events if s["id"] == ev["id"]), None)
        if snap_match:
            snap_start = _parse_iso(snap_match["start"])
            if snap_start and start_dt and abs((snap_start - start_dt).total_seconds()) > 60:
                es.add("R1", -10, f"start moved from {snap_match['start']} to {ev['start']}")
        # New events that weren't in snapshot at 3am → added same-day. Don't penalize
        # (could be a legit "JM accepted a new invite today").

    # ── R2: arrival on time ────────────────────────────────────────────────────
    start_rec, stop_rec = _match_arrival(ev, arrivals)
    if start_rec:
        arr_ts = _parse_iso(start_rec["ts"])
        late_min = (arr_ts - start_dt).total_seconds() / 60 if start_dt and arr_ts else 0
        if late_min <= 4:
            pass  # on time
        elif late_min < 10:
            es.add("R2", -5, f"{late_min:.0f} min late")
        else:
            es.add("R2", -10, f"{late_min:.0f} min late")
    else:
        if no_show_penalty:
            es.add("R2", -10, "no /d357 fired (treated as no-show)")
        else:
            es.add("R2", 0, "no /d357 (skipped, --no-no-show)")

    # ── R3a: preread for large meetings ────────────────────────────────────────
    if ev["attendee_count"] > 5:
        if ev["body_preview_len"] >= PREREAD_MIN_PREVIEW:
            es.add("R3a", 10, f"preread present ({ev['body_preview_len']} chars)")
        else:
            es.add("R3a", -5, f"large meeting, no preread ({ev['body_preview_len']} chars)")

    # ── R5: end on time (I run) ────────────────────────────────────────────────
    if ev["is_organizer"] and stop_rec and end_dt:
        stop_ts = _parse_iso(stop_rec["ts"])
        if stop_ts:
            over_min = (stop_ts - end_dt).total_seconds() / 60
            if over_min > 5:
                es.add("R5", -5, f"ran {over_min:.0f} min over")
    elif ev["is_organizer"] and start_rec and not stop_rec:
        es.add("R5", -5, "no /d357 stop logged")

    # ── R6: agenda (I organized) ───────────────────────────────────────────────
    if ev["is_organizer"]:
        if ev["body_preview_len"] < AGENDA_MIN_PREVIEW:
            es.add("R6", -5, f"no agenda ({ev['body_preview_len']} chars body)")

    es.success = es.net >= 0
    return es


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--my-email", default=MY_EMAIL_DEFAULT)
    p.add_argument("--no-no-show", action="store_true",
                   help="skip -10 penalty when /d357 didn't fire")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    # Live events for target day
    live_raw = fetch_events(target)
    live = [normalize_event(e, args.my_email) for e in live_raw]

    snapshot = _load_snapshot(target)
    snapshot_events = snapshot["events"] if snapshot else None
    snapshot_taken = snapshot["snapshot_taken_at"] if snapshot else None

    arrivals = _load_arrivals(target)

    scored = [
        score_event(ev, arrivals, snapshot_events, no_show_penalty=not args.no_no_show)
        for ev in live
    ]

    counted = [s for s in scored if not s.skipped]
    success = sum(1 for s in counted if s.success)
    total = len(counted)
    sum_pts = sum(s.net for s in counted)
    pct = (100 * success / total) if total else 0.0

    if args.json:
        out = {
            "date": target.isoformat(),
            "snapshot_taken_at": snapshot_taken,
            "snapshot_present": snapshot is not None,
            "arrivals_count": len(arrivals),
            "events_counted": total,
            "success_count": success,
            "success_pct": pct,
            "sum_points": sum_pts,
            "events": [asdict(s) for s in scored],
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"\n=== Professionalism — {target} ===")
    print(f"Snapshot: {'YES (' + snapshot_taken + ')' if snapshot else 'NO (R1 reschedule detection disabled)'}")
    print(f"Arrivals logged: {len(arrivals)}")
    print(f"Events counted: {total}   Success: {success}/{total} = {pct:.0f}%   Sum: {sum_pts:+d}\n")

    for s in scored:
        start_short = (s.start or "")[11:16]
        end_short = (s.end or "")[11:16]
        if s.skipped:
            print(f"  · {start_short}-{end_short}  {s.subject[:60]:60} SKIP ({s.skipped})")
            continue
        flag = "✓" if s.success else "✗"
        org = "(I run) " if s.is_organizer else ""
        print(f"  {flag} {start_short}-{end_short}  {s.subject[:55]:55} {org}net={s.net:+d}")
        for h in s.hits:
            print(f"       {h.rule:4s} {h.points:+d}  {h.note}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
