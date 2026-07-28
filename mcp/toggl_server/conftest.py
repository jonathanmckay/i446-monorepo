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
