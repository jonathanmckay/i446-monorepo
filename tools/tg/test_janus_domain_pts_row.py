"""User request 2026-09-17: a third habit-strip line, under the hcb/Daily
Dozen row, showing today's 分 by domain as colored cells in the 0分 sheet's
own P:Y order (-1₦, 0g, i9, m5, 个, 媒, 思, hcb, xk, 社)."""
import datetime as dtm
import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_dpr", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_dpr"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_domain_row_renders_every_cell_in_sheet_order_with_zeros_and_negatives():
    m = _load_tui()
    m.STATE.domain_points = {"-1₦": 68, "0g": 109, "i9": 65, "m5": -15, "个": 184,
                             "媒": 54, "思": 26, "hcb": 192, "xk": 595, "社": 0}
    chips = m._domain_pts_chips()
    assert [t for _, t in chips] == [" 68 ", " 109 ", " 65 ", " -15 ", " 184 ",
                                     " 54 ", " 26 ", " 192 ", " 595 ", " 0 "]
    styles = [s for s, _ in chips]
    assert styles[2] == f"bold bg:{m.PROJECT_COLORS['i9']} #ffffff"
    assert styles[3] == f"bold bg:{m.PROJECT_COLORS['m5x2']} #ffffff"
    assert len(set(styles)) == 10  # ten distinct fills, like the sheet


def test_domain_row_is_third_line_of_the_habit_strip():
    m = _load_tui()
    m.STATE.habits_today = [("0g", 10.0)]
    m.STATE.habits_ytd = {}
    m.STATE.daily_dozen = [("bn", -2.0)]
    m.STATE.hcbi_behind = {}
    m.STATE.prayer_count = None
    m.STATE.day_offset = 1  # skip the build-order -1n chip read
    m.STATE.domain_points = {k: 1 for k, _ in m.DOMAIN_PTS_ROW}
    frags = m.render_habits_today(bo_emojis={})
    lines = "".join(t for _, t in frags).split("\n")
    assert len([ln for ln in lines if ln]) == 3
    assert lines[2] == " 1 " * 10


def test_domain_row_hidden_until_first_read():
    m = _load_tui()
    m.STATE.domain_points = {}
    assert m._domain_pts_chips() == []


def test_fetch_points_keeps_py_cells_per_domain(monkeypatch):
    """The P:Y values fetch_points already reads (parts[10:20]) land in
    STATE.domain_points, in DOMAIN_PTS_ROW order, only with a trusted total."""
    m = _load_tui()
    fixed = dtm.datetime(2026, 9, 16, 12, 0, tzinfo=m.TZ)
    monkeypatch.setattr(m, "view_now", lambda: fixed)
    py = ["74", "360", "3", "156", "87", "29", "57", "1", "70", "0"]
    total = str(sum(int(x) for x in py))
    raw = "|".join([total] + ["100"] * 9 + py + ["100"] * 9)
    fake = types.SimpleNamespace(returncode=0, stdout=raw, stderr="")
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    monkeypatch.setattr(m, "_total_trustworthy", lambda c, s: True)
    monkeypatch.setattr(m, "_blocks_consistent", lambda *a: True)
    monkeypatch.setattr(m, "_blocks_plausible", lambda *a: True)
    m.STATE.points_day = None
    m.fetch_points()
    assert m.STATE.domain_points == {"-1₦": 74, "0g": 360, "i9": 3, "m5": 156, "个": 87,
                                     "媒": 29, "思": 57, "hcb": 1, "xk": 70, "社": 0}


def test_fetch_points_does_not_adopt_domains_from_an_untrusted_read(monkeypatch):
    m = _load_tui()
    fixed = dtm.datetime(2026, 9, 16, 12, 0, tzinfo=m.TZ)
    monkeypatch.setattr(m, "view_now", lambda: fixed)
    raw = "|".join(["9999"] + ["100"] * 9 + ["1"] * 10 + ["100"] * 9)
    fake = types.SimpleNamespace(returncode=0, stdout=raw, stderr="")
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    monkeypatch.setattr(m, "_total_trustworthy", lambda c, s: False)
    m.STATE.points_day = None
    m.STATE.domain_points = {"i9": 42}
    m.fetch_points()
    assert m.STATE.domain_points == {"i9": 42}
