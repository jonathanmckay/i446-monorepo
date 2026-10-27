"""drive_gap buckets: identical bytes, same name with different bytes, missing."""
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("drive_gap", Path(__file__).with_name("drive_gap.py"))
dg = importlib.util.module_from_spec(spec); sys.modules["drive_gap"] = dg
spec.loader.exec_module(dg)


def test_buckets(tmp_path):
    (tmp_path / "drives.tsv").write_text("ID1\tm5x2 Main\n")
    (tmp_path / "idx-ID1.json").write_text(json.dumps([
        {"Path": "r204 /a.pdf", "Name": "a.pdf", "Hashes": {"md5": "1" * 32}},
        {"Path": "Portfolio/b.xlsx", "Name": "b.xlsx", "Hashes": {"md5": "2" * 32}},
        {"Path": "Notes.gdoc", "Name": "Notes.gdoc"}]))
    by_md5, by_name = dg.drive_index(tmp_path)
    rows = dg.classify([("1" * 32, "x/renamed.pdf"), ("9" * 32, "x/b.xlsx"), ("8" * 32, "y/c.pdf"),
                        ("7" * 32, "y/.DS_Store")], by_md5, by_name)
    assert [r["bucket"] for r in rows] == ["in_drive", "name_match", "missing"]
    assert rows[0]["drive"] == "m5x2 Main/r204 /a.pdf"
    assert dg.summary(rows)["TOTAL"] == {"in_drive": 1, "name_match": 1, "missing": 1}
