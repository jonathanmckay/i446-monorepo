"""Tests for s897_update.py — /s897 people-metadata shorthand."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(tmp_path=None):
    spec = importlib.util.spec_from_file_location("s897_update", HERE / "s897_update.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["s897_update"] = mod
    spec.loader.exec_module(mod)
    if tmp_path is not None:
        mod.D359 = tmp_path
    return mod


FM = """---
title: "Jessica Allen"
cadence: weekly
last_contact: 2026-07-10
updated: 2026-05-26
---

profile body
"""


def _fixture(tmp_path):
    (tmp_path / "jessica-allen-d359.md").write_text(FM)
    (tmp_path / "Jordan Allen d359.md").write_text(FM.replace("Jessica", "Jordan"))
    (tmp_path / "carol-bryan d359.md").write_text(FM.replace("Jessica Allen", "Carol Bryan"))


def test_resolve_longest_prefix(tmp_path):
    m = _load(tmp_path); _fixture(tmp_path)
    p, used, _ = m.resolve_person(["Jessica", "Allen", "met", "yesterday"])
    assert p.name == "jessica-allen-d359.md" and used == 2


def test_resolve_space_style_filename(tmp_path):
    m = _load(tmp_path); _fixture(tmp_path)
    p, used, _ = m.resolve_person(["jordan", "allen", "cadence", "monthly"])
    assert p.name == "Jordan Allen d359.md" and used == 2


def test_resolve_ambiguous_lists_candidates(tmp_path):
    m = _load(tmp_path); _fixture(tmp_path)
    p, _used, cands = m.resolve_person(["Allen", "met"])
    assert p is None and len(cands) == 2


def test_resolve_unique_substring_single_token(tmp_path):
    m = _load(tmp_path); _fixture(tmp_path)
    p, used, _ = m.resolve_person(["carol", "met"])
    assert p.name == "carol-bryan d359.md" and used == 1


def test_parse_date_forms():
    m = _load()
    assert m.parse_date([]) == dt.date.today()
    assert m.parse_date(["yesterday"]) == dt.date.today() - dt.timedelta(days=1)
    assert m.parse_date(["2026-07-15"]) == dt.date(2026, 7, 15)
    assert m.parse_date(["nonsense"]) is None


def test_patch_field_replaces_and_inserts():
    m = _load()
    out = m.patch_field(FM, "last_contact", "2026-07-20")
    assert "last_contact: 2026-07-20" in out and "2026-07-10" not in out
    out2 = m.patch_field(FM, "role", "EA")
    assert "role: EA" in out2
    assert out2.index("role: EA") < out2.index("---\n\nprofile")  # inside frontmatter
    assert m.patch_field("no frontmatter here", "x", "y") is None


def test_slug_matches_stale_contacts_convention(tmp_path):
    m = _load(tmp_path); _fixture(tmp_path)
    assert m._slug(tmp_path / "jessica-allen-d359.md") == "jessica-allen"
    assert m._slug(tmp_path / "Jordan Allen d359.md") == "jordan-allen"


def test_unknown_field_is_rejected(tmp_path, monkeypatch, capsys):
    m = _load(tmp_path); _fixture(tmp_path)
    monkeypatch.setattr(sys, "argv", ["s897", "--dry-run", "Jessica Allen flurb x"])
    assert m.main() == 1
    before = (tmp_path / "jessica-allen-d359.md").read_text()
    assert before == FM  # no write


def test_met_dry_run_updates_nothing_but_reports(tmp_path, monkeypatch, capsys):
    m = _load(tmp_path); _fixture(tmp_path)
    monkeypatch.setattr(m, "delete_robot_outreach", lambda slug, dry: ["😈 Reach out to Jessica Allen [10]"])
    monkeypatch.setattr(sys, "argv", ["s897", "--dry-run", "Jessica Allen met yesterday"])
    assert m.main() == 0
    out = capsys.readouterr().out
    assert "last_contact →" in out and "😈" in out
    assert (tmp_path / "jessica-allen-d359.md").read_text() == FM


def test_met_writes_frontmatter(tmp_path, monkeypatch):
    m = _load(tmp_path); _fixture(tmp_path)
    monkeypatch.setattr(m, "delete_robot_outreach", lambda slug, dry: [])
    monkeypatch.setattr(sys, "argv", ["s897", "Jessica Allen met 2026-07-15"])
    assert m.main() == 0
    text = (tmp_path / "jessica-allen-d359.md").read_text()
    assert "last_contact: 2026-07-15" in text
    assert f"updated: {dt.date.today().isoformat()}" in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
