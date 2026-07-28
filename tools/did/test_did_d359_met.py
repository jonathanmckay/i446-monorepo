"""Regression test: completing a d359 outreach reminder via /did (and hence
dtd's enter/alt-enter) routes through the same 'met' flow /s897 uses.

stale-contacts creates `😈 Reach out to <Name> ...` tasks labelled
`d359/<slug>`. Before this feature, marking one done in dtd just closed the
Todoist task and credited its [N] points like any other task — last_contact
never updated, so the contact silently drifted stale again. /s897's own
policy (2026-07-21): the robot task is DELETED (not completed) once contact
happens, and no points are claimed for a task a daemon invented. This test
covers the pure label/content matcher (functional) and the did-fast main-path
intercept that diverts BEFORE the generic close/point-credit paths ever see
the task (structural, matching this repo's test_did_ritual_card_routing.py
style — network-dependent code is verified by source inspection, not by
executing main() against live Todoist/Excel).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_d359", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_d359"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Functional: d359_outreach_slug ───────────────────────────────────────────

def test_daemon_outreach_task_matches():
    m = _load()
    task = {"content": "😈 Reach out to Scott Van Vliet (overdue weekly: last contact 2026-07-16) [10] (30)",
            "labels": ["s897", "d359/scott-van-vliet"]}
    assert m.d359_outreach_slug(task) == "scott-van-vliet"


def test_hand_written_task_with_label_is_not_hijacked():
    # No 😈 marker → hand-written, even if it happens to carry the label.
    m = _load()
    task = {"content": "Reach out to Scott Van Vliet", "labels": ["d359/scott-van-vliet"]}
    assert m.d359_outreach_slug(task) is None


def test_daemon_task_without_d359_label_is_none():
    m = _load()
    task = {"content": "😈 -1ibx", "labels": ["-1neon"]}
    assert m.d359_outreach_slug(task) is None


def test_missing_labels_key_is_safe():
    m = _load()
    assert m.d359_outreach_slug({"content": "😈 x"}) is None


def test_slug_reconstructs_hyphenated_name():
    m = _load()
    task = {"content": "😈 Reach out to Jean-Paul Smith", "labels": ["d359/jean-paul-smith"]}
    slug = m.d359_outreach_slug(task)
    assert slug == "jean-paul-smith"
    assert slug.replace("-", " ") == "jean paul smith"


# ── Structural: the did-fast main-path intercept is wired correctly ─────────

def _intercept_segment() -> tuple[str, str]:
    src = DID_FAST.read_text()
    i_fast = src.index('fast = [r for r in routes if r.step in ("0n", "todoist", "1n", "variable")]')
    i_intercept = src.index("d359_outreach_slug(", i_fast)
    i_toggl = src.index("stop_matching_toggl(all_names)", i_intercept)
    assert i_fast < i_intercept < i_toggl, (
        "d359 intercept must sit between the fast-path split and Toggl/point-credit steps")
    return src, src[src.index("# 3a-ii.", i_fast):i_toggl]


def test_intercept_runs_before_point_credit_and_close():
    src, _seg = _intercept_segment()
    i_intercept = src.index("d359_outreach_slug(")
    i_close = src.index("close_todoist_tasks(task_ids)")
    i_fen = src.index("fen_appends = []")
    assert i_intercept < i_close
    assert i_intercept < i_fen


def test_intercept_gated_on_points_only():
    # --points-only promises no Todoist side effects; run_d359_met deletes a
    # task and writes the vault — all side effects, so it must not run.
    _src, seg = _intercept_segment()
    assert "if not points_only:" in seg


def test_diverted_items_removed_from_fast():
    # The diverted item must not also flow into the generic close/credit path.
    _src, seg = _intercept_segment()
    assert "remaining.append(r)" in seg
    assert "fast = remaining" in seg


def test_intercept_refreshes_cache_when_diverting():
    # Same regression class as ritual cards (2026-06-29): the cache mtime
    # bump is dtd's only reload signal, and a DELETEd task must disappear
    # from dtd immediately, not linger until some unrelated refresh.
    _src, seg = _intercept_segment()
    assert "refresh_task_queue(block=True)" in seg


def test_output_includes_d359_met_entries():
    src, _seg = _intercept_segment()
    assert 'output = {"results": list(ritual_entries) + d359_met_entries' in src


def test_run_d359_met_calls_s897_update_with_met():
    m = _load()
    calls = []

    class FakeProc:
        returncode = 0
        stdout = "scott-van-vliet: last_contact → 2026-07-28 · deleted 😈 task: Reach out to Scott Van Vliet"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    m.subprocess.run = fake_run
    entry = m.run_d359_met("😈 Reach out to Scott Van Vliet", "scott-van-vliet")
    assert entry["step"] == "d359_met"
    assert entry["d359"]["ok"] is True
    assert entry["d359"]["slug"] == "scott-van-vliet"
    assert calls[0][:2] == ["python3", str(m.S897_UPDATE)]
    assert calls[0][2] == "scott van vliet met"


def test_run_d359_met_reports_failure():
    m = _load()

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "no d359 match for: scott van vliet"

    m.subprocess.run = lambda cmd, **kwargs: FakeProc()
    entry = m.run_d359_met("x", "scott-van-vliet")
    assert entry["d359"]["ok"] is False
    assert "no d359 match" in entry["d359"]["output"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
