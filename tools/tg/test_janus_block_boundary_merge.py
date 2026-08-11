"""Bug 2026-08-11: "at the 辰/巳 boundary, janus should be showing the xk22
time entry... not sure why it's not." Two separate Toggl entries with the
same desc — one ending right before a block boundary (辰), the next starting
right after it (巳) — were silently glued into a single span by the
same-desc merge in render_morning/_current_block_lines. Block attribution
then went entirely to the EARLIER block (hour_to_block on the merged span's
retained start_dt), so the later block's own occurrence never rendered
anywhere: not as its own pick (merged away), not via the spill-clip path
either (neither raw entry individually straddles the boundary)."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_boundary", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_boundary"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, eid, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": eid,
            "tags": []}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


# ── unit: the block-boundary gate itself ────────────────────────────────────

def test_same_block_true_within_a_block():
    mod = _load_tui()
    today = _midnight()
    assert mod._same_block(today.replace(hour=7, minute=50), today.replace(hour=7, minute=55))


def test_same_block_false_across_chen_si_boundary():
    mod = _load_tui()
    today = _midnight()
    assert not mod._same_block(today.replace(hour=7, minute=50), today.replace(hour=8, minute=0))


# ── integration: render_morning must not merge across the boundary ─────────

def test_render_morning_keeps_same_desc_entries_in_their_own_blocks():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("xk22", today.replace(hour=7, minute=50), today.replace(hour=7, minute=59), 1),
        _entry("xk22", today.replace(hour=8, minute=0), today.replace(hour=8, minute=29), 2),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.entries_known = True
    mod.detail_window = lambda: (today.replace(hour=10), today.replace(hour=14))
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        frags = mod.render_morning()
    text = "".join(t for _, t, *_ in frags)
    assert "29m" in text, f"the 巳 entry (08:00-08:29) must render with its own 29m duration:\n{text}"
    assert "39m" not in text, f"the two entries must not merge into one 39m span:\n{text}"
    assert text.count("xk22") == 2, f"each entry must render as its own row:\n{text}"
