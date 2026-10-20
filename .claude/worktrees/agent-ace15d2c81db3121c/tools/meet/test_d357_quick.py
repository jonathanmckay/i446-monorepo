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
    log_path = mod.Path("/tmp/d357-janus-test.log")
    teams = mod.build_tmux_command("Huddle: XBOX Developer", mic_only=False,
                                    log_path=log_path, max_minutes=35)
    assert "meet.py 'Huddle: XBOX Developer' --domain d357" in teams
    assert "--max-duration 35" in teams
    assert "> /tmp/d357-janus-test.log 2>&1" in teams
    assert "tee" not in teams and "--no-teams" not in teams
    mic = mod.build_tmux_command("1:1", mic_only=True, log_path=log_path)
    assert "--no-teams --idle-timeout 0" in mic


def test_tmux_command_strips_single_quotes():
    mod = _load()
    log_path = mod.Path("/tmp/d357-janus-test.log")
    cmd = mod.build_tmux_command("Ming's 1:1", mic_only=False, log_path=log_path)
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


def test_cmd_stop_second_caller_does_not_resend_interrupt(monkeypatch, tmp_path):
    """2026-08-02 incident: janus's detached stop + cmd_start's own
    belt-and-suspenders both sent Ctrl-C to the SAME session, and the second
    interrupt landed mid-transcribe/mid-artifact-write, losing the meeting
    (2 of 3 back-to-back recordings that day). The per-session lock in
    cmd_stop() must make a second concurrent call a no-op: exactly one
    tmux send-keys C-c, not two."""
    mod = _load()
    state_file = tmp_path / "state.json"
    log_file = tmp_path / "active.log"
    log_file.write_text("Done!\nTXT → /tmp/fake.txt\n")
    session = "d357-janus-test-session"
    import datetime
    today = datetime.date.today().isoformat()
    state_file.write_text(json.dumps({
        "pid": 1, "tmux": session, "name": "Test Meeting",
        "started": f"{today}T09:00:00", "log": str(log_file), "source": "janus",
    }))
    monkeypatch.setattr(mod, "STATE", state_file)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(mod, "_tmux_alive", lambda s: True)
    monkeypatch.setattr(mod, "_prof", lambda *a: None)

    send_keys_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["tmux", "send-keys"]:
            send_keys_calls.append(cmd)
        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    lock_path = Path(f"/tmp/d357-stopping-{session}.lock")
    lock_path.unlink(missing_ok=True)
    try:
        assert mod.cmd_stop() == 0
        assert mod.cmd_stop() == 0  # simulates the redundant belt-and-suspenders call
        assert len(send_keys_calls) == 1, "a second stop call must not send a second C-c"
    finally:
        lock_path.unlink(missing_ok=True)


def test_cmd_start_safety_net_skips_daemon_owned_recording(monkeypatch, tmp_path):
    """The safety-net cleanup spawn in cmd_start() must never touch a
    meeting-daemon.py-owned recording (source 'daemon') — only a desynced
    janus-owned one. Otherwise janus could kill a daemon-managed recording
    out from under it."""
    mod = _load()
    state_file = tmp_path / "state.json"
    import datetime
    today = datetime.date.today().isoformat()
    state_file.write_text(json.dumps({
        "pid": 99999999, "tmux": "d357-daemon-123", "name": "Daemon Meeting",
        "started": f"{today}T09:00:00", "log": "/tmp/whatever.log", "source": "daemon",
    }))
    monkeypatch.setattr(mod, "STATE", state_file)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(mod, "_tmux_alive", lambda s: True)

    spawned = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    # Prevent the real recording launch from running (not under test here).
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit))
    try:
        mod.cmd_start("New Meeting")
    except SystemExit:
        pass
    assert not spawned, "must not spawn a stop against a daemon-owned recording"


def test_path_includes_homebrew_for_gui_callers():
    """2026-08-02: janus (GUI-app PATH) launched the wrapper and tmux /
    SwitchAudioSource weren't found — the recording silently never started.
    The wrapper must prepend homebrew paths itself."""
    mod = _load()
    import os
    assert os.environ["PATH"].startswith("/opt/homebrew/bin"), \
        "wrapper must be PATH-self-sufficient for GUI-spawned callers"
