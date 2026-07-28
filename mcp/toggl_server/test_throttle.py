"""Tests for the cross-process Toggl client-side throttle."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(tmp_path, **overrides):
    spec = importlib.util.spec_from_file_location("toggl_throttle_t", HERE / "throttle.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["toggl_throttle_t"] = m
    spec.loader.exec_module(m)
    m.STATE = tmp_path / "throttle.json"
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def test_burst_is_free_then_paces(tmp_path):
    # No refill, capacity 2: first two go free, the third must wait (capped).
    m = _load(tmp_path, RATE=0.001, BURST=2.0, MAX_WAIT=0.05)
    assert m.acquire() == 0.0
    assert m.acquire() == 0.0
    waited = m.acquire()
    assert waited > 0.0, "an empty bucket must pace the next request"
    assert waited <= 0.2, "but never longer than MAX_WAIT"


def test_refill_lets_more_through(tmp_path):
    # Fast refill: after spending the one token, the next is available almost immediately.
    m = _load(tmp_path, RATE=100.0, BURST=1.0, MAX_WAIT=1.0)
    assert m.acquire() == 0.0
    assert m.acquire() < 0.3, "a refilled bucket should not block long"


def test_cooldown_paces_but_is_capped(tmp_path):
    m = _load(tmp_path, RATE=100.0, BURST=5.0, MAX_WAIT=0.05, COOLDOWN=30.0)
    m.note_rate_limit()  # arm a 30s cooldown
    waited = m.acquire()
    assert waited > 0.0, "a live cooldown must back off"
    assert waited <= 0.2, "but capped at MAX_WAIT so a CLI never freezes"


def test_cooldown_shared_via_state_file(tmp_path):
    # A 402 noted by 'one process' is seen by a freshly-loaded module sharing the file.
    m1 = _load(tmp_path, COOLDOWN=30.0)
    m1.note_rate_limit()
    m2 = _load(tmp_path, RATE=100.0, BURST=5.0, MAX_WAIT=0.05)
    assert m2.acquire() > 0.0, "cooldown must be visible cross-process via the state file"


def test_expired_cooldown_does_not_block(tmp_path):
    m = _load(tmp_path, RATE=100.0, BURST=5.0, MAX_WAIT=1.0)
    m.note_rate_limit(-1)  # already in the past
    assert m.acquire() == 0.0


def test_no_fcntl_is_noop(tmp_path, monkeypatch):
    m = _load(tmp_path)
    monkeypatch.setattr(m, "fcntl", None)
    assert m.acquire() == 0.0
    m.note_rate_limit()  # must not raise


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_cooling_down_reflects_shared_cooldown(tmp_path):
    m = _load(tmp_path, COOLDOWN=30.0)
    assert m.cooling_down() is False, "no cooldown initially"
    m.note_rate_limit()
    assert m.cooling_down() is True, "a 402 puts every process into cooldown"


def test_cooling_down_false_after_expiry(tmp_path):
    m = _load(tmp_path)
    m.note_rate_limit(-1)  # already expired
    assert m.cooling_down() is False
