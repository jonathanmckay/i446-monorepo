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
    assert f.status == "warn" and f.review and f.detail == ["vault/pc-stores-windows.md"]


def test_filename_date_shapes():
    bad = ["notice-a627-2-92018", "call-20180731", "pfs-2022q1", "travel-breaks-08-2024"]
    good = ["a627-non-compliance-2018-02-09", "qz12-pfs-2022-q1", "f694-checklist-2026-07",
            "2026.04.30-build-order", "2026-05-13-retention", "2016-02-24"]
    assert all(vh._BAD_DATE_RE.search(n) for n in bad)
    assert not any(vh._BAD_DATE_RE.search(n) for n in good)


def test_root_audits_home_layout(tmp_path):
    vault, home = tmp_path / "vault", tmp_path / "home"
    vault.mkdir(); home.mkdir()
    for d in ("vault", "files", "code", "Library", ".config"):
        (home / d).mkdir()
    (home / "bin").symlink_to(home / "code")
    assert vh.check_root(vault, home).status == "ok"
    (home / "dead").symlink_to(home / "nope")
    (home / "old-backup-20260101").mkdir()
    (home / "neon.xlsx").write_text("x")
    (home / "repo").mkdir(); (home / "repo" / ".git").mkdir()
    (home / "notes.md").write_text("x")
    d = vh.check_root(vault, home).detail
    assert any(s.startswith("~/dead → broken symlink") for s in d)
    assert any(s.startswith("~/old-backup-20260101 → ~/.to-delete/") for s in d)
    assert "~/neon.xlsx → OneDrive (Excel lives there)" in d
    assert "~/repo → ~/code/" in d and "~/notes.md → ~/vault/<domain>/" in d
