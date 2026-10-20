"""Regression test (2026-06-21): /current was polled independently by janus
(30s), every open dtd picker (dtd-ticker), and others, each hitting Toggl's
~1 req/sec bucket. Fix: a shared write-through cache — get_current() persists the
running entry, get_current_cached() serves it within CURRENT_CACHE_TTL so N
pollers collapse to ~one fetch per window, and any mutation invalidates it.
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
    monkeypatch.setattr(toggl_api.time, "time", lambda: 1_100.0)  # 100s old > TTL
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


def test_mutation_invalidates_cache(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 99}}))  # would be "fresh"
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp(b'{"id": 1}'))
    toggl_api.stop_timer(123)
    assert not cache.exists(), "a mutation must drop the shared current cache"


def test_delete_invalidates_cache(monkeypatch, tmp_path):
    cache = _isolate_cache(monkeypatch, tmp_path)
    cache.write_text(json.dumps({"ts": 9e9, "entry": {"id": 99}}))

    class _DelResp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _DelResp())
    assert toggl_api.delete_entry(123) is True
    assert not cache.exists(), "deleting an entry must drop the shared current cache"
