import base64
import json
import os
import signal
import time
import urllib.request
import urllib.error
from pathlib import Path

from .config import TOGGL_API_KEY, TOGGL_WORKSPACE_ID

BASE_URL = "https://api.track.toggl.com/api/v9"

# tg-tui polls the running timer only every 30s; signalling it after a
# timer-state change makes it refresh immediately. This lives in toggl_api
# (the shared HTTP layer) so EVERY caller benefits — the MCP server and
# /d357, not just the toggl_cli path that previously had its own nudge.
TG_TUI_PID = Path.home() / ".cache" / "tg-tui.pid"

# Shared running-timer cache. Toggl is a ~1 req/sec leaky bucket, and several
# processes poll /current independently (tg-tui every 30s, every open dtd picker
# via dtd-ticker, …). Each live get_current() write-throughs here; pollers read
# this file within CURRENT_CACHE_TTL instead of each hitting the API — collapsing
# N independent pollers into ~one network read per window. Load scales with UI
# activity (idle → zero), which a standalone 24/7 daemon would not give.
CURRENT_CACHE = Path.home() / ".cache" / "toggl-current.json"
CURRENT_CACHE_TTL = 30.0  # seconds; matches tg-tui's steady-state poll cadence


def _notify_tui():
    """SIGUSR1 the running tg-tui so it refreshes now instead of on its poll.
    Best-effort: a missing/stale pid file or dead process is ignored."""
    try:
        os.kill(int(TG_TUI_PID.read_text().strip()), signal.SIGUSR1)
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
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", _auth_header())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    result = json.loads(resp.read())
                    if method != "GET":  # a mutation succeeded → wake tg-tui now
                        _invalidate_current_cache()  # running state may have changed
                        _notify_tui()
                    return result
                return None
        except urllib.error.HTTPError as e:
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
        raise RuntimeError(f"Toggl API DELETE -> {e.code}: {e.read().decode()}")
