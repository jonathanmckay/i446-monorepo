"""Regression tests for dashboard.py"""

import ast
import pathlib
import unittest

SRC = pathlib.Path(__file__).parent / "dashboard.py"
# Never let tests touch the real sidecar cache (~/.cache/jm-ai-dash).
import os as _os
_os.environ["AI_DASH_JSONL_CACHE_DB"] = ""


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


def _reference_parse_jsonl_daily_stats(m, since_date_str):
    """Verbatim pre-2026-11-01 whole-tree implementation (minus the cache
    decorator), kept as the oracle for the incremental version."""
    import json
    from collections import defaultdict
    from datetime import datetime
    import pytz
    pacific = pytz.timezone("America/Los_Angeles")
    daily_activity = defaultdict(lambda: {"messageCount": 0, "sessionCount": 0})
    daily_tokens = defaultdict(lambda: defaultdict(int))
    extra_model_costs = defaultdict(float)
    all_jsonl = []
    for projects_root, _host in m._PROJECTS_ROOTS:
        if projects_root.exists():
            all_jsonl.extend(projects_root.rglob("*.jsonl"))
    if not all_jsonl:
        return {}, {}, {}
    for fpath in all_jsonl:
        try:
            session_counted = set()
            with open(fpath) as fh:
                for line in fh:
                    obj = json.loads(line.strip())
                    if obj.get("type") != "assistant":
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                    if day <= since_date_str:
                        continue
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict) or m._is_synthetic(msg):
                        continue
                    daily_activity[day]["messageCount"] += 1
                    session_id = obj.get("sessionId", str(fpath))
                    if (session_id, day) not in session_counted:
                        session_counted.add((session_id, day))
                        daily_activity[day]["sessionCount"] += 1
                    usage = msg.get("usage", {})
                    model = msg.get("model", "unknown")
                    daily_tokens[day][model] += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    model_type = "opus" if "opus" in model.lower() else "sonnet"
                    prices = m.PRICING[model_type]
                    extra_model_costs[model_type] += (
                        (usage.get("input_tokens", 0) / 1_000_000) * prices["input"] +
                        (usage.get("output_tokens", 0) / 1_000_000) * prices["output"] +
                        (usage.get("cache_read_input_tokens", 0) / 1_000_000) * prices["cache_read"] +
                        (usage.get("cache_creation_input_tokens", 0) / 1_000_000) * prices["cache_write"])
        except Exception:
            continue
    a = {d: {"date": d, "messageCount": v["messageCount"], "sessionCount": v["sessionCount"]}
         for d, v in daily_activity.items()}
    t = {d: {"date": d, "tokensByModel": dict(ms)} for d, ms in daily_tokens.items()}
    return a, t, dict(extra_model_costs)


class TestIncrementalJsonlStats(unittest.TestCase):
    """Perf fix (2026-11-01, Dream v175): /api/stats timed out at 30/60/240 s
    because every cache miss re-read all 2.4 GB of JSONL. Per-file daily
    aggregates are now cached by (mtime_ns, size); output must equal the old
    whole-tree parse exactly (costs to float-summation tolerance)."""

    def setUp(self):
        import tempfile
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name) / "projects"
        self.db = pathlib.Path(self.td.name) / "cache.sqlite"

    def tearDown(self):
        self.td.cleanup()

    def _mod(self):
        m = _load_dashboard_module()
        m._PROJECTS_ROOTS = [(self.root, "straylight-refit")]
        m._JSONL_CACHE_DB = str(self.db)
        return m

    def _write(self, rel, lines):
        import json
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))
        return p

    @staticmethod
    def _a(sid, ts, model="claude-opus-4-7", i=10, o=5, cr=1000, cw=200):
        return {"type": "assistant", "sessionId": sid, "timestamp": ts,
                "message": {"model": model, "usage": {"input_tokens": i, "output_tokens": o,
                            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}

    def _fixture(self):
        self._write("-Users-mckay/s1.jsonl", [
            {"type": "user", "sessionId": "s1", "timestamp": "2026-06-30T20:00:00Z"},
            self._a("s1", "2026-06-30T20:00:01Z"),            # before cutoff
            self._a("s1", "2026-07-02T06:30:00Z"),            # 07-01 PT (UTC rollover)
            self._a("s1", "2026-07-02T08:00:00Z", "claude-sonnet-4-5", 3, 4, 0, 0),
            self._a("s1", "2026-07-02T09:00:00Z"),
        ])
        self._write("-Users-mckay-vault/s2.jsonl", [
            self._a("s2", "2026-10-31T23:00:00Z"),
            self._a("other", "2026-10-31T23:01:00Z"),         # 2nd session id, same file
            {"type": "assistant", "sessionId": "s2", "timestamp": "2026-10-31T23:02:00Z",
             "message": {"model": "<synthetic>", "usage": {}}},
            {"type": "assistant", "timestamp": "2026-11-01T10:00:00Z",   # no sessionId
             "message": {"model": "claude-opus-4-7", "usage": {"input_tokens": 7}}},
        ])
        self._write("-Users-mckay-vault/broken.jsonl", [
            self._a("b", "2026-10-15T18:00:00Z"),
            "{not json",                                       # aborts rest of file
            self._a("b", "2026-10-15T19:00:00Z"),
        ])
        self._write("-Users-mckay/sub/agent.jsonl", [
            self._a("s1", "2026-07-02T10:00:00Z"),            # same sid, other file
        ])

    def _assert_same(self, got, want):
        self.assertEqual(got[0], want[0])
        self.assertEqual(got[1], want[1])
        self.assertEqual(set(got[2]), set(want[2]))
        for k in want[2]:
            self.assertAlmostEqual(got[2][k], want[2][k], places=9)

    def test_matches_reference_cold_warm_and_reloaded(self):
        self._fixture()
        m = self._mod()
        for since in ("2026-06-30", "2026-07-01", "2000-01-01"):
            want = _reference_parse_jsonl_daily_stats(m, since)
            self._assert_same(m._parse_jsonl_daily_stats.__wrapped__(since), want)   # cold
            self._assert_same(m._parse_jsonl_daily_stats.__wrapped__(since), want)   # warm (memo)
            m2 = self._mod()                                                         # new process
            self._assert_same(m2._parse_jsonl_daily_stats.__wrapped__(since), want)  # from sqlite

    def test_reparses_only_changed_files_and_drops_deleted(self):
        import os
        self._fixture()
        m = self._mod()
        m._parse_jsonl_daily_stats.__wrapped__("2026-06-30")
        calls = []
        real = m._parse_jsonl_file_daily
        m._parse_jsonl_file_daily = lambda p: (calls.append(p.name), real(p))[1]
        m2 = self._mod()  # fresh memo, warm sqlite: nothing to parse
        m2._parse_jsonl_file_daily = m._parse_jsonl_file_daily
        m2._parse_jsonl_daily_stats.__wrapped__("2026-06-30")
        self.assertEqual(calls, [])
        p = self._write("-Users-mckay-vault/s2.jsonl", [self._a("s2", "2026-11-01T18:00:00Z")])
        os.utime(p, ns=(1, 1))
        os.remove(self.root / "-Users-mckay-vault" / "broken.jsonl")
        got = m2._parse_jsonl_daily_stats.__wrapped__("2026-06-30")
        self.assertEqual(calls, ["s2.jsonl"])
        self._assert_same(got, _reference_parse_jsonl_daily_stats(m2, "2026-06-30"))
        self.assertNotIn("2026-10-15", got[0])

    def test_parse_version_bump_forces_reparse(self):
        self._fixture()
        m = self._mod()
        m._parse_jsonl_daily_stats.__wrapped__("2026-06-30")
        m2 = self._mod()
        m2._JSONL_PARSE_VERSION = m._JSONL_PARSE_VERSION + 1
        calls = []
        real = m2._parse_jsonl_file_daily
        m2._parse_jsonl_file_daily = lambda p: (calls.append(p.name), real(p))[1]
        m2._parse_jsonl_daily_stats.__wrapped__("2026-06-30")
        self.assertEqual(len(calls), 4)


class TestSingleFlightCache(unittest.TestCase):
    def test_concurrent_misses_share_one_computation(self):
        """Overlapping /api/stats requests each re-ran the full parse (4 worker
        threads seen in `sample` on 2026-11-01). A miss must be single-flight."""
        import threading, time
        m = _load_dashboard_module()
        n = {"calls": 0}

        @m._cached("slow", lambda: 1)
        def slow():
            n["calls"] += 1
            time.sleep(0.2)
            return 42

        out = []
        ts = [threading.Thread(target=lambda: out.append(slow())) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out, [42] * 6)
        self.assertEqual(n["calls"], 1)


class TestIncrementalIngest(unittest.TestCase):
    def test_second_ingest_skips_unchanged_files(self):
        import json, sqlite3, tempfile
        m = _load_dashboard_module()
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            root = td / "projects" / "-Users-mckay"
            root.mkdir(parents=True)
            (root / "a.jsonl").write_text(json.dumps(
                TestIncrementalJsonlStats._a("a", "2026-10-31T23:00:00Z")) + "\n")
            db = td / "llm.db"
            c = sqlite3.connect(db)
            c.execute("""CREATE TABLE sessions (session_id TEXT PRIMARY KEY, provider, product, model,
                start_time, end_time, message_count, input_tokens, output_tokens, total_tokens,
                cached_tokens, cache_write_tokens, cost_usd, project_dir, user_id, status)""")
            c.commit(); c.close()
            m.LLM_DB = db
            m._PROJECTS_ROOTS = [(td / "projects", "straylight-refit")]
            m._JSONL_CACHE_DB = str(td / "cache.sqlite")
            m.ingest_claude_sessions.__wrapped__()
            c = sqlite3.connect(db)
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
            c.execute("DELETE FROM sessions"); c.commit(); c.close()
            m.ingest_claude_sessions.__wrapped__()   # unchanged file: skipped
            c = sqlite3.connect(db)
            self.assertEqual(c.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
            c.close()


if __name__ == "__main__":
    unittest.main()
