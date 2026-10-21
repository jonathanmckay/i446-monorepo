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


def test_duplicate_notes_flags_live_dupes_not_journals_or_archives(tmp_path, monkeypatch):
    vh = _load()
    monkeypatch.setattr(vh.singleton_audit, "EXCLUDE_PREFIXES", ("z_arcv",))
    for rel, body in {
        "hcmc/epcn.md": "", "hcmp/epcn.md": "",             # live dupes: the 2026-10-09 case
        "hcmc/hcmc.md": "[[epcn]] and [[hcmp/epcn|ok]]",     # same-folder bare link still listed
        "hcmp/hcmp.md": "[[epcn|EPCN]] ![[epcn]]",
        "o314/2019/life.md": "", "o314/2020/life.md": "",    # dated journal folders: not a conflict
        "z_arcv/old/epcn.md": "[[epcn]]",                    # archive: ignored entirely
        "a/CLAUDE.md": "", "b/CLAUDE.md": "",                # DUP_NAMES_OK
    }.items():
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
    d = vh.scan_duplicate_notes(tmp_path)
    assert set(d) == {"epcn"}
    assert d["epcn"]["paths"] == ["hcmc/epcn.md", "hcmp/epcn.md"]
    assert sorted(d["epcn"]["linked_from"]) == ["hcmc/hcmc.md", "hcmp/hcmp.md"]


def test_skill_claims_flags_only_unlinked_undeclined_name_matches():
    vh = _load()
    cards = ["1 i9 (10) [40]", "moral audit (30) [40]", "1 f695 (10) [25]", "1s (20) [45]", "s897 (120) [30]",
             "hiit (10) [23]"]
    pairs = vh.unlinked_skill_cards(["1i9", "moralaudit", "f695", "s", "s897", "did", "newskill"], cards,
                                    linked={"1i9"}, declined={"s897"})
    assert pairs == [("f695", "1 f695"), ("moralaudit", "moral audit"), ("s", "1s")]


def test_skill_claims_live_registry_is_clean():
    """Every current skill that name-matches a real recurring card is either
    linked or declined (card names as of 2026-10-11)."""
    vh = _load()
    cards = ["0g (4) [8]", "0t (3) [10]", "1 -1n (10) [15]", "1 -2g (13) [20]", "1 f695 (10) [25]", "1 hcb (5) [15]",
             "1 hcmc (8) [15]", "1 i9 (10) [40]", "1 m5x2 (30) [30]", "1 s897 (25) [30]", "1g (20) [20]",
             "1s (20) [45]", "3s (30) [40]", "moral audit (30) [40]", "notes (3) [8]", "s897 (120) [30]", "早餐 (15) [5]"]
    f = vh.check_skill_claims(cards)
    assert f.status == "ok", f.detail


def test_time_machine_exempts_work_managed_straylight(monkeypatch):
    """JM 2026-10-11: Straylight is a work-managed Mac and never gets Time
    Machine; only Ix's backup is checked from there."""
    vh = _load()
    monkeypatch.setattr(vh.socket, "gethostname", lambda: "Straylight-Refit.local")
    monkeypatch.setattr(vh, "on_ix", lambda: False)
    monkeypatch.setattr(vh, "ix", lambda c: "2026-10-11-040000" if "latest" in c else "Name : Ix Backup")
    monkeypatch.setattr(vh, "sh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local tmutil called")))
    f = vh.check_time_machine()
    assert f.status in ("ok", "warn") and not any("no Time Machine destination" in d for d in f.detail)
    assert f.detail[0].startswith("Straylight-Refit.local: exempt")


def test_style_scan_flags_filenames_and_titles_and_skips_people_and_dated(tmp_path):
    vh = _load()
    def w(rel, title):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{title}"\n---\nx\n')
    w("hcmp/hcms.md", "hcms")                                   # clean
    w("hcmp/Untitled 2.md", "Untitled 2")                       # filename
    w("hcmp/x-祷.md", "祷 prayer صلاة")                            # filename + arabic
    w("h335/h335.md", "h335 - 循环 (Career & Business)")          # translated (code h335 is not a word)
    w("g245/goals.md", "A title that is far too long to be a good title at all, really")  # long
    w("d359/Jane Doe.md", "Jane Doe")                           # people notes skipped
    w("hcmp/o314/2015/Some Entry.md", "عنوان")                   # dated journal folder skipped
    found = vh.scan_style(tmp_path)
    assert [r for r, _ in found["filename"]] == ["hcmp/Untitled 2.md", "hcmp/x-祷.md"]
    assert [r for r, _ in found["arabic"]] == ["hcmp/x-祷.md"]
    assert [r for r, _ in found["translated"]] == ["h335/h335.md"]
    assert [r for r, _ in found["long"]] == ["g245/goals.md"]


def test_style_finding_counts_and_is_a_review_item(tmp_path, monkeypatch):
    vh = _load()
    monkeypatch.setattr(vh, "VAULT", tmp_path)
    (tmp_path / "a").mkdir(); (tmp_path / "a/Bad Name.md").write_text("---\ntitle: ok\n---\n")
    f = vh.check_style()
    assert f.status == "warn" and f.review and "1 filename" in f.summary
    assert f.detail[0].startswith("filename: 1 — a 1") and f.detail[1].strip() == "a/Bad Name.md"
    (tmp_path / "a/Bad Name.md").rename(tmp_path / "a/good-name.md")
    assert vh.check_style().status == "ok"


def _toggl_store(tmp_path, days, synced_hours_ago=2.0, incomplete=0, now=None):
    import sqlite3, time as _t
    db = tmp_path / "toggl.sqlite"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE entries (id INTEGER PRIMARY KEY, day TEXT, seconds INTEGER);"
                    "CREATE TABLE sync_log (window_start TEXT, window_end TEXT, fetched_at INTEGER, pages INTEGER, rows INTEGER, deleted INTEGER, complete INTEGER);")
    for i, d in enumerate(days):
        c.execute("INSERT INTO entries VALUES (?,?,?)", (i, d, 60))
    ts = int((now or dt.datetime.now(dt.timezone.utc)).timestamp() - synced_hours_ago * 3600)
    c.execute("INSERT INTO sync_log VALUES ('x','x',?,1,1,0,1)", (ts,))
    for _ in range(incomplete):
        c.execute("INSERT INTO sync_log VALUES (?,?,?,1,0,0,0)", (days[-1], days[-1], ts))
    c.commit(); c.close()
    return db


def test_toggl_store_healthy_when_last_week_present_and_sync_fresh(tmp_path):
    vh = _load()
    now = dt.datetime(2026, 10, 21, 12, tzinfo=vh.TZ)
    days = [(now.date() - dt.timedelta(days=i)).isoformat() for i in range(0, 9)]
    db = _toggl_store(tmp_path, days, now=now)
    exp = tmp_path / "cache.json"; exp.write_text(json.dumps({d: {} for d in days}))
    r = vh.scan_toggl_store(now, db, exp)
    assert r["ok"] and "all of the last 7 days have entries" in r["facts"]


def test_toggl_store_flags_missing_days_stale_sync_incomplete_windows_and_stale_export(tmp_path):
    vh = _load()
    now = dt.datetime(2026, 10, 21, 12, tzinfo=vh.TZ)
    days = [(now.date() - dt.timedelta(days=i)).isoformat() for i in (0, 1, 2, 4, 5, 6, 7, 8)]   # day -3 missing
    db = _toggl_store(tmp_path, days, synced_hours_ago=50, incomplete=1, now=now)
    exp = tmp_path / "cache.json"; exp.write_text(json.dumps({"2026-10-01": {}}))
    r = vh.scan_toggl_store(now, db, exp)
    assert not r["ok"]
    joined = " | ".join(r["problems"])
    assert "no entries on 2026-10-18" in joined and "50 h old" in joined and "1 incomplete" in joined and "export ends 2026-10-01" in joined


def test_toggl_store_missing_file_is_a_review_item(tmp_path, monkeypatch):
    vh = _load()
    monkeypatch.setattr(vh, "TOGGL_STORE", tmp_path / "none.sqlite")
    f = vh.check_toggl_store(dt.datetime(2026, 10, 21, 12, tzinfo=vh.TZ))
    assert f.status == "fail" and f.review and "missing" in f.summary
