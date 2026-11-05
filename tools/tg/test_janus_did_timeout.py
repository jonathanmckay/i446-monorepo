"""janus must not kill a slow did-fast that is still working (bug 2026-11-05).

Excel took 8s a write: 一起发's credits landed 34s in, the 45s timeout killed
did-fast before it finished, and the row showed no points. A second ⌥↵ would
then have credited it twice. Mobile janus already allows 90s."""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_didtimeout", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_didtimeout"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_did_fast_timeout_matches_mobile(monkeypatch):
    mod = _load()
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.run_did_fast_data("一起发 0738-0742", ())
    assert seen["timeout"] >= 90


def test_timeout_warns_that_the_points_may_have_landed(monkeypatch):
    mod = _load()

    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res, data = mod.run_did_fast_data("一起发 0738-0742", ())
    assert res.startswith("err") and "may have landed" in res and data is None
