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


def _notify_tui():
    """SIGUSR1 the running tg-tui so it refreshes now instead of on its poll.
    Best-effort: a missing/stale pid file or dead process is ignored."""
    try:
        os.kill(int(TG_TUI_PID.read_text().strip()), signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


def _auth_header():
    creds = base64.b64encode(f"{TOGGL_API_KEY}:api_token".encode()).decode()
    return f"Basic {creds}"


_MAX_429_RETRIES = 3  # Toggl is a ~1 req/sec leaky bucket; bursts get 429.


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
                        _notify_tui()
                    return result
                return None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _MAX_429_RETRIES - 1:
                retry_after = e.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                time.sleep(min(delay, 30))
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
    return _request("GET", "/me/time_entries/current")


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
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Toggl API DELETE -> {e.code}: {e.read().decode()}")
