"""d357_quick.py — the headless janus-facing start/stop wrapper (2026-08-02:
"hitting 'enter' on a meeting will also kick off a d357 recording session")."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("d357_quick", HERE / "d357_quick.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["d357_quick"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_tmux_command_matches_skill_contract():
    """Must mirror the /d357 skill's step-6 line: `>` redirect (tee breaks
    Ctrl-C finalization), mic-only forces --no-teams --idle-timeout 0."""
    mod = _load()
    teams = mod.build_tmux_command("Huddle: XBOX Developer", mic_only=False, max_minutes=35)
    assert "meet.py 'Huddle: XBOX Developer' --domain d357" in teams
    assert "--max-duration 35" in teams
    assert "> /tmp/d357-active.log 2>&1" in teams
    assert "tee" not in teams and "--no-teams" not in teams
    mic = mod.build_tmux_command("1:1", mic_only=True)
    assert "--no-teams --idle-timeout 0" in mic


def test_tmux_command_strips_single_quotes():
    mod = _load()
    cmd = mod.build_tmux_command("Ming's 1:1", mic_only=False)
    assert "Ming's" not in cmd and "Mings 1:1" in cmd


def test_liveness_guard_rejects_stale_state(monkeypatch):
    """The 43-hour-zombie guard: pid gone, tmux gone, or a prior-day start
    are each individually disqualifying."""
    mod = _load()
    monkeypatch.setattr(mod, "_tmux_alive", lambda s: True)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    import datetime
    today = datetime.date.today().isoformat()
    assert mod.is_alive({"pid": 1, "started": f"{today}T09:00:00"})
    assert not mod.is_alive({"pid": None, "started": f"{today}T09:00:00"})
    assert not mod.is_alive({"pid": 1, "started": "2020-01-01T09:00:00"}), \
        "a prior-day recording is stale no matter what"

    def _dead(pid, sig):
        raise OSError
    monkeypatch.setattr(mod.os, "kill", _dead)
    assert not mod.is_alive({"pid": 1, "started": f"{today}T09:00:00"})


def test_parse_stop_log_extracts_txt_path():
    mod = _load()
    log = "Recording...\nSaved WAV\nTXT → /Users/mckay/vault/h335/i9/recordings/x.txt\nDone!"
    assert mod.parse_stop_log(log) == "/Users/mckay/vault/h335/i9/recordings/x.txt"
    assert mod.parse_stop_log("no transcript here") is None


def test_wrapper_never_writes_points_or_toggl():
    """Points and Toggl belong to janus's finalize flow (did-fast) — the
    wrapper writing either would double-credit. Ban the functional handles,
    not the words (the docstring legitimately explains the division)."""
    src = (HERE / "d357_quick.py").read_text()
    for banned in ("toggl_api", "toggl_cli", "neon_excel", "localhost:9876",
                   "ix-osa", "did-fast.py"):
        assert banned not in src, f"wrapper must not touch {banned}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
