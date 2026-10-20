"""Regression test (2026-06-21): /current was polled independently by janus
(30s), every open dtd picker (dtd-ticker), and others, each hitting Toggl's
~1 req/sec bucket. Fix: a shared write-through cache — get_current() persists the
running entry, get_current_cached() serves it within CURRENT_CACHE_TTL so N
pollers collapse to ~one fetch per window.

2026-10-20: /me/time_entries/current is charged to Toggl's 30/hr user bucket
on every plan, so a mutation no longer INVALIDATES the cache (which made every
poller re-read): it write-throughs the running state it already knows from its
own response — start -> the new entry, stop/delete-of-running -> None.
"""
import json
import sys
import urllib.error
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]  # .../i446-monorepo/mcp
sys.path.insert(0, str(MCP_DIR))
from toggl_server import toggl_api  # noqa: E402


class _FakeResp:
    status = 200

    def __init__(self, payload=b'{"id": 7, "start": "2026-06-21T10:00:00Z"}'):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _isolate_cache(monkeypatch, tmp_path):
    cache = tmp_path / "toggl-current.json"
    monkeypatch.setattr(toggl_api, "CURRENT_CACHE", cache)
    monkeypatch.setattr(toggl_api, "JANUS_PID", tmp_path / "absent.pid")
    return cache


def test_get_current_writes_through_to_cache(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp())
    entry = toggl_api.get_current()
    assert entry["id"] == 7
    raw = json.loads(cache.read_text())
    assert raw["entry"]["id"] == 7 and "ts" in raw


def test_cached_read_serves_fresh_without_network(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 1_000.0, "entry": {"id": 99}}))
    monkeypatch.setattr(toggl_api.time, "time", lambda: 1_010.0)  # 10s old < 30s TTL

    def _boom(*a, **k):
        raise AssertionError("fresh cache must not hit the network")

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", _boom)
    assert toggl_api.get_current_cached()["id"] == 99


def test_cached_read_refetches_when_stale(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 1_000.0, "entry": {"id": 99}}))
    monkeypatch.setattr(toggl_api.time, "time", lambda: 1_400.0)  # 400s old > 300s TTL
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp())
    assert toggl_api.get_current_cached()["id"] == 7, "stale cache must refetch live"


def test_cached_read_falls_back_on_missing_or_torn(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp())
    assert toggl_api.get_current_cached()["id"] == 7, "missing cache → live"
    cache.write_text("{not json")
    assert toggl_api.get_current_cached()["id"] == 7, "torn cache → live"


def test_stop_writes_idle_through_to_cache(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 99}}))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp(b'{"id": 99, "duration": 60}'))
    toggl_api.stop_timer(99)
    raw = json.loads(cache.read_text())
    assert raw["entry"] is None, "a stop must record 'idle' without a re-read"


def test_start_writes_new_entry_through_to_cache(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp(b'{"id": 5, "duration": -1, "start": "2026-10-20T10:00:00Z"}'))
    toggl_api.start_timer("teams")
    assert json.loads(cache.read_text())["entry"]["id"] == 5


def test_create_completed_entry_leaves_cache_alone(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 99}}))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp(b'{"id": 1, "duration": 600}'))
    toggl_api.create_entry("backfill", "2026-10-20T08:00:00Z", "2026-10-20T08:10:00Z", 600)
    assert json.loads(cache.read_text())["entry"]["id"] == 99, "a backfilled entry does not change what's running"


class _DelResp:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_delete_of_running_entry_writes_idle(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 123}}))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _DelResp())
    assert toggl_api.delete_entry(123) is True
    assert json.loads(cache.read_text())["entry"] is None


def test_delete_of_other_entry_leaves_cache_alone(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 99}}))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _DelResp())
    assert toggl_api.delete_entry(123) is True
    assert json.loads(cache.read_text())["entry"]["id"] == 99


def test_reopen_via_update_writes_entry_as_running(monkeypatch, tmp_path):
    """janus reopen: PUT stop=None/duration=-epoch on a NON-running id. The
    response says it runs now; the cache must say so without a re-read."""
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": None}))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp(b'{"id": 77, "duration": -1700000000}'))
    toggl_api.update_entry(77, stop=None, duration=-1700000000)
    assert json.loads(cache.read_text())["entry"]["id"] == 77
