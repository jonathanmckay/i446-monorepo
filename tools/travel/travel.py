#!/usr/bin/env python3
"""travel.py — explicit /travel timezone override for DTD + Janus.

Usage:
    python3 travel.py <city-or-IANA-zone>   # start/switch travel
    python3 travel.py home                  # clear the override
    python3 travel.py status                # show current state

Design (see the international-travel hardening pass, 2026-08-23):

Deliberately NO auto-detection (IP geolocation, phone TZ push, calendar
inference). Every rollover point in DTD/Janus works by polling and reacting
to equality of a freshly-computed "now," never to "has real time elapsed" —
building reliable auto-detection on top of that is a lot of code for a
single-user tool that already knows exactly when it's boarding a plane.
Explicit + idempotent + immediately visible beats a heuristic that
occasionally guesses wrong with no one in the loop to correct it.

Writes ~/.local/state/jm/travel.json: {active_tz, home_tz, switched_at_utc}.
lib/daytime.py's active_zone() checks this file first, falling back to the
OS's own local timezone when absent — so every DTD/Janus "now"/"today" call
that already routes through daytime.py (or, for dtd.sh's Python helpers,
through the TZ env var dtd.sh exports from this same file) picks up the
switch with no other code changes.

No day-boundary special-casing for a short (eastward) or long (westward)
first day: once daytime.py resolves "now" through the new zone, the
existing forward-only date gates (mark-completed.py, dtd-skipped-today,
dtd-block-snooze) handle an early or late local midnight correctly on
their own — a day that turns over sooner or later just does.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import daytime  # noqa: E402

TRAVEL_FILE = daytime.TRAVEL_FILE

# City/region aliases -> IANA zone. Not exhaustive — anything not here can
# still be passed as a raw IANA zone name (e.g. "Asia/Seoul"), which is
# tried first below regardless of this table.
CITY_ALIASES = {
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "hongkong": "Asia/Hong_Kong", "hk": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei", "taiwan": "Asia/Taipei",
    "singapore": "Asia/Singapore",
    "bangkok": "Asia/Bangkok", "thailand": "Asia/Bangkok",
    "jakarta": "Asia/Jakarta", "indonesia": "Asia/Jakarta",
    "manila": "Asia/Manila", "philippines": "Asia/Manila",
    "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai",
    "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul",
    "moscow": "Europe/Moscow", "russia": "Europe/Moscow",
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "paris": "Europe/Paris", "france": "Europe/Paris",
    "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "madrid": "Europe/Madrid", "spain": "Europe/Madrid",
    "rome": "Europe/Rome", "italy": "Europe/Rome",
    "amsterdam": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "zurich": "Europe/Zurich", "switzerland": "Europe/Zurich",
    "dublin": "Europe/Dublin", "ireland": "Europe/Dublin",
    "lisbon": "Europe/Lisbon", "portugal": "Europe/Lisbon",
    "cairo": "Africa/Cairo", "egypt": "Africa/Cairo",
    "johannesburg": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
    "nairobi": "Africa/Nairobi", "kenya": "Africa/Nairobi",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "australia": "Australia/Sydney",
    "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland", "nz": "Pacific/Auckland",
    "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "anchorage": "America/Anchorage", "alaska": "America/Anchorage",
    "vancouver": "America/Vancouver",
    "seattle": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "la": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "pt": "America/Los_Angeles",
    "denver": "America/Denver", "mt": "America/Denver", "phoenix": "America/Phoenix",
    "chicago": "America/Chicago", "ct": "America/Chicago",
    "new york": "America/New_York", "nyc": "America/New_York", "et": "America/New_York",
    "toronto": "America/Toronto",
    "mexico city": "America/Mexico_City", "cdmx": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires", "argentina": "America/Argentina/Buenos_Aires",
}


def resolve_zone(text: str) -> str:
    """Resolve user input to an IANA zone name, or raise ValueError with a
    clear message. Tries the raw input as a real zone first (so an explicit
    'Asia/Seoul' always works even if not in CITY_ALIASES), then the alias
    table (case/space-insensitive)."""
    raw = text.strip()
    if raw in available_timezones():
        return raw
    key = raw.lower()
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    raise ValueError(
        f"'{text}' isn't a known city alias or IANA timezone name. "
        f"Try an IANA zone directly, e.g. 'Asia/Tokyo' or 'Europe/London'."
    )


def _load_state() -> dict:
    try:
        return json.loads(TRAVEL_FILE.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    TRAVEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRAVEL_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, TRAVEL_FILE)


def _notify_janus() -> None:
    """Best-effort nudge so an already-open janus redraws immediately on the
    new zone instead of waiting for its next poll — mirrors toggl_cli.py's
    own _notify_tui()."""
    try:
        pid = int((Path.home() / ".cache" / "janus.pid").read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


def _resync() -> list[str]:
    """One atomic, best-effort resync fired on every /travel invocation —
    see the module docstring for why this is explicit-trigger rather than
    polling-based. Each step is independent and never raises; a failure in
    one must not block the others or the TZ switch itself, which has
    already been written by the time this runs."""
    did = ["done"]
    steps = [
        ("absorb cross-machine completions", [
            sys.executable, str(Path.home() / "i446-monorepo/tools/did/mark-completed.py"),
            "--absorb-remote",
        ]),
        ("refresh task cache", [
            sys.executable, str(Path.home() / "i446-monorepo/tools/did/did-fast.py"),
            "--refresh-cache",
        ]),
    ]
    for label, cmd in steps:
        try:
            subprocess.run(cmd, capture_output=True, timeout=15, check=False)
            did.append(f"✓ {label}")
        except Exception as e:  # noqa: BLE001 — resync is best-effort by design
            did.append(f"✗ {label} ({e})")
    _notify_janus()
    return did[1:]


def cmd_switch(text: str) -> int:
    try:
        zone = resolve_zone(text)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    state = _load_state()
    state["active_tz"] = zone
    state.setdefault("home_tz", str(daytime.HOME_TZ))
    state["switched_at_utc"] = daytime.local_now().astimezone(ZoneInfo("UTC")).isoformat()
    _write_state(state)
    local = daytime.local_now()
    home = daytime.home_now()
    print(f"✈ travel → {zone}")
    print(f"  local now: {local.strftime('%a %Y-%m-%d %H:%M')}")
    print(f"  home now:  {home.strftime('%a %Y-%m-%d %H:%M')} ({daytime.HOME_TZ})")
    for line in _resync():
        print(f"  {line}")
    return 0


def cmd_home() -> int:
    if TRAVEL_FILE.exists():
        TRAVEL_FILE.unlink()
    print(f"✈ travel override cleared — following system local time ({daytime.active_zone()})")
    for line in _resync():
        print(f"  {line}")
    return 0


def cmd_status() -> int:
    state = _load_state()
    zone = daytime.active_zone()
    local = daytime.local_now()
    home = daytime.home_now()
    if state.get("active_tz"):
        print(f"✈ traveling: {state['active_tz']} (since {state.get('switched_at_utc', '?')})")
    else:
        print(f"⌂ not traveling — following system local time ({zone})")
    print(f"  local now: {local.strftime('%a %Y-%m-%d %H:%M %Z')}")
    print(f"  home now:  {home.strftime('%a %Y-%m-%d %H:%M')} ({daytime.HOME_TZ})")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: travel.py <city-or-tz> | home | status", file=sys.stderr)
        return 2
    arg = argv[1]
    if arg == "home":
        return cmd_home()
    if arg == "status":
        return cmd_status()
    return cmd_switch(arg)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
