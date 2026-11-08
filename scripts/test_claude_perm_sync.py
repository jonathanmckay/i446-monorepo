#!/usr/bin/env python3
"""claude-perm-sync: two fake hosts exchanging state through one shared dir."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("claude_perm_sync", HERE / "claude-perm-sync.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["claude_perm_sync"] = mod
    spec.loader.exec_module(mod); return mod


ps = _load()


def _host(tmp_path, name, allow, tombstones=None, extra=None):
    d = tmp_path / name / ".claude"; d.mkdir(parents=True)
    settings = {"permissions": {"allow": allow, "deny": []}, **(extra or {})}
    (d / "settings.local.json").write_text(json.dumps(settings))
    if tombstones is not None:
        (d / "perm-sync-removals.json").write_text(json.dumps(tombstones))
    return d


def _allow(d):
    return json.loads((d / "settings.local.json").read_text())["permissions"]["allow"]


def test_two_runs_converge_without_ssh(tmp_path):
    shared = tmp_path / "files/perm-sync"
    ix = _host(tmp_path, "ix", ["Bash(a)", "Bash(b)"], extra={"model": "keep-me"})
    st = _host(tmp_path, "st", ["Bash(c)"])
    ps.sync("ix", ix, shared, now=100)
    ps.sync("straylight", st, shared, now=200)
    ps.sync("ix", ix, shared, now=300)
    assert _allow(ix) == _allow(st) == ["Bash(a)", "Bash(b)", "Bash(c)"]
    assert json.loads((ix / "settings.local.json").read_text())["model"] == "keep-me"
    stray = json.loads((shared / "straylight.json").read_text())
    ixs = json.loads((shared / "ix.json").read_text())
    assert stray["merged"] == {"ix": 100} and ixs["merged"] == {"straylight": 200}
    assert ixs["synced_at"] == 300


def test_tombstone_on_one_host_removes_from_both(tmp_path):
    shared = tmp_path / "s"
    ix = _host(tmp_path, "ix", ["Bash(a)", "Bash(bad)"])
    st = _host(tmp_path, "st", ["Bash(bad)", "Bash(c)"], tombstones=["Bash(bad)"])
    ps.sync("straylight", st, shared, now=1)
    ps.sync("ix", ix, shared, now=2)
    ps.sync("straylight", st, shared, now=3)
    assert "Bash(bad)" not in _allow(ix) and "Bash(bad)" not in _allow(st)
    assert json.loads((ix / "perm-sync-removals.json").read_text()) == ["Bash(bad)"]
    assert any((ix / "perm-sync-backups").glob("settings.local.*.json"))


def test_unreadable_or_conflict_peer_files_are_skipped(tmp_path):
    shared = tmp_path / "s"; shared.mkdir()
    (shared / "straylight.json").write_text('{"allow": ["Bash(x)"')  # half-synced
    (shared / "ix.sync-conflict-20261108-1.json").write_text(json.dumps({"allow": ["Bash(evil)"]}))
    ix = _host(tmp_path, "ix", ["Bash(a)"])
    state = ps.sync("ix", ix, shared, now=5)
    assert _allow(ix) == ["Bash(a)"] and state["merged"] == {}


def test_unchanged_settings_are_not_rewritten(tmp_path):
    shared = tmp_path / "s"
    ix = _host(tmp_path, "ix", ["Bash(a)"])
    ps.sync("ix", ix, shared, now=1)
    assert not (ix / "perm-sync-backups").exists()
    assert (shared / "ix.json").exists()


def test_unknown_hostname_refuses_to_guess(monkeypatch):
    monkeypatch.setattr(ps.socket, "gethostname", lambda: "ixtra-box.local")
    with pytest.raises(SystemExit):
        ps.default_host()
    monkeypatch.setattr(ps.socket, "gethostname", lambda: "Straylight-Refit.local")
    assert ps.default_host() == "straylight"


def test_reordered_but_identical_allowlist_is_left_alone(tmp_path):
    shared = tmp_path / "s"
    ix = _host(tmp_path, "ix", ["Bash(b)", "Bash(a)"])
    ps.sync("ix", ix, shared, now=1)
    assert _allow(ix) == ["Bash(b)", "Bash(a)"]
