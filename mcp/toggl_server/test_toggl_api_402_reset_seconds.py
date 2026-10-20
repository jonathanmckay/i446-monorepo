"""Regression (2026-10-16): janus "hit the rate limit and now is just stalling".

Toggl's free-tier HOURLY cap answers 402 with "Your quota will reset in N
seconds". The shared cooldown ignored the body and armed a fixed 60s, so every
poller re-tripped the cap once a minute for the rest of the hour and janus's
screen stopped updating with no explanation. _request must hand the server's
reset estimate to throttle.note_rate_limit; the throttle must honour it.
"""
import io
import sys
import urllib.error
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))
from toggl_server import throttle, toggl_api  # noqa: E402

BODY_402 = (b"You have hit your hourly limit for API calls. Please upgrade to a paid plan "
            b"for increased access. Your quota will reset in 653 seconds. CTA: Go to subscriptions page.")


def _http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))


def test_parse_reset_seconds():
    assert throttle.parse_reset_seconds(BODY_402) == 653.0
    assert throttle.parse_reset_seconds(BODY_402.decode()) == 653.0
    assert throttle.parse_reset_seconds(b"something else") is None
    assert throttle.parse_reset_seconds(None) is None


def test_402_body_reset_seconds_reach_the_shared_cooldown(monkeypatch):
    seen = []
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    monkeypatch.setattr(toggl_api.throttle, "note_quota_reset",
                        lambda s=None, bucket=None: seen.append((s, bucket)))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: (_ for _ in ()).throw(_http_error(402, BODY_402)))
    with pytest.raises(RuntimeError, match="402"):
        toggl_api.get_current()
    assert seen == [(653.0, "user")], "a /me 402 arms the USER bucket only"


def test_402_without_reset_hint_keeps_default_cooldown(monkeypatch):
    seen = []
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    monkeypatch.setattr(toggl_api.throttle, "note_quota_reset",
                        lambda s=None, bucket=None: seen.append(s))
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: (_ for _ in ()).throw(_http_error(402, b"nope")))
    with pytest.raises(RuntimeError):
        toggl_api.get_current()
    assert seen == [None]


def test_note_quota_reset_honours_server_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(throttle, "STATE", tmp_path / "throttle.json")
    throttle.note_quota_reset(653.0)
    left = throttle.cooldown_remaining()
    assert 653.0 <= left <= 653.0 + throttle.RESET_MARGIN + 1, left
    assert throttle.cooling_down()


def test_note_quota_reset_floors_and_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(throttle, "STATE", tmp_path / "throttle.json")
    throttle.note_quota_reset(1.0)   # tiny estimate: never below the default
    assert throttle.cooldown_remaining() >= throttle.COOLDOWN - 1
    throttle.note_quota_reset(10 ** 6)  # absurd estimate: capped
    assert throttle.cooldown_remaining() <= throttle.MAX_COOLDOWN
    throttle.note_quota_reset(None)  # no estimate: the default
    assert throttle.cooldown_remaining() <= throttle.COOLDOWN
    monkeypatch.setattr(throttle, "STATE", tmp_path / "missing.json")
    assert throttle.cooldown_remaining() == 0.0
