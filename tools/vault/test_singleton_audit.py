#!/usr/bin/env python3
"""singleton-audit (2026-09-24): flags folders under the ~3-doc rule, skips
structural trees (date buckets, m5x2 property folders, archives)."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("singleton_audit", HERE / "singleton-audit.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["singleton_audit"] = mod
    spec.loader.exec_module(mod); return mod


def test_scan_flags_singletons_and_skips_structural(tmp_path):
    sa = _load()
    (tmp_path / "hcbi/hcbs").mkdir(parents=True); (tmp_path / "hcbi/hcbs/hcbs.md").write_text("x")
    (tmp_path / "hcbi/hcbc").mkdir(); [ (tmp_path / f"hcbi/hcbc/{n}.md").write_text("x") for n in "abc" ]
    (tmp_path / "g245/archive/2026/2026.06.01").mkdir(parents=True)
    (tmp_path / "g245/archive/2026/2026.06.01/build-order.md").write_text("x")
    (tmp_path / "h335/m5x2/fund-iii/portfolio/l912").mkdir(parents=True)
    (tmp_path / "h335/m5x2/fund-iii/portfolio/l912/l912.md").write_text("x")
    (tmp_path / "xk87/empty").mkdir(parents=True)
    (tmp_path / "z_asts").mkdir(); (tmp_path / "z_asts/one.png").write_bytes(b"")
    rows = {r["rel"]: r for r in sa.scan(tmp_path)}
    assert "hcbi/hcbs" in rows and rows["hcbi/hcbs"]["note"] == "hcbs.md" and rows["hcbi/hcbs"]["files"] == []
    assert "xk87/empty" in rows
    assert "hcbi/hcbc" not in rows                       # 3 docs → earns the folder
    assert not any(k.startswith("g245/archive") for k in rows)
    assert "h335/m5x2/fund-iii/portfolio/l912" not in rows
    assert "z_asts" not in rows


def test_excluded_patterns():
    sa = _load()
    assert sa.excluded("g245/archive/2026/2026.06.01")
    assert sa.excluded("hcmp/o314/2026")
    assert sa.excluded("h335/m5x2/sold/a617")
    assert not sa.excluded("h335/m5x2/r202/move-out-packet")
    assert not sa.excluded("hcbi/hcbm")


def test_description_is_numbered_and_bounded():
    sa = _load()
    rows = [{"rel": f"x/{i}", "files": ["a.md"], "note": None} for i in range(400)]
    d = sa.build_description(rows, "z_meta/r.md")
    assert d.startswith("Weekly vault folder-hygiene audit. Report: z_meta/r.md")
    assert "\n1. x/0/ — a.md" in d and len(d) <= 15000
