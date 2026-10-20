"""2026-10-20: Toggl's hourly sliding-window quota is per BUCKET: /api/v9/me/*
reads are charged to one, /workspaces/* writes to another (measured live:
/me/current had 184 left while /workspaces showed 0, and the reverse an hour
earlier). The one global cooldown used to park the writes whenever the read
bucket was exhausted.

Contract: reads stay on /me (so read load can never block a write); a 402
arms only its own bucket; a request to an exhausted bucket raises without
touching the network; the other bucket keeps flowing; get_entries sends
local-midnight RFC3339 bounds and is v9-shaped.
"""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))
from toggl_server import throttle, toggl_api  # noqa: E402

BODY_402 = (b"You have hit your hourly limit for API calls. Please upgrade to a paid plan "
            b"for increased access. Your quota will reset in 1259 seconds. CTA: Go to subscriptions page.")


class _Resp:
    status = 200

    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(throttle, "STATE", tmp_path / "throttle.json")
    monkeypatch.setattr(toggl_api, "CURRENT_CACHE", tmp_path / "current.json")
    monkeypatch.setattr(toggl_api, "JANUS_PID", tmp_path / "absent.pid")
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    return tmp_path


def test_bucket_for_paths():
    assert throttle.bucket_for("/me/time_entries/current") == "user"
    assert throttle.bucket_for("/me/time_entries?start_date=x") == "user"
    assert throttle.bucket_for("/workspaces/1/projects") == "ws"
    assert throttle.bucket_for("/workspace/1/search/time_entries") == "ws"
    assert throttle.bucket_for("/workspaces/1/time_entries", "POST") == "write"
    assert throttle.bucket_for("/workspaces/1/time_entries/5/stop", "PATCH") == "write"
    assert throttle.bucket_for("/workspaces/1/time_entries/5", "DELETE") == "write"


def test_workspace_read_402_never_blocks_a_write(iso, monkeypatch):
    """The 09:30 outage: workspace GETs (projects/tags/Reports) at 0 must not
    make the fail-fast refuse a POST, which has its own counter."""
    def fake(req, *a, **k):
        if req.get_method() == "GET":
            raise _http_error(402, BODY_402)
        return _Resp(b'{"id": 3, "duration": -1}', {"X-Toggl-Quota-Remaining": "599"})
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="402"):
        toggl_api.get_projects()
    assert throttle.cooling_down("ws") and not throttle.cooling_down("write")
    assert toggl_api.start_timer("teams")["id"] == 3


def test_user_402_does_not_block_workspace_requests(iso, monkeypatch):
    urls = []

    def fake(req, *a, **k):
        urls.append(req.full_url)
        if "/me/" in req.full_url:
            raise _http_error(402, BODY_402)
        return _Resp(b'{"id": 1}', {"X-Toggl-Quota-Remaining": "580"})

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="402"):
        toggl_api.get_current()
    assert throttle.cooling_down("user") and not throttle.cooling_down("write")
    assert throttle.cooling_down(), "no-arg view stays conservative (any bucket)"
    # Writes keep flowing on their own bucket …
    assert toggl_api.stop_timer(1)["id"] == 1
    # … and the exhausted bucket is not even sent to.
    n = len(urls)
    with pytest.raises(RuntimeError, match="not sent"):
        toggl_api.get_current()
    assert len(urls) == n, "an exhausted bucket must not cost a network request"


def test_zero_remaining_header_arms_cooldown_proactively(iso, monkeypatch):
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", lambda req, *a, **k: _Resp(
        b'null', {"X-Toggl-Quota-Remaining": "0", "X-Toggl-Quota-Resets-In": "900"}))
    assert toggl_api.get_current() is None
    assert 890 <= throttle.quota_remaining("user") <= 910
    assert throttle.quota_remaining("ws") == 0.0


V9_PAGE = [
    {"id": 4594024688, "description": "ibx m5x2", "project_id": 108359987, "tags": ["-1"],
     "start": "2026-10-20T15:22:46+00:00", "stop": "2026-10-20T15:39:11+00:00", "duration": 985},
    {"id": 555, "description": "sons", "project_id": None, "tags": [],
     "start": "2026-10-20T16:00:00+00:00", "stop": None, "duration": -1792512000},
]


def test_get_entries_reads_me_bucket_with_local_midnight_bounds(iso, monkeypatch):
    calls = []

    def fake(req, *a, **k):
        calls.append(req.full_url)
        return _Resp(json.dumps(V9_PAGE).encode(), {"X-Toggl-Quota-Remaining": "500"})
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake)
    monkeypatch.setattr(toggl_api, "_local_tz", lambda: __import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    out = toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert len(calls) == 1 and "/me/time_entries?" in calls[0], "one read, on the /me bucket"
    from urllib.parse import unquote
    assert "start_date=2026-10-20T00:00:00-07:00" in unquote(calls[0])
    assert "end_date=2026-10-21T00:00:00-07:00" in unquote(calls[0]), "end exclusive, local midnight"
    assert [e["id"] for e in out] == [4594024688, 555]
    assert all("/workspace" not in u for u in calls), "reads never touch the write bucket"
    # completed-only view drops the running entry
    out2 = toggl_api.get_entries("2026-10-20", "2026-10-21", include_running=False)
    assert [e["id"] for e in out2] == [4594024688]
    # RFC3339 bounds (1i9 / 1s-survey style) pass through as the same local day
    toggl_api.get_entries("2026-10-19T00:00:00-07:00", "2026-10-20T00:00:00-07:00")
    assert "start_date=2026-10-19T00:00:00-07:00" in unquote(calls[-1])


def test_read_402_never_blocks_a_write(iso, monkeypatch):
    def fake(req, *a, **k):
        if "/me/" in req.full_url:
            raise _http_error(402, BODY_402)
        return _Resp(b'{"id": 9, "duration": -1}', {"X-Toggl-Quota-Remaining": "599"})
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="402"):
        toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert throttle.cooling_down("user") and not throttle.cooling_down("write")
    assert toggl_api.start_timer("teams")["id"] == 9
