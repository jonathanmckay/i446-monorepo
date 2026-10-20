#!/usr/bin/env python3
"""Auto-place a 'night hcmc' Toggl entry by carving it out of the sleep /
'generic placeholder' entry that covers the target evening.

JM listens to an audiobook (hcmc) while falling asleep, so there is never a
live Toggl session for it -- by the time `/did night hcmc <N>` runs (usually
the next morning), that evening is already logged as one continuous 睡觉 or
"generic placeholder" block spanning past midnight. This module finds that
block and splits it into: the night-hcmc listening at the front, then sleep
for the rest -- split at the midnight day-barrier like every other overnight
entry in this system (see toggl_cli.py's cmd_create).

Two layers, deliberately separated for testability without live API calls:
  - plan_placement(): pure function, entries in -> a plan (or a reason it
    can't act) out. No network, no side effects.
  - apply_placement(): fetches real entries, calls plan_placement(), and if
    a plan came back, deletes the original entry and creates the replacements.

Called from did-fast.py as a best-effort side effect of completing the
"night hcmc" 0n habit -- never allowed to fail the habit completion itself;
see the try/except at the call site.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp"))
from toggl_server.config import PROJECT_MAP  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))
import daytime  # noqa: E402  shared "now"/"today" resolution — see lib/daytime.py


def _tz() -> ZoneInfo:
    """Live-resolved active timezone — see lib/daytime.py. Not cached: an
    in-progress /travel change or an OS TZ change must be picked up on the
    next call, not frozen at import."""
    return daytime.active_zone()

SLEEP_PROJECT_ID = PROJECT_MAP["睡觉"]
INFRA_PROJECT_ID = PROJECT_MAP["infra"]
HCMC_PROJECT_ID = PROJECT_MAP["hcmc"]
GENERIC_PLACEHOLDER_DESC = "generic placeholder"
NIGHT_HCMC_DESC = "night hcmc"
SLEEP_DESC = "睡觉"

# The window the user actually falls asleep in -- narrower than this and it's
# not "falling asleep with the book on", it's some other entry.
WINDOW_START_HOUR = 20  # 20:00 (8pm)
WINDOW_END_HOUR = 23    # exclusive -- up to but not including 23:00 (11pm)


def _local(dt_iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(dt_iso).astimezone(_tz())


def find_candidate(entries: list[dict], target_date: datetime.date):
    """Find the single sleep/generic-placeholder entry starting the evening
    of `target_date` (local, WINDOW_START_HOUR..WINDOW_END_HOUR) that crosses
    into the next day. Returns (entry, None) on a clean single match,
    (None, "no_candidate") if nothing matches, (None, "ambiguous") if more
    than one entry matches (never guess which one)."""
    matches = []
    for e in entries:
        start_raw = e.get("start")
        stop_raw = e.get("stop")
        if not start_raw or not stop_raw:
            continue  # skip entries with no stop (still running) -- never touch a live timer
        proj = e.get("project_id")
        desc = (e.get("description") or "").strip()
        is_sleep = proj == SLEEP_PROJECT_ID
        is_placeholder = (proj == INFRA_PROJECT_ID
                           and desc.lower() == GENERIC_PLACEHOLDER_DESC)
        if not (is_sleep or is_placeholder):
            continue
        start_local = _local(start_raw)
        stop_local = _local(stop_raw)
        if start_local.date() != target_date:
            continue
        if not (WINDOW_START_HOUR <= start_local.hour < WINDOW_END_HOUR):
            continue
        if stop_local.date() <= target_date:
            continue  # doesn't cross midnight
        matches.append(e)

    if not matches:
        return None, "no_candidate"
    if len(matches) > 1:
        return None, "ambiguous"
    return matches[0], None


def plan_placement(entries: list[dict], target_date: datetime.date, minutes: int):
    """Pure planning step. Returns a dict:
      {"ok": True, "candidate": entry, "segments": [(desc, project_id, start_dt, end_dt), ...]}
    or
      {"ok": False, "reason": "..."}
    `segments` is in chronological order; each is either the night-hcmc
    listening or a 睡觉 segment (day-barrier already applied -- never crosses
    midnight)."""
    if minutes <= 0:
        return {"ok": False, "reason": "no minutes to place"}

    candidate, err = find_candidate(entries, target_date)
    if err:
        return {"ok": False, "reason": err}

    start = _local(candidate["start"])
    end = _local(candidate["stop"])
    total_seconds = (end - start).total_seconds()
    if minutes * 60 >= total_seconds:
        # Consuming the whole (or more than the whole) block is almost
        # certainly the wrong candidate or a mistyped minute count -- don't
        # guess, leave it for a human.
        return {"ok": False, "reason": "minutes >= candidate duration"}

    hcmc_end = start + datetime.timedelta(minutes=minutes)
    midnight = datetime.datetime.combine(
        target_date + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=_tz())

    breakpoints = sorted({start, hcmc_end, midnight, end})
    segments = []
    for a, b in zip(breakpoints, breakpoints[1:]):
        if a >= b:
            continue  # zero-length -- e.g. hcmc_end lands exactly on midnight
        if b <= hcmc_end:
            segments.append((NIGHT_HCMC_DESC, HCMC_PROJECT_ID, a, b))
        else:
            segments.append((SLEEP_DESC, SLEEP_PROJECT_ID, a, b))

    return {"ok": True, "candidate": candidate, "segments": segments}


def apply_placement(minutes: int, target_date: datetime.date) -> dict:
    """Live version: fetches entries, plans, and if a plan exists, deletes
    the original entry and creates the replacement segments. Never raises --
    callers (did-fast.py) treat this as a best-effort side effect and must
    not have the habit completion itself fail on a Toggl-side problem."""
    try:
        from toggl_server import toggl_api
    except ImportError:
        return {"ok": False, "reason": "toggl_api import failed"}

    try:
        entries = toggl_api.get_entries(
            start_date=target_date.isoformat(),
            end_date=(target_date + datetime.timedelta(days=2)).isoformat(),
        ) or []
    except Exception as e:  # noqa: BLE001 — best-effort, never propagate
        return {"ok": False, "reason": f"fetch failed: {e}"}

    plan = plan_placement(entries, target_date, minutes)
    if not plan["ok"]:
        return plan

    candidate = plan["candidate"]
    try:
        toggl_api.delete_entry(candidate["id"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"delete failed (nothing created): {e}"}

    created = []
    for desc, project_id, seg_start, seg_end in plan["segments"]:
        duration = int((seg_end - seg_start).total_seconds())
        try:
            entry = toggl_api.create_entry(
                desc, seg_start.isoformat(), seg_end.isoformat(), duration, project_id)
            created.append({"description": desc, "id": entry.get("id"),
                             "start": seg_start.isoformat(), "stop": seg_end.isoformat()})
        except Exception as e:  # noqa: BLE001 — report partial state, don't hide it
            return {"ok": False,
                    "reason": f"create failed partway ({len(created)}/{len(plan['segments'])} "
                              f"segments made, original entry {candidate['id']} already deleted): {e}",
                    "created_so_far": created}

    return {"ok": True, "deleted_id": candidate["id"], "created": created}
