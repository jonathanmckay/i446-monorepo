"""Tests for 1g_review.py."""
import importlib.util
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

_P = Path(__file__).parent / "1g_review.py"
_S = importlib.util.spec_from_file_location("r1g", _P)
r = importlib.util.module_from_spec(_S)
_S.loader.exec_module(r)

DOC = """---
updated: 2026-10-05
---
### Weekly arc

- **Oct 4 – Oct 10** —
\t- qz12 
\t\t- Do the things needed for taxes 
\t- hcmc
\t\t- 7 days good media {70} 
\t- m5x2 
\t\t- 600分
- **Oct 11 – Oct 17** —
\t- i9
\t\t- 900分
"""
SUN, SAT = date(2026, 10, 4), date(2026, 10, 10)


def test_week_bounds_last_completed_sunday_to_saturday():
    assert r.week_bounds(None, today=date(2026, 10, 11)) == (SUN, SAT)  # Sunday → previous week
    assert r.week_bounds(None, today=date(2026, 10, 17)) == (SUN, SAT)  # Saturday → previous full week
    assert r.week_bounds("2026-10-07") == (SUN, SAT)     # Wednesday → its week


def test_parse_only_the_target_week():
    goals = r.parse_week_goals(DOC.splitlines(), SUN, SAT)
    assert [(g["domain"], g["text"]) for g in goals] == [
        ("qz12", "Do the things needed for taxes"),
        ("hcmc", "7 days good media {70}"),
        ("m5x2", "600分"),
    ]


def _ctx(points):
    return {"days": r.week_days(SUN), "points": points, "tasks": []}


def test_points_goal_sums_sunday_through_saturday():
    pts = {d.isoformat(): {"m5": 100} for d in r.week_days(SUN)}  # every day present
    pts["2026-10-11"] = {"m5": 999}                     # next Sunday excluded
    g = r.evaluate({"domain": "m5x2", "text": "600分", "line_no": 0}, _ctx(pts))
    assert g["actual"] == 700 and g["status"] == "✅"


def test_net_negative_day_missing_from_cache_is_read_live():
    pts = {d.isoformat(): {"hcb": 100} for d in r.week_days(SUN)}
    del pts["2026-10-06"]["hcb"]                        # cache drops -17
    with patch.object(r, "_neon_day_value", return_value=-17.0):
        g = r.evaluate({"domain": "hcb", "text": "500分", "line_no": 0}, _ctx(pts))
    assert g["actual"] == 583


def test_points_goal_with_extra_text_needs_question():
    pts = {d.isoformat(): {"hcb": 100} for d in r.week_days(SUN)}
    g = r.evaluate({"domain": "hcb", "text": "1117分 -> weight should be 192.x", "line_no": 0}, _ctx(pts))
    assert g["status"] == "❌" and g["needs_question"]
    assert "weight" in g["unscored_remainder"]


def test_good_media_counts_days_without_junk():
    vals = iter(["-1", "-1", "5", "6", "-1", "-1", ""])
    with patch.dict("sys.modules", {"neon": type("m", (), {"excel": type("e", (), {
            "read": staticmethod(lambda *a, **k: {"ok": True, "value": next(vals)})})})}):
        g = r.evaluate({"domain": "hcmc", "text": "7 days good media {70}", "line_no": 0}, _ctx({}))
    assert g["actual"] == 5 and g["status"] == "❌"


def test_unknown_goal_is_question_with_keyword_evidence():
    ctx = _ctx({})
    ctx["tasks"] = [{"content": "file tax extension", "domains": ["m5x2"], "date": "2026-10-06"}]
    g = r.evaluate({"domain": "qz12", "text": "Do the things needed for taxes", "line_no": 0}, ctx)
    assert g["status"] == "❓" and g["evidence"][0]["content"] == "file tax extension"


def test_apply_writes_and_replaces_child_bullets(tmp_path):
    doc = tmp_path / "n.md"
    doc.write_text(DOC)
    v = tmp_path / "v.json"
    v.write_text(json.dumps({"doc": str(doc), "verdicts": [
        {"text": "600分", "line_no": 11, "status": "✅", "note": "729分"}]}))
    r.apply(str(v))
    r.apply(str(v))                                       # idempotent
    lines = doc.read_text().splitlines()
    i = lines.index("\t\t- 600分")
    assert lines[i + 1] == "\t\t\t- ✅ 729分 <!--1g-->"
    assert sum(r.MARK in l for l in lines) == 1
    # annotations don't get parsed back as goals
    assert len(r.parse_week_goals(lines, SUN, SAT)) == 3
