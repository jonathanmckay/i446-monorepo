"""Regression test (2026-08-21): a bare Todoist 503 killed the scheduled
'Todoist Hygiene Check' GitHub Actions run with no retry -- get_all_tasks()
called requests.get() once and raised straight through. Fix: _get_with_retry
retries transient 5xx with backoff before giving up."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent

with patch.dict("os.environ", {"TODOIST_API_KEY": "test-key"}):
    spec = importlib.util.spec_from_file_location(
        "todoist_hygiene_check", HERE / "todoist-hygiene-check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    def raise_for_status():
        if status >= 400:
            raise m.requests.exceptions.HTTPError(f"{status} Server Error")
    r.raise_for_status.side_effect = raise_for_status
    return r


def test_retries_transient_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    responses = [_resp(503), _resp(503), _resp(200, {"results": [], "next_cursor": None})]
    get = MagicMock(side_effect=responses)
    monkeypatch.setattr(m.requests, "get", get)

    tasks = m.get_all_tasks()

    assert tasks == []
    assert get.call_count == 3, "must retry on 503 instead of failing on the first attempt"


def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    get = MagicMock(return_value=_resp(503))
    monkeypatch.setattr(m.requests, "get", get)

    try:
        m.get_all_tasks()
        assert False, "expected the persistent 503 to eventually raise"
    except Exception as e:  # noqa: BLE001
        assert "503" in str(e)
    assert get.call_count == 4, "should stop retrying at max_attempts, not loop forever"


def test_non_5xx_error_does_not_retry(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    get = MagicMock(return_value=_resp(401))
    monkeypatch.setattr(m.requests, "get", get)

    try:
        m.get_all_tasks()
        assert False, "expected a 401 to raise"
    except Exception:
        pass
    assert get.call_count == 1, "a non-transient error (e.g. bad auth) must not be retried"
