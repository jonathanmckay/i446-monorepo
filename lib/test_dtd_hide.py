import importlib

import dtd_hide


def test_noop_without_dtd_env(monkeypatch, tmp_path):
    monkeypatch.delenv(dtd_hide.ENV_ID, raising=False)
    monkeypatch.delenv(dtd_hide.ENV_FILE, raising=False)
    assert dtd_hide.hide_card() is False
    assert dtd_hide.unhide_card() is False
    assert list(tmp_path.iterdir()) == []


def test_hide_appends_and_unhide_strips_only_that_id(monkeypatch, tmp_path):
    f = tmp_path / "removed.ids"
    f.write_text("OTHER\n")
    monkeypatch.setenv(dtd_hide.ENV_ID, "CARD1")
    monkeypatch.setenv(dtd_hide.ENV_FILE, str(f))
    assert dtd_hide.hide_card() is True
    assert f.read_text().splitlines() == ["OTHER", "CARD1"]
    assert dtd_hide.unhide_card() is True
    assert f.read_text().splitlines() == ["OTHER"]
    assert dtd_hide.unhide_card() is False          # nothing left to strip
    assert not (tmp_path / "removed.ids.tmp").exists()


def test_hide_creates_missing_file(monkeypatch, tmp_path):
    f = tmp_path / "removed.ids"
    monkeypatch.setenv(dtd_hide.ENV_ID, "X")
    monkeypatch.setenv(dtd_hide.ENV_FILE, str(f))
    assert dtd_hide.hide_card() is True
    assert f.read_text() == "X\n"
    importlib.reload(dtd_hide)
