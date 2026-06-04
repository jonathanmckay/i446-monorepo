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

TZ = ZoneInfo("America/Los_Angeles")
CACHE_DIR = Path.home() / ".cache" / "tg-tui"
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

    # Fetch from Agency
    try:
        raw = mcp.call_tool("calendar", "ListEvents", {
            "startDateTime": day_start.strftime("%Y-%m-%dT00:00:00"),
            "endDateTime": day_end.strftime("%Y-%m-%dT00:00:00"),
            "top": "50",
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
        return parsed.astimezone(TZ)

    zone_key = WINDOWS_TZ_MAP.get(timezone_name, timezone_name)
    try:
        event_tz = ZoneInfo(zone_key) if zone_key else dt.timezone.utc
    except Exception:
        event_tz = dt.timezone.utc
    return parsed.replace(tzinfo=event_tz).astimezone(TZ)


def _normalize(raw_events: list[dict]) -> list[dict]:
    """Convert raw API events to the tg-tui event format."""
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
