"""Past-block gap rows: untracked stretches >= GAP_MIN render as their own
body line (faint ┄ fill) with the minutes figure in alarm red; the 卯 sleep
total is dim like other duration figures, not bold white."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_gaps", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_gaps"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_block_gaps_sees_stop_resume_on_same_task():
    """A break between two entries of the SAME description must surface —
    the merged display spans would join them, so the sweep uses raw entries."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries = [
        _entry("email", today.replace(hour=8), today.replace(hour=8, minute=40)),
        _entry("email", today.replace(hour=9, minute=10), today.replace(hour=10)),
    ]
    gaps = mod._block_gaps(8, 9, cutoff)  # 巳 block
    assert len(gaps) == 1
    assert gaps[0]["dur_min"] == 30
    assert gaps[0]["time_str"] == "08:40"
    assert gaps[0]["is_gap"] is True


def test_block_gaps_below_threshold_folded():
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries = [
        _entry("a", today.replace(hour=8), today.replace(hour=8, minute=58)),
        _entry("b", today.replace(hour=9, minute=2), today.replace(hour=10)),
    ]
    assert mod._block_gaps(8, 9, cutoff) == []  # 4m < GAP_MIN


def test_block_gaps_fully_empty_block_is_one_full_gap():
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries = []
    gaps = mod._block_gaps(8, 9, cutoff)
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 120


def test_block_gaps_spillover_coverage_clips_at_boundary():
    """An entry starting in the prior block still covers this one's start."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=7, minute=30), today.replace(hour=9, minute=15)),
        _entry("standup", today.replace(hour=9, minute=45), today.replace(hour=10)),
    ]
    gaps = mod._block_gaps(8, 9, cutoff)
    assert len(gaps) == 1
    assert gaps[0]["time_str"] == "09:15" and gaps[0]["dur_min"] == 30


def test_gap_row_renders_red_minutes_on_own_line():
    mod = _load_tui()
    today = _midnight()
    gap = {"start_dt": today.replace(hour=8, minute=40), "time_str": "08:40",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    entry = {"start_dt": today.replace(hour=8), "time_str": "08:00",
             "label": "email", "style": "#888888", "dur_min": 40}
    frags = mod._compact_block_lines("巳", 8, [entry, gap], 0, "")
    text = "".join(t for _, t in frags)
    assert text.count("\n") == 4, "block must stay exactly 4 lines"
    red = [t for s, t in frags if "no_entry" in s]
    assert any("30" in t for t in red), "gap minutes must use the red alarm style"
    assert any("┄" in t for s, t in frags if "idle" in s)


def test_gap_never_rides_the_header_rule():
    """All-gap picks (spillover-covered block) render a bare rule + gap rows."""
    mod = _load_tui()
    today = _midnight()
    gap = {"start_dt": today.replace(hour=9, minute=15), "time_str": "09:15",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    frags = mod._compact_block_lines("巳", 8, [gap], 0, "")
    header = "".join(t for _, t in frags).split("\n")[0]
    assert "09:15" not in header, "gap must be a body row, not inline in the rule"
    assert any("no_entry" in s for s, _ in frags)


def test_mao_sleep_total_is_dim_not_bold_white():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=5, minute=31))]
    mod.STATE.entries_yday = []
    frags = mod._mao_line(emojis="")
    sleep_frag = [(s, t) for s, t in frags if "331m" in t]
    assert sleep_frag, "sleep total missing"
    assert sleep_frag[0][0] == "class:dim"
