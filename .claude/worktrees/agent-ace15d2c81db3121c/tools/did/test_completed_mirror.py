"""Cross-machine completed-today mirroring (2026-07-30): a completion on ix
(janus-mobile → did-fast) must reach Straylight's dtd via the synced vault.
Each host writes ONLY its own z_ibx/completed-today-<host>.json (single
writer per file); absorb_remote() folds other hosts' files into local."""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def mc(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mc_mirror", _HERE / "mark-completed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mc_mirror"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "COMPLETED", tmp_path / "completed-today.json")
    monkeypatch.setattr(mod, "MIRROR_DIR", tmp_path / "z_ibx")
    return mod


def test_local_write_mirrors_to_vault(mc):
    mc.append_names(["stats"], ids={"stats": "123"})
    mirror = mc._mirror_path()
    assert mirror.exists()
    d = json.loads(mirror.read_text())
    assert "stats" in d["names"] and d["ids"]["stats"] == "123"


def test_tmp_path_writes_do_not_mirror(mc, tmp_path):
    mc.append_names(["stats"], path=tmp_path / "other.json")
    assert not mc._mirror_path().exists()


def test_absorb_remote_merges_other_host(mc):
    today = date.today().isoformat()
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    remote = mc.MIRROR_DIR / "completed-today-otherbox.json"
    remote.write_text(json.dumps({
        "date": today, "names": ["poke bowl [10]"],
        "points": {"poke bowl [10]": 10}, "ids": {"poke bowl [10]": "999"}}))
    n = mc.absorb_remote()
    assert n == 1
    local = json.loads(mc.COMPLETED.read_text())
    assert "poke bowl [10]" in local["names"]
    assert local["ids"]["poke bowl [10]"] == "999"


def test_absorb_remote_ignores_own_and_stale(mc):
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    # own host's mirror must not be re-absorbed
    mc._mirror_path().write_text(json.dumps({
        "date": date.today().isoformat(), "names": ["self-echo"], "ids": {}}))
    # stale remote (yesterday) must be date-gated out
    (mc.MIRROR_DIR / "completed-today-otherbox.json").write_text(json.dumps({
        "date": "2020-01-01", "names": ["ancient"], "ids": {}}))
    assert mc.absorb_remote() == 0
    local = json.loads(mc.COMPLETED.read_text()) if mc.COMPLETED.exists() else {"names": []}
    assert "self-echo" not in local.get("names", [])
    assert "ancient" not in local.get("names", [])


def test_absorb_is_idempotent(mc):
    today = date.today().isoformat()
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    (mc.MIRROR_DIR / "completed-today-otherbox.json").write_text(json.dumps({
        "date": today, "names": ["once"], "ids": {}}))
    assert mc.absorb_remote() == 1
    assert mc.absorb_remote() == 0
