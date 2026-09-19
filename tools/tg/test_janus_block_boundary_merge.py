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


def test_mao_card_keeps_the_longer_entry_over_an_earlier_shorter_one():
    """User report 2026-09-17: 卯 showed "-1t 2m" (05:54) and not "0t 8m"
    (05:56, running on into 辰). After prepending the sleep-spillover row,
    render_morning sliced [sleep]+picks to four items CHRONOLOGICALLY, so
    the last-starting entry lost regardless of length. The body cap in
    _compact_block_lines already keeps rows by importance (duration); the
    slice must not pre-empt it."""
    mod = _load_tui()
    today = _midnight()
    h = lambda hh, mm: today.replace(hour=hh, minute=mm)  # noqa: E731
    mod.STATE.entries = [
        _entry("睡觉", h(0, 0), h(5, 33), 1),
        _entry("wake up", h(5, 34), h(5, 45), 2),
        _entry("新闻", h(5, 45), h(5, 53), 3),
        _entry("-1t", h(5, 54), h(5, 56), 4),
        _entry("0t", h(5, 56), h(6, 4), 5),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.entries_known = True
    mod.STATE.day_offset = 0
    mod.detail_window = lambda: (h(8, 0), h(12, 0))
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        frags = mod.render_morning()
    text = "".join(t for _, t, *_ in frags)
    mao = text.split("辰")[0]
    assert "0t" in mao and "8m" in mao, f"the 8m 0t entry must make 卯's card:\n{mao}"
    assert "-1t" not in mao, f"the 2m -1t is the one to drop, not 0t:\n{mao}"


def test_question_mark_entries_are_never_merged():
    """User report 2026-09-19: Toggl had two "?" entries (16:53-17:26, 33m
    and 17:26-18:11, 44m) but janus showed one 78m "?" row. Each "?" is a
    distinct unknown to identify, so same-desc merging must skip it; the
    /tg auto-filler "generic placeholder" keeps merging."""
    mod = _load_tui()
    today = _midnight()
    h = lambda hh, mm: today.replace(hour=hh, minute=mm)  # noqa: E731
    mod.STATE.entries = [
        _entry("tasks", h(16, 0), h(16, 29), 1),
        _entry("?", h(16, 53), h(17, 26), 2),
        _entry("?", h(17, 26), h(18, 11), 3),
        _entry("generic placeholder", h(18, 20), h(18, 30), 4),
        _entry("generic placeholder", h(18, 30), h(18, 45), 5),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.entries_known = True
    mod.STATE.day_offset = 1
    mod.view_now = lambda: h(20, 0)
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        text = "".join(t for _, t, *_ in mod.render_morning(bo_emojis={}))
    you = text.split("酉", 1)[1].split("戌")[0]
    # (fixture entries are whole minutes: 17:26-18:11 is 45m; Toggl's real
    # one was 44:48 → 44m)
    assert "33m" in you and "45m" in you and "78m" not in you, f"two ? rows expected:\n{you}"
    xu = text.split("戌", 1)[1].split("亥")[0]
    assert "25m" in xu and xu.count("generic placeholder") == 1, f"auto-filler still merges:\n{xu}"
