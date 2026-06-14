"""Regression: compact block body rows (render_morning/render_evening) must use
the SAME leading indent as the detail band (render_detail). A past compact entry
once carried two leading spaces while the detail band used one, so past time
entries sat one space right of the future calendar (◇) rows."""
import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_indent", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_indent"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, pid=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": pid, "running": False, "id": 1}


def _time_col(line):
    """Index of the first digit (start of the HH:MM time column) in a body row."""
    m = re.search(r"\d", line)
    return m.start() if m else None


def _body_indices(text):
    out = set()
    for ln in text.split("\n"):
        if ln.startswith("─") or not ln.strip():
            continue
        if any(c.isdigit() for c in ln[:8]):
            i = _time_col(ln)
            if i is not None:
                out.add(i)
    return out


def test_compact_body_uses_single_leading_space():
    """A compact past block's entry rows start with exactly one leading space."""
    mod = _load_tui()
    today = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    # Two entries in 辰: the longer becomes the inline header, the shorter a body
    # row (header rides the rule, so we assert indent on the body row).
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=6), today.replace(hour=7, minute=30)),
        _entry("quick task", today.replace(hour=7, minute=30), today.replace(hour=7, minute=50)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    text = "".join(t for _, t in mod.render_morning())
    rows = [ln for ln in text.split("\n") if "quick task" in ln]
    assert rows, "expected a body row with the entry"
    assert rows[0].startswith(" 0"), f"row must have ONE leading space, got {rows[0]!r}"
    assert not rows[0].startswith("  "), f"row must not be double-indented: {rows[0]!r}"


def test_compact_and_detail_time_columns_align():
    """Compact (past/future) and detail-band rows share one time-column index."""
    mod = _load_tui()
    today = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    # Past compact entries + future gcal events, detail window mid-day.
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=6, minute=10), today.replace(hour=7)),
        _entry("now task", today.replace(hour=12, minute=5), today.replace(hour=12, minute=20)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = [
        {"start_dt": today.replace(hour=18, minute=10),
         "end_dt": today.replace(hour=19), "summary": "standup", "project_id": None},
    ]
    mod.STATE.current = None
    mod.STATE.current_known = True
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))

    morning = _body_indices("".join(t for _, t in mod.render_morning()))
    evening = _body_indices("".join(t for _, t in mod.render_evening()))
    detail = _body_indices("".join(t for _, t in mod.render_detail()))

    cols = morning | evening | detail
    assert cols == {1}, (
        f"all body rows must align at time-col index 1; got morning={morning}, "
        f"evening={evening}, detail={detail}"
    )
