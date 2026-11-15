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


# ── Undo across hosts (2026-10-08): last-writer-wins on done_at vs undone ──

def _remote(mc, name, data):
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    (mc.MIRROR_DIR / f"completed-today-{name}.json").write_text(json.dumps(data))


def test_undo_is_not_resurrected_by_other_hosts_copy(mc):
    """The original bug: undo here, the other host's mirror still lists the
    name, absorb_remote put it right back."""
    today = date.today().isoformat()
    mc.append_names(["stats"])
    done_t = json.loads(mc.COMPLETED.read_text())["done_at"]["stats"]
    _remote(mc, "otherbox", {"date": today, "names": ["stats"], "done_at": {"stats": done_t}})
    mc.remove_names(["stats"])
    mc.absorb_remote()
    assert "stats" not in json.loads(mc.COMPLETED.read_text())["names"]
    assert "stats" in json.loads(mc._mirror_path().read_text())["undone"], "undo must be mirrored"


def test_remote_undo_removes_local_completion(mc):
    today = date.today().isoformat()
    mc.append_names(["stats"])
    t = json.loads(mc.COMPLETED.read_text())["done_at"]["stats"]
    _remote(mc, "otherbox", {"date": today, "names": [], "undone": {"stats": t + 5}})
    assert mc.absorb_remote() == -1
    assert "stats" not in json.loads(mc.COMPLETED.read_text())["names"]


def test_recompletion_after_remote_undo_wins(mc):
    today = date.today().isoformat()
    _remote(mc, "otherbox", {"date": today, "names": [], "undone": {"stats": 100.0}})
    mc.absorb_remote()
    mc.append_names(["stats"])  # done_at = now >> 100
    mc.absorb_remote()
    assert "stats" in json.loads(mc.COMPLETED.read_text())["names"]


def test_legacy_remote_without_timestamps_still_absorbs(mc):
    _remote(mc, "otherbox", {"date": date.today().isoformat(), "names": ["xk20"]})
    assert mc.absorb_remote() == 1


def test_absorb_skips_sync_conflict_copies(mc):
    _remote(mc, "otherbox.sync-conflict-20260101-000000-ABC", {
        "date": date.today().isoformat(), "names": ["ghost"]})
    assert mc.absorb_remote() == 0


# --- fast pull (2026-11-15) ----------------------------------------------------
# /fin closed `1g` on Ix; Straylight's dtd kept the row for 66s, until Syncthing
# delivered Ix's mirror. agent-claims-pull.sh now also pulls Ix's mirrors into
# a non-synced dir every ~2s; absorb_remote must read that copy too.

def test_absorb_reads_fast_pulled_ix_mirror_before_syncthing_delivers(mc, tmp_path, monkeypatch):
    today = date.today().isoformat()
    fast = tmp_path / "remote-done"
    (fast / "ix").mkdir(parents=True)
    monkeypatch.setattr(mc, "FAST_DIR", fast)
    mc.MIRROR_DIR.mkdir(parents=True, exist_ok=True)   # Syncthing copy not here yet
    # Not "ix-local": on Ix that is this host's own mirror name, which is skipped.
    (fast / "ix" / "completed-today-otherbox.json").write_text(json.dumps({
        "date": today, "names": ["1g (20)"], "ids": {"1g (20)": "6gJPwc4QXW8JhrX6"}}))
    assert mc.absorb_remote() == 1
    assert json.loads(mc.COMPLETED.read_text())["ids"]["1g (20)"] == "6gJPwc4QXW8JhrX6"


def test_fast_dir_skips_own_mirror_and_conflict_copies(mc, tmp_path, monkeypatch):
    today = date.today().isoformat()
    fast = tmp_path / "remote-done"
    (fast / "ix").mkdir(parents=True)
    monkeypatch.setattr(mc, "FAST_DIR", fast)
    rec = json.dumps({"date": today, "names": ["echo"], "ids": {"echo": "1"}})
    (fast / "ix" / mc._mirror_path().name).write_text(rec)
    (fast / "ix" / "completed-today-ix.sync-conflict-20261115.json").write_text(rec)
    assert mc.absorb_remote() == 0


def test_redirected_mirror_dir_never_reads_the_live_fast_dir(monkeypatch):
    """conftest points JM_COMPLETED_MIRROR_DIR at a temp dir; without its own
    override the fast dir must be off, or tests would absorb live Ix rows."""
    monkeypatch.setenv("JM_COMPLETED_MIRROR_DIR", "/tmp/jm-test-nowhere")
    monkeypatch.delenv("JM_COMPLETED_FAST_DIR", raising=False)
    spec = importlib.util.spec_from_file_location("mc_fast_off", _HERE / "mark-completed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FAST_DIR is None


def test_dtd_watcher_watches_fast_dir_by_signature_not_max_mtime():
    """A pulled file keeps Ix's mtime, which can be older than this host's own
    mirror, so `sort -rn | head -1` never moved; and a bare glob on an empty
    fast dir aborts the whole stat in zsh."""
    line = next(l for l in (_HERE / "dtd.sh").read_text().splitlines()
                if l.strip().startswith("cur_rm=$(stat"))
    assert "remote-done/*/completed-today-*.json(N)" in line
    assert "z_ibx/completed-today-*.json(N)" in line
    assert "%N %Fm" in line and "head -1" not in line


def test_pull_script_fetches_ix_mirrors_outside_the_vault():
    sh = (_HERE.parents[1] / "scripts" / "agent-claims-pull.sh").read_text()
    assert "jm/remote-done/ix" in sh
    assert "--include='completed-today-*.json' --exclude='*'" in sh
    assert "--exclude='*.sync-conflict-*'" in sh
