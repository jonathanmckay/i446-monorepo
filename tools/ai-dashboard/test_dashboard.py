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
