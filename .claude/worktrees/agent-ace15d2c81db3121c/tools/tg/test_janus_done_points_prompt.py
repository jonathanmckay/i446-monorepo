"""User request 2026-08-09: "If I hit opt enter on the current task - what's
the right way for it to ask for the points if it's a task that points can't
automatically be inferred?" Followed by: "janus is meant to be my interface
for converting into points" — a deliberate, scoped exception to
[[zero-points-default]] (did-fast/batch flows still silently default to 0;
this one high-intent gesture asks instead).

_resolvable_points(desc) mirrors what did-fast would resolve on its own: an
inline [N] in the description, or a [N] on a matching OPEN task in the same
cached task-queue.json did-fast's own Todoist-content match reads. When
neither exists, _run_current_timer_done (⌥↵ / swipe-right on the pinned
current-timer row) arms STATE.done_target instead of running did-fast blind
— same chokepoint-consumed-first discipline as edit_target/split_target. The
next Enter reads the input line as the point value (blank = 0, matching
did-fast's own silent default — just chosen deliberately instead of guessed)
and completes the /done.
"""
import asyncio
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_donepts", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_donepts"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeApp:
    def create_background_task(self, coro):
        coro.close()

    def invalidate(self):
        pass


class _FakeEvent:
    app = _FakeApp()


def _binding(mod, keys):
    hits = [b for b in mod.kb.bindings if b.keys == keys]
    assert hits, f"no binding for {keys!r}"
    return hits[0]


def _freeze_now(mod, when, monkeypatch):
    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(mod.dt, "datetime", _DT)


def _setup(mod, desc="fix the parser bug", tmp_path=None):
    # 20:30 UTC = 13:30 Pacific — well past the "timer just started" 60s
    # guard against the 14:00 Pacific "now" most of these tests freeze.
    mod.STATE.current = {"description": desc, "start": "2026-08-09T20:30:00+00:00",
                         "project_id": None}
    mod.STATE.recording = None
    mod.STATE.visible_events = [{"kind": "current", "raw_desc": desc}]
    mod.STATE.event_sel = mod._sel_key({"kind": "current"})
    mod.STATE.current_swipe_start = None
    mod.STATE.queued_cmds = set()
    mod.STATE.work_q = None
    mod.STATE.done_target = None
    mod.input_buffer.text = ""
    if tmp_path is not None:
        mod.TASK_QUEUE = tmp_path / "task-queue.json"
        mod.TASK_QUEUE.write_text("{}")


# ─── _resolvable_points ──────────────────────────────────────────────────

def test_inline_bracket_in_description_resolves_directly():
    mod = _load_tui()
    assert mod._resolvable_points("fix the bug [20]") == 20


def test_matching_task_queue_entry_resolves(tmp_path):
    mod = _load_tui()
    mod.TASK_QUEUE = tmp_path / "task-queue.json"
    mod.TASK_QUEUE.write_text(json.dumps(
        {"0neon": [{"content": "fix the parser bug [15]", "short": "fix parser"}]}))
    assert mod._resolvable_points("fix the parser bug") == 15


def test_matching_task_without_bracket_is_unresolved(tmp_path):
    mod = _load_tui()
    mod.TASK_QUEUE = tmp_path / "task-queue.json"
    mod.TASK_QUEUE.write_text(json.dumps(
        {"0neon": [{"content": "fix the parser bug", "short": "fix parser"}]}))
    assert mod._resolvable_points("fix the parser bug") is None


def test_no_match_at_all_is_unresolved(tmp_path):
    mod = _load_tui()
    mod.TASK_QUEUE = tmp_path / "task-queue.json"
    mod.TASK_QUEUE.write_text("{}")
    assert mod._resolvable_points("something totally untracked") is None


def test_missing_task_queue_file_is_unresolved_not_a_crash(tmp_path):
    mod = _load_tui()
    mod.TASK_QUEUE = tmp_path / "does-not-exist.json"
    assert mod._resolvable_points("anything") is None


# ─── _run_current_timer_done arms the prompt when unresolvable ────────────

def test_unresolvable_points_arms_done_target_not_did_fast(tmp_path, monkeypatch):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    mod._run_current_timer_done(_FakeApp())
    assert mod.STATE.queued_cmds == set(), "must not run did-fast blind"
    assert mod.STATE.done_target == {
        "desc": "fix the parser bug",
        "start_dt": dtm.datetime(2026, 8, 9, 20, 30, 0, tzinfo=dtm.timezone.utc)
                      .astimezone(mod.TZ),
        "code": mod.toggl_project_code(None, "fix the parser bug"),
    }
    assert mod.input_buffer.text == ""
    assert "no points" in mod.STATE.flash
    assert mod.STATE.event_sel is None


def test_resolvable_points_runs_immediately_no_prompt(tmp_path, monkeypatch):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug [30]", tmp_path=tmp_path)
    mod._run_current_timer_done(_FakeApp())
    assert mod.STATE.done_target is None
    assert len(mod.STATE.queued_cmds) == 1
    (cmd,) = mod.STATE.queued_cmds
    assert cmd.startswith("fix the parser bug [30] ")


# ─── Enter consumes done_target ────────────────────────────────────────────

def test_enter_with_typed_number_completes_done_with_that_value(tmp_path, monkeypatch):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    start = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    mod.STATE.done_target = {"desc": "fix the parser bug", "start_dt": start, "code": "i9"}
    mod.input_buffer.text = "25"
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert mod.STATE.done_target is None
    assert len(mod.STATE.queued_cmds) == 1
    (cmd,) = mod.STATE.queued_cmds
    assert cmd == "fix the parser bug 1400-1430 @i9 [25]"


def test_enter_with_blank_input_defaults_to_zero_not_cancel(tmp_path, monkeypatch):
    """Blank matches did-fast's own silent-0 default — chosen deliberately
    via the prompt, not skipped."""
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    start = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    mod.STATE.done_target = {"desc": "fix the parser bug", "start_dt": start, "code": None}
    mod.input_buffer.text = ""
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert len(mod.STATE.queued_cmds) == 1
    (cmd,) = mod.STATE.queued_cmds
    assert cmd == "fix the parser bug 1400-1430 [0]"


def test_enter_with_non_numeric_input_cancels_without_running_did_fast(tmp_path, monkeypatch):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    start = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    mod.STATE.done_target = {"desc": "fix the parser bug", "start_dt": start, "code": None}
    mod.input_buffer.text = "not a number"
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert mod.STATE.done_target is None
    assert mod.STATE.queued_cmds == set(), "garbage input must not run did-fast"
    assert "cancelled" in mod.STATE.flash
    assert "still running" in mod.STATE.flash


def test_done_target_is_consumed_before_normal_empty_enter_handling(tmp_path, monkeypatch):
    """The chokepoint must fire even when the input line is otherwise empty
    and nothing is selected — done_target alone is enough to route here,
    same discipline as split_target/edit_target."""
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    mod.STATE.event_sel = None
    mod.STATE.visible_events = []
    start = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    mod.STATE.done_target = {"desc": "fix the parser bug", "start_dt": start, "code": None}
    mod.input_buffer.text = "10"
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert len(mod.STATE.queued_cmds) == 1


# ─── Reached via the real ⌥↵ / swipe gestures, not just a direct call ─────

def test_alt_enter_on_current_row_arms_prompt_when_unresolvable(tmp_path, monkeypatch):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 14, 0, 0, tzinfo=TZ)
    _freeze_now(mod, now, monkeypatch)
    _setup(mod, desc="fix the parser bug", tmp_path=tmp_path)
    _binding(mod, ("escape", "c-m")).handler(_FakeEvent())
    assert mod.STATE.done_target is not None
    assert mod.STATE.queued_cmds == set()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
