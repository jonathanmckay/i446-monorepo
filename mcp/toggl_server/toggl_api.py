import base64
import json
import os
import signal
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from . import throttle
from .config import TOGGL_API_KEY, TOGGL_WORKSPACE_ID

BASE_URL = "https://api.track.toggl.com/api/v9"

# janus polls the running timer only every 30s; signalling it after a
# timer-state change makes it refresh immediately. This lives in toggl_api
# (the shared HTTP layer) so EVERY caller benefits — the MCP server and
# /d357, not just the toggl_cli path that previously had its own nudge.
JANUS_PID = Path.home() / ".cache" / "janus.pid"

# Shared running-timer cache. Toggl is a ~1 req/sec leaky bucket, and several
# processes poll /current independently (janus every 30s, every open dtd picker
# via dtd-ticker, …). Each live get_current() write-throughs here; pollers read
# this file within CURRENT_CACHE_TTL instead of each hitting the API — collapsing
# N independent pollers into ~one network read per window. Load scales with UI
# activity (idle → zero), which a standalone 24/7 daemon would not give.
CURRENT_CACHE = Path.home() / ".cache" / "toggl-current.json"
CURRENT_CACHE_TTL = 30.0  # seconds; matches janus's steady-state poll cadence


def _notify_tui():
    """SIGUSR1 the running janus so it refreshes now instead of on its poll.
    Best-effort: a missing/stale pid file or dead process is ignored."""
    try:
        os.kill(int(JANUS_PID.read_text().strip()), signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


def _write_current_cache(entry):
    """Persist the last-known running entry (or None when idle) so concurrent
    pollers can share one fetch. Atomic tmp+replace so a reader never sees a
    torn file. Best-effort: a write failure just means no sharing this round."""
    try:
        CURRENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CURRENT_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "entry": entry}))
        tmp.replace(CURRENT_CACHE)
    except OSError:
        pass


def _invalidate_current_cache():
    """Drop the cache so the next read fetches fresh. Called after any mutation
    (start/stop/create/delete) since those can change what's running."""
    try:
        CURRENT_CACHE.unlink(missing_ok=True)
    except OSError:
        pass


def _auth_header():
    creds = base64.b64encode(f"{TOGGL_API_KEY}:api_token".encode()).decode()
    return f"Basic {creds}"


# Toggl is a ~1 req/sec leaky bucket; bursts get 429. Defaults ride the backoff
# out (good for background/MCP callers). Interactive callers that must never
# freeze the UI (dtd's execute-silent action scripts) lower these via env so a
# 429 fails fast instead of sleeping up to ~60s mid-keystroke.
_MAX_429_RETRIES = int(os.environ.get("TOGGL_MAX_429_RETRIES", "3"))
_MAX_429_DELAY = float(os.environ.get("TOGGL_MAX_429_DELAY", "30"))


def _request(method, path, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    # On 429, honour Retry-After (Toggl doesn't always send it, so fall back to
    # capped exponential backoff). Without this a tripped limit raises straight
    # to the UI and the next poll re-trips it — the same pattern ibx/slack.py
    # and ibx/sync_external_replies.py already use for their APIs.
    for attempt in range(_MAX_429_RETRIES):
        throttle.acquire()  # client-side pacing, shared across all processes
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", _auth_header())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    result = json.loads(resp.read())
                    if method != "GET":  # a mutation succeeded → wake janus now
                        _invalidate_current_cache()  # running state may have changed
                        _notify_tui()
                    return result
                return None
        except urllib.error.HTTPError as e:
            # 402 (free-tier cap) and 429 both mean "slow down": arm the shared
            # cooldown so every process — not just this one — backs off.
            if e.code in (402, 429):
                throttle.note_rate_limit()
            if e.code == 429 and attempt < _MAX_429_RETRIES - 1:
                retry_after = e.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                time.sleep(min(delay, _MAX_429_DELAY))
                continue
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Toggl API {method} {path} -> {e.code}: {error_body}")


def create_entry(description, start_iso, stop_iso, duration_sec, project_id=None, tags=None):
    body = {
        "description": description,
        "start": start_iso,
        "stop": stop_iso,
        "duration": duration_sec,
        "workspace_id": TOGGL_WORKSPACE_ID,
        "created_with": "mcp-toggl-custom",
    }
    if project_id:
        body["project_id"] = project_id
    if tags:
        body["tags"] = tags
    return _request("POST", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries", body)


def start_timer(description, project_id=None, tags=None, start_time=None):
    import datetime
    if start_time:
        start = start_time  # ISO format string
    else:
        start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "description": description,
        "start": start,
        "duration": -1,
        "workspace_id": TOGGL_WORKSPACE_ID,
        "created_with": "mcp-toggl-custom",
    }
    if project_id:
        body["project_id"] = project_id
    if tags:
        body["tags"] = tags
    return _request("POST", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries", body)


def stop_timer(entry_id):
    return _request("PATCH", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}/stop")


def get_current():
    """Live fetch of the running entry (None when idle). Write-throughs to the
    shared cache so concurrent pollers can ride this fetch via get_current_cached."""
    entry = _request("GET", "/me/time_entries/current")
    _write_current_cache(entry)
    return entry


def get_current_cached(max_age=CURRENT_CACHE_TTL):
    """Running entry from the shared cache when it's younger than max_age, else a
    live get_current() (which refreshes the cache). Lets many pollers share ~one
    network read per max_age window. The footer/elapsed clock is computed from the
    entry's start time, so a slightly stale entry still renders an exact clock;
    only detection of an externally-changed timer lags by up to max_age, and any
    mutation invalidates the cache immediately. Falls back to live on any cache
    problem (missing, torn, malformed)."""
    try:
        raw = json.loads(CURRENT_CACHE.read_text())
        if time.time() - float(raw["ts"]) <= max_age:
            return raw["entry"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return get_current()


def get_entries(start_date=None, end_date=None):
    params = []
    if start_date:
        params.append(f"start_date={start_date}")
    if end_date:
        params.append(f"end_date={end_date}")
    qs = "?" + "&".join(params) if params else ""
    return _request("GET", f"/me/time_entries{qs}")


def get_projects():
    """List all workspace projects (id, name, active, ...)."""
    return _request("GET", f"/workspaces/{TOGGL_WORKSPACE_ID}/projects")


def update_entry(entry_id, **fields):
    """Update a time entry. Supported fields: description, start, stop, duration, project_id, tags."""
    body = {"workspace_id": TOGGL_WORKSPACE_ID}
    body.update(fields)
    return _request("PUT", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}", body)


def delete_entry(entry_id):
    url = f"{BASE_URL}/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}"
    throttle.acquire()  # same client-side pacing as _request()
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", _auth_header())
    try:
        with urllib.request.urlopen(req) as resp:
            ok = resp.status in (200, 204)
            if ok:  # deleting the running entry changes current → drop the cache
                _invalidate_current_cache()
                _notify_tui()
            return ok
    except urllib.error.HTTPError as e:
        if e.code in (402, 429):
            throttle.note_rate_limit()
        raise RuntimeError(f"Toggl API DELETE -> {e.code}: {e.read().decode()}")


def trim_range(start_dt, end_dt, exclude_ids=None):
    """Ensure no existing Toggl entry -- completed or the currently-running
    one -- keeps covering [start_dt, end_dt) once a new or retimed entry
    claims it. Split/trim/delete whatever overlaps, except any id in
    exclude_ids (the entry itself, when this is a RETIME rather than a plain
    create -- it shouldn't try to trim itself out from under its own edit).

    Shared by every caller that creates or moves a definite time range:
    tg-fast.py's "<desc> <start>-<end>" range creation, did-fast.py's
    time-range /did items, and janus.py's entry-edit-to-a-new-time path
    (2026-07-19: "if I edit an event with a new time... MECE -- shorten [an
    overlapping entry] to make room, or delete the old one if full overlap").
    Originally did-fast-only (as `_trim_toggl_range`, fixing the 2026-07-16
    "asha"/"asha prep" double-count); promoted here once a third caller
    needed the identical logic rather than a third copy of it.

    The running entry is special-cased: it has no fixed end, so its portion
    AFTER the range isn't trimmed to a stop -- it's RESUMED as a new running
    entry starting right after, so live tracking keeps going instead of
    silently vanishing. Returns human-readable log lines, one per entry
    trimmed/split/resumed."""
    exclude_ids = exclude_ids or set()
    tz = start_dt.tzinfo
    results = []
    day = start_dt.date()
    entries = get_entries(
        start_date=day.isoformat(),
        end_date=(day + timedelta(days=1)).isoformat(),
    ) or []
    for e in entries:
        if e.get("id") in exclude_ids:
            continue
        try:
            e_start = datetime.fromisoformat(e["start"]).astimezone(tz)
        except (KeyError, ValueError, TypeError):
            continue
        is_running = (e.get("duration") or 0) < 0
        if is_running:
            e_end = datetime.now(tz)
        else:
            stop = e.get("stop")
            if not stop:
                continue
            try:
                e_end = datetime.fromisoformat(stop).astimezone(tz)
            except (ValueError, TypeError):
                continue
        if e_end <= start_dt or e_start >= end_dt:
            continue  # no overlap
        desc = e.get("description") or ""
        proj_id = e.get("project_id")
        tags = e.get("tags") or None
        if e_start < start_dt:
            pre_end = start_dt - timedelta(minutes=1)
            if pre_end > e_start:
                create_entry(desc, e_start.isoformat(), pre_end.isoformat(),
                              int((pre_end - e_start).total_seconds()), proj_id, tags)
                results.append(f"Trimmed: {desc} {e_start:%H:%M}-{pre_end:%H:%M}")
        if is_running:
            post_start = end_dt + timedelta(minutes=1)
            start_timer(desc, proj_id, tags, start_time=post_start.isoformat())
            results.append(f"Resumed: {desc} from {post_start:%H:%M}")
        elif e_end > end_dt:
            post_start = end_dt + timedelta(minutes=1)
            if e_end > post_start:
                create_entry(desc, post_start.isoformat(), e_end.isoformat(),
                              int((e_end - post_start).total_seconds()), proj_id, tags)
                results.append(f"Trimmed: {desc} {post_start:%H:%M}-{e_end:%H:%M}")
        delete_entry(e["id"])
    return results
