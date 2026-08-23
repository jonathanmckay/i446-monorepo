"""Single source of truth for "now"/"today" across DTD and Janus.

Every reader of "today," "the current local hour," or "what timezone am I
in" must go through this module instead of independently calling
datetime.now()/date.today() or hardcoding a home timezone. Two failure modes
this replaces (found auditing both tools for international-travel hardening,
2026-08-23):

  - Hardcoded ZoneInfo("America/Los_Angeles") (janus.py, toggl_cli.py,
    gcal_client.py, outlook_client.py, did-fast.py, ...): correct only when
    the device stays on Pacific time. Travel with the laptop following local
    time and every one of these silently keeps showing PT.
  - Naive datetime.now()/date.today() (dtd.sh's Python heredocs, did-fast.py,
    mark-completed.py, refresh-cache.py, ...): silently follows whatever
    timezone the OS reports, with no override and no record of what zone was
    active when a cached timestamp was written.

Resolution order for the active timezone:
  1. TRAVEL_FILE (~/.local/state/jm/travel.json), if present and valid:
     {"active_tz": "<IANA zone>", ...} — an explicit override set by the
     /travel command. Deliberately NOT auto-detected (IP geolocation, phone
     push, etc.) — see /travel's own docs for why.
  2. The OS's own local timezone, via datetime.now().astimezone(). This is
     the common case: the laptop follows wherever it physically is.

HOME_TZ is fixed at America/Los_Angeles — used only as a display reference
(e.g. showing "home" time alongside local time) and as TRAVEL_FILE's
implicit baseline before any /travel override is written. It is never used
to compute "today."
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HOME_TZ = ZoneInfo("America/Los_Angeles")

TRAVEL_FILE = Path.home() / ".local" / "state" / "jm" / "travel.json"


def _travel_state() -> dict | None:
    try:
        d = json.loads(TRAVEL_FILE.read_text())
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def is_traveling() -> bool:
    """True if an explicit /travel override is active (not just that the
    device happens to be off its home timezone)."""
    st = _travel_state()
    return bool(st and st.get("active_tz"))


def active_zone() -> ZoneInfo | dt.tzinfo:
    """The timezone every DTD/Janus 'today' computation should use right now.

    An explicit /travel override wins; otherwise the OS's own local zone —
    NOT a hardcoded home zone, so a laptop that follows its physical location
    (the common case) needs zero per-tool code changes to stay correct.
    Call this fresh at each use site rather than caching the result: it can
    change mid-session (a /travel invocation, or the OS TZ itself changing),
    and every reader must agree on the current value at the moment it acts.
    """
    st = _travel_state()
    if st and st.get("active_tz"):
        try:
            return ZoneInfo(st["active_tz"])
        except Exception:
            pass  # malformed override — fall through to system local
    return dt.datetime.now().astimezone().tzinfo


def local_now() -> dt.datetime:
    return dt.datetime.now(active_zone())


def today() -> dt.date:
    return local_now().date()


def today_iso() -> str:
    return today().isoformat()


def home_now() -> dt.datetime:
    return dt.datetime.now(HOME_TZ)


def _export() -> str:
    n = local_now()
    zone = active_zone()
    return (
        f"LOCAL_TODAY={n.date().isoformat()}\n"
        f"LOCAL_HOUR={n.hour}\n"
        f"LOCAL_TIME={n.strftime('%H:%M')}\n"
        f"ACTIVE_TZ={getattr(zone, 'key', str(zone))}\n"
        f"TRAVELING={'1' if is_traveling() else '0'}\n"
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--export":
        print(_export(), end="")
    elif args and args[0] == "--zone":
        zone = active_zone()
        print(getattr(zone, "key", str(zone)))
    else:
        print(today_iso())
