"""Regression (user request 2026-09-28): points logged against the hcbp
category ("... @hcbp", a Todoist task labelled hcbp) must land in hcbi!Y, the
sheet's own hcbp column, not be appended raw onto 0分!W. 0分!W is a formula
(=hcbi!AA+hcbi!Y+...), so a Y write already reaches it; a direct W append
would both misfile the points and double-count them."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "did-fast.py").read_text()
TREE = ast.parse(SRC)


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_hcbp", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_hcbp"] = mod
    spec.loader.exec_module(mod)
    return mod


def _main_body() -> str:
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == "main")
    return ast.get_source_segment(SRC, fn)


def test_hcbp_label_routes_to_hcbi_y_not_0fen_w():
    m = _load()
    assert m.LABEL_TO_0FEN["hcbp"] == "hcbi:Y"
    assert m._hcbi_domain_col(m.LABEL_TO_0FEN["hcbp"]) == "Y"
    assert m._hcbi_domain_col("W") is None and m._hcbi_domain_col(None) is None
    # plain hcb is untouched
    assert m.LABEL_TO_0FEN["hcb"] == "W"


def test_project_fen_col_for_hcbp_task_is_hcbi_route():
    m = _load()
    assert m._project_fen_col(["hcbp"]) == "hcbi:Y"
    assert m._project_fen_col(["0neon", "hcbp"]) == "hcbi:Y"


def test_fen_appends_skip_hcbi_domain_and_hcbi_appends_take_the_points():
    body = _main_body()
    start = body.index("fen_appends = []")
    fen_loop = body[start:body.index("fen_result = None", start)]
    assert "is_hcbi_domain" in fen_loop and "not is_hcbi_domain" in fen_loop, \
        "the 0分 points append must be gated off for hcbi-routed domains"
    hstart = body.index("hcbi_appends = []")
    hcbi_loop = body[hstart:body.index("hcbi_result = None", hstart)]
    assert "_hcbi_domain_col(r.fen_col)" in hcbi_loop and "r.fen_points" in hcbi_loop, \
        "hcbi append batch must carry the hcbp-routed points"


def test_result_entry_records_hcbi_pts_not_0fen():
    body = _main_body()
    rec = body[body.index('entry["hcbi"] = {"col": dom_col, "pts": r.fen_points}'):]
    assert 'if r.fen_col and not _hcbi_domain_col(r.fen_col):' in rec, \
        'the "0fen" result entry must be suppressed for hcbi-routed points (undo would strip 0分)'


def test_undo_strips_hcbi_pts_or_mins():
    undo = (HERE / "undo-fast.py").read_text()
    assert 'e["hcbi"].get("pts", e["hcbi"].get("mins"))' in undo
