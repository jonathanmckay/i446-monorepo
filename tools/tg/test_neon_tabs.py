"""Neon tabs in janus (2026-11-21): tab state, scrolling, the rendered grid,
and the janus key bindings that drive them. No tty, no Excel."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import neon_grid as ng  # noqa: E402
import neon_tabs as nt  # noqa: E402


def _sheet(name="0分", nrows=60, ncols=12, today=50, tab="#123456"):
    styles = [[None, None, 0, "r"], ["#ff0000", "#ffffff", 1, "r"], [None, None, 0, "l"]]
    rows = [[["H%d" % c, 2] for c in range(ncols)]]
    for r in range(1, nrows):
        rows.append([["w%d" % r if c == 0 else "d%d" % r if c == 1 else str(r * 100 + c), 1 if c == 3 else 0]
                     for c in range(ncols)])
    rows[10][0] = ["11.1", 2]
    for r in range(11, 17):
        rows[r][0] = ["", 2, r - 10]                       # vertical merge under row 10
    return {"name": name, "tab": tab, "header_rows": 1, "frozen_cols": 2, "today_row": today,
            "cols": [chr(65 + c) for c in range(ncols)], "row_nums": list(range(1, nrows + 1)),
            "widths": [5, 6] + [5] * (ncols - 2), "styles": styles, "rows": rows, "skipped_rules": 0}


def _tabs(tmp_path, sheets=None):
    grid = tmp_path / "grid.json"
    grid.write_text(json.dumps({"version": ng.VERSION, "hash": "h1", "built": 1, "src_mtime": 1,
                                "sheets": sheets or [_sheet(), _sheet("0n", today=None, tab=None)]}))
    return nt.Tabs(state_file=tmp_path / "tab.json", grid_file=grid, src=tmp_path / "neon.xlsx")


def _text(frags):
    return "".join(f[1] for f in frags)


def test_tabs_cycle_and_persist(tmp_path):
    t = _tabs(tmp_path)
    assert t.names() == ["janus", "0分", "0n"] and t.tab == 0
    t.next(); t.next()
    assert t.sheet()["name"] == "0n"
    t.next()
    assert t.tab == 0                                       # wraps back to the timeline
    t.select(1)
    t.move(drow=-5, dcol=2, body_h=20)
    again = nt.Tabs(state_file=tmp_path / "tab.json", grid_file=tmp_path / "grid.json")
    assert again.tab == 1 and again.scroll["0分"] == t.scroll["0分"]   # survives a janus self-restart
    again.select(99)
    assert again.tab == 0


def test_sheet_opens_on_today_and_clamps(tmp_path):
    t = _tabs(tmp_path)
    t.select(1)
    lines = _text(t.render(80, 24)).split("\n")             # wide enough for one line per row
    assert len(lines) == 24                                 # exactly fills the pane
    body = lines[2:-1]
    assert body[-1 - nt.TODAY_FROM_BOTTOM].lstrip().startswith("w50")   # today sits a few rows above the bottom
    t.move(drow=999, body_h=t.body_height(24))
    assert "w59" in _text(t.render(80, 24))
    t.move(drow=-9999, body_h=t.body_height(24))
    assert _text(t.render(80, 24)).split("\n")[2].lstrip().startswith("w1")
    t.home(t.body_height(24))
    assert _text(t.render(80, 24)).split("\n")[2:-1][-1 - nt.TODAY_FROM_BOTTOM].lstrip().startswith("w50")


def test_a_wide_sheet_wraps_instead_of_scrolling_sideways(tmp_path):
    """JM 2026-11-25: "2-3 lines (so I get the full horizontal width) per day.
    That way I don't have to h-scroll, which I'll almost never do"."""
    t = _tabs(tmp_path)
    t.select(1)
    lines = _text(t.render(31, 13)).split("\n")
    assert len(lines) == 13
    assert lines[1].split() == ["H0", "H1", "H2", "H3", "H4", "H5"]     # 5+6 frozen, four 5-wide columns
    assert lines[2].split() == ["H6", "H7", "H8", "H9"]     # the rest, hanging under the frozen columns
    assert lines[3].split() == ["H10", "H11"]
    assert t.body_height(13, 31) == 2                       # (13 - strip - status - 3 header lines) // 3
    first, second = lines[4].split(), lines[5].split()
    assert first[0].startswith("w") and len(first) == 6 and len(second) == 4
    assert int(second[0]) == int(first[-1]) + 1             # one row, continued
    assert "C–L" in lines[-1] and "→" not in lines[-1]     # everything is on screen: no pages


def test_columns_past_three_lines_are_a_second_page(tmp_path):
    t = _tabs(tmp_path)
    t.select(1)
    lines = _text(t.render(20, 20)).split("\n")
    assert [ln.split() for ln in lines[1:4]] == [["H0", "H1", "H2"], ["H3", "H4", "H5", "H6"], ["H7", "H8", "H9", "H10"]]
    assert "1/2→" in lines[-1]
    rows = t.body_height(20, 20)
    t.move(dcol=1, body_h=rows)
    lines = _text(t.render(20, 20)).split("\n")
    assert [ln.split() for ln in lines[1:4]] == [["H0", "H1", "H11"], [], []]
    assert t.body_height(20, 20) == rows                    # the short last page keeps three lines per row
    assert "L–L" in lines[-1] and "←2/2" in lines[-1]
    t.move(dcol=99, body_h=rows)
    assert t.scroll["0分"][1] == 1                           # clamped to the last page, not to a column
    t.home(rows)
    assert t.scroll["0分"][1] == 0


def test_today_date_cell_is_highlighted_and_wrapped_rows_striped(tmp_path):
    """JM 2026-11-25: "0分 can we highlight today in the date column? That way I can find it"."""
    t = _tabs(tmp_path)
    t.select(1)
    frags = t.render(80, 24)
    marked = [f for f in frags if f[0] == nt.TODAY_STYLE]
    assert [f[1].strip() for f in marked] == ["d50"]        # the last frozen column of today's row, nothing else
    assert not any(nt.ZEBRA_BG in f[0] for f in frags)      # one line per row: no stripes needed
    t.home(t.body_height(24, 31))                           # fewer rows fit now: back to today
    frags = t.render(31, 24)
    assert [f[1].strip() for f in frags if f[0] == nt.TODAY_STYLE] == ["d50"]
    striped = [f for f in frags if nt.ZEBRA_BG in f[0]]
    assert striped and ("bg:#ff0000 #ffffff bold", " 4903") in frags     # a filled cell keeps its own colour
    t2 = _tabs(tmp_path, [_sheet("0n", today=None)])
    t2.select(1)
    assert not [f for f in t2.render(80, 24) if f[0] == nt.TODAY_STYLE]


def test_cell_styles_and_merge_label(tmp_path):
    t = _tabs(tmp_path)
    t.select(1)
    t.scroll["0分"] = [12, 0]                                # top of the merge (row 10) is off-screen
    frags = t.render(80, 12)
    assert ("bg:#ff0000 #ffffff bold", " 1303") in frags    # coloured cell, right-aligned in 5 chars
    first_body = _text(frags).split("\n")[2]
    assert first_body.startswith(" 11.1")                   # the merged week label shows on the first visible row
    assert _text(frags).split("\n")[3].startswith("     ")


def _hcbi(today=6):
    cols = ["A", "B", "C"] + [m[1] for m in nt.HCBI_PLAN]
    styles = [[None, None, 0, "r"]]
    rows = [[[c, 0] for c in cols]]
    for r in range(1, 9):
        rows.append([["", 0], ["11/%d" % (18 + r), 0], ["M", 0]] + [["", 0] for _ in nt.HCBI_PLAN])
    def put(r, **kw):
        for col, v in kw.items():
            rows[r][cols.index(col)] = [v, 0]
    put(4, E="3", F="1", G="0", P="120", U="1,810", AA="90", Y="45")
    put(5, E="2", U="730", AA="-50", Y="0")
    put(6, E="1", P="16", U="450", AA="15")
    return {"name": "hcbi", "tab": None, "header_rows": 1, "frozen_cols": 2, "today_row": today,
            "cols": cols, "row_nums": list(range(1, 10)), "widths": [5, 6, 3] + [4] * len(nt.HCBI_PLAN),
            "styles": styles, "rows": rows, "skipped_rules": 0}


def test_hcbi_opens_with_three_days_against_plan(tmp_path):
    """JM 2026-11-25: "the main thing it shows me is today and like the last
    two days, how I'm doing against plan"."""
    t = _tabs(tmp_path, [_hcbi()])
    t.select(1)
    frags = t.render(47, 60)
    lines = _text(frags).split("\n")
    assert len(lines) == 60
    assert lines[1].split() == ["vs", "plan", "plan", "11/22", "11/23", "11/24"]
    assert (nt.TODAY_STYLE, "  11/24") in frags              # today is the rightmost day
    row = {ln.split()[0]: ln.split() for ln in lines[2:2 + len(nt.HCBI_PLAN)]}
    assert row["beans"] == ["beans", "3", "3", "2", "1"]
    assert row["kcal"] == ["kcal", "≤1800", "1,810", "730", "450"]
    assert (nt._MET, "      3") in frags and (nt._MISS, "      2") in frags     # beans: met, then missed
    assert (nt._OPEN, "      1") in frags                   # today's shortfall is still open, not a miss
    assert (nt._MISS, "  1,810") in frags and (nt._MET, "    730") in frags     # kcal is a ceiling
    assert row["food"] == ["food", "分", "86", "90", "–", "15"]          # -50 = nothing logged that day
    assert row["grains"] == ["grains", "·", "·", "·"]       # no goal: not scored
    assert [ln for ln in lines if ln.split()[:1] == ["met"]][0].split() == ["met", "5/14", "1/14", "1/14"]
    assert t.body_height(60, 47) == (60 - 2 - (len(nt.HCBI_PLAN) + 3) - 2) // 2
    short = _text(t.render(47, 14)).split("\n")
    assert len(short) == 14 and "vs plan" not in "".join(short)          # a short pane keeps the grid
    bare = _hcbi()
    bare["cols"], bare["rows"], bare["widths"] = bare["cols"][:5], [r[:5] for r in bare["rows"]], bare["widths"][:5]
    assert nt.hcbi_panel(bare, 47) == []                    # not the hcbi we know: no panel


def test_hcbi_plan_matches_the_weekly_review():
    spec = importlib.util.spec_from_file_location("hcb1", HERE.parent / "1hcb" / "1hcb.py")
    hcb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hcb)
    plan = {m[1]: m[2] for m in nt.HCBI_PLAN}
    assert [plan[c] for c in "EFGHIJKLMNO"] == [g[2] for g in hcb.FOOD_GROUPS]
    assert (plan["P"], plan["U"], plan["AA"], plan["Y"]) == (
        hcb.PROTEIN_TARGET, hcb.CAL_CEILING, hcb.FOOD_TARGET, hcb.HCBP_TARGET)


def test_a_split_part_reads_its_source_sheet(tmp_path):
    sh = _sheet("1n")
    sh["source"] = "1n+"
    sh["live"] = {"row": 50, "excel_row": 51, "cells": {"D": {"fmt": "0", "base": [None, None, False, None], "rules": []}}}
    t = _tabs(tmp_path, [sh])
    asked = []
    t.row_source = lambda name, row, cols: asked.append((name, row)) or ({"D": 77}, "10:00:00")
    assert t.refresh_live() is True
    assert asked == [("1n+", 51)] and "1n" in t.book_through
    t.select(1)
    assert t.names() == ["janus", "1n"] and "   77" in _text(t.render(80, 24))


def test_strip_marks_current_tab_and_scrolls(tmp_path):
    sheets = [_sheet("sheet%02d" % i, today=None, tab=None) for i in range(12)]
    t = _tabs(tmp_path, sheets)
    t.select(11)
    strip = _text(t.render(40, 10)).split("\n")[0]
    assert "sheet10" in strip and strip.startswith("‹") and len(strip) == 40
    t.select(1)
    frags = t.render(40, 10)
    assert frags[1][0].startswith("bold bg:") and frags[1][1] == " sheet00 "
    assert _text(frags).split("\n")[0].rstrip().endswith("›")


def test_wide_glyph_columns_keep_their_width(tmp_path):
    sh = _sheet()
    sh["rows"][0][2] = ["卯辰巳午", 2]                      # 8 cells wide in a 5-wide column
    t = _tabs(tmp_path, [sh])
    t.select(1)
    head = _text(t.render(40, 10)).split("\n")[1]
    assert ng.cwidth(head) <= 40 and "卯辰" in head and "卯辰巳" not in head


def test_needs_build_only_for_a_visible_stale_tab(tmp_path):
    t = _tabs(tmp_path)
    (tmp_path / "neon.xlsx").write_text("x")
    assert not t.needs_build(now=1000)                      # timeline showing, snapshot exists
    t.select(1)
    assert t.needs_build(now=1000)
    calls = []

    class R:
        returncode, stdout, stderr = 0, "written", ""
    t.build(run=lambda cmd, **kw: calls.append(cmd) or R())
    assert calls and calls[0][2] == "build" and not t.error
    assert not t.needs_build()                              # workbook unchanged since that build
    R.returncode, R.stderr = 1, "error: PermissionError: denied"
    t.built_src_mtime = 0
    t.last_build = 0
    t.build(run=lambda cmd, **kw: R())
    assert "PermissionError" in t.error
    assert "PermissionError" in _text(t.render(80, 10)).split("\n")[-1]   # said in the status line, not hidden


def test_no_snapshot_says_so(tmp_path):
    t = nt.Tabs(state_file=tmp_path / "tab.json", grid_file=tmp_path / "none.json", src=tmp_path / "neon.xlsx")
    assert t.names() == ["janus"] and t.needs_build(now=5) is False     # no workbook either: nothing to build
    (tmp_path / "neon.xlsx").write_text("x")
    assert t.needs_build(now=5) is True


# --- janus glue --------------------------------------------------------------

def _load_janus():
    spec = importlib.util.spec_from_file_location("janus_neon_tabs", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_neon_tabs"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Event:
    def __init__(self, app):
        self.app = app


class _App:
    def invalidate(self):
        pass


def _binding(mod, *keys):
    want = tuple(keys)
    got = [b for b in mod.kb.bindings if tuple(getattr(k, "value", k) for k in b.keys) == want]
    assert got, want
    return got


def test_janus_tab_keys_and_render(tmp_path, monkeypatch):
    mod = _load_janus()
    assert mod.NEON.tab == 0 and mod.NEON.snap is None      # headless never picks up JM's open tab
    timeline = _text(mod.render_all())
    mod.NEON = _tabs(tmp_path)
    monkeypatch.setattr(mod, "_bg_fetch", lambda app, fn: None)
    ev = _Event(_App())
    mod.STATE.event_sel = ("x", 1)
    _binding(mod, "c-t")[0].handler(ev)
    assert mod.NEON.tab == 1 and mod.STATE.event_sel is None   # a hidden selection must not take ^X/⏎
    out = mod.render_all()
    assert out[0] == ("[SetCursorPosition]", "") and " 0分 " in _text(out) and _text(out) != timeline
    assert all(len(f) == 3 for f in out[1:])                # every fragment carries the wheel handler
    assert sum(1 for f in out if f[0] == "[SetCursorPosition]") == 1
    _binding(mod, "escape", "3")[0].handler(ev)             # ⌥3 = second sheet
    assert mod.NEON.sheet()["name"] == "0n"
    _binding(mod, "escape", "0")[0].handler(ev)             # no tenth tab: stays put
    assert mod.NEON.sheet()["name"] == "0n"
    esc = [b for b in _binding(mod, "escape") if b.filter()][0]
    esc.handler(ev)
    assert mod.NEON.tab == 0 and _text(mod.render_all()) == timeline


def test_janus_arrows_scroll_the_grid_not_the_selection(tmp_path, monkeypatch):
    mod = _load_janus()
    mod.NEON = _tabs(tmp_path, [_sheet(ncols=90)])           # wide enough for a second page of columns
    monkeypatch.setattr(mod, "_bg_fetch", lambda app, fn: None)
    ev = _Event(_App())
    live = lambda key: [b for b in _binding(mod, key) if b.filter()]
    assert len(live("down")) == 1 and len(live("c-i")) == 1  # timeline: selection keys
    mod.NEON.select(1)
    mod.render_all()
    assert live("c-i") == []                                # Tab no longer selects timeline rows
    down = live("down")
    assert len(down) == 1
    before = list(mod.NEON.scroll["0分"])
    down[0].handler(ev)
    live("up")[0].handler(ev)
    live("up")[0].handler(ev)
    live("right")[0].handler(ev)
    assert mod.NEON.scroll["0分"] == [before[0] - 1, 1]
    live("home")[0].handler(ev)
    assert mod.NEON.scroll["0分"] == before
    mod.input_buffer.text = "0900-0930 x"
    assert live("down") == []                               # typing: ↓ is the input line's own key again
    mod.input_buffer.reset()


def test_live_source_repaints_today_and_is_named(tmp_path):
    sh = _sheet()
    sh["live"] = {"row": 50, "cells": {"D": {"fmt": "#,##0", "base": [None, None, False, None],
                                             "rules": [[1, "greaterThan", [1000.0], "00ff00", None, None, False]]}}}
    t = _tabs(tmp_path, [sh])
    t.select(1)
    assert "5003" in _text(t.render(40, 24))
    feed = {"v": ({"D": 1236.14}, "18:29:53")}
    t.live_sources["0分"] = lambda: feed["v"]
    assert t.refresh_live() is True
    frags = t.render(58, 24)
    assert ("bg:#00ff00 #000000", "1,236") in frags
    status = _text(frags).split("\n")[-1]
    assert "today ledger 18:29" in status and "^T" not in status      # narrow pane: the key hint yields
    assert t.refresh_live() is False                        # nothing new: no repaint
    feed["v"] = (None, None)                                # ledger has no view (e.g. before 03:50)
    t.refresh_live()
    assert "ledger" not in _text(t.render(58, 24)).split("\n")[-1]
    t.live_sources["0分"] = lambda: 1 / 0                    # a failing source never breaks the tab
    assert t.refresh_live() is False


def test_janus_feeds_0fen_from_the_ledger(monkeypatch):
    mod = _load_janus()
    assert mod.NEON.live_sources["0分"] is mod._neon_ledger_today
    import neon.view as nview
    monkeypatch.setattr(nview, "day_view", lambda iso: {
        "total": 1236.143, "cols": {"P": 42.0, "Y": 70.0}, "blocks": {"卯": 3.0, "戌": 310.0},
        "through": "2026-11-21T18:29:53"})
    values, through = mod._neon_ledger_today()
    assert values == {"D": 1236.143, "P": 42.0, "Y": 70.0, "G": 3.0, "N": 310.0} and through == "18:29:53"
    monkeypatch.setattr(nview, "day_view", lambda iso: None)
    assert mod._neon_ledger_today() == (None, None)


def test_entering_a_tab_lands_on_today_again(tmp_path):
    t = _tabs(tmp_path, [_sheet(ncols=60)])
    t.select(1)
    t.render(58, 12)
    home = list(t.scroll["0分"])
    t.move(-5, 1, body_h=9)
    assert t.scroll["0分"] != home
    t.select(0)
    t.select(1)                                             # back in: today's row, same page of columns
    t.render(58, 12)
    assert t.scroll["0分"] == [home[0], 1]
    again = _tabs(tmp_path)                                 # and the pending re-anchor survives a reload
    again.select(1)
    again.load()
    again.render(58, 12)
    assert again.scroll["0分"][0] == home[0]


def test_ledger_writes_since_the_save_repaint_today_on_other_tabs(tmp_path):
    """2026-11-22: only 0分 had a source fresher than the saved workbook. The
    other sheets take the cell writes journaled since the save, on their
    today row only, and never from a snapshot built for another day."""
    import datetime as dt
    recipe = {"fmt": "General", "base": [None, None, False, None], "rules": []}
    zero = _sheet("0分")
    zero["live"] = {"row": 50, "excel_row": 51, "cells": {"D": recipe}}
    other = _sheet("0n", tab=None)
    other["live"] = {"row": 50, "excel_row": 51, "cells": {"D": recipe, "E": recipe}}
    other["rows"][50][4] = ["", 0]
    t = _tabs(tmp_path, [zero, other])
    zero, other = t.sheets()
    t.live_sources["0分"] = lambda: (None, None)
    seen = []
    feed = {("0n", 51, "D"): (7.0, "2026-11-22T07:16:05"), ("0n", 51, "E"): (0.0, "2026-11-22T07:15:00"),
            ("0n", 40, "D"): (9.0, "2026-11-22T07:17:00"),         # another row: waits for the rebuild
            ("0分", 51, "D"): (1.0, "2026-11-22T07:18:00")}         # 0分 has its own source
    t.write_source = lambda since: seen.append(since) or feed
    assert t.refresh_live() is True and seen == [1 - nt.WRITE_MARGIN_S]
    assert other["rows"][50][3][0] == "7" and other["rows"][50][4][0] == "0"     # a written 0 is news here
    assert other["rows"][40][3][0] == "4003" and zero["rows"][50][3][0] == "5003"
    t.select(2)
    assert "ledger write 07:16" in _text(t.render(80, 24)).split("\n")[-1]
    assert t.refresh_live() is False                        # nothing new: no repaint
    t.write_source = lambda since: 1 / 0                    # a failing source never breaks the tab
    assert t.refresh_live() is True and "ledger write" not in _text(t.render(80, 24))
    # a snapshot built yesterday: its today row is yesterday's, so nothing is
    # painted on it and the tab asks for a rebuild though the workbook is unmoved
    t.write_source = lambda since: feed
    other["rows"][50][3] = ["5003", 0]
    t.snap["day"] = "2026-01-01"
    assert t.refresh_live() is False and other["rows"][50][3][0] == "5003"
    (tmp_path / "neon.xlsx").write_text("x")
    t.built_src_mtime = (tmp_path / "neon.xlsx").stat().st_mtime
    assert t.needs_build(now=1000)
    t.snap["day"] = dt.date.today().isoformat()
    assert not t.needs_build(now=1000) and t.refresh_live() is True


def test_janus_feeds_the_other_tabs_from_ledger_writes(monkeypatch):
    mod = _load_janus()
    assert mod.NEON.write_source is mod._neon_ledger_writes
    assert mod._neon_ledger_writes(0) == {}                 # pytest never reads the live ledger
    import neon.ledger as nledger
    monkeypatch.setenv("NEON_TEST_DB_OK", "1")
    got = []
    monkeypatch.setattr(nledger, "writes_since", lambda since: got.append(since) or {"k": 1})
    assert mod._neon_ledger_writes(1795360397) == {"k": 1}
    assert got == [mod.dt.datetime.fromtimestamp(1795360397).isoformat(timespec="seconds")]


def test_row_source_repaints_the_whole_live_row_and_stands_the_ledger_overlays_down(tmp_path):
    """2026-11-22: the Neon book supplies the live row, dependents included."""
    import neon_tabs
    t = neon_tabs.Tabs(state_file=tmp_path / "tab.json", grid_file=tmp_path / "grid.json")
    t.snap = {"day": None, "src_mtime": 0, "sheets": [{
        "name": "0分", "cols": ["B", "D", "P"], "widths": [5, 5, 5], "styles": [[None, None, 0, "r"]],
        "header_rows": 0, "frozen_cols": 1, "today_row": 0, "rows": [[["11.22", 0], ["13", 0], ["", 0]]],
        "live": {"row": 0, "excel_row": 324, "recolor": None,
                 "cells": {c: {"fmt": "General", "base": [None, None, 0, "r"], "rules": []} for c in ("D", "P")}}}]}
    asked = []
    t.write_source = lambda since: asked.append("writes") or {}
    t.live_sources["0分"] = lambda: asked.append("ledger") or ({"D": 1}, "x")
    t.row_source = lambda sheet, row, cols: ({"D": 30.0, "P": ""}, "08:04:25")
    assert t.refresh_live() is True
    row = t.snap["sheets"][0]["rows"][0]
    assert row[1][0] == "30" and row[2][0] == ""          # the total moved; a blank that was blank is left alone
    assert t.book_through == {"0分": "08:04:25"} and "ledger" not in asked
    t.row_source = lambda sheet, row, cols: (None, None)   # the book stops answering: the old sources return
    t.refresh_live()
    assert "0分" not in t.book_through and "ledger" in asked


# ── off Ix: the snapshot comes from Ix (2026-11-22, Straylight's stale OneDrive copy) ──

def _remote_snap(**kw):
    import neon_grid, time as _t
    sh = _sheet()
    sh["live"] = {"row": 50, "excel_row": 324, "cells": {}}
    snap = {"version": neon_grid.VERSION, "hash": "h", "built": 1, "src_mtime": int(_t.time()),
            "day": dt.date.today().isoformat(), "sheets": [sh], "host": "ix", "emitted": int(_t.time()),
            "book": {"0分": "08:12:05"}}
    snap.update(kw)
    return snap


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_remote_mode_fetches_from_ix_and_never_reads_the_local_workbook(tmp_path):
    import neon_tabs
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _Proc(0, json.dumps(_remote_snap(), ensure_ascii=False))
    t = neon_tabs.Tabs(state_file=tmp_path / "tab.json", grid_file=tmp_path / "grid.json",
                       src=tmp_path / "no-such.xlsx", remote=True)
    t.write_source = lambda since: 1 / 0            # the local ledger overlays must stay out of it
    t.live_sources["0分"] = lambda: 1 / 0
    t.row_source = lambda *a: 1 / 0
    assert t.needs_build() is True                  # no snapshot yet, whatever the local file is
    t.build(run)
    assert calls and calls[0][0] == "ssh" and "ix" in calls[0] and calls[0][-1].endswith("neon_grid.py emit")
    assert t.error == "" and t.names() == ["janus", "0分"]
    assert t.book_through == {"0分": "08:12:05"} and not t.live_through
    t.select(1)
    status = _text(t.render(120, 24)).split("\n")[-1]
    assert " ix " in status and "book 08:12" in status and "stale" not in status
    assert t.needs_build() is False                 # just fetched
    t.last_build -= neon_tabs.REMOTE_EVERY_S + 1
    assert t.needs_build() is True                  # a Neon tab is showing: refetch on the timer
    t.select(0)
    assert t.needs_build() is False                 # timeline showing: no ssh traffic


def test_remote_fetch_failure_keeps_the_last_snapshot_and_says_so(tmp_path):
    import neon_tabs
    t = neon_tabs.Tabs(state_file=tmp_path / "tab.json", grid_file=tmp_path / "grid.json", remote=True)
    t.build(lambda cmd, **kw: _Proc(0, json.dumps(_remote_snap())))
    before = (tmp_path / "grid.json").read_text()
    t.build(lambda cmd, **kw: _Proc(255, "", "ssh: connect to host ix port 22: Operation timed out"))
    assert "timed out" in t.error and (tmp_path / "grid.json").read_text() == before and t.names() == ["janus", "0分"]
    t.build(lambda cmd, **kw: _Proc(0, json.dumps(_remote_snap(version=1))))
    assert "snapshot v1" in t.error and (tmp_path / "grid.json").read_text() == before
    t.build(lambda cmd, **kw: _Proc(0, "Last login: banner, not JSON"))
    assert t.error.startswith("ix: ") and (tmp_path / "grid.json").read_text() == before


def test_a_stale_workbook_save_shows_its_date(tmp_path):
    """The status line read 'saved 12:34' for a copy six weeks old."""
    import neon_tabs, time as _t
    old = _t.mktime((2026, 10, 12, 12, 34, 0, 0, 0, -1))
    t = _tabs(tmp_path, [_sheet()])
    t.snap["src_mtime"] = old
    t.select(1)
    status = _text(t.render(140, 24)).split("\n")[-1]
    assert "saved 10-12 12:34 ⚠ stale" in status


def test_local_mode_is_the_default_under_pytest_and_with_explicit_files(tmp_path, monkeypatch):
    import neon_tabs
    monkeypatch.setattr(neon_tabs, "on_ix", lambda: False)
    assert neon_tabs.Tabs(state_file=tmp_path / "t.json", grid_file=tmp_path / "g.json").remote is False
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert neon_tabs.Tabs(state_file=tmp_path / "t.json", grid_file=tmp_path / "g.json").remote is False
    monkeypatch.setenv("NEON_GRID_REMOTE", "1")
    assert neon_tabs.Tabs(state_file=tmp_path / "t.json", grid_file=tmp_path / "g.json").remote is True


def test_janus_header_marks_a_local_run(monkeypatch):
    """A janus that is not the one on Ix says so in its header (2026-11-22)."""
    mod = _load_janus()
    import socket
    monkeypatch.setattr(mod, "_LOCAL_TAG", None)
    monkeypatch.setattr(mod.neon_tabs, "on_ix", lambda: False)
    monkeypatch.setattr(socket, "gethostname", lambda: "Straylight-Refit.local")
    assert mod._local_tag() == " · LOCAL straylight-ref"
    assert "LOCAL straylight-ref" in "".join(f[1] for f in mod.render_header())
    monkeypatch.setattr(mod, "_LOCAL_TAG", None)
    monkeypatch.setattr(mod.neon_tabs, "on_ix", lambda: True)
    assert mod._local_tag() == "" and "LOCAL" not in "".join(f[1] for f in mod.render_header())


def test_continuation_lines_hang_under_the_date_columns(tmp_path):
    """JM 2026-11-25: "For 0n let's do 3 lines / day, but everything is
    indented after the first line so I can navigate", then "on 0s897 and hcbi
    etc also". The task lists (2n, 3n, 4n) stay flush left."""
    flush = _tabs(tmp_path, [_sheet("2n", ncols=14, today=None)])
    flush.select(1)
    assert _text(flush.render(31, 24)).split("\n")[2].split() == ["H6", "H7", "H8", "H9", "H10", "H11"]
    t = _tabs(tmp_path, [_sheet("0n", ncols=14)])
    t.select(1)
    frags = t.render(31, 24)
    lines = _text(frags).split("\n")
    assert len(lines) == 24
    assert lines[1].split() == ["H0", "H1", "H2", "H3", "H4", "H5"]
    assert lines[2].startswith(" " * 11) and lines[2].split() == ["H6", "H7", "H8", "H9"]   # 31 - 11 = four columns
    assert lines[3].startswith(" " * 11) and lines[3].split() == ["H10", "H11", "H12", "H13"]
    body = lines[4:-1]
    days = [ln for ln in body if ln.strip() and not ln.startswith(" " * 11)]
    assert days and all(ln.lstrip().startswith("w") for ln in days)      # only a day's first line reaches the left edge
    assert len(days) == t.body_height(24, 31) == (24 - 2 - 3) // 3
    assert all(ng.cwidth(ln) <= 31 for ln in lines)
