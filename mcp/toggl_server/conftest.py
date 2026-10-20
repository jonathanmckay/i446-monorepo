"""Shared pytest fixtures for the toggl_server tests.

Every toggl_api call now passes through the cross-process throttle, whose state
lives in ~/.cache/toggl-throttle.json. A test that simulates a 402 arms a
cooldown there, and any later test making a real toggl_api call would then block
for MAX_WAIT (~8s) — cross-test pollution that ballooned the suite to ~90s.
Give each test a clean throttle slate so they neither leak cooldowns into one
another nor inherit the developer's live cooldown.
"""
import pytest

from pathlib import Path

_THROTTLE_STATE = Path.home() / ".cache" / "toggl-throttle.json"


@pytest.fixture(autouse=True)
def _clean_toggl_throttle():
    _THROTTLE_STATE.unlink(missing_ok=True)
    yield
    _THROTTLE_STATE.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _isolate_toggl_caches(monkeypatch, tmp_path):
    """Mutations write-through the shared running-timer cache (2026-10-20), so
    a test's fake start_timer({"id": 1}) would otherwise plant a phantom
    running entry in the developer's live ~/.cache/toggl-current.json (it
    did, once). Point every cache file at tmp unless the test overrides it."""
    from toggl_server import toggl_api
    monkeypatch.setattr(toggl_api, "CURRENT_CACHE", tmp_path / "toggl-current.json")
    monkeypatch.setattr(toggl_api, "JANUS_PID", tmp_path / "janus.pid")
    monkeypatch.setattr(toggl_api, "PROJECTS_CACHE", tmp_path / "toggl-projects.json")
    yield
