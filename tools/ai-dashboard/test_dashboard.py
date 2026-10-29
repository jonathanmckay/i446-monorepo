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


def _load_dashboard_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ai_dash_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGithubCommits(unittest.TestCase):
    def test_paginates_past_100_and_excludes_autopush_snapshots(self):
        """Bug (2026-10-27): get_github_commits() asked for per_page=100 with
        no paging. Autopush repos (~144 snapshots/day) fill that in under a
        day, so the heatmap showed ~36 h then a hole from 10-02 to 10-25.
        Must follow Link rel=next and drop vault backup:/auto: snapshots."""
        from datetime import datetime, timedelta, timezone
        from unittest import mock

        m = _load_dashboard_module()
        now = datetime.now(timezone.utc)
        commits = []
        for i in range(250):  # newest first, like the API
            ts = now - timedelta(hours=i)
            if i % 5 == 0:
                msg = f"feat: real work {i}"
            elif i % 2:
                msg = f"vault backup: {ts:%Y-%m-%d %H:%M:%S}"
            else:
                msg = f"auto: {ts:%Y-%m-%d %H:%M}"
            commits.append({
                "sha": f"s{i}",
                "author": {"login": m.GITHUB_USER},
                "commit": {"message": msg,
                           "author": {"name": "Jonathan McKay",
                                      "date": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}},
            })
        real = [c for c in commits if not c["commit"]["message"].startswith(("vault", "auto"))]
        pages = [commits[i:i + 100] for i in range(0, 250, 100)]
        base = "https://api.github.com/repos/jonathanmckay/vault/commits"

        def fake_get(url, params=None, headers=None, timeout=None):
            r = mock.Mock(status_code=200)
            if "/user/repos" in url or "/users/" in url:
                r.json.return_value = [{"full_name": "jonathanmckay/vault",
                                        "pushed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}]
                r.links = {}
                return r
            idx = int(url.split("page=")[1]) - 1 if "page=" in url else 0
            r.json.return_value = pages[idx]
            r.links = ({"next": {"url": f"{base}?page={idx + 2}"}}
                       if idx + 1 < len(pages) else {})
            return r

        with mock.patch.object(m.requests, "get", side_effect=fake_get):
            out = m.get_github_commits()
        self.assertNotIn("error", out)
        self.assertEqual(out["total_commits"], len(real))  # 50, not "first 100 rows"
        self.assertEqual(sum(out["daily_commits"].values()), len(real))
        oldest_real = min(c["commit"]["author"]["date"] for c in real)
        oldest_key = (datetime.fromisoformat(oldest_real.replace("Z", "+00:00"))
                      .astimezone(m.pytz.timezone("America/Los_Angeles")).strftime("%Y-%m-%d"))
        self.assertIn(oldest_key, out["daily_commits"])  # reached page 3


class TestSnapshotFilter(unittest.TestCase):
    def test_snapshot_regex_matches_autopush_formats_only(self):
        m = _load_dashboard_module()
        for msg in ("vault backup: 2026-10-27 04:57:48", "vault: 2026-08-11 05:00",
                    "auto: 2026-10-27 04:50"):
            self.assertTrue(m._is_snapshot_commit(msg), msg)
        for msg in ("vault: fix broken links", "auto: add retry", "janus: log-points",
                    "dashboard: weekly chart reads the daily cache"):
            self.assertFalse(m._is_snapshot_commit(msg), msg)

    def test_extra_branches_are_read_and_deduped(self):
        """i446-monorepo work lands on wip; main alone missed it."""
        from datetime import datetime, timezone
        from unittest import mock

        m = _load_dashboard_module()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        def c(sha, msg):
            return {"sha": sha, "author": {"login": m.GITHUB_USER},
                    "commit": {"message": msg, "author": {"name": "JM", "date": now}}}
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            r = mock.Mock(status_code=200, links={})
            if "/user/repos" in url or "/users/" in url:
                r.json.return_value = [{"full_name": "jonathanmckay/i446-monorepo", "pushed_at": now}]
            else:
                seen.append((params or {}).get("sha"))
                r.json.return_value = ([c("a", "fix: x"), c("b", "feat: y")]
                                       if (params or {}).get("sha") == "wip"
                                       else [c("a", "fix: x")])
            return r

        with mock.patch.object(m.requests, "get", side_effect=fake_get):
            out = m.get_github_commits()
        self.assertEqual(seen, [None, "wip"])
        self.assertEqual(out["total_commits"], 2)


class TestSyntheticSessionsExcluded(unittest.TestCase):
    """Bug (2026-10-29, Dream v172 E14): claude.daily_activity.sessionCount
    sat at a constant 49/day (09-30..10-16) and total_sessions was inflated.
    Root cause: every keychain-less headless `claude --print` (the hourly
    d357-auto-file cron, 2 calls/run) left a session JSONL whose only
    assistant record is model "<synthetic>" ("Not logged in · Please run
    /login"); _parse_jsonl_daily_stats counted each as a session+message."""

    def _write(self, root, name, records):
        import json
        d = root / "-Users-mckay-vault"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def _asst(self, sid, ts, model, text="ok"):
        return {"type": "assistant", "sessionId": sid, "timestamp": ts,
                "message": {"model": model, "content": [{"type": "text", "text": text}],
                            "usage": {"input_tokens": 0 if model == "<synthetic>" else 10,
                                      "output_tokens": 0 if model == "<synthetic>" else 5}}}

    def _run(self, records_by_file):
        import tempfile
        m = _load_dashboard_module()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "projects"
            for name, recs in records_by_file.items():
                self._write(root, name, recs)
            m._PROJECTS_ROOTS = [(root, "straylight-refit")]
            return m._parse_jsonl_daily_stats.__wrapped__("2026-10-01")

    def test_not_logged_in_sessions_are_not_counted(self):
        files = {
            f"junk{i}": [
                {"type": "user", "sessionId": f"j{i}", "timestamp": "2026-10-14T16:17:00Z",
                 "message": {"role": "user", "content": "Follow the d357 skill..."}},
                self._asst(f"j{i}", "2026-10-14T16:17:02Z", "<synthetic>",
                           "Not logged in · Please run /login"),
            ]
            for i in range(49)
        }
        files["real"] = [
            self._asst("r1", "2026-10-14T18:00:00Z", "claude-opus-4-7"),
            self._asst("r1", "2026-10-14T18:05:00Z", "claude-opus-4-7"),
        ]
        activity, tokens, _ = self._run(files)
        self.assertEqual(activity["2026-10-14"]["sessionCount"], 1)
        self.assertEqual(activity["2026-10-14"]["messageCount"], 2)
        self.assertNotIn("<synthetic>", tokens["2026-10-14"]["tokensByModel"])

    def test_day_with_only_synthetic_sessions_has_no_activity_entry(self):
        files = {"junk": [self._asst("j", "2026-10-15T16:17:02Z", "<synthetic>",
                                     "Credit balance is too low")]}
        activity, _, _ = self._run(files)
        self.assertNotIn("2026-10-15", activity)

    def test_session_spanning_days_counts_once_per_day(self):
        files = {"long": [
            self._asst("L", "2026-10-14T20:00:00Z", "claude-opus-4-7"),  # 10-14 PT
            self._asst("L", "2026-10-15T20:00:00Z", "claude-opus-4-7"),  # 10-15 PT
        ]}
        activity, _, _ = self._run(files)
        self.assertEqual(activity["2026-10-14"]["sessionCount"], 1)
        self.assertEqual(activity["2026-10-15"]["sessionCount"], 1)


if __name__ == "__main__":
    unittest.main()
