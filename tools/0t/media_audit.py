"""Passive media-use audit for /0t.

Compares what the passive trackers saw (ActivityWatch on Straylight; Apple
Screen Time's cross-device store on Ix, once its remote-user FDA + sharing
are on) against what Toggl says was media time (hcmc/hcmc2), for one local day.
The interesting output is the gap: passive media minutes with no corresponding
Toggl entry — the honesty signal the points system runs on.

Every source degrades gracefully: unreachable/unreadable sources are reported
as notes, never exceptions. /0t treats this step as informational.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
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
    ("Memeorandum", re.compile(r"memeorandum", re.I)),
    ("Techmeme", re.compile(r"techmeme", re.I)),
]
# Domain classification for aw-watcher-web events (active-tab URLs). Matched
# against the URL host only, so a YouTube link in a title doesn't count.
SITE_PATTERNS = [
    ("YouTube", re.compile(r"(^|\.)youtube\.com$", re.I)),
    ("Memeorandum", re.compile(r"(^|\.)memeorandum\.com$", re.I)),
    ("Techmeme", re.compile(r"(^|\.)techmeme\.com$", re.I)),
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


def _classify_url(url: str) -> str | None:
    try:
        host = urllib.parse.urlparse(url).netloc.split(":")[0]
    except ValueError:
        return None
    for label, rx in SITE_PATTERNS:
        if rx.search(host):
            return label
    return None


def _intersect_intervals(a: list[tuple[datetime, datetime]],
                         b: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out = []
    for x in a:
        for y in b:
            lo, hi = max(x[0], y[0]), min(x[1], y[1])
            if hi > lo:
                out.append((lo, hi))
    return out


def media_minutes_from_aw_web(day: date) -> dict | None:
    """Per-label minutes from aw-watcher-web (Chrome extension) active-tab URLs.

    A tab counts only where its event intersects BOTH not-AFK time and
    Chrome-is-frontmost window time — a background tab, an idle machine, or
    Chrome behind another app all don't count. Returns None when the web
    bucket doesn't exist (extension not installed / not reporting).
    """
    lo, hi = _day_bounds_utc(day)
    rng = urllib.parse.urlencode(
        {"start": lo.isoformat(), "end": hi.isoformat(), "limit": -1})
    try:
        buckets = _aw_get("/buckets/")
        web_ids = [b for b in buckets if b.startswith("aw-watcher-web")]
        if not web_ids:
            return None
        afk = _aw_get(f"/buckets/aw-watcher-afk_{AW_HOST}/events?{rng}")
        win = _aw_get(f"/buckets/aw-watcher-window_{AW_HOST}/events?{rng}")
        web = []
        for bid in web_ids:
            web.extend(_aw_get(f"/buckets/{bid}/events?{rng}"))
    except Exception:
        return None
    active = _intervals_from_events(
        [e for e in afk if e.get("data", {}).get("status") == "not-afk"])
    chrome = _intervals_from_events(
        [e for e in win if "chrome" in e.get("data", {}).get("app", "").lower()])
    countable = _intersect_intervals(active, chrome)
    # Union each label's intervals across ALL web buckets before measuring:
    # several Chrome profiles (and the extension's legacy + hostname bucket
    # pair) report into the same server, so the same wall-clock second can
    # appear in more than one event — summing per event would double-count it.
    by_label: dict[str, list[tuple[datetime, datetime]]] = {}
    for e in web:
        label = _classify_url(e.get("data", {}).get("url", ""))
        if label is None:
            continue
        try:
            start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        dur = float(e.get("duration") or 0)
        if dur > 0:
            by_label.setdefault(label, []).append((start, start + timedelta(seconds=dur)))
    mins: dict[str, float] = {}
    for label, ivs in by_label.items():
        ivs.sort()
        merged: list[tuple[datetime, datetime]] = []
        for iv in ivs:
            if merged and iv[0] <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], iv[1]))
            else:
                merged.append(iv)
        secs = sum(_overlap_seconds(iv, countable) for iv in merged)
        if secs > 0:
            mins[label] = round(secs / 60, 1)
    return mins


_ST_QUERY = """SELECT ti.ZBUNDLEIDENTIFIER, SUM(ti.ZTOTALTIMEINSECONDS)
FROM ZUSAGETIMEDITEM ti
JOIN ZUSAGECATEGORY c ON ti.ZCATEGORY = c.Z_PK
JOIN ZUSAGEBLOCK b ON c.ZBLOCK = b.Z_PK
WHERE b.ZSTARTDATE >= {lo} AND b.ZSTARTDATE < {hi}
GROUP BY 1"""


def media_minutes_from_screentime(day: date) -> dict | None:
    """Per-label media minutes from the Screen Time cross-device store ON IX.

    Straylight is Intune-managed (TCC/Screen Time policy-locked), so the sync
    target is Ix — personal, un-enrolled, already /0t's ssh workhorse. Requires
    on Ix: Remote Login's "allow full disk access for remote users" + Screen
    Time App & Website Activity + Share Across Devices (and on the iPhone).
    Returns None when unreadable/absent. Schema per DFIR documentation of
    RMAdminStore (USAGE tables) — treat failures as absence.
    """
    apple = 978307200  # 2001-01-01 epoch offset
    lo = int(datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TZ).timestamp()) - apple
    sql = _ST_QUERY.format(lo=lo, hi=lo + 86400)
    cmd = (
        'D=$(getconf DARWIN_USER_DIR); '
        'S="$D/com.apple.ScreenTimeAgent/Store/RMAdminStore-Cloud.sqlite"; '
        f'[ -r "$S" ] && sqlite3 -separator "|" "file:$S?mode=ro" {shlex.quote(sql)}'
    )
    try:
        res = subprocess.run(["ssh", "ix", cmd], capture_output=True, text=True, timeout=25)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    mins: dict[str, float] = {}
    for line in res.stdout.splitlines():
        bundle, _, secs = line.partition("|")
        label = MEDIA_BUNDLES.get(bundle.strip())
        try:
            s = float(secs)
        except ValueError:
            continue
        if label and s > 0:
            mins[label] = mins.get(label, 0) + s / 60
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
    aww = media_minutes_from_aw_web(day)
    if aww is None:
        notes.append("aw-watcher-web bucket missing (Chrome extension not installed/reporting)")
    st = media_minutes_from_screentime(day)
    if st is None:
        notes.append("Screen Time store on Ix unreadable (needs Ix remote-user FDA + Share Across Devices)")

    # Merge sources per label, taking the max per source-label (they overlap
    # only if the same app is measured twice, e.g. desktop YouTube in both
    # AW and Mac Screen Time — max avoids double counting, sum would inflate).
    passive: dict[str, float] = {}
    for src in (aw or {}), (aww or {}), (st or {}):
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
