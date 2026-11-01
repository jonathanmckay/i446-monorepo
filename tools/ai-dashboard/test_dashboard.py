"""Regression tests for dashboard.py"""

import ast
import pathlib
import unittest

SRC = pathlib.Path(__file__).parent / "dashboard.py"


class TestImports(unittest.TestCase):
    def test_os_is_imported(self):
        """dashboard.py uses os.path.expanduser but previously omitted 'import os', causing a 500."""
        tree = ast.parse(SRC.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn(
            "os", imported,
            "dashboard.py must import 'os' — get_permissions_stats() uses os.path.expanduser/exists"
        )


class TestSkillStats(unittest.TestCase):
    """get_skill_stats read only projects/-Users-mckay, missed model Skill calls
    and every /did run from dtd/janus, so most skills showed 0 uses (2026-11-01)."""

    def test_skill_stats_counts_all_project_dirs_model_calls_and_outside_runs(self):
        import json
        import sys
        import tempfile
        from datetime import datetime, timezone
        sys.path.insert(0, str(SRC.parent))
        import dashboard as d

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            skills = tmp / "skills"
            for name in ("bug", "notes", "did"):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text("x")
            proj = tmp / "projects"
            vault = proj / "-Users-mckay-vault"          # not -Users-mckay
            vault.mkdir(parents=True)

            def user(uuid, text):
                return json.dumps({"type": "user", "uuid": uuid, "timestamp": now,
                                   "message": {"role": "user", "content": text}})

            model = json.dumps({"type": "assistant", "uuid": "a1", "timestamp": now,
                                "message": {"content": [{"type": "tool_use", "id": "t1",
                                            "name": "Skill", "input": {"skill": "notes"}}]}})
            lines = [user("u1", "<command-name>/bug</command-name>"),
                     user("u2", "<command-name>/resume</command-name>"),  # built-in
                     model]
            (vault / "s1.jsonl").write_text("\n".join(lines) + "\n")
            # a fork repeats u1 under the same uuid: still one /bug
            (vault / "s2.jsonl").write_text(lines[0] + "\n")

            log = tmp / "source-log"
            log.mkdir()
            (log / "ix-2026-11.jsonl").write_text(json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "metric": "skill", "ext_id": "did", "via": "janus"}) + "\n")

            saved = (d._PROJECTS_ROOTS, d._SKILLS_DIR, d._SOURCE_LOG_DIR)
            d._PROJECTS_ROOTS = [(proj, "ix")]
            d._SKILLS_DIR, d._SOURCE_LOG_DIR = skills, log
            d._SKILL_FILE_CACHE.clear()
            try:
                out = d.get_skill_stats.__wrapped__(days=3)
            finally:
                d._PROJECTS_ROOTS, d._SKILLS_DIR, d._SOURCE_LOG_DIR = saved
                d._SKILL_FILE_CACHE.clear()

        self.assertEqual(sorted(out["skills"]), ["/bug", "/did", "/notes"])
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["by_via"], {"slash": 1, "model": 1, "janus": 1})


class TestClaudeTotals(unittest.TestCase):
    def test_headline_totals_include_days_after_stale_stats_cache(self):
        """Bug (2026-11-01, Dream v175): ~/.claude/stats-cache.json stopped
        updating 2026-07-01, so total_sessions/total_messages read straight
        from it froze at 660 / 237,402 for four months while total_cost kept
        growing. Totals must add the JSONL-parsed days after the cache."""
        import importlib.util, json, sys, tempfile
        spec = importlib.util.spec_from_file_location("ai_dash_under_test", SRC)
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as td:
            cache = pathlib.Path(td) / "stats-cache.json"
            cache.write_text(json.dumps({
                "totalSessions": 660, "totalMessages": 600, "modelUsage": {},
                "dailyActivity": [{"date": "2026-07-01", "messageCount": 10, "sessionCount": 2}],
                "dailyModelTokens": []}))
            m._stats_cache_path = lambda: cache
            m._parse_jsonl_daily_stats = lambda since: ({
                "2026-07-01": {"date": "2026-07-01", "messageCount": 12, "sessionCount": 3},
                "2026-10-31": {"date": "2026-10-31", "messageCount": 100, "sessionCount": 90},
            }, {}, {})
            out = m.get_claude_stats.__wrapped__()
        self.assertEqual(out["total_sessions"], 750)
        self.assertEqual(out["total_messages"], 700)


if __name__ == "__main__":
    unittest.main()
