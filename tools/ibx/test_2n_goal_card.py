"""Regression tests for the /inbound goal card.

Bug: running /inbound did NOT prompt to set/append block goals when the current
block already had a goal. The goal card was gated `if not goals_set`, so any
existing goal silenced it. /inbound (rituals-only mode) should always surface
the goal card; when goals exist it shows them and APPENDS new ones without
wiping the existing goals or their completion state.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()


# ── append_block_goals: preserves existing goals + done state ───────────────

def test_spawn_1g_background_refreshes_dtd_cache(monkeypatch):
    """Regression (2026-07-01): a -1g goal set via inbound created the Todoist
    task but never refreshed the dtd cache, so it didn't surface in dtd. The
    spawned `claude -p /-1g` runs headless in an untrusted workspace, which
    suppresses its Bash tool calls, so the skill's own Step-5 refresh never ran.
    spawn_1g_background must therefore run `did-fast --refresh-cache` itself,
    AFTER creating the goal, so dtd's cache-mtime watcher reloads it in."""
    captured = {}

    class FakePopen:
        def __init__(self, args, **kw):
            captured["args"] = args
            captured["kw"] = kw

    monkeypatch.setattr(m.subprocess, "Popen", FakePopen)
    m.spawn_1g_background("plan the day {10}")

    args = captured["args"]
    # spawn wraps the work in a shell so it can chain claude → refresh-cache.
    cmd = args[-1] if isinstance(args, (list, tuple)) else str(args)
    assert "/-1g" in cmd, "must still invoke the /-1g skill"
    assert "--refresh-cache" in cmd, "must refresh the dtd cache after setting the goal"
    assert cmd.index("--refresh-cache") > cmd.index("/-1g"), \
        "refresh-cache must run AFTER the goal is created, not before"
    assert captured["kw"].get("start_new_session") is True, \
        "background job must stay detached so it survives the TUI exiting"


def test_append_block_goals_preserves_existing_and_done_state(tmp_path, monkeypatch):
    bo = tmp_path / "build-order.md"
    bo.write_text(
        "## -1₲\n"
        "\n"
        "- 未 ☀️\n"
        "    - [ ] healthiish ((20)) {8}\n"
        "    - [x] already done\n"
        "    - actual:\n"
        "        - 发呆 @infra (13:00-13:27, 27m)\n"
        "- 申\n"
        "    - [ ] other block goal\n"
        "\n"
        "## 0₲\n"
    )
    monkeypatch.setattr(m, "BUILD_ORDER", bo)

    assert m.append_block_goals("未", ["new goal {5}"]) is True
    out = bo.read_text()

    # Existing goals untouched, including the completed one.
    assert "- [ ] healthiish ((20)) {8}" in out
    assert "- [x] already done" in out, "done-state of existing goal was clobbered"
    # New goal added as an open checkbox.
    assert "- [ ] new goal {5}" in out
    # The actual: time log is preserved.
    assert "发呆 @infra (13:00-13:27, 27m)" in out
    # Other blocks untouched.
    assert "- [ ] other block goal" in out

    lines = out.splitlines()
    i_new = next(i for i, l in enumerate(lines) if "new goal" in l)
    i_actual = next(i for i, l in enumerate(lines) if "actual:" in l)
    assert i_new < i_actual, "new goal should sit above the actual: log"

    # 🎯 stamped on the 未 header.
    hdr = next(l for l in lines if l.startswith("- 未"))
    assert m.GOAL_MARKER in hdr


def test_append_block_goals_into_empty_block(tmp_path, monkeypatch):
    bo = tmp_path / "build-order.md"
    bo.write_text(
        "## -1₲\n"
        "\n"
        "- 未\n"
        "    - actual:\n"
        "        - 发呆 @infra (13:00-13:27, 27m)\n"
        "\n"
        "## 0₲\n"
    )
    monkeypatch.setattr(m, "BUILD_ORDER", bo)
    assert m.append_block_goals("未", ["first goal"]) is True
    out = bo.read_text()
    assert "- [ ] first goal" in out
    lines = out.splitlines()
    i_new = next(i for i, l in enumerate(lines) if "first goal" in l)
    i_actual = next(i for i, l in enumerate(lines) if "actual:" in l)
    assert i_new < i_actual


# ── /inbound flow: goal card fires + appends when goals already exist ────────

class _DummyThread:
    """Stand-in for the block-watcher thread so the test never spawns it."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def _stub_common(monkeypatch, calls):
    monkeypatch.setattr(m, "set_term_color", lambda c: None)
    monkeypatch.setattr(m, "snapshot_build_order", lambda: None)
    monkeypatch.setattr(m, "check_neon_column", lambda c: "done")
    monkeypatch.setattr(m, "has_prayer_marker", lambda b: True)
    monkeypatch.setattr(m, "daily_habits_due_today", lambda: False)
    monkeypatch.setattr(m, "check_time_gaps", lambda *a, **k: [])
    monkeypatch.setattr(m, "is_asleep_now", lambda *a, **k: False)
    monkeypatch.setattr(m, "fetch_block_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(m, "fetch_suggested_goals", lambda *a, **k: [])
    monkeypatch.setattr(m, "spawn_1g_background", lambda t: None)
    monkeypatch.setattr(m, "spawn_ate_background", lambda t: None)
    # Every block already has an open goal.
    monkeypatch.setattr(
        m, "read_block_goals_with_status",
        lambda: {b[0]: [("healthiish {8}", False)] for b in m.BLOCKS},
    )

    def fake_append(block, goals):
        calls["append"].append((block, list(goals)))
        return True

    def fake_write(block, goals):
        calls["write"].append((block, list(goals)))
        return True

    monkeypatch.setattr(m, "append_block_goals", fake_append)
    monkeypatch.setattr(m, "write_block_goals", fake_write)

    # Don't spawn the real block-watcher thread; no-op sleep for safety.
    monkeypatch.setattr(m.threading, "Thread", _DummyThread)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)

    # The post-cards "Start timer?" prompt is the deterministic exit: raise
    # KeyboardInterrupt there → main() returns 2 without entering the idle loop.
    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(m.console, "input", _interrupt)


def test_inbound_goal_card_fires_and_appends_when_goals_exist(monkeypatch):
    calls = {"append": [], "write": [], "saw_goal_card": False}
    _stub_common(monkeypatch, calls)

    def fake_prompt(card_num, total, title, body, **k):
        if title.strip() == "-1g":
            calls["saw_goal_card"] = True
            assert "healthiish" in body, "existing goal should be shown on the card"
            return "extra goal"
        return "skip"  # eat card etc.

    monkeypatch.setattr(m, "prompt_card", fake_prompt)

    block_name = m.get_current_block()[1]
    m.main(skip_comms=True)

    assert calls["saw_goal_card"], "/inbound must show the goal card even when goals exist"
    assert calls["append"] == [(block_name, ["extra goal"])], "new goal must be appended"
    assert calls["write"] == [], "must NOT overwrite existing goals via write_block_goals"


def test_dash2n_goal_card_silent_when_goals_exist(monkeypatch):
    """/-2n (skip_comms=False) keeps the old behavior: no goal card when the
    block already has goals."""
    calls = {"append": [], "write": [], "saw_goal_card": False}
    _stub_common(monkeypatch, calls)
    monkeypatch.setattr(m, "write_inbox_marker", lambda b: None)

    # Fake ibx0 so the comms branch returns instead of launching the inbox.
    # It returns normally; main() then reaches the idle path, where the stubbed
    # console.input raises KeyboardInterrupt → main returns 2.
    fake_ibx0 = types.ModuleType("ibx0")
    fake_ibx0.main = lambda: None
    monkeypatch.setitem(sys.modules, "ibx0", fake_ibx0)

    def fake_prompt(card_num, total, title, body, **k):
        if title.strip() == "-1g":
            calls["saw_goal_card"] = True
        return "skip"

    monkeypatch.setattr(m, "prompt_card", fake_prompt)

    m.main(skip_comms=False)

    assert not calls["saw_goal_card"], "/-2n should not prompt for goals when goals already exist"
    assert calls["append"] == []
    assert calls["write"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
