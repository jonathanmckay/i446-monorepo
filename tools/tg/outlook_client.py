#!/usr/bin/env python3
"""outlook_client — Fetch Outlook calendar events via Agency MCP.

Mirrors gcal_client.py: returns a list of event dicts with start_dt, end_dt,
title, calendar. Uses a file cache to avoid hammering the Agency server.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# Self-locating: guarantees `daytime` resolves regardless of whether the
# caller (janus.py) already put lib/ on sys.path — see lib/daytime.py, the
# shared "now"/"today" resolution every DTD/Janus TZ read must go through.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
import daytime  # noqa: E402


def _tz() -> ZoneInfo:
    """Live-resolved active timezone — see lib/daytime.py. Not cached: an
    in-progress /travel change or an OS TZ change must be picked up on the
    next call, not frozen at import."""
    return daytime.active_zone()


def __getattr__(name):
    # PEP 562 module __getattr__: `outlook_client.TZ` used to be a constant
    # frozen at import. Anything that still reaches for `.TZ` gets the same
    # live-resolved zone as every internal `_tz()` call.
    if name == "TZ":
        return daytime.active_zone()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


CACHE_DIR = Path.home() / ".cache" / "janus"
WINDOWS_TZ_MAP = {
    "UTC": "UTC",
    "Coordinated Universal Time": "UTC",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}

sys.path.insert(0, str(Path.home() / "i446-monorepo/tools/ibx"))
try:
    import agency_mcp as mcp
except ImportError:
    mcp = None


def list_events(day_start: dt.datetime, day_end: dt.datetime,
                force: bool = False) -> list[dict]:
    """Fetch Outlook calendar events for the given day range.

    Returns list of dicts with keys: start_dt, end_dt, title, calendar, all_day,
    transparency (always "opaque" for Outlook events).
    """
    if mcp is None:
        raise RuntimeError("agency_mcp unavailable")

    today_str = day_start.strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / f"outlook-{today_str}.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache (5 min TTL)
    if not force and cache_file.exists():
        try:
            age = dt.datetime.now().timestamp() - cache_file.stat().st_mtime
            if age < 300:
                return _parse_cache(cache_file)
        except Exception:
            pass

    # Fetch from Agency. ListCalendarView, NOT ListEvents: Graph's /events
    # returns recurring series as their master (whose original start falls
    # outside the window), so every recurring meeting — standups, weekly
    # 1:1s, holds — silently vanished from janus (user report 2026-07-27:
    # "janus is missing a lot of the meetings on my calendar today").
    # calendarView expands series into that day's actual instances. Offsets
    # included per the tool's contract ("UTC or contain the offset").
    try:
        raw = mcp.call_tool("calendar", "ListCalendarView", {
            "startDateTime": day_start.isoformat(),
            "endDateTime": day_end.isoformat(),
            "top": 50,
        }, timeout=15)
    except Exception as exc:
        # Agency not available; return cached data if any
        if cache_file.exists():
            return _parse_cache(cache_file)
        raise RuntimeError(f"outlook fetch failed: {exc}") from exc

    events = []
    if raw and raw.get("content"):
        for item in raw["content"]:
            text = item.get("text", "")
            # calendar.ListEvents prefixes the JSON payload with a status line
            # like "Events retrieved successfully.\n{...}". Strip anything
            # before the first '{' or '[' so json.loads succeeds.
            brace = min((i for i in (text.find("{"), text.find("[")) if i != -1),
                        default=-1)
            if brace > 0:
                text = text[brace:]
            try:
                data = json.loads(text)
                ev_list = data if isinstance(data, list) else data.get("value", [])
                for ev in ev_list:
                    events.append({
                        "subject": ev.get("subject", ""),
                        "start": ev.get("start", {}).get("dateTime", ""),
                        "end": ev.get("end", {}).get("dateTime", ""),
                        "start_tz": ev.get("start", {}).get("timeZone", ""),
                        "end_tz": ev.get("end", {}).get("timeZone", ""),
                        "is_all_day": ev.get("isAllDay", False),
                    })
            except (json.JSONDecodeError, TypeError):
                pass

    # Empty-fetch guard (2026-07-30): an Agency flake can return a successful
    # but EMPTY calendar view. Overwriting a non-empty same-day cache with it
    # blanked every meeting in janus for the cache TTL (and 2-byte poisoned
    # caches litter ~/.cache/janus from past occurrences). A day that really
    # has no meetings also has an empty/absent cache, so the guard never
    # suppresses a legitimate empty.
    if not events and cache_file.exists():
        prior = _parse_cache(cache_file)
        if prior:
            return prior

    # Write cache
    try:
        cache_file.write_text(json.dumps(events, ensure_ascii=False))
    except Exception:
        pass

    return _normalize(events)


def _parse_cache(cache_file: Path) -> list[dict]:
    try:
        raw = json.loads(cache_file.read_text())
        return _normalize(raw)
    except Exception:
        return []


def _parse_graph_dt(s: str, timezone_name: str = "") -> dt.datetime:
    """Parse a Graph API datetime, preserving Graph's separate timeZone field."""
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        # Truncate fractional seconds to 6 digits (Python max precision)
        s = f"{head}.{frac[:6]}"
    parsed = dt.datetime.fromisoformat(s)
    if parsed.tzinfo is not None:
        return parsed.astimezone(_tz())

    zone_key = WINDOWS_TZ_MAP.get(timezone_name, timezone_name)
    try:
        event_tz = ZoneInfo(zone_key) if zone_key else dt.timezone.utc
    except Exception:
        event_tz = dt.timezone.utc
    return parsed.replace(tzinfo=event_tz).astimezone(_tz())


def _normalize(raw_events: list[dict]) -> list[dict]:
    """Convert raw API events to the janus event format."""
    out = []
    for ev in raw_events:
        try:
            start_str = ev.get("start", "")
            end_str = ev.get("end", "")
            if not start_str or not end_str:
                continue
            start_dt = _parse_graph_dt(start_str, ev.get("start_tz", ""))
            end_dt = _parse_graph_dt(end_str, ev.get("end_tz", ev.get("start_tz", "")))
            out.append({
                "start_dt": start_dt,
                "end_dt": end_dt,
                "title": ev.get("subject", "(no subject)"),
                "calendar": "Outlook",
                "all_day": ev.get("is_all_day", False),
                "transparency": "opaque",
            })
        except Exception:
            continue
    out.sort(key=lambda e: e["start_dt"])
    return out
