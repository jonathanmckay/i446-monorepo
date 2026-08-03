#!/usr/bin/env python3
"""Regression (2026-07-27): "did all the -1n for 午 but it's showing 7pts".

Ritual completions run on BOTH machines (Straylight dtd/skills, Ix mobile
dtd), and each stamped its own build-order.md — Syncthing last-writer-wins
then dropped whichever side synced second (午 lost 🎯 from Straylight and ⏱️
from Ix; the header merged to ☀️📧✅ and the recompute set the block term to
7 of 13). Stamps must have a single writer: Ix's copy, via ssh, with the
local write only as a noted fallback when Ix is unreachable — and a remote
"already stamped" must not double-credit points.
"""
import importlib.util
import multiprocessing
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "lib"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_stamp", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_stamp"] = mod
    spec.loader.exec_module(mod)
    return mod


class _P:
    def __init__(self, out, rc=0):
        self.stdout = out
        self.stderr = ""
        self.returncode = rc


def test_stamp_on_ix_parses_ch_nc_and_failure(monkeypatch):
    df = _load()
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("input", "")))
        return _P("CH\n")
    monkeypatch.setattr(df.subprocess, "run", fake_run)
    assert df._stamp_on_ix("午", "🎯") is True
    cmd, payload = calls[0]
    assert cmd[:2] == ["ssh", "-o"] and "ix" in cmd
    assert "stamp_emoji" in payload and "午" in payload and "🎯" in payload

    monkeypatch.setattr(df.subprocess, "run", lambda *a, **k: _P("NC\n"))
    assert df._stamp_on_ix("午", "🎯") is False

    monkeypatch.setattr(df.subprocess, "run", lambda *a, **k: _P("", rc=255))
    assert df._stamp_on_ix("午", "🎯") is None

    def boom(*a, **k):
        raise OSError("no ssh")
    monkeypatch.setattr(df.subprocess, "run", boom)
    assert df._stamp_on_ix("午", "🎯") is None


def test_run_ritual_routes_stamps_through_ix():
    src = (_HERE / "did-fast.py").read_text()
    body = src[src.index("def run_ritual"):]
    assert "_stamp_on_ix(block, emoji)" in body, \
        "off-Ix completions must stamp Ix's build-order copy (single writer)"
    assert "stamp_fallback_local" in body, \
        "Ix-unreachable fallback must be noted in the result"
    # Remote truth gates the credit: a remote NC must zero `changed` so the
    # P credit can't double-fire for a ritual another machine already stamped.
    assert "changed = remote" in body


def test_stamp_on_ix_remote_script_uses_a_lock():
    """Regression (2026-07-29): "did all the -1n for 申 and 午 but it's still
    wrong". Even with a single writer (Ix), rituals are routinely completed
    within seconds of each other (dtd batch-completions) -- 4 of 申's 5
    rituals were completed within a 4-second window and only one survived.
    Each completion spawns its own ssh call, and without a lock, concurrent
    read-modify-write cycles on Ix's build-order.md race: every call reads
    the same pre-stamp text, and whichever write lands last silently
    discards every other call's stamp. The remote script must serialize the
    read-modify-write with an exclusive file lock."""
    df = _load()
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("input", "")))
        return _P("CH\n")
    df.subprocess.run = fake_run
    df._stamp_on_ix("申", "☀️")
    _, payload = calls[0]
    assert "fcntl" in payload and "flock" in payload, (
        "the remote read-modify-write must be lock-protected, or concurrent "
        "ritual completions silently clobber each other's stamps"
    )
    # The read (bo.read_text) must happen AFTER acquiring the lock, and the
    # write (bo.write_text) before releasing it, or the lock protects nothing.
    lock_idx = payload.index("LOCK_EX")
    unlock_idx = payload.index("LOCK_UN")
    read_idx = payload.index("read_text")
    write_idx = payload.index("write_text")
    assert lock_idx < read_idx < write_idx < unlock_idx, (
        "read and write must both happen strictly between acquiring and "
        "releasing the lock"
    )


def _locked_stamp_worker(build_order_path: str, emoji: str) -> None:
    """Standalone (picklable) reproduction of _stamp_on_ix's locked
    read-modify-write, run in a separate OS process so real flock semantics
    apply -- a same-process thread wouldn't reproduce the race two separate
    ssh-spawned processes hit on Ix."""
    import fcntl
    import neon_blocks as nb

    bo = Path(build_order_path)
    lock_path = bo.with_suffix(".lock")
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            t = bo.read_text(encoding="utf-8")
            nt, _ = nb.stamp_emoji(t, "申", emoji)
            bo.write_text(nt, encoding="utf-8")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _unlocked_stamp_worker(build_order_path: str, emoji: str) -> None:
    """Same as above, minus the lock -- reproduces the pre-fix race."""
    import neon_blocks as nb

    bo = Path(build_order_path)
    t = bo.read_text(encoding="utf-8")
    nt, _ = nb.stamp_emoji(t, "申", emoji)
    bo.write_text(nt, encoding="utf-8")


def test_concurrent_ritual_completions_all_survive_with_lock(tmp_path):
    """The actual regression, reproduced: 5 rituals completed within
    seconds of each other must all land on the header -- not just whichever
    process's write happens to run last."""
    build_order = tmp_path / "build-order.md"
    build_order.write_text("## -1₲\n\n- 申\n    - [ ] some goal\n", encoding="utf-8")

    emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
    procs = [multiprocessing.Process(target=_locked_stamp_worker,
                                     args=(str(build_order), e))
             for e in emojis]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)

    final = build_order.read_text(encoding="utf-8")
    header = next(l for l in final.split("\n") if l.startswith("- 申"))
    missing = [e for e in emojis if e not in header]
    assert not missing, f"lock failed to prevent lost updates: {missing} missing from {header!r}"


def test_concurrent_ritual_completions_lose_stamps_without_lock(tmp_path):
    """Sanity check for the test above: WITHOUT the lock, the same
    concurrent scenario is expected to lose at least one stamp most of the
    time, confirming the lock (not some unrelated factor) is what fixes it.
    Flaky-by-nature (races aren't guaranteed), so this only asserts across
    several repetitions that losses happen at least once -- if this stops
    failing, the race stopped being reproducible and the test above may be
    passing for the wrong reason."""
    losses_observed = 0
    for _ in range(5):
        build_order = tmp_path / f"build-order-{_}.md"
        build_order.write_text("## -1₲\n\n- 申\n    - [ ] some goal\n", encoding="utf-8")
        emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
        procs = [multiprocessing.Process(target=_unlocked_stamp_worker,
                                         args=(str(build_order), e))
                 for e in emojis]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
        final = build_order.read_text(encoding="utf-8")
        header = next(l for l in final.split("\n") if l.startswith("- 申"))
        if any(e not in header for e in emojis):
            losses_observed += 1
    assert losses_observed > 0, (
        "expected the unlocked path to lose at least one stamp across 5 runs -- "
        "if it never does, this environment can't reproduce the race and the "
        "locked test isn't proving much"
    )


def test_run_ritual_on_ix_branch_uses_a_lock():
    """Regression (2026-08-02): "I've cleared out 酉 and 戌 -1n, yet system
    has not granted me full points yet." 酉 lost its 🎯 stamp and 戌 lost
    ⏱️/✅ despite all 5 -1neon Todoist cards for both blocks showing
    completed. Root cause: `_stamp_on_ix`'s flock only protects the remote
    (Straylight -> ssh -> Ix) path. `run_ritual`'s local `if _on_ix():`
    branch -- hit by completions that run directly ON Ix, e.g. mobile dtd --
    read-modify-wrote build-order.md with NO lock at all, so the exact same
    race `_stamp_on_ix` was built to prevent could still happen one level
    up: 戌's -1ibx/-1t/-1l completions landed within 2 seconds of each
    other and only 📧 survived."""
    src = (_HERE / "did-fast.py").read_text()
    fn_body = src[src.index("def run_ritual"):]
    branch = fn_body[fn_body.index("if _on_ix():"):fn_body.index("\n    else:")]
    assert "fcntl" in branch and "flock" in branch, (
        "the on-Ix local stamp path must be lock-protected too, or "
        "completions running directly on Ix can still clobber each other"
    )
    lock_idx = branch.index("LOCK_EX")
    unlock_idx = branch.index("LOCK_UN")
    read_idx = branch.index("read_text")
    write_idx = branch.index("write_text")
    assert lock_idx < read_idx < write_idx < unlock_idx, (
        "the on-Ix branch's read and write must both happen strictly "
        "between acquiring and releasing the lock"
    )


def _locked_on_ix_worker(build_order_path: str, block: str, emoji: str) -> None:
    """Reproduction of run_ritual's (fixed) on-Ix local branch, run in a
    separate OS process so real flock semantics apply."""
    import fcntl
    import neon_blocks as nb

    bo = Path(build_order_path)
    lock_path = bo.with_suffix(".lock")
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            t = bo.read_text(encoding="utf-8")
            nt, changed = nb.stamp_emoji(t, block, emoji)
            if changed:
                bo.write_text(nt, encoding="utf-8")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _unlocked_on_ix_worker(build_order_path: str, block: str, emoji: str) -> None:
    """Same, minus the lock -- reproduces the pre-fix on-Ix race."""
    import neon_blocks as nb

    bo = Path(build_order_path)
    t = bo.read_text(encoding="utf-8")
    nt, changed = nb.stamp_emoji(t, block, emoji)
    if changed:
        bo.write_text(nt, encoding="utf-8")


def test_concurrent_on_ix_ritual_completions_all_survive_with_lock(tmp_path):
    """The actual reported bug, reproduced for 戌: rituals completed within
    seconds of each other directly on Ix must all land on the header."""
    build_order = tmp_path / "build-order.md"
    build_order.write_text("## -1₲\n\n- 戌\n    - [ ] some goal\n", encoding="utf-8")

    emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
    procs = [multiprocessing.Process(target=_locked_on_ix_worker,
                                     args=(str(build_order), "戌", e))
             for e in emojis]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)

    final = build_order.read_text(encoding="utf-8")
    header = next(l for l in final.split("\n") if l.startswith("- 戌"))
    missing = [e for e in emojis if e not in header]
    assert not missing, f"lock failed to prevent lost updates: {missing} missing from {header!r}"


def test_concurrent_on_ix_ritual_completions_lose_stamps_without_lock(tmp_path):
    """Sanity check: WITHOUT the lock, the same concurrent scenario loses at
    least one stamp most of the time -- confirms the lock is what fixes it,
    same rationale as the remote-path sanity check above."""
    losses_observed = 0
    for i in range(5):
        build_order = tmp_path / f"build-order-onix-{i}.md"
        build_order.write_text("## -1₲\n\n- 戌\n    - [ ] some goal\n", encoding="utf-8")
        emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
        procs = [multiprocessing.Process(target=_unlocked_on_ix_worker,
                                         args=(str(build_order), "戌", e))
                 for e in emojis]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
        final = build_order.read_text(encoding="utf-8")
        header = next(l for l in final.split("\n") if l.startswith("- 戌"))
        if any(e not in header for e in emojis):
            losses_observed += 1
    assert losses_observed > 0, (
        "expected the unlocked on-Ix path to lose at least one stamp across "
        "5 runs -- if it never does, this environment can't reproduce the "
        "race and the locked test isn't proving much"
    )


def test_on_ix_hostname_detection(monkeypatch):
    df = _load()
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "Jonathans-Mac-mini.local")
    assert df._on_ix() is True
    monkeypatch.setattr(socket, "gethostname", lambda: "Straylight-Refit.local")
    assert df._on_ix() is False
