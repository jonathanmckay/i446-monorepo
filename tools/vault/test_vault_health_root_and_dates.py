"""2026-10-24 style-guide additions: the vault root holds only folders,
CLAUDE.md and dotfiles; dates in filenames are YYYY-MM-DD / YYYY-MM / YYYY-qN."""
import importlib.util
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location("vh_rd", Path(__file__).parent / "vault_health.py")
vh = importlib.util.module_from_spec(_s); sys.modules["vh_rd"] = vh; _s.loader.exec_module(vh)


def test_root_flags_stray_files_only(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("x")
    (tmp_path / ".gitignore").write_text("x")
    (tmp_path / "h335").mkdir()
    assert vh.check_root(tmp_path).status == "ok"
    (tmp_path / "pc-stores-windows.md").write_text("x")
    f = vh.check_root(tmp_path)
    assert f.status == "warn" and f.review and f.detail == ["pc-stores-windows.md"]


def test_filename_date_shapes():
    bad = ["notice-a627-2-92018", "call-20180731", "pfs-2022q1", "travel-breaks-08-2024"]
    good = ["a627-non-compliance-2018-02-09", "qz12-pfs-2022-q1", "f694-checklist-2026-07",
            "2026.04.30-build-order", "2026-05-13-retention", "2016-02-24"]
    assert all(vh._BAD_DATE_RE.search(n) for n in bad)
    assert not any(vh._BAD_DATE_RE.search(n) for n in good)
