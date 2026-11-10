"""Regression (2026-11-10): janus on Straylight froze for 2h on a flapping hotel
link with its Toggl tickers stuck inside urlopen, which had no timeout. Every
urlopen in toggl_api must carry timeout=_HTTP_TIMEOUT so a stalled connect or
half-open read raises instead of blocking forever."""
import ast
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from toggl_server import toggl_api  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_throttle(monkeypatch):
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


def test_request_passes_a_finite_timeout_to_urlopen(monkeypatch):
    seen = {}

    def fake_urlopen(req, *a, **k):
        seen.update(k)
        return _FakeResp()

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake_urlopen)
    assert toggl_api.get_current() == {"id": 1}
    assert seen.get("timeout") == toggl_api._HTTP_TIMEOUT
    assert 0 < toggl_api._HTTP_TIMEOUT <= 30


def test_every_urlopen_in_toggl_api_has_a_timeout():
    tree = ast.parse((HERE / "toggl_api.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "urlopen"]
    assert calls, "expected urlopen call sites"
    missing = [c.lineno for c in calls if not any(k.arg == "timeout" for k in c.keywords)]
    assert not missing, f"urlopen without timeout= at lines {missing}"
