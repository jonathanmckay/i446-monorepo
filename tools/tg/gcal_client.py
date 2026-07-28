#!/usr/bin/env python3
"""Google Calendar helper for janus.

Reads OAuth credentials saved by google-calendar-mcp at
~/.config/google-calendar-mcp/{tokens.json,gcp-oauth.keys.json} and lists
events across every calendar visible on the m5c7 account.

5-minute file cache at ~/.cache/janus/gcal-YYYY-MM-DD.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import warnings
from pathlib import Path
from typing import Iterable

warnings.filterwarnings("ignore", category=FutureWarning)

from google.oauth2.credentials import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
TOKENS_PATH = Path("~/.config/google-calendar-mcp/tokens.json").expanduser()
KEYS_PATH = Path("~/.config/google-calendar-mcp/gcp-oauth.keys.json").expanduser()
CACHE_DIR = Path("~/.cache/janus").expanduser()
CACHE_TTL = 300  # seconds
ACCOUNT = "m5c7"


def _load_credentials() -> Credentials:
    tokens = json.loads(TOKENS_PATH.read_text())[ACCOUNT]
    keys = json.loads(KEYS_PATH.read_text())["installed"]
    return Credentials(
        token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        token_uri=keys["token_uri"],
        client_id=keys["client_id"],
        client_secret=keys["client_secret"],
        scopes=tokens.get("scope", "").split() or None,
    )


def _persist_refreshed(creds: Credentials) -> None:
    """Write the refreshed access token back to tokens.json."""
    try:
        all_tokens = json.loads(TOKENS_PATH.read_text())
        slot = all_tokens.get(ACCOUNT, {})
        slot["access_token"] = creds.token
        if creds.expiry:
            slot["expiry_date"] = int(creds.expiry.timestamp() * 1000)
        all_tokens[ACCOUNT] = slot
        TOKENS_PATH.write_text(json.dumps(all_tokens, indent=2))
    except Exception:
        pass


def _service():
    creds = _load_credentials()
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return svc, creds


def _cache_path(day: dt.date) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"gcal-{day.isoformat()}.json"


# lx@m5c7.com is Louisa's calendar, visible on the m5c7 account — it carries
# her personal solo events (call nanny, dentist, ...), m5x2 business meetings
# Jonathan is actually part of (m5x2 Strat, m5x2 People), AND meetings that
# are purely between her and someone else on the m5x2 team (HZ/LX 1:1, her
# 1:1 with Han Zhao). Only the middle category belongs in janus — filter the
# rest at fetch time rather than hiding the whole calendar, which would also
# hide the meetings Jonathan actually needs to see.
PERSONAL_CALENDARS_FILTERED = {"lx@m5c7.com"}
JONATHAN_EMAILS = {"mckay@m5c7.com"}


def _should_hide_lx_event(cal_summary: str, title: str, attendees: list | None) -> bool:
    """True for an lx@m5c7.com event that shouldn't surface in janus.

    A literal "m5x2" in the title is an unambiguous business signal (same
    rule janus.py's gcal_project_code uses) and always wins, in case attendee
    data is ever missing/incomplete. Otherwise: hide anything Jonathan isn't
    actually an attendee of. The 2026-07-13 fix only dropped her SOLO events
    (no attendees besides herself — "call nanny"); meetings between her and
    someone else, with Jonathan not invited, still leaked through (user
    report 2026-07-15: "can we not show the lx events")."""
    if cal_summary not in PERSONAL_CALENDARS_FILTERED:
        return False
    if "m5x2" in title.lower():
        return False
    attendee_emails = {(a.get("email") or "").lower() for a in (attendees or [])}
    return not (attendee_emails & JONATHAN_EMAILS)


def list_events(start: dt.datetime, end: dt.datetime, *, force: bool = False) -> list[dict]:
    """Return events overlapping [start, end). Times are tz-aware."""
    cache = _cache_path(start.astimezone(TZ).date())
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            cached = json.loads(cache.read_text())
            return _hydrate(cached, start, end)
        except Exception:
            pass

    svc, _ = _service()
    cal_list = svc.calendarList().list().execute().get("items", [])
    out: list[dict] = []
    for cal in cal_list:
        cid = cal["id"]
        try:
            resp = svc.events().list(
                calendarId=cid,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=200,
            ).execute()
        except Exception:
            continue
        for ev in resp.get("items", []):
            s = ev["start"].get("dateTime") or ev["start"].get("date")
            e = ev["end"].get("dateTime") or ev["end"].get("date")
            if not s or not e:
                continue
            title = ev.get("summary", "(no title)")
            cal_summary = cal.get("summary", cid)
            if _should_hide_lx_event(cal_summary, title, ev.get("attendees")):
                continue
            out.append({
                "title": title,
                "start": s,
                "end": e,
                "calendar": cal_summary,
                "all_day": "date" in ev["start"],
                "transparency": ev.get("transparency", "opaque"),
            })
    cache.write_text(json.dumps(out))
    return _hydrate(out, start, end)


def _hydrate(raw: Iterable[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
    result = []
    for ev in raw:
        try:
            s = dt.datetime.fromisoformat(ev["start"])
            e = dt.datetime.fromisoformat(ev["end"])
        except Exception:
            continue
        if s.tzinfo is None:
            # all-day event
            s = s.replace(tzinfo=TZ)
            e = e.replace(tzinfo=TZ)
        if e <= start or s >= end:
            continue
        result.append({**ev, "start_dt": s.astimezone(TZ), "end_dt": e.astimezone(TZ)})
    result.sort(key=lambda x: x["start_dt"])
    return result


if __name__ == "__main__":
    now = dt.datetime.now(TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + dt.timedelta(days=1)
    for ev in list_events(day_start, day_end, force=True):
        print(f"{ev['start_dt']:%H:%M}-{ev['end_dt']:%H:%M}  {ev['title']}  ({ev['calendar']})")
