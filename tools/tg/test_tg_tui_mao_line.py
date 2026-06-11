"""卯 layout exception: the sleep block renders as ONE wake-time line
(─卯 睡觉 →HH:MM ──── Nm) instead of the standard 4-line compact block.
The right-justified figure is total minutes slept, INCLUDING last night's
pre-midnight portion (day-barrier rule splits overnight sleep at 00:00)."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_mao", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_mao"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_mao_line_is_single_line_with_wake_time():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=5, minute=31)),
        _entry("早餐", today.replace(hour=5, minute=40), today.replace(hour=6, minute=0)),
    ]
    mod.STATE.entries_yday = []
    frags = mod._mao_line(emojis="")
    text = "".join(t for _, t in frags)
    assert text.count("\n") == 1, "卯 must render as exactly one line"
    assert "睡觉 →05:31" in text
    assert "331m" in text  # 00:00→05:31


def test_mao_line_sleep_minutes_include_last_night():
    """The right-justified total must add yesterday evening's 睡觉 entry."""
    mod = _load_tui()
    today = _midnight()
    yday = today - dtm.timedelta(days=1)
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=5, minute=31)),       # 331m
    ]
    mod.STATE.entries_yday = [
        _entry("睡觉", yday.replace(hour=21, minute=30), yday.replace(hour=23, minute=59)),  # 149m
        _entry("hcmc", yday.replace(hour=20, minute=0), yday.replace(hour=21, minute=0)),    # not sleep
        _entry("睡觉", yday, yday.replace(hour=5, minute=20)),         # yesterday MORNING — excluded
    ]
    text = "".join(t for _, t in mod._mao_line(emojis=""))
    assert "480m" in text, f"expected 331+149=480m, got: {text!r}"


def test_mao_line_uses_latest_morning_sleep_not_naps():
    """An afternoon 睡觉 (nap) must not become the wake time or the total."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=5, minute=12)),
        _entry("睡觉", today.replace(hour=13, minute=0), today.replace(hour=13, minute=45)),
    ]
    mod.STATE.entries_yday = []
    text = "".join(t for _, t in mod._mao_line(emojis=""))
    assert "→05:12" in text
    assert "13:45" not in text
    assert "312m" in text  # nap's 45m not added


def test_mao_line_no_sleep_entry_still_one_line():
    mod = _load_tui()
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    text = "".join(t for _, t in mod._mao_line(emojis=""))
    assert text.count("\n") == 1
    assert "卯" in text
    assert "睡觉" not in text


def test_render_morning_routes_mao_to_exception():
    """Structural: render_morning must use _mao_line for 卯, not the 4-line
    compact block."""
    src = (HERE / "tg-tui.py").read_text()
    body = src.split("def render_morning", 1)[1].split("\ndef ", 1)[0]
    assert "_mao_line(" in body, "render_morning no longer special-cases 卯"
