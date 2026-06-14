"""Regression test (2026-06-14): /d357 (and any MCP-driven timer change) didn't
update tg-tui's timer fast enough. toggl_cli.py nudged tg-tui via SIGUSR1 after a
timer change, but the MCP server path (server.py → toggl_api) did not, so tg-tui
only caught the change on its 30s ticker_current poll. Fix: nudge in the shared
toggl_api._request layer on any successful non-GET (mutation), so every caller —
MCP server, /d357, toggl_cli — wakes tg-tui immediately. GETs must NOT signal.
"""
import signal
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]  # .../i446-monorepo/mcp
sys.path.insert(0, str(MCP_DIR))
from toggl_server import toggl_api  # noqa: E402


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"id": 1}'


def _patch(monkeypatch, tmp_path, pid="4242"):
    pidfile = tmp_path / "tg-tui.pid"
    pidfile.write_text(pid)
    monkeypatch.setattr(toggl_api, "TG_TUI_PID", pidfile)
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp())
    sent = []
    monkeypatch.setattr(toggl_api.os, "kill", lambda p, s: sent.append((p, s)))
    return sent


def test_start_timer_signals_tg_tui(monkeypatch, tmp_path):
    sent = _patch(monkeypatch, tmp_path)
    toggl_api.start_timer("Francois 1:1")
    assert (4242, signal.SIGUSR1) in sent, "start must SIGUSR1 tg-tui"


def test_stop_timer_signals_tg_tui(monkeypatch, tmp_path):
    sent = _patch(monkeypatch, tmp_path)
    toggl_api.stop_timer(123)
    assert (4242, signal.SIGUSR1) in sent, "stop must SIGUSR1 tg-tui"


def test_create_entry_signals_tg_tui(monkeypatch, tmp_path):
    sent = _patch(monkeypatch, tmp_path)
    toggl_api.create_entry("backfill", "2026-06-14T10:00:00Z",
                           "2026-06-14T10:30:00Z", 1800)
    assert (4242, signal.SIGUSR1) in sent


def test_get_current_does_not_signal(monkeypatch, tmp_path):
    sent = _patch(monkeypatch, tmp_path)
    toggl_api.get_current()
    assert sent == [], "reads must not signal tg-tui"


def test_nudge_silent_when_tg_tui_not_running(monkeypatch, tmp_path):
    """A missing pid file (tg-tui not open) must never break a timer write."""
    monkeypatch.setattr(toggl_api, "TG_TUI_PID", tmp_path / "absent.pid")
    monkeypatch.setattr(toggl_api.urllib.request, "urlopen",
                        lambda req, *a, **k: _FakeResp())
    killed = []
    monkeypatch.setattr(toggl_api.os, "kill", lambda p, s: killed.append((p, s)))
    toggl_api.start_timer("meeting")  # must not raise
    assert killed == []
