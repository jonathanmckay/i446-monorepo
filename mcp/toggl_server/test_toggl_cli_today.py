"""Regression tests for toggl_cli cmd_today.

Bug: cmd_today filtered to entries whose START date is today, so an overnight
timer (睡觉 21:47 → 07:38 next morning) was invisible to today's coverage.
/inbound's gap checker then asked "what did you do 04:00-06:00?" even though
the user was demonstrably asleep. Cross-midnight entries must be included,
clipped to 00:00 (the day-barrier view).
"""
import datetime
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("toggl_cli_t", HERE / "toggl_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules["toggl_cli_t"] = cli
_spec.loader.exec_module(cli)


def _run_today(monkeypatch, entries):
    monkeypatch.setattr(cli.toggl_api, "get_entries", lambda **kw: entries)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_today([])
    return buf.getvalue()


def _entry(eid, desc, start_dt, stop_dt):
    return {
        "id": eid,
        "description": desc,
        "project_id": None,
        "start": start_dt.isoformat(),
        "stop": stop_dt.isoformat() if stop_dt else None,
        "duration": (int((stop_dt - start_dt).total_seconds()) if stop_dt else -1),
    }


def test_today_includes_cross_midnight_entry_clipped(monkeypatch):
    TZ = cli.TZ
    today = datetime.datetime.now(TZ).date()
    yest = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yest, datetime.time(21, 47), tzinfo=TZ)
    stop = datetime.datetime.combine(today, datetime.time(7, 38), tzinfo=TZ)
    out = _run_today(monkeypatch, [_entry(1, "睡觉", start, stop)])
    # Visible, clipped to the day barrier
    assert "00:00-07:38 睡觉" in out, out
    # Duration clipped to today's portion (7h38m = 458 min), not the full 9h51m
    assert "(458min)" in out, out


def test_today_excludes_entry_fully_yesterday(monkeypatch):
    TZ = cli.TZ
    today = datetime.datetime.now(TZ).date()
    yest = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yest, datetime.time(10, 0), tzinfo=TZ)
    stop = datetime.datetime.combine(yest, datetime.time(11, 0), tzinfo=TZ)
    out = _run_today(monkeypatch, [_entry(2, "old meeting", start, stop)])
    assert "old meeting" not in out, out


def test_today_keeps_normal_same_day_entry(monkeypatch):
    TZ = cli.TZ
    today = datetime.datetime.now(TZ).date()
    start = datetime.datetime.combine(today, datetime.time(8, 0), tzinfo=TZ)
    stop = datetime.datetime.combine(today, datetime.time(8, 30), tzinfo=TZ)
    out = _run_today(monkeypatch, [_entry(3, "work", start, stop)])
    assert "08:00-08:30 work" in out, out
    assert "(30min)" in out, out


def _load_server_module():
    """Load server.py with its MCP-SDK and package-relative imports stubbed,
    so the pure filter logic is testable without the mcp package installed."""
    import types

    if "mcp.server.fastmcp" not in sys.modules:
        mcp_pkg = types.ModuleType("mcp")
        server_pkg = types.ModuleType("mcp.server")
        fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

        class _FakeFastMCP:
            def __init__(self, *a, **kw):
                pass

            def tool(self, *a, **kw):
                def deco(fn):
                    return fn
                return deco

            def run(self, *a, **kw):
                pass

        fastmcp_mod.FastMCP = _FakeFastMCP
        mcp_pkg.server = server_pkg
        server_pkg.fastmcp = fastmcp_mod
        sys.modules.setdefault("mcp", mcp_pkg)
        sys.modules.setdefault("mcp.server", server_pkg)
        sys.modules["mcp.server.fastmcp"] = fastmcp_mod

    # server.py uses package-relative imports (from . import toggl_api) —
    # load it as a submodule of a real package context.
    sys.path.insert(0, str(HERE.parent))
    try:
        import importlib
        srv = importlib.import_module("toggl_server.server")
    finally:
        sys.path.pop(0)
    return srv


def test_server_filter_includes_cross_midnight_clipped():
    """Same bug, second site: the MCP server's day filter must also include
    cross-midnight entries clipped to 00:00 (used by toggl_today/toggl_date)."""
    srv = _load_server_module()

    TZ = srv.TZ
    today = datetime.datetime.now(TZ).date()
    yest = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yest, datetime.time(21, 47), tzinfo=TZ)
    stop = datetime.datetime.combine(today, datetime.time(7, 38), tzinfo=TZ)
    entry = {"id": 9, "description": "睡觉", "start": start.isoformat(),
             "stop": stop.isoformat(),
             "duration": int((stop - start).total_seconds())}
    out = srv._filter_entries_by_local_date([entry], today)
    assert len(out) == 1, out
    clipped = out[0]
    cs = datetime.datetime.fromisoformat(clipped["start"])
    assert (cs.hour, cs.minute) == (0, 0) and cs.date() == today
    assert clipped["duration"] == 7 * 3600 + 38 * 60  # clipped to today's portion
    # original entry dict untouched (clip must copy)
    assert entry["start"] == start.isoformat()
