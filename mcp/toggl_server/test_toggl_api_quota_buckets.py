"""2026-10-20: Toggl's hourly sliding-window quota is per BUCKET. /api/v9/me/*
is 30/hr on EVERY plan (the 402 body's "upgrade" text notwithstanding);
workspace-scoped calls and the Reports API get the plan's 600/hr. Every read
used /me/* and all of them 402'd by 09:00 most mornings while 580 workspace
requests sat unused -- and the one global cooldown then parked the writes too.

Contract: get_entries is Reports-backed (workspace bucket) and v9-shaped;
a 402 arms only its own bucket; a request to an exhausted bucket raises
without touching the network; the other bucket keeps flowing.
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
    monkeypatch.setattr(toggl_api, "TAGS_CACHE", tmp_path / "tags.json")
    monkeypatch.setattr(toggl_api, "JANUS_PID", tmp_path / "absent.pid")
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    return tmp_path


def test_bucket_for_paths():
    assert throttle.bucket_for("/me/time_entries/current") == "user"
    assert throttle.bucket_for("/me/time_entries?start_date=x") == "user"
    assert throttle.bucket_for("/workspaces/1/time_entries") == "ws"
    assert throttle.bucket_for("/workspace/1/search/time_entries") == "ws"


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
    assert throttle.cooling_down("user") and not throttle.cooling_down("ws")
    assert throttle.cooling_down(), "no-arg view stays conservative (any bucket)"
    # Writes keep flowing on the workspace bucket …
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


REPORT_PAGE = [{
    "user_id": 2924766, "project_id": 108359987, "task_id": None, "billable": False,
    "description": "ibx m5x2", "tag_ids": [11069640],
    "time_entries": [{"id": 4594024688, "seconds": 985, "start": "2026-10-20T08:22:46-07:00",
                      "stop": "2026-10-20T08:39:11-07:00", "at": "2026-10-20T15:39:11+00:00"}],
}, {
    "user_id": 2924766, "project_id": None, "task_id": None, "billable": False,
    "description": "late night", "tag_ids": [],
    "time_entries": [{"id": 2, "seconds": 60, "start": "2026-10-19T23:30:00-07:00",
                      "stop": "2026-10-19T23:31:00-07:00", "at": "x"}],
}]


def _reports_fake(calls, pages=None):
    pages = pages or {1: (REPORT_PAGE, None)}

    def fake(req, *a, **k):
        calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        if "/reports/api/v3/" in req.full_url:
            first = json.loads(req.data)["first_row_number"]
            rows, nxt = pages.get(first, ([], None))
            h = {"X-Next-Row-Number": str(nxt)} if nxt else {}
            return _Resp(json.dumps(rows).encode(), h)
        if req.full_url.endswith("/tags"):
            return _Resp(b'[{"id": 11069640, "name": "-1"}]')
        raise AssertionError(f"unexpected {req.full_url}")
    return fake


def test_get_entries_is_reports_backed_and_v9_shaped(iso, monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", _reports_fake(calls))
    monkeypatch.setattr(toggl_api, "_local_tz", lambda: __import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    out = toggl_api.get_entries("2026-10-20", "2026-10-21")  # v9 contract: end exclusive
    assert all("/me/" not in u for _, u, _ in calls), "no user-bucket read for entries"
    body = calls[0][2]
    assert body["start_date"] == "2026-10-19" and body["end_date"] == "2026-10-21", "widened a day each side"
    assert [e["id"] for e in out] == [4594024688], "the 10-19 23:30 row is filtered out locally"
    e = out[0]
    assert e["duration"] == 985 and e["project_id"] == 108359987 and e["description"] == "ibx m5x2"
    assert e["tags"] == ["-1"], "tag ids resolved to names via the cached workspace tag list"
    assert e["stop"] and e["start"].startswith("2026-10-20")
    # second call: tag map served from disk, no /tags refetch
    n = len(calls)
    toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert not any(u.endswith("/tags") for _, u, _ in calls[n:])


def test_get_entries_paginates_on_next_row_header(iso, monkeypatch):
    calls = []
    page2 = [{"user_id": 1, "project_id": None, "task_id": None, "billable": False, "description": "p2",
              "tag_ids": [], "time_entries": [{"id": 9, "seconds": 5, "start": "2026-10-20T09:00:00-07:00",
                                               "stop": "2026-10-20T09:00:05-07:00", "at": "x"}]}]
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        _reports_fake(calls, {1: (REPORT_PAGE, 3), 3: (page2, None)}))
    monkeypatch.setattr(toggl_api, "_local_tz", lambda: __import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    out = toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert sorted(e["id"] for e in out) == [9, 4594024688]
    assert [c[2]["first_row_number"] for c in calls if c[2]] == [1, 3]


def test_get_entries_merges_cached_running_entry_without_network(iso, monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", _reports_fake(calls))
    monkeypatch.setattr(toggl_api, "_local_tz", lambda: __import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    running = {"id": 555, "description": "sons", "start": "2026-10-20T16:00:00+00:00",
               "stop": None, "duration": -1, "project_id": None, "tags": []}
    toggl_api._write_current_cache(running)
    out = toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert [e["id"] for e in out][-1] == 555 and out[-1]["duration"] < 0
    assert all("/me/" not in u for _, u, _ in calls)
    # Reports already has it completed (stopped from the phone): Reports wins, no duplicate.
    toggl_api._write_current_cache({**running, "id": 4594024688})
    out = toggl_api.get_entries("2026-10-20", "2026-10-21")
    assert [e["id"] for e in out].count(4594024688) == 1 and out[0]["duration"] == 985
    # Outside the window, or asked for completed time only: not merged.
    toggl_api._write_current_cache(running)
    assert all(e["id"] != 555 for e in toggl_api.get_entries("2026-10-19", "2026-10-20"))
    assert all(e["id"] != 555 for e in toggl_api.get_entries("2026-10-20", "2026-10-21", include_running=False))


def test_get_entries_accepts_rfc3339_bounds(iso, monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", _reports_fake(calls))
    monkeypatch.setattr(toggl_api, "_local_tz", lambda: __import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
    out = toggl_api.get_entries("2026-10-19T00:00:00-07:00", "2026-10-20T00:00:00-07:00")
    assert [e["id"] for e in out] == [2], "1i9/1s-style local datetime bounds reduce to the local day"
