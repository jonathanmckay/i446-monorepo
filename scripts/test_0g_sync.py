"""Regression tests for 0g-sync.py — ensures completed Todoist tasks flip
build-order MD lines from `- [ ]` to `- [x]` (rather than removing them, which
would erase the day's accomplishment record from v_logs/archive)."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_module():
    # 0g-sync.py has a leading digit and a hyphen in its name — load by spec.
    path = Path(__file__).parent / "0g-sync.py"
    spec = importlib.util.spec_from_file_location("zerog_sync", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["zerog_sync"] = module
    spec.loader.exec_module(module)
    return module


zg = _load_module()


# --- flip_to_checked --------------------------------------------------------

class TestFlipToChecked:
    def test_flips_unchecked_to_checked(self):
        assert zg.flip_to_checked("- [ ] foo") == "- [x] foo"

    def test_preserves_indent(self):
        assert zg.flip_to_checked("    - [ ] block goal") == "    - [x] block goal"

    def test_idempotent_on_checked(self):
        assert zg.flip_to_checked("- [x] done") == "- [x] done"

    def test_ignores_non_task_lines(self):
        assert zg.flip_to_checked("## 0₲") == "## 0₲"

    def test_preserves_annotations(self):
        assert zg.flip_to_checked("- [ ] task {30}") == "- [x] task {30}"


# --- find_neg1_section ------------------------------------------------------

class TestFindNeg1Section:
    def test_finds_section(self):
        lines = [
            "## 0₲",
            "- [ ] foo",
            "## -1₲",
            "- 卯",
            "    - [ ] bar",
            "## next",
        ]
        start, end = zg.find_neg1_section(lines)
        assert start == 2
        assert end == 5

    def test_returns_negative_when_missing(self):
        lines = ["## 0₲", "- [ ] foo"]
        assert zg.find_neg1_section(lines) == (-1, -1)


# --- mark_neg1_completed ----------------------------------------------------

class TestMarkNeg1Completed:
    def test_flips_lines_matching_completed_tasks(self):
        """An unchecked -1₲ line matching a completed Todoist task gets flipped."""
        lines = [
            "## -1₲",
            "- 卯",
            "    - [ ] Improve d357",
            "    - [ ] Keep ibx at 0",
            "    - [ ] Untouched goal",
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [
            {"content": "Improve d357 (60)"},
            {"content": "Keep ibx at 0"},
        ]
        flipped = zg.mark_neg1_completed(client, lines, dry_run=True)
        # Lines 2 and 3 should be flipped; line 4 is "Untouched", not in completed list.
        assert sorted(flipped) == [2, 3]

    def test_skips_already_checked_lines(self):
        lines = [
            "## -1₲",
            "    - [x] Already done",
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [{"content": "Already done"}]
        assert zg.mark_neg1_completed(client, lines, dry_run=True) == []

    def test_no_op_when_section_missing(self):
        lines = ["## 0₲", "- [ ] foo"]
        client = MagicMock()
        assert zg.mark_neg1_completed(client, lines) == []
        client.get_completed_tasks.assert_not_called()

    def test_skips_blank_unchecked_placeholders(self):
        """The /-1g daily-reset writes empty `- [ ] ` placeholders. Those must
        never match completed tasks (otherwise every empty bullet flips)."""
        lines = [
            "## -1₲",
            "- 卯",
            "    - [ ] ",  # placeholder with trailing space
            "    - [ ]",   # placeholder with no trailing space (won't match CHECKBOX_RE)
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [{"content": "Some task"}]
        assert zg.mark_neg1_completed(client, lines, dry_run=True) == []


# --- AST regression: ensure run_sync flips instead of removing --------------

class TestRunSyncBehavior:
    """The original bug: 0g-sync removed completed lines from MD, erasing the
    day's record. The fix flips [ ] to [x] so the 03:59 archive captures it.
    Guard against regression by checking source for the removed pattern."""

    def test_run_sync_does_not_remove_lines(self):
        src = (Path(__file__).parent / "0g-sync.py").read_text()
        # The old buggy logic built a removal set called `lines_to_remove` and
        # did a list comprehension excluding those indices. Any future code
        # that re-introduces a remove-by-index step on the build order MD
        # should be reviewed against this regression.
        assert "lines_to_remove" not in src, (
            "0g-sync.py should flip [ ] to [x] rather than remove lines "
            "(needed so v_logs/archive captures the day's completed tasks)."
        )
        # Affirmative check that we still flip.
        assert "flip_to_checked" in src


# --- Project routing (JM 2026-11-09: "infer the project and add that as the
# 'project' for the tasks") ---------------------------------------------------

class TestDomainRouting:
    def test_explicit_codes_and_at_codes_win(self):
        assert zg.detect_domain("@i9 ship the dashboard") == "i9"
        assert zg.detect_domain("review m5x2 P&L") == "m5x2"      # code beats the g245 'review'

    def test_old_label_names_normalize_to_what_did_fast_knows(self):
        """hcbi/hcmp were emitted before; did-fast's LABEL_TO_0FEN only knows
        hcb/hcm/hcbp, so those goals logged to 个."""
        assert zg.detect_domain("read book on hcbi") == "hcb"
        assert zg.detect_domain("hcmp sit") == "hcm"

    def test_family_and_home_are_xk87_not_xk88(self):
        assert zg.detect_domain("family dinner at home") == "xk87"

    def test_bare_run_is_not_exercise(self):
        assert zg.detect_domain("run the numbers on l912") == "m5x2"
        assert zg.detect_domain("Full HIIT with weights") == "hcbp"

    def test_unknown_goal_has_no_domain_and_stays_in_0g_project(self):
        r = zg.route_goal("Progress with hiring {15}")
        assert r["domain"] is None and r["labels"] == ["#0g"]
        assert r["project_id"] == zg.TODOIST_0G_PROJECT_ID

    def test_route_goal_maps_domain_to_its_todoist_project(self):
        r = zg.route_goal("Full HIIT with weights {25}")
        assert r["labels"] == ["#0g", "hcbp"] and r["project_id"] == zg.DOMAIN_PROJECT["hcbp"]
        assert zg.project_for("nope") == zg.TODOIST_0G_PROJECT_ID
        for dom, pid in zg.DOMAIN_PROJECT.items():
            assert pid.startswith("6") and len(pid) == 16, (dom, pid)


class TestLabelBasedSync:
    def test_run_sync_finds_goals_by_label_not_project(self):
        """Goals live in their domain projects now; the active set and the
        adopt loop must come from the #0g label fetch or every goal outside the
        0g project would be flipped [x] as 'completed'."""
        src = (Path(__file__).parent / "0g-sync.py").read_text()
        body = src[src.index("def run_sync("):src.index("def run_cleanup(")]
        assert "client.get_label_tasks(OG_LABEL)" in body
        assert "get_project_tasks(TODOIST_0G_PROJECT_ID)" not in body
        assert "route_goal(task.description)" in body and "project_id=project_id" in body

    def test_create_task_posts_the_routed_project(self, monkeypatch):
        seen = {}
        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"id": "x"}
        monkeypatch.setattr(zg.requests, "post", lambda url, headers=None, json=None: seen.update(json) or _Resp())
        c = zg.TodoistClient("tok")
        c.create_task("Full HIIT {25}", labels=["#0g", "hcbp"], project_id=zg.DOMAIN_PROJECT["hcbp"])
        assert seen["project_id"] == zg.DOMAIN_PROJECT["hcbp"]
        c.create_task("plain", labels=["#0g"])
        assert seen["project_id"] == zg.TODOIST_0G_PROJECT_ID

    def test_label_fetch_uses_the_label_param(self, monkeypatch):
        calls = []
        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"results": [{"id": "a"}], "next_cursor": None}
        monkeypatch.setattr(zg.requests, "get", lambda url, headers=None, params=None: calls.append(params) or _Resp())
        assert zg.TodoistClient("tok").get_label_tasks("#0g") == [{"id": "a"}]
        assert calls == [{"label": "#0g", "limit": 200}]

    def test_cleanup_deletes_union_of_0g_project_and_label(self, monkeypatch, tmp_path):
        md = tmp_path / "build-order.md"
        md.write_text("## 0₲\n\n### 以后的目标\n")
        monkeypatch.setattr(zg, "MD_FILE", md)
        monkeypatch.setattr(zg, "save_state", lambda state: None)
        client = MagicMock()
        client.get_project_tasks.return_value = [{"id": "a"}]            # leftover -1g in 0g project
        client.get_label_tasks.return_value = [{"id": "a"}, {"id": "b"}]  # b = goal filed in m5x2
        zg.run_cleanup(client, dry_run=False)
        assert sorted(c.args[0] for c in client.delete_task.call_args_list) == ["a", "b"]
