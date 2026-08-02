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

    assert mod.file_meeting(txt) is True
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

    assert mod.file_meeting(txt) is False


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


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
