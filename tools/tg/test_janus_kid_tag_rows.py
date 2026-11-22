"""JM 2026-11-21: "Chinese merged into one section, which it shouldn't...
It should render tags as well as task name." Kid time is Toggl project xk87
plus a kid tag, so back-to-back "中文 #xk20" and "中文 #xk22" share a desc;
the same-desc merge glued them into one row, and the kid tag had no chip
colour so it never showed."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_kid_rows", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_kid_rows"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, eid, tags):
    return {"start_dt": start, "end_dt": end, "desc": desc, "project_id": None,
            "running": False, "id": eid, "tags": tags}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _morning_text(mod, entries):
    today = _midnight()
    mod.STATE.entries = entries
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.entries_known = True
    mod.detail_window = lambda: (today.replace(hour=10), today.replace(hour=14))
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        frags = mod.render_morning()
    return "".join(t for _, t, *_ in frags)


def test_kid_tags_are_visible_chips_with_dark_text():
    mod = _load_tui()
    assert mod._visible_tags(["xk20", "junk", "-1", "xk26"]) == ["xk20", "-1", "xk26"]
    for kid in ("xk20", "xk22", "xk26"):
        assert "#000000" in mod._tag_chip_style(kid) and "bg:" in mod._tag_chip_style(kid)
    assert "#ffffff" in mod._tag_chip_style("-1")  # other chips unchanged


def test_kid_tags_helper_ignores_other_tags():
    mod = _load_tui()
    assert mod._kid_tags(["-2", "xk22", "其他人"]) == frozenset({"xk22"})
    assert mod._kid_tags(None) == frozenset()


def test_same_desc_entries_for_different_kids_stay_two_rows():
    mod = _load_tui()
    t = _midnight()
    text = _morning_text(mod, [
        _entry("中文", t.replace(hour=8, minute=10), t.replace(hour=8, minute=33), 1, ["xk20"]),
        _entry("中文", t.replace(hour=8, minute=33), t.replace(hour=9, minute=10), 2, ["xk22"]),
    ])
    assert text.count("中文") == 2, text
    assert "xk20" in text and "xk22" in text, text


def test_same_kid_fragments_still_merge_and_other_tags_do_not_split():
    mod = _load_tui()
    t = _midnight()
    text = _morning_text(mod, [
        _entry("中文", t.replace(hour=8, minute=10), t.replace(hour=8, minute=33), 1, ["xk22"]),
        _entry("中文", t.replace(hour=8, minute=33), t.replace(hour=9, minute=10), 2, ["xk22", "-2"]),
    ])
    assert text.count("中文") == 1, text
    assert "xk22" in text, text


def test_detail_band_keeps_kids_apart_and_names_them():
    mod = _load_tui()
    t = _midnight()
    mod.STATE.entries = [
        _entry("中文", t.replace(hour=8, minute=10), t.replace(hour=8, minute=33), 1, ["xk20"]),
        _entry("中文", t.replace(hour=8, minute=33), t.replace(hour=9, minute=10), 2, ["xk22"]),
    ]
    segs = mod._detail_merge_past(t.replace(hour=8), t.replace(hour=10))
    assert [s["desc"] for s in segs] == ["中文 #xk20", "中文 #xk22"], segs
