"""Tiny Todoist API client.

Single source for the API token + create/close primitives. Skills should
import from here instead of hand-rolling HTTP calls.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

API = "https://api.todoist.com/api/v1"
TIMEOUT = 10
TOKEN_FILE = Path.home() / ".config/todoist/token"


def _token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    t = os.environ.get("TODOIST_TOKEN") or os.environ.get("TODOIST_API_TOKEN")
    if t:
        return t
    raise RuntimeError("Todoist token not found at ~/.config/todoist/token or in env")


def _request(method: str, path: str, body: Optional[dict] = None) -> Any:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Todoist {method} {path} → {e.code}: {e.read().decode('utf-8', 'replace')}")


def create_task(content: str, *, labels: Optional[list] = None,
                due_string: Optional[str] = None, priority: Optional[int] = None,
                project_id: Optional[str] = None) -> dict:
    """Create a task. Returns the created task dict."""
    body: dict = {"content": content}
    if labels:        body["labels"] = labels
    if due_string:    body["due_string"] = due_string
    if priority:      body["priority"] = priority
    if project_id:    body["project_id"] = project_id
    return _request("POST", "/tasks", body)


def close_task(task_id: str) -> None:
    _request("POST", f"/tasks/{task_id}/close")


def find_tasks(*, labels: Optional[list] = None, content_contains: Optional[str] = None,
               limit: int = 50) -> list:
    """Paginated search. Returns up to `limit` matching tasks."""
    out: list = []
    cursor = None
    while True:
        path = f"/tasks?limit={min(limit - len(out), 50)}"
        if cursor:
            path += f"&cursor={urllib.parse.quote(cursor)}"
        if labels:
            for lbl in labels:
                path += f"&label={urllib.parse.quote(lbl, safe='')}"
        page = _request("GET", path)
        results = page.get("results", page) if isinstance(page, dict) else page
        if not results:
            break
        for t in results:
            if content_contains and content_contains.lower() not in t.get("content", "").lower():
                continue
            out.append(t)
            if len(out) >= limit:
                return out
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor:
            break
    return out
