"""Regression (2026-11-07): "when I do an overwrite, it will first show as
blank and flash at me for 3-5 seconds before showing the new task."

Every Toggl write SIGUSR1s janus, including janus's OWN writes mid-sequence:
an edit retimed over another entry DELETEd the overlapped one, the nudge
refresh re-read Toggl before the edit's PUT landed (blank gap) and flashed
☀️ because the entry count changed. While janus is inside its own write
sequence (_own_writes) the nudge must be deferred into ONE refresh after it,
quiet for edits; non-quiet flows compare against the pre-sequence count.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_overwrite", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_overwrite"] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


class _App:
    def __init__(self):
        self.tasks = []

    def invalidate(self):
        pass

    def create_background_task(self, coro):
        self.tasks.append(coro)


def _harness(monkeypatch, entries_after):
    calls, flashes = [], []
    app = _App()

    def fetch_today(force=False):
        calls.append("fetch_today")
        MOD.STATE.entries = list(entries_after)

    monkeypatch.setattr(MOD, "app", app, raising=False)
    monkeypatch.setattr(MOD, "fetch_current", lambda *a, **k: calls.append("fetch_current"))
    monkeypatch.setattr(MOD, "fetch_today", fetch_today)
    monkeypatch.setattr(MOD, "fetch_short_names", lambda *a, **k: None)
    monkeypatch.setattr(MOD, "_bg_fetch", lambda *a, **k: None)
    monkeypatch.setattr(MOD, "flash", lambda msg, *a, **k: flashes.append(msg))
    monkeypatch.setattr(MOD.asyncio, "sleep", _no_sleep)
    MOD.STATE.own_writes = 0
    MOD.STATE.usr1_deferred = False
    MOD.STATE.current = {"id": 1}
    return app, calls, flashes


async def _no_sleep(*_a, **_k):
    return None


def _run_with_nudge(app, quiet, entries_before):
    """A write sequence that receives a SIGUSR1 mid-way, like trim's DELETE."""
    MOD.STATE.entries = list(entries_before)

    async def sequence():
        await MOD._sigusr1_refresh()   # the nudge from janus's own DELETE

    async def main():
        await MOD._guarded(app, sequence(), quiet=quiet)
        while app.tasks:                # the deferred refresh, if scheduled
            await app.tasks.pop(0)

    asyncio.run(main())


def test_nudge_during_own_edit_is_deferred_and_quiet(monkeypatch):
    app, calls, flashes = _harness(monkeypatch, entries_after=[{"id": 2}])
    _run_with_nudge(app, quiet=True, entries_before=[{"id": 1}, {"id": 2}])
    # Nothing re-read Toggl while the sequence was mid-flight; exactly one
    # refresh ran after it, and the entry-count change did not ☀️.
    assert calls.count("fetch_today") == 1
    assert "☀️" not in flashes
    assert MOD.STATE.own_writes == 0 and not MOD.STATE.usr1_deferred


def test_nudge_during_command_keeps_prayer_flash_against_pre_count(monkeypatch):
    # The flow's own fetch already updated STATE.entries before the deferred
    # pass; ☀️ must still fire because the count grew versus the START.
    app, calls, flashes = _harness(monkeypatch, entries_after=[{"id": 1}, {"id": 2}])
    _run_with_nudge(app, quiet=False, entries_before=[{"id": 1}])
    assert calls.count("fetch_today") == 1
    assert "☀️" in flashes


def test_no_own_writes_refreshes_immediately(monkeypatch):
    app, calls, flashes = _harness(monkeypatch, entries_after=[{"id": 1}])
    MOD.STATE.entries = [{"id": 1}]
    asyncio.run(MOD._sigusr1_refresh())
    assert calls.count("fetch_today") == 1
    assert not app.tasks
