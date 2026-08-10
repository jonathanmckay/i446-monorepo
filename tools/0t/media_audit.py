"""Passive media-use audit for /0t.

Compares what the passive trackers saw (ActivityWatch on Straylight; Apple
Screen Time's cross-device store once Full Disk Access + Share Across Devices
are on) against what Toggl says was media time (hcmc/hcmc2), for one local day.
The interesting output is the gap: passive media minutes with no corresponding
Toggl entry — the honesty signal the points system runs on.

Every source degrades gracefully: unreachable/unreadable sources are reported
as notes, never exceptions. /0t treats this step as informational.
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")

AW_BASE = "http://localhost:5600/api/0"
AW_HOST = "Straylight-Refit.local"
# Passive media time above Toggl media time by more than this is surfaced
# as a discrepancy in the /0t report.
MEDIA_GAP_THRESHOLD_MIN = 20

# Title/bundle classification. Keep deliberately narrow: the audit is about
# the known time sinks, not a general taxonomy.
MEDIA_PATTERNS = [
    ("YouTube", re.compile(r"youtube", re.I)),
    ("Audible", re.compile(r"audible", re.I)),
    ("Netflix", re.compile(r"netflix", re.I)),
    ("Twitch", re.compile(r"twitch", re.I)),
]
# iOS/macOS bundle ids for the Screen Time store, same labels.
MEDIA_BUNDLES = {
    "com.google.ios.youtube": "YouTube",
    "com.audible.iphone": "Audible",
    "com.audible.application": "Audible",
    "com.netflix.Netflix": "Netflix",
    "tv.twitch": "Twitch",
}

# Toggl media projects (hcmc, hcmc2)
MEDIA_PROJECT_IDS = {109932707, 108359992}

# Screen Time cross-device store (TCC-protected: requires Full Disk Access
# for the shell's host app; empty of other devices until Share Across
# Devices is enabled on the iPhone + Mac).
_ST_STORE_GLOB = "/var/folders/*/*/0/com.apple.ScreenTimeAgent/Store/RMAdminStore-Cloud.sqlite"


def _classify(text: str) -> str | None:
    for label, rx in MEDIA_PATTERNS:
        if rx.search(text):
            return label
    return None


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TZ)
    return start.astimezone(ZoneInfo("UTC")), (start + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _aw_get(path: str) -> object:
    with urllib.request.urlopen(f"{AW_BASE}{path}", timeout=10) as r:
        return json.load(r)


def _intervals_from_events(events: list) -> list[tuple[datetime, datetime]]:
    out = []
    for e in events:
        try:
            start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        dur = float(e.get("duration") or 0)
        if dur > 0:
            out.append((start, start + timedelta(seconds=dur)))
    return out


def _overlap_seconds(a: tuple[datetime, datetime], bs: list[tuple[datetime, datetime]]) -> float:
    total = 0.0
    for b in bs:
        lo, hi = max(a[0], b[0]), min(a[1], b[1])
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def media_minutes_from_aw(day: date) -> dict | None:
    """Per-label active (not-AFK) media minutes from ActivityWatch window titles.

    Window events count only where they intersect not-AFK time — an idle
    machine with YouTube frontmost is not watching.
    """
    lo, hi = _day_bounds_utc(day)
    # ISO offsets contain "+", which decodes to a space in a query string and
    # 500s aw-server — always URL-encode the params.
    rng = urllib.parse.urlencode(
        {"start": lo.isoformat(), "end": hi.isoformat(), "limit": -1})
    try:
        win = _aw_get(f"/buckets/aw-watcher-window_{AW_HOST}/events?{rng}")
        afk = _aw_get(f"/buckets/aw-watcher-afk_{AW_HOST}/events?{rng}")
    except Exception:
        return None
    active = _intervals_from_events(
        [e for e in afk if e.get("data", {}).get("status") == "not-afk"])
    mins: dict[str, float] = {}
    for e in win:
        data = e.get("data", {})
        label = _classify(f"{data.get('app', '')} {data.get('title', '')}")
        if label is None:
            continue
        try:
            start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        dur = float(e.get("duration") or 0)
        if dur <= 0:
            continue
        secs = _overlap_seconds((start, start + timedelta(seconds=dur)), active)
        if secs > 0:
            mins[label] = mins.get(label, 0) + secs / 60
    return {k: round(v, 1) for k, v in mins.items()}


def media_minutes_from_screentime(day: date) -> dict | None:
    """Per-label media minutes from the Screen Time cross-device store.

    Returns None when the store is unreadable (no Full Disk Access) or absent.
    Schema per DFIR documentation of RMAdminStore (USAGE tables); verified
    live only once FDA is granted — treat failures as absence.
    """
    stores = sorted(Path("/var/folders").glob(
        "*/*/0/com.apple.ScreenTimeAgent/Store/RMAdminStore-Cloud.sqlite"))
    if not stores:
        return None
    apple = 978307200  # 2001-01-01 epoch offset
    lo = datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TZ).timestamp() - apple
    hi = lo + 86400
    mins: dict[str, float] = {}
    try:
        con = sqlite3.connect(f"file:{stores[-1]}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            """SELECT ti.ZBUNDLEIDENTIFIER, SUM(ti.ZTOTALTIMEINSECONDS)
               FROM ZUSAGETIMEDITEM ti
               JOIN ZUSAGECATEGORY c ON ti.ZCATEGORY = c.Z_PK
               JOIN ZUSAGEBLOCK b ON c.ZBLOCK = b.Z_PK
               WHERE b.ZSTARTDATE >= ? AND b.ZSTARTDATE < ?
               GROUP BY 1""", (lo, hi)).fetchall()
        con.close()
    except Exception:
        return None
    for bundle, secs in rows:
        label = MEDIA_BUNDLES.get(bundle or "")
        if label and secs:
            mins[label] = mins.get(label, 0) + secs / 60
    return {k: round(v, 1) for k, v in mins.items()}


def toggl_media_minutes(entries: list, day: date,
                        entry_local_dt) -> int:
    """Minutes of Toggl hcmc/hcmc2 entries that STARTED on the local day."""
    total = 0
    for e in entries:
        if e.get("project_id") not in MEDIA_PROJECT_IDS:
            continue
        ldt = entry_local_dt(e)
        if ldt is None or ldt.date() != day:
            continue
        dur = e.get("duration", 0)
        if dur > 0:
            total += dur // 60
    return total


def media_audit(day: date, toggl_entries: list, entry_local_dt) -> dict:
    """The /0t step: passive vs Toggl media minutes for one local day."""
    notes = []
    aw = media_minutes_from_aw(day)
    if aw is None:
        notes.append("ActivityWatch unreachable (runs on Straylight only)")
    st = media_minutes_from_screentime(day)
    if st is None:
        notes.append("Screen Time store unreadable (needs Full Disk Access + Share Across Devices)")

    # Merge sources per label, taking the max per source-label (they overlap
    # only if the same app is measured twice, e.g. desktop YouTube in both
    # AW and Mac Screen Time — max avoids double counting, sum would inflate).
    passive: dict[str, float] = {}
    for src in (aw or {}), (st or {}):
        for k, v in src.items():
            passive[k] = max(passive.get(k, 0), v)

    passive_total = round(sum(passive.values()))
    toggl_total = toggl_media_minutes(toggl_entries, day, entry_local_dt)
    gap = passive_total - toggl_total
    result = {
        "passive": passive,
        "passive_total_min": passive_total,
        "toggl_media_min": toggl_total,
        "gap_min": gap,
        "flagged": gap > MEDIA_GAP_THRESHOLD_MIN,
    }
    if notes:
        result["notes"] = notes
    return result
