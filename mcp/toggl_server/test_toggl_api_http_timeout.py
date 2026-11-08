"""Regression (2026-11-05 06:01 janus/dtd freeze): every Toggl urlopen()
carried no timeout, so a connection left half-open across a laptop sleep
could block its caller forever. Both call sites must pass HTTP_TIMEOUT."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp
from toggl_server import toggl_api  # noqa: E402


class _Resp(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_and_delete_pass_a_timeout(monkeypatch):
    seen = []

    def fake_urlopen(req, *a, **k):
        seen.append(k.get("timeout", a[1] if len(a) > 1 else None))
        return _Resp(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr(toggl_api.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(toggl_api.throttle, "acquire", lambda *a, **k: 0.0)
    monkeypatch.setattr(toggl_api.throttle, "quota_remaining", lambda *a: 0)
    monkeypatch.setattr(toggl_api, "_notify_tui", lambda: None)
    monkeypatch.setattr(toggl_api, "_write_current_cache", lambda *a: None, raising=False)
    toggl_api._request("GET", "/me")
    toggl_api.delete_entry(1)
    assert len(seen) == 2
    assert all(isinstance(t, (int, float)) and 0 < t <= 60 for t in seen), seen
