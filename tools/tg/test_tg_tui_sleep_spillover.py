"""Late wake-up: the overnight 睡觉 entry starts at 00:00 (outside every
block), so blocks it spills into (辰 onward) must synthesize a 睡觉 pick
covering the slept portion instead of rendering it as missing time."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_sleep", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_sleep"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_partial_spillover_wake_after_six():
    """Wake 07:03 → 辰 (6-8) gets a 63m 睡觉 pick ending at the wake time."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=7, minute=3))]
    item = mod._block_sleep_item(6, 7, today.replace(hour=10))
    assert item is not None
    assert item["label"] == "睡觉"
    assert item["dur_min"] == 63
    assert item["time_str"] == "07:03"  # sleep end-time convention
    assert item["start_dt"] == today.replace(hour=6)


def test_fully_slept_block():
    """Wake 10:30 → 辰 is entirely sleep: full 120m, clipped at block end."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=10, minute=30))]
    item = mod._block_sleep_item(6, 7, today.replace(hour=12))
    assert item["dur_min"] == 120
    assert item["time_str"] == "08:00"


def test_no_spillover_on_early_wake():
    """Wake 05:40 (inside 卯) → 辰 gets no synthetic sleep pick."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=5, minute=40))]
    assert mod._block_sleep_item(6, 7, today.replace(hour=10)) is None


def test_non_sleep_spillover_ignored():
    """A long non-sleep entry crossing the block boundary is not relabelled."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=5), today.replace(hour=7)),
    ]
    assert mod._block_sleep_item(6, 7, today.replace(hour=10)) is None


def test_render_morning_headers_sleep_block():
    """Integration: wake 07:03 + 新闻 after → 辰 header carries 睡觉, body 新闻."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=7, minute=3)),
        _entry("新闻", today.replace(hour=7, minute=3), today.replace(hour=7, minute=30)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.detail_window = lambda: (today.replace(hour=8), today.replace(hour=12))
    frags = mod.render_morning()
    text = "".join(t for _, t in frags)
    chen = [ln for ln in text.split("\n") if ln.startswith("─辰")]
    assert chen and "睡觉" in chen[0], f"辰 header must read 睡觉, got: {chen}"
    assert "新闻" in text
