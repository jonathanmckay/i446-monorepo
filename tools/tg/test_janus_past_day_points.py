"""User request 2026-09-28: "if I add points on a previous day, those points
accrue to the day I'm viewing." A task with [N] points or a known habit
typed while viewing a past day now routes through did-fast with the viewed
M/D and --past-ok (the flag is its own argv element, ahead of the text),
instead of being rejected. Ad-hoc descriptions are still rejected (they
would start a timer TODAY)."""
import datetime as _dt
import importlib.util
import re
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_pastpts", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_pastpts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _resolve_part_fn(loggable):
    src = (HERE / "janus.py").read_text()
    start = src.index("    def _has_completed_range(part: str) -> bool:")
    end = src.index("\n    resolved_pairs = [r for r in", start)
    body = "from __future__ import annotations\n" + textwrap.dedent(src[start:end])
    calls = {"flash": []}

    class _State:
        day_offset = -1
    ns = {
        "STATE": _State(),
        "flash": lambda msg, secs=4.0: calls["flash"].append(msg),
        "re": re,
        "view_now": lambda: _dt.datetime(2026, 9, 27, 23, 59, 59),
        "_is_loggable_on_past_day": loggable,
    }
    exec(compile(body, "<_resolve_part>", "exec"), ns)
    return ns["_resolve_part"], calls


def test_loggable_task_on_past_day_routes_to_did_with_viewed_date():
    resolve_part, calls = _resolve_part_fn(lambda p: True)
    assert resolve_part("大孩子文学时间") == ("大孩子文学时间 9/27", True)
    assert resolve_part("0l") == ("0l 9/27", True)
    assert not calls["flash"]


def test_adhoc_description_on_past_day_still_rejected():
    resolve_part, calls = _resolve_part_fn(lambda p: False)
    assert resolve_part("meeting prep") is None
    assert any("HHMM-HHMM" in m for m in calls["flash"])


def test_live_commands_untouched_on_past_day():
    resolve_part, calls = _resolve_part_fn(lambda p: True)
    assert resolve_part("stop") == ("stop", False)


def test_did_flags_only_on_past_day_view_and_argv_shape():
    m = _load_tui()
    m.STATE.day_offset = 0
    assert m._did_flags() == ()
    m.STATE.day_offset = -1
    assert m._did_flags() == ("--past-ok",)
    argv = m._did_argv("0l 9/27", m._did_flags())
    assert argv[-1] == "0l 9/27", "command text stays the LAST element (did-fast's date parse)"
    assert argv[-2] == "--past-ok"
    m.STATE.day_offset = 0


def test_is_loggable_uses_task_points_or_habit_names(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m, "_resolvable_points", lambda d: 50 if "文学" in d else None)
    monkeypatch.setattr(m, "_habit_tags", lambda tags: [t for t in tags if t in ("0l", "notes")])
    assert m._is_loggable_on_past_day("大孩子文学时间") is True
    assert m._is_loggable_on_past_day("0l 30 @hcm") is True
    assert m._is_loggable_on_past_day("meeting prep") is False
    assert m._is_loggable_on_past_day("") is False


def test_alt_enter_guard_no_longer_today_only():
    src = (HERE / "janus.py").read_text()
    assert "if STATE.day_offset == 0 and _cmd_done_today(cmd):" not in src
    assert "if _cmd_done_today(cmd):" in src
