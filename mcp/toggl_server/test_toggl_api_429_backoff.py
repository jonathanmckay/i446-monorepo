"""Regression test (2026-06-21): Toggl is a ~1 req/sec leaky bucket, and the
shared client had no 429 handling — a tripped limit raised RuntimeError straight
to the UI and the next poll re-tripped it (self-amplifying). Fix: _request honours
Retry-After (falling back to capped exponential backoff) and retries, mirroring
ibx/slack.py and ibx/sync_external_replies.py. A non-429 HTTPError must still
raise immediately; a 429 that never clears must raise after the retry budget.
"""
import sys
import urllib.error
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]  # .../i446-monorepo/mcp
sys.path.insert(0, str(MCP_DIR))
from toggl_server import toggl_api  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_throttle(monkeypatch):
    """These tests exercise _request's 429 retry/backoff in isolation. The
    client-side throttle is tested separately (test_throttle.py); stub it here so
    its cross-process state file and internal time.sleep() (which shares the same
    `time` module the tests monkeypatch) don't perturb the backoff assertions."""
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    monkeypatch.setattr(toggl_api.throttle, "note_rate_limit", lambda *a, **k: None)


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"id": 1}'


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("u", code, "msg", headers, None)


def test_retries_after_429_then_succeeds(monkeypatch):
    calls = []
    slept = []
    monkeypatch.setattr(toggl_api.time, "sleep", lambda s: slept.append(s))

    def fake_urlopen(req, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, "1")
        return _FakeResp()

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake_urlopen)
    assert toggl_api.get_current() == {"id": 1}
    assert len(calls) == 2, "must retry once after a 429"
    assert slept == [1], "must honour Retry-After before retrying"


def test_429_without_retry_after_uses_backoff(monkeypatch):
    slept = []
    monkeypatch.setattr(toggl_api.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: (_ for _ in ()).throw(_http_error(429)))
    try:
        toggl_api.get_current()
    except RuntimeError:
        pass
    # No header → exponential fallback (2**0, 2**1) on the first two attempts.
    assert slept == [1, 2], f"expected exponential backoff, got {slept}"


def test_persistent_429_raises_after_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api.time, "sleep", lambda s: None)
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: calls.append(1) or (_ for _ in ()).throw(_http_error(429)))
    try:
        toggl_api.get_current()
        assert False, "a 429 that never clears must raise"
    except RuntimeError:
        pass
    assert len(calls) == toggl_api._MAX_429_RETRIES, "must exhaust the retry budget"


def test_non_429_error_raises_immediately(monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("must not sleep on a 500")))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: calls.append(1) or (_ for _ in ()).throw(_http_error(500)))
    try:
        toggl_api.get_current()
        assert False, "a 500 must raise"
    except RuntimeError:
        pass
    assert len(calls) == 1, "non-429 must not retry"
