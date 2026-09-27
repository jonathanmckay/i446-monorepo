#!/usr/bin/env python3
"""1-i446 vault health runner: pure-part tests (alert grouping, description, git state)."""
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("vault_health", HERE / "vault_health.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["vault_health"] = mod
    spec.loader.exec_module(mod); return mod


def test_alerts_grouped_by_tool_reason_and_only_last_7_days(tmp_path, monkeypatch):
    vh = _load()
    now = dt.datetime(2026, 9, 27, 12, tzinfo=vh.TZ)
    lines = [
        {"ts": "2026-09-27T11:00:00Z", "tool": "dream", "reason": "keychain", "severity": "critical", "detail": "x"},
        {"ts": "2026-09-26T11:00:00Z", "tool": "dream", "reason": "keychain", "severity": "critical", "detail": "y"},
        {"ts": "2026-08-01T11:00:00Z", "tool": "old", "reason": "stale", "severity": "warning", "detail": "z"},
    ]
    p = tmp_path / "alerts.jsonl"; p.write_text("\n".join(json.dumps(l) for l in lines))
    monkeypatch.setattr(vh, "ALERTS", p)
    f = vh.check_alerts(now)
    assert f.status == "fail" and f.review
    assert len(f.detail) == 1 and "×2" in f.detail[0] and "old" not in f.detail[0]


def test_description_lists_only_review_items(tmp_path):
    vh = _load()
    fs = [vh.Finding("a", "ok", "fine"), vh.Finding("b", "warn", "needs you", ["d1", "d2"], review=True)]
    d = vh.build_description(fs, "z_meta/r.md")
    assert "1. [b] needs you" in d and "[a]" not in d and "- d1" in d


def test_git_state_flags_stuck_rebase_and_unpushed(tmp_path):
    vh = _load()
    repo = tmp_path / "r"; repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    now = dt.datetime.now(vh.TZ)
    s, d, r = vh._git_state(repo, None, now, is_pusher=True)
    assert s == "ok" and not r
    (repo / ".git/rebase-merge").mkdir()
    s, d, r = vh._git_state(repo, None, now, is_pusher=True)
    assert s == "fail" and r and "REBASE" in d[0]
