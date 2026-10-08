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
  2. where.active_tz() (2026-10-08): the zone of the device nearest JM,
     imago → fuchikoma → straylight → ix, never moving "today" backwards.
     Auto-detection, overriding the 2026-08-23 no-auto decision at JM's
     request; the never-backwards hold in where.py is what makes it safe for
     the equality-polling rollover logic. See lib/where.py.
  3. The OS's own local timezone, via datetime.now().astimezone() (no device
     has reported yet).

The /travel override lives in the synced vault (z_ibx/where/override.json)
so it applies on every machine, not just the one /travel ran on.

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

TRAVEL_FILE = Path.home() / "vault" / "z_ibx" / "where" / "override.json"
_LEGACY_TRAVEL_FILE = Path.home() / ".local" / "state" / "jm" / "travel.json"

sys.path.insert(0, str(Path(__file__).parent))
import where  # noqa: E402


def _travel_state() -> dict | None:
    for f in (TRAVEL_FILE, _LEGACY_TRAVEL_FILE):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict):
            return d
    return None


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
            pass  # malformed override — fall through
    try:
        tz = where.active_tz()
        if tz:
            return ZoneInfo(tz)
    except Exception:
        pass  # never let location resolution break "today"
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
    elif args and args[0] == "--tz-env":
        # IANA name for `export TZ=...`, or nothing when only the OS zone is
        # known (an abbreviation like "PDT" is not a valid TZ value).
        key = getattr(active_zone(), "key", "")
        print(key if "/" in key else "", end="")
    else:
        print(today_iso())
