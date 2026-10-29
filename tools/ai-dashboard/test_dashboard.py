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


if __name__ == "__main__":
    unittest.main()
