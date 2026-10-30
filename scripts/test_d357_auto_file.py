"""Regression tests for d357-auto-file.py.

Bug (2026-08-02): "no emoji indicator for the meetings that I recorded" —
the emoji itself worked; nothing had ever been filed for janus-started
recordings, because meet.py deliberately never files notes itself (that's
always been a separate Claude Code step) and nothing invoked that step for
janus recordings. These tests cover the pure candidate-selection logic
(matching, age-gating, locking) without spawning any real `claude` call."""
import importlib.util
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _load_mod():
    spec = importlib.util.spec_from_file_location("d357af", HERE / "d357-auto-file.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["d357af"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_unfiled_recording_with_no_matching_doc_is_a_candidate(tmp_path):
    mod = _load_mod()
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.RECORDINGS_DIR.mkdir()
    mod.D357_DIR.mkdir()
    txt = mod.RECORDINGS_DIR / "2026.08.02-1044-m5x2-strat-remote.txt"
    txt.write_text("transcript text")
    old_mtime = time.time() - mod.MIN_AGE_SEC - 10
    os.utime(txt, (old_mtime, old_mtime))

    out = mod.find_unfiled()
    assert out == [txt]


def test_filed_recording_is_not_a_candidate(tmp_path):
    """A vault/d357 doc sharing a slug token (even filed in a week
    subfolder, as real docs are) suppresses the candidate."""
    mod = _load_mod()
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.RECORDINGS_DIR.mkdir()
    week_dir = mod.D357_DIR / "7.4"
    week_dir.mkdir(parents=True)
    txt = mod.RECORDINGS_DIR / "2026.08.02-1044-m5x2-strat-remote.txt"
    txt.write_text("transcript text")
    old_mtime = time.time() - mod.MIN_AGE_SEC - 10
    os.utime(txt, (old_mtime, old_mtime))
    (week_dir / "2026.08.02-m5x2-strat-111.md").write_text("# note")

    assert mod.find_unfiled() == []


def test_too_recent_recording_is_not_yet_a_candidate(tmp_path):
    """Age-gate: a transcript that JUST finished waits MIN_AGE_SEC before
    being offered, so any in-flight d357_quick.py stop-cleanup can settle."""
    mod = _load_mod()
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.RECORDINGS_DIR.mkdir()
    mod.D357_DIR.mkdir()
    txt = mod.RECORDINGS_DIR / "2026.08.02-1044-m5x2-strat-remote.txt"
    txt.write_text("transcript text")  # fresh mtime = now

    assert mod.find_unfiled() == []


def test_backlog_before_cutoff_is_never_a_candidate(tmp_path):
    """User decision 2026-08-02: 'only file going forward' — the 36-meeting
    pre-existing backlog (April-July) must never be auto-filed, only
    transcripts dated on/after CUTOFF_DATE."""
    mod = _load_mod()
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.RECORDINGS_DIR.mkdir()
    mod.D357_DIR.mkdir()
    old_mtime = time.time() - mod.MIN_AGE_SEC - 10
    old = mod.RECORDINGS_DIR / "2026.04.27-1053-joe-1-1.txt"
    old.write_text("t")
    os.utime(old, (old_mtime, old_mtime))
    on_cutoff = mod.RECORDINGS_DIR / f"{mod.CUTOFF_DATE}-1235-focus-properties.txt"
    on_cutoff.write_text("t")
    os.utime(on_cutoff, (old_mtime, old_mtime))

    assert mod.find_unfiled() == [on_cutoff]


def test_non_recording_txt_files_are_ignored(tmp_path):
    """Filenames that don't match the YYYY.MM.DD-HHMM-<slug> shape (e.g. a
    stray .txt) are skipped, not mis-parsed."""
    mod = _load_mod()
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.RECORDINGS_DIR.mkdir()
    mod.D357_DIR.mkdir()
    stray = mod.RECORDINGS_DIR / "notes.txt"
    stray.write_text("not a recording")
    old_mtime = time.time() - mod.MIN_AGE_SEC - 10
    os.utime(stray, (old_mtime, old_mtime))

    assert mod.find_unfiled() == []


def test_prompt_points_at_skill_file_not_reencoded_logic():
    """The filing steps must be read from SKILL.md at call time (the single
    source of truth), never paraphrased/duplicated into the prompt — so the
    prompt can't drift out of sync as the skill evolves."""
    mod = _load_mod()
    txt = Path("/tmp/2026.08.02-1044-m5x2-strat-remote.txt")
    prompt = mod.build_prompt(txt)
    assert str(mod.SKILL_PATH) in prompt
    assert "m5x2 strat remote" in prompt
    assert str(txt) in prompt


def test_claude_invocation_uses_approved_budget_and_model(monkeypatch, tmp_path):
    """User-approved 2026-08-02: $0.50/meeting, sonnet, unattended flags."""
    mod = _load_mod()
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stdout = "FILED /some/path.md\n"
            stderr = ""
        return R()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    txt = tmp_path / "2026.08.02-1044-m5x2-strat-remote.txt"
    txt.write_text("t")

    assert mod.file_meeting(txt, env={})[0] == "filed"
    argv = captured["argv"]
    assert mod.CLAUDE in argv
    assert "--max-budget-usd" in argv and argv[argv.index("--max-budget-usd") + 1] == "0.50"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert "--dangerously-skip-permissions" in argv


def test_claude_skipped_line_does_not_count_as_filed(monkeypatch, tmp_path):
    mod = _load_mod()

    def fake_run(argv, **kwargs):
        class R:
            returncode = 0
            stdout = "SKIPPED empty transcript\n"
            stderr = ""
        return R()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    txt = tmp_path / "2026.08.02-1044-m5x2-strat-remote.txt"
    txt.write_text("t")

    assert mod.file_meeting(txt, env={}) == ("skipped", "empty transcript")


def test_max_per_run_caps_batch_regardless_of_backlog_size(tmp_path):
    """User-approved cap: at most 2 unfiled meetings processed per run, even
    with a larger backlog — bounds worst-case spend per run."""
    mod = _load_mod()
    assert mod.MAX_PER_RUN == 2


def test_lock_prevents_concurrent_runs(tmp_path):
    mod = _load_mod()
    mod.LOCK = tmp_path / "lock"
    assert mod._acquire_lock() is True
    assert mod._acquire_lock() is False  # our own pid is alive -> locked out
    mod._release_lock()
    assert mod._acquire_lock() is True


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path):
    mod = _load_mod()
    mod.LOCK = tmp_path / "lock"
    # A pid that can't possibly be alive.
    mod.LOCK.write_text("999999")
    assert mod._acquire_lock() is True


# ---- v173 (2026-10-30): auth fail-fast, retry cap, `:`/`|` slugs ----------

def _setup_dirs(mod, tmp_path):
    mod.RECORDINGS_DIR = tmp_path / "recordings"
    mod.D357_DIR = tmp_path / "d357"
    mod.STATE_FILE = tmp_path / "state.json"
    mod.ALERTS = tmp_path / "alerts.jsonl"
    mod.LOCK = tmp_path / "lock"
    mod.LOG = tmp_path / "log"
    mod.RECORDINGS_DIR.mkdir()
    mod.D357_DIR.mkdir()


def _old_txt(mod, name):
    txt = mod.RECORDINGS_DIR / name
    txt.write_text("transcript")
    t = time.time() - mod.MIN_AGE_SEC - 10
    os.utime(txt, (t, t))
    return txt


def test_punctuated_recordings_match_their_sanitized_filed_docs(tmp_path):
    """The real loop: `ja:-jm-1:1` / `im|jm-1|1` were filed on 08-07/08-09 as
    `ja-jm-1-1.md` / `im-jm-1-1.md`, but the -/_ tokeniser saw `ja:` and `1:1`
    as tokens and never matched, so they were re-offered hourly forever."""
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    (mod.D357_DIR / "8.1").mkdir()
    (mod.D357_DIR / "8.2").mkdir()
    (mod.D357_DIR / "8.1" / "2026.08.07-ja-jm-1-1.md").write_text("x")
    (mod.D357_DIR / "8.2" / "2026.08.09-im-jm-1-1.md").write_text("x")
    (mod.D357_DIR / "8.2" / "2026.08.09-m5x2-strat-111.md").write_text("x")
    _old_txt(mod, "2026.08.07-1142-ja:-jm-1:1.txt")
    _old_txt(mod, "2026.08.09-1033-im|jm-1|1.txt")
    _old_txt(mod, "2026.08.09-1050-m5x2-strat-(1|1|1).txt")
    assert mod.find_unfiled() == []


def test_short_slug_does_not_match_a_different_short_slug(tmp_path):
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    (mod.D357_DIR / "2026.08.07-ja-jm-1-1.md").write_text("x")
    txt = _old_txt(mod, "2026.08.07-1500-es:jm-1:1.txt")
    assert mod.find_unfiled() == [txt]


def test_safe_slug_and_prompt_pin_filesystem_safe_doc_name():
    mod = _load_mod()
    assert mod.safe_slug("ja:-jm-1:1") == "ja-jm-1-1"
    assert mod.safe_slug("im|jm-1|1") == "im-jm-1-1"
    assert mod.safe_slug("m5x2-strat-(1|1|1)") == "m5x2-strat-1-1-1"
    assert mod.safe_slug("🚗-←-cais-basketball") == "cais-basketball"
    assert mod.safe_slug("周会:-jm") == "周会-jm"
    prompt = mod.build_prompt(Path("/tmp/2026.08.09-1033-im|jm-1|1.txt"))
    assert "2026.08.09-im-jm-1-1.md" in prompt
    assert '"im jm 1 1"' in prompt  # name guess has no pipes


def _fake_run_factory(calls, auth_json=None, print_stdout="", print_rc=0):
    def fake_run(argv, **kwargs):
        calls.append(argv)

        class R:
            stderr = ""
        if argv[1:3] == ["auth", "status"]:
            R.returncode = 0
            R.stdout = auth_json or '{"loggedIn": true, "authMethod": "oauth_token"}'
        else:
            R.returncode = print_rc
            R.stdout = print_stdout
        return R()
    return fake_run


def test_not_logged_in_fails_fast_with_one_line_and_no_filing_calls(monkeypatch, tmp_path):
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    _old_txt(mod, "2026.08.07-1142-ja:-jm-1:1.txt")
    _old_txt(mod, "2026.08.10-1936-josh-1:1.txt")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory(
        calls, auth_json='{"loggedIn": false, "authMethod": "none"}'))
    monkeypatch.setattr(sys, "argv", ["d357-auto-file.py"])
    for _ in range(3):  # three hourly runs
        assert mod.main() == 0
    assert all(c[1:3] == ["auth", "status"] for c in calls), "no --print call may run"
    log = mod.LOG.read_text().splitlines()
    assert sum("AUTH:" in l for l in log) == 3  # exactly one line per run
    assert not any("filing" in l for l in log)
    # alert raised once (on the transition), not every run
    assert len(mod.ALERTS.read_text().splitlines()) == 1
    # auth failures never count against the recordings themselves
    assert mod.load_state().get("recordings", {}) == {}


def test_auth_restored_clears_flag_and_files(monkeypatch, tmp_path):
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    mod.save_state({"auth_broken_since": "2026-08-07T12:17:00"})
    _old_txt(mod, "2026.10.25-1034-im|jm-1|1.txt")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory(
        calls, print_stdout="FILED /v/d357/10.4/2026.10.25-im-jm-1-1.md\n"))
    monkeypatch.setattr(sys, "argv", ["d357-auto-file.py"])
    assert mod.main() == 0
    assert "auth_broken_since" not in mod.load_state()
    assert "done: 1/1 filed" in mod.LOG.read_text()


def test_mid_run_not_logged_in_stops_batch_without_charging_file(monkeypatch, tmp_path):
    """Probe passes but the real call says Not logged in (token revoked): stop
    the batch after the first call; neither recording gains a failure."""
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    _old_txt(mod, "2026.08.10-1936-josh-1:1.txt")
    _old_txt(mod, "2026.08.12-1335-rr-:-jm-1:1.txt")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory(
        calls, print_stdout="Not logged in · Please run /login\n", print_rc=1))
    monkeypatch.setattr(sys, "argv", ["d357-auto-file.py"])
    assert mod.main() == 0
    assert sum("--print" in c for c in calls) == 1
    assert mod.load_state().get("recordings", {}) == {}
    assert "auth_broken_since" in mod.load_state()


def test_retry_cap_retires_recording_after_max_failures(monkeypatch, tmp_path):
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    txt = _old_txt(mod, "2026.08.10-1936-josh-1:1.txt")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory(
        calls, print_stdout="Error: something else broke\n", print_rc=1))
    monkeypatch.setattr(sys, "argv", ["d357-auto-file.py"])
    for _ in range(mod.MAX_FAILURES + 2):
        mod.main()
    assert sum("--print" in c for c in calls) == mod.MAX_FAILURES
    st = mod.load_state()["recordings"][txt.name]
    assert st["status"] == "gave_up" and st["failures"] == mod.MAX_FAILURES
    assert sum("GAVE UP" in l for l in mod.LOG.read_text().splitlines()) == 1
    assert mod.find_unfiled(state=mod.load_state()) == []


def test_skipped_is_terminal_not_reoffered(monkeypatch, tmp_path):
    """Closes the old 'Known limitation': a legit SKIPPED isn't paid for hourly."""
    mod = _load_mod()
    _setup_dirs(mod, tmp_path)
    _old_txt(mod, "2026.08.10-1936-josh-1:1.txt")
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory(
        calls, print_stdout="SKIPPED empty transcript\n"))
    monkeypatch.setattr(sys, "argv", ["d357-auto-file.py"])
    mod.main()
    mod.main()
    assert sum("--print" in c for c in calls) == 1


def test_long_reply_mentioning_401_is_not_an_auth_failure(monkeypatch, tmp_path):
    mod = _load_mod()
    txt = tmp_path / "2026.08.10-1936-josh-1:1.txt"
    txt.write_text("t")
    body = "\n".join(["line"] * 10 + ["unit 401 auth handoff discussed", "done"])
    monkeypatch.setattr(mod.subprocess, "run", _fake_run_factory([], print_stdout=body))
    assert mod.file_meeting(txt, env={})[0] == "failed"


def test_oauth_token_file_is_passed_to_claude(monkeypatch, tmp_path):
    mod = _load_mod()
    mod.OAUTH_TOKEN_FILE = tmp_path / "oauth-token"
    mod.OAUTH_TOKEN_FILE.write_text("tok-123\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert mod.claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
