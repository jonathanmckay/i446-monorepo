#!/usr/bin/env python3
"""youtube-history — estimate YouTube watch time + content from a Google
Takeout export, so mobile YouTube (invisible to ActivityWatch and blocked
from Screen Time on Ix — see media_audit.py) becomes visible.

Source: Takeout > YouTube and YouTube Music > history > watch-history.json
(request at takeout.google.com; select JSON format). Server-side YouTube
history, so it covers every device tied to the account — phone, TV, desktop.

Takeout logs *when* you watched, not *how long*. This estimates per-video
watch seconds as min(gap to the next entry, the video's real length) — a
video left playing in the background can't inflate past its own runtime,
and a video watched to completion with a long gap after it (e.g. the last
entry of a session) is capped at its own length rather than the gap.

Video length requires the YouTube Data API v3 (`videos.list`, no OAuth,
just an API key — https://console.cloud.google.com, enable "YouTube Data
API v3", create an API key). Without a key, falls back to capping every
gap at --default-cap-min (still useful for totals, less precise per video).

Usage:
  youtube_history.py <watch-history.json> [--api-key KEY] [--since YYYY-MM-DD]
                      [--default-cap-min 40] [--top 15]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

API_KEY_ENV = "YOUTUBE_API_KEY"
VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
# ISO 8601 duration like "PT14M32S" / "PT1H2M" / "PT45S"
import re as _re
_ISO8601_DUR = _re.compile(
    r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def parse_iso8601_duration(s: str) -> int:
    m = _ISO8601_DUR.fullmatch(s)
    if not m:
        return 0
    h, mi, se = (int(g) if g else 0 for g in m.group("h", "m", "s"))
    return h * 3600 + mi * 60 + se


def load_entries(path: str, since: str | None = None) -> list[dict]:
    """Parse Takeout's watch-history.json into normalized video-watch entries.

    Filters out ads (details: From Google Ads) and non-video entries
    (surveys, "Used YouTube" with no titleUrl) and YouTube Music plays
    (titleUrl host is music.youtube.com, not www.youtube.com/watch).
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None
    out = []
    for item in raw:
        url = item.get("titleUrl", "")
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname not in ("www.youtube.com", "youtube.com") or parsed.path != "/watch":
            continue
        if any(d.get("name") == "From Google Ads" for d in item.get("details", [])):
            continue
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        if not video_id:
            continue
        try:
            ts = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if since_dt and ts < since_dt:
            continue
        title = item.get("title", "").removeprefix("Watched ")
        channel = (item.get("subtitles") or [{}])[0].get("name", "Unknown channel")
        out.append({"video_id": video_id, "title": title, "channel": channel, "time": ts})

    out.sort(key=lambda e: e["time"])
    return out


def fetch_durations(video_ids: list[str], api_key: str) -> dict[str, int]:
    """Batch-fetch real video lengths (seconds) via YouTube Data API v3.

    videos.list takes up to 50 ids per call and needs no OAuth — duration
    is public metadata. Missing/deleted videos are simply absent from the
    result (caller falls back to the default cap for those).
    """
    durations: dict[str, int] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        params = urllib.parse.urlencode({
            "part": "contentDetails", "id": ",".join(chunk), "key": api_key})
        try:
            with urllib.request.urlopen(f"{VIDEOS_ENDPOINT}?{params}", timeout=15) as r:
                data = json.load(r)
        except Exception as ex:
            print(f"warning: duration fetch failed for a batch: {ex}", file=sys.stderr)
            continue
        for item in data.get("items", []):
            secs = parse_iso8601_duration(item["contentDetails"]["duration"])
            if secs > 0:
                durations[item["id"]] = secs
    return durations


def estimate_watch_seconds(entries: list[dict], durations: dict[str, int],
                            default_cap_sec: int) -> list[dict]:
    """Attach `est_seconds` to each entry: min(gap-to-next, real or default cap).

    Entries must already be time-sorted (load_entries does this). The last
    entry overall has no "next" gap to bound it, so it just uses the cap.
    """
    n = len(entries)
    for i, e in enumerate(entries):
        cap = durations.get(e["video_id"], default_cap_sec)
        if i + 1 < n:
            gap = (entries[i + 1]["time"] - e["time"]).total_seconds()
            e["est_seconds"] = int(min(gap, cap)) if gap > 0 else int(cap)
        else:
            e["est_seconds"] = int(cap)
    return entries


def summarize(entries: list[dict], top: int) -> dict:
    total = sum(e["est_seconds"] for e in entries)
    by_day: dict[str, int] = defaultdict(int)
    by_channel: dict[str, int] = defaultdict(int)
    for e in entries:
        by_day[e["time"].astimezone().strftime("%Y-%m-%d")] += e["est_seconds"]
        by_channel[e["channel"]] += e["est_seconds"]
    top_channels = sorted(by_channel.items(), key=lambda kv: -kv[1])[:top]
    top_videos = sorted(entries, key=lambda e: -e["est_seconds"])[:top]
    return {
        "total_seconds": total,
        "video_count": len(entries),
        "by_day": dict(sorted(by_day.items())),
        "top_channels": top_channels,
        "top_videos": top_videos,
        "range": (entries[0]["time"], entries[-1]["time"]) if entries else None,
    }


def _fmt_hm(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def render_report(summary: dict, had_api_key: bool) -> str:
    lines = []
    if not summary["video_count"]:
        return "No YouTube watch entries found in range.\n"
    lo, hi = summary["range"]
    lines.append(f"# YouTube watch history: {lo:%Y-%m-%d} to {hi:%Y-%m-%d}\n")
    lines.append(f"**Total: {_fmt_hm(summary['total_seconds'])}** across {summary['video_count']} videos")
    n_days = len(summary["by_day"])
    if n_days:
        avg_day = summary["total_seconds"] / n_days
        lines.append(f"Average on days watched: {_fmt_hm(int(avg_day))}/day ({n_days} days)")
    if not had_api_key:
        lines.append("\n_No YOUTUBE_API_KEY set — durations use the default cap, not real video length. "
                     "Totals are a rougher upper bound._")
    lines.append("\n## By day\n")
    for day, secs in summary["by_day"].items():
        lines.append(f"- {day}: {_fmt_hm(secs)}")
    lines.append("\n## Top channels\n")
    for channel, secs in summary["top_channels"]:
        lines.append(f"- {channel}: {_fmt_hm(secs)}")
    lines.append("\n## Longest individual videos\n")
    for e in summary["top_videos"]:
        lines.append(f"- {_fmt_hm(e['est_seconds'])} — {e['title']} ({e['channel']}, {e['time']:%m/%d %H:%M})")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("history_json", help="path to Takeout watch-history.json")
    ap.add_argument("--api-key", default=None, help=f"YouTube Data API v3 key (or set {API_KEY_ENV})")
    ap.add_argument("--since", default=None, help="only count entries on/after this date (YYYY-MM-DD)")
    ap.add_argument("--default-cap-min", type=int, default=40,
                     help="fallback per-video cap in minutes when duration is unknown (default 40)")
    ap.add_argument("--top", type=int, default=15, help="how many channels/videos to list (default 15)")
    ap.add_argument("--json", action="store_true", help="print machine-readable summary instead of markdown")
    args = ap.parse_args()

    import os
    api_key = args.api_key or os.environ.get(API_KEY_ENV)

    entries = load_entries(args.history_json, since=args.since)
    if not entries:
        print("No YouTube watch entries found in range.", file=sys.stderr)
        return 1

    durations = fetch_durations(list({e["video_id"] for e in entries}), api_key) if api_key else {}
    entries = estimate_watch_seconds(entries, durations, args.default_cap_min * 60)
    summary = summarize(entries, args.top)

    if args.json:
        print(json.dumps({
            "total_seconds": summary["total_seconds"],
            "video_count": summary["video_count"],
            "by_day": summary["by_day"],
            "top_channels": summary["top_channels"],
        }, indent=2))
    else:
        print(render_report(summary, had_api_key=bool(api_key)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
