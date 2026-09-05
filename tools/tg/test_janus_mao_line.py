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
    spec = importlib.util.spec_from_file_location("janus_mao", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mao"] = mod
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
    text = "".join(t for _, t, *_ in frags)
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
    text = "".join(t for _, t, *_ in mod._mao_line(emojis=""))
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
    text = "".join(t for _, t, *_ in mod._mao_line(emojis=""))
    assert "→05:12" in text
    assert "13:45" not in text
    assert "312m" in text  # nap's 45m not added


def test_mao_line_no_sleep_entry_still_one_line():
    mod = _load_tui()
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    text = "".join(t for _, t, *_ in mod._mao_line(emojis=""))
    assert text.count("\n") == 1
    assert "卯" in text
    assert "睡觉" not in text


def test_render_morning_routes_mao_to_exception():
    """Structural: render_morning must use _mao_line for 卯, not the 4-line
    compact block."""
    src = (HERE / "janus.py").read_text()
    body = src.split("def render_morning", 1)[1].split("\ndef ", 1)[0]
    assert "_mao_line(" in body, "render_morning no longer special-cases 卯"


def test_render_morning_shows_kmao_activity_when_awake(monkeypatch):
    """Regression (2026-07-03): on an early wake you work through part of 卯
    (prayer/ibx/…). Those entries must SHOW, not be collapsed away by the
    sleep-only _mao_line. Bug report: '-1n prayer during 卯 isn't showing up'."""
    mod = _load_tui()
    today = _midnight()
    # Pin the morning window so 卯 is the (only) past block rendered here.
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=6), today.replace(hour=10)))
    monkeypatch.setattr(mod, "_read_block_emojis", lambda *a, **k: {"卯": "☀️"})
    mod.STATE.entries_known = True  # a confirmed fetch, not a cold-start 402
    mod.STATE.entries = [
        _entry("-1n", today.replace(hour=5, minute=23),
               today.replace(hour=5, minute=32)),  # the prayer, done awake in 卯
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "-1n" in text, f"early-wake 卯 activity hidden by sleep collapse: {text!r}"


def test_render_morning_collapses_kmao_when_all_sleep(monkeypatch):
    """The collapse still applies when 卯 is genuinely all sleep: one wake-time
    line, no per-entry rows."""
    mod = _load_tui()
    today = _midnight()
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=6), today.replace(hour=10)))
    monkeypatch.setattr(mod, "_read_block_emojis", lambda *a, **k: {})
    mod.STATE.entries_known = True  # a confirmed fetch, not a cold-start 402
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=5, minute=45))]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "睡觉 →05:45" in text
    assert text.count("\n") == 1, f"all-sleep 卯 must stay one collapsed line: {text!r}"
