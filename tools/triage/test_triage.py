"""triage: rule order, duplicate detection, dry run vs apply, undo log."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("triage", Path(__file__).with_name("triage.py"))
triage = importlib.util.module_from_spec(spec)
import sys; sys.modules["triage"] = triage
spec.loader.exec_module(triage)

RULES = json.loads((triage.CONFIG).read_text())["sources"]["downloads"]["rules"]


def _item(p: Path, days: float) -> "triage.Item":
    return triage.Item(p, dt.datetime.now() - dt.timedelta(days=days), p.stat().st_size if p.exists() else 0)


def _touch(d: Path, name: str, data: bytes = b"x") -> Path:
    p = d / name
    p.write_bytes(data)
    return p


def test_installer_needs_14_days(tmp_path):
    p = _touch(tmp_path, "Foo.dmg")
    assert triage.decide(_item(p, 3), RULES).action == "keep"
    assert triage.decide(_item(p, 20), RULES).rule == "installer"


def test_duplicate_copy_only_when_identical(tmp_path):
    _touch(tmp_path, "report.pdf", b"same")
    same = _touch(tmp_path, "report (1).pdf", b"same")
    diff = _touch(tmp_path, "notes (1).pdf", b"other")
    _touch(tmp_path, "notes.pdf", b"different")
    assert triage.decide(_item(same, 0), RULES).rule == "duplicate-copy"
    assert triage.decide(_item(diff, 0), RULES).rule != "duplicate-copy"


def test_tax_record_is_proposal_until_enabled(tmp_path):
    p = _touch(tmp_path, "2025 Tax Return - MCKAY.pdf")
    d = triage.decide(_item(p, 400), RULES)
    assert (d.action, d.rule) == ("review", "tax-record") and d.note.startswith("proposed: move")


def test_stale_moves_to_flat_archive(tmp_path):
    p = _touch(tmp_path, "random.txt")
    d = triage.decide(_item(p, 45), RULES)
    assert d.action == "move" and d.dest == Path.home() / "Archive"


def test_apply_moves_and_logs_for_undo(tmp_path, monkeypatch):
    src = tmp_path / "dl"
    src.mkdir()
    p = _touch(src, "old.dmg")
    monkeypatch.setattr(triage, "TRASH", tmp_path / "trash")
    d = triage.decide(_item(p, 30), RULES)
    log = tmp_path / "log.jsonl"
    assert triage.apply([d], log) == 1
    rec = json.loads(log.read_text())
    assert not p.exists() and Path(rec["dst"]).exists() and rec["src"] == str(p)


def test_dry_run_touches_nothing(tmp_path, capsys):
    src = tmp_path / "dl"
    src.mkdir()
    p = _touch(src, "old.dmg")
    old = (dt.datetime.now() - dt.timedelta(days=60)).timestamp()
    os.utime(p, (old, old))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"sources": {"t": {"path": str(src), "rules": RULES}}}))
    triage.main(["--config", str(cfg)])
    assert p.exists() and "dry run" in capsys.readouterr().out


def test_review_rules_beat_trash_by_type(tmp_path):
    p = _touch(tmp_path, "2025 K-1 bundle.zip")
    assert triage.decide(_item(p, 60), RULES).rule == "lp-k1-copy"


def test_plain_zip_is_reviewed_not_trashed(tmp_path):
    import zipfile
    p = tmp_path / "export.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("notes/a.md", "x")
    assert triage.decide(_item(p, 60), RULES).action == "review"


def test_app_zip_and_extracted_zip_are_trashed(tmp_path):
    import zipfile
    app = tmp_path / "Tool.zip"
    with zipfile.ZipFile(app, "w") as z:
        z.writestr("Tool.app/Contents/Info.plist", "x")
    ext = tmp_path / "bundle.zip"
    with zipfile.ZipFile(ext, "w") as z:
        z.writestr("a.txt", "x")
    (tmp_path / "bundle").mkdir()
    assert triage.decide(_item(app, 5), RULES).rule == "zip-app-installer"
    assert triage.decide(_item(ext, 5), RULES).rule == "zip-extracted"


def test_jpg_beside_heic_is_trashed(tmp_path):
    _touch(tmp_path, "IMG_1.HEIC")
    j = _touch(tmp_path, "IMG_1.jpg")
    assert triage.decide(_item(j, 10), RULES).rule == "image-twin"


def test_own_k1_is_tax_record_others_are_lp_copies(tmp_path):
    own = _touch(tmp_path, "BRADEN LLC_JONATHAN MCKAY_2025_1065_K1.pdf")
    lp = _touch(tmp_path, "2025 K-1 - M5X2 FUND III - THE X TRUST.pdf")
    assert triage.decide(_item(own, 5), RULES).rule == "tax-record"
    assert triage.decide(_item(lp, 5), RULES).rule == "lp-k1-copy"
