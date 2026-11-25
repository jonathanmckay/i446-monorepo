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


def test_net_probe_link_alerts_never_make_a_review_item(tmp_path, monkeypatch):
    """2026-11-14: Straylight wifi drops (net-probe) are real-time notices, not weekly decisions."""
    vh = _load()
    now = dt.datetime(2026, 11, 14, 21, tzinfo=vh.TZ)
    probe = {"ts": "2026-11-12T14:50:47Z", "tool": "net-probe", "reason": "link_degraded",
             "severity": "warning", "detail": "MSFTCONNECT: loss 100.0%"}
    p = tmp_path / "alerts.jsonl"; p.write_text(json.dumps(probe))
    monkeypatch.setattr(vh, "ALERTS", p)
    monkeypatch.setattr(vh, "DISMISSED", tmp_path / "none.json")
    f = vh.check_alerts(now)
    assert f.status == "ok" and not f.review and "net-probe" in f.detail[0]
    other = {"ts": "2026-11-13T10:00:00Z", "tool": "triage", "reason": "apply_failed", "severity": "warning", "detail": "x"}
    p.write_text(json.dumps(probe) + "\n" + json.dumps(other))
    f = vh.check_alerts(now)
    assert f.status == "warn" and f.review and "1 alert(s)" in f.summary
    assert "triage" in f.detail[0] and f.detail[-1].startswith("not for review")


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


def test_duplicates_check_reviews_name_clashes_without_deleting(tmp_path, monkeypatch):
    vh = _load()
    t = vh._load_triage()
    a = tmp_path / "x.pdf"; a.write_bytes(b"one")
    c = tmp_path / "x (1).pdf"; c.write_bytes(b"two")
    monkeypatch.setattr(t, "STATE", tmp_path)
    monkeypatch.setattr(t, "TRASH", tmp_path / "trash")
    monkeypatch.setattr(t, "DUPES", tmp_path / "duplicates.json")
    t.DUPES.write_text(json.dumps([{"path": str(c), "original": str(a), "rule": "name-clash", "size": 3}]))
    monkeypatch.setattr(vh, "_load_triage", lambda: t)
    monkeypatch.setattr(vh, "APPLY", True)
    f = vh.check_duplicates()
    assert c.exists() and f.review and f.status == "warn" and "x (1).pdf vs x.pdf" in f.detail[0]


def test_time_machine_falls_back_to_prefs_plist_without_fda():
    import plistlib, datetime as _dt
    vh = _load()
    recent = _dt.datetime.utcnow() - _dt.timedelta(hours=3)
    xml = plistlib.dumps({"Destinations": [{"SnapshotDates": [recent - _dt.timedelta(days=5), recent]}]}).decode()
    def host(c):
        if "destinationinfo" in c: return "Name : Ix Backup"
        if "latestbackup" in c: return "tmutil: latestbackup requires Full Disk Access privileges."
        return xml
    s, d, r = vh._tm(host)
    assert s == "ok" and not r and d == ["latest backup 0d ago"]
    assert vh._tm(lambda c: "garbage" if "defaults" in c else host(c))[0] == "warn"


def test_dismissed_alerts_and_neon_days_are_skipped(tmp_path, monkeypatch):
    import datetime as _dt, json as _j
    vh = _load()
    now = _dt.datetime(2026, 11, 1, 12, tzinfo=vh.TZ)
    alerts = tmp_path / "alerts.jsonl"
    alerts.write_text("\n".join(_j.dumps(a) for a in [
        {"ts": "2026-10-30T10:00:00Z", "tool": "t", "reason": "r", "severity": "warning"},
        {"ts": "2026-10-31T10:00:00Z", "tool": "t", "reason": "r", "severity": "warning"}]))
    dis = tmp_path / "dismissed.json"
    dis.write_text(_j.dumps({"alerts": [{"tool": "t", "reason": "r", "through": "2026-10-30T12:00:00+00:00", "why": "x"}],
                             "neon-rules": [{"column": "D", "date": "2026-10-27", "why": "x"}]}))
    monkeypatch.setattr(vh, "ALERTS", alerts); monkeypatch.setattr(vh, "DISMISSED", dis)
    f = vh.check_alerts(now)
    assert f.summary == "1 alert(s) in 7 days"          # the later one still surfaces
    rd = tmp_path / "rd"; rd.mkdir()
    (rd / "2026-10-27.json").write_text(_j.dumps({"summary": {"mismatch": ["D"]}}))
    (rd / "2026-10-31.json").write_text(_j.dumps({"summary": {"mismatch": []}}))
    monkeypatch.setattr(vh, "NEON_DIFF_DIR", rd)
    assert vh.check_neon_rules(now).status == "ok"
    (rd / "2026-10-30.json").write_text(_j.dumps({"summary": {"mismatch": ["D"]}}))
    assert vh.check_neon_rules(now).status == "warn"   # a new D day is not dismissed


def _perm_state(d, host, synced_at, merged):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{host}.json").write_text(json.dumps({"host": host, "synced_at": synced_at, "allow": ["a"], "merged": merged}))


def test_perm_sync_reads_synced_state_files(tmp_path, monkeypatch):
    vh = _load()
    now = dt.datetime(2026, 11, 8, 12, tzinfo=vh.TZ); t = now.timestamp()
    monkeypatch.setattr(vh, "PERM_SYNC_DIR", tmp_path)
    _perm_state(tmp_path, "ix", t - 3600, {"straylight": t - 7200})
    _perm_state(tmp_path, "straylight", t - 7200, {"ix": t - 10800})
    assert vh.check_perm_sync(now).status == "ok"


def test_perm_sync_warns_when_straylight_never_ran(tmp_path, monkeypatch):
    vh = _load()
    now = dt.datetime(2026, 11, 8, 12, tzinfo=vh.TZ)
    monkeypatch.setattr(vh, "PERM_SYNC_DIR", tmp_path)
    _perm_state(tmp_path, "ix", now.timestamp(), {})
    f = vh.check_perm_sync(now)
    assert f.status == "warn" and f.review and "straylight: no state file" in f.summary
    assert "install-perm-sync.sh" in f.summary


def test_perm_sync_warns_on_one_way_syncthing_lag(tmp_path, monkeypatch):
    vh = _load()
    now = dt.datetime(2026, 11, 8, 12, tzinfo=vh.TZ); t = now.timestamp()
    monkeypatch.setattr(vh, "PERM_SYNC_DIR", tmp_path)
    _perm_state(tmp_path, "ix", t - 600, {"straylight": t - 1200})
    _perm_state(tmp_path, "straylight", t - 1200, {"ix": t - 5 * 86400})  # never sees Ix's new state
    f = vh.check_perm_sync(now)
    assert f.status == "warn" and "straylight last merged ix's state" in f.summary


def test_lease_signer_finding_states():
    vh = _load()
    now = dt.datetime(2026, 11, 14, 4, 40, tzinfo=vh.TZ)
    ok = "RUNNING\nSESSION\n{\"ts\": \"2026-11-13T07:13:35\", \"source\": \"sign\"}\n"
    f = vh.lease_signer_finding(ok, now)
    assert f.status == "ok" and not f.review and "via sign" in f.summary
    f = vh.lease_signer_finding("STOPPED\nSESSION\n" + ok.split("SESSION\n")[1], now)
    assert f.status == "fail" and f.review and "kickstart" in f.detail[0]
    f = vh.lease_signer_finding("RUNNING\nALERT\nSESSION\n" + ok.split("SESSION\n")[1], now)
    assert f.status == "fail" and "relogin.sh" in f.detail[0]
    stale = "RUNNING\nSESSION\n{\"ts\": \"2026-11-09T07:00:00\", \"source\": \"probe\"}\n"
    f = vh.lease_signer_finding(stale, now)
    assert f.status == "warn" and f.review and "4d ago" in f.summary
    f = vh.lease_signer_finding("RUNNING\nSESSION\n\n", now)
    assert f.status == "warn" and "never probed" in f.summary
    f = vh.lease_signer_finding("ERROR: ssh timed out", now)
    assert f.status == "warn" and "could not reach Ix" in f.summary


# ── launchd agents stopped on a failed exit (2026-11-21) ────────────────────
# Bug: com.jm.lease-report failed daily on a deleted script and
# com.jm.extract-creds had pointed at a vanished /tmp script since May; no
# check read launchd exit codes, so neither was ever seen.
_LAUNCHD = """gui/501 = {
	services = {
		       0      0 	com.jm.claude-export
		    2158      - 	com.jm.neg1n-status
		   28663    -15 	com.jm.dashboard-personal
		       0      2 	com.jm.lease-report
		       0     78 	com.jm.extract-creds
		       0      - 	com.jm.never-ran
		       0      1 	com.apple.not-ours
	}
}
"""


def test_launchd_flags_only_stopped_agents_with_a_failed_exit():
    vh = _load()
    seen, bad = vh.launchd_failing(_LAUNCHD)
    assert seen == 6                                   # com.apple.* is not ours
    assert bad == [("com.jm.extract-creds", 78), ("com.jm.lease-report", 2)]
    f = vh.launchd_finding(_LAUNCHD, {"com.jm.lease-report": "/x/lease_report.py"})
    assert f.status == "warn" and f.review and "2 of 6" in f.summary
    assert any("lease-report: last exit 2; its program is gone: /x/lease_report.py" in d for d in f.detail)
    assert not any("dashboard-personal" in d or "never-ran" in d for d in f.detail)


def test_launchd_ok_when_clean_and_never_ok_when_unreadable(monkeypatch):
    vh = _load()
    clean = _LAUNCHD.replace("      2 \tcom.jm.lease-report", "      0 \tcom.jm.lease-report").replace(
        "     78 \tcom.jm.extract-creds", "      0 \tcom.jm.extract-creds")
    f = vh.launchd_finding(clean)
    assert f.status == "ok" and not f.review
    for unreadable in ("", "ERROR: ssh timed out", "Could not find domain for user gui: 501"):
        f = vh.launchd_finding(unreadable)
        assert f.status == "warn" and f.review and "could not read" in f.summary
    monkeypatch.setitem(vh.LAUNCHD_OK, "com.jm.lease-report", "known")
    assert vh.launchd_failing(_LAUNCHD)[1] == [("com.jm.extract-creds", 78)]


def test_launchd_check_is_registered_and_described():
    vh = _load()
    assert "launchd" in vh.CHECK_WHAT
    assert "check_launchd" in (HERE / "vault_health.py").read_text().split("def run_all")[1].split("def ")[0]


def _gh_data(counted=None, mono=None, vault=None, restricted=900, week=None, base=300):
    """A _gh_query() answer as of 2026-11-22 16:00Z; healthy unless overridden."""
    def repo(full, default, mine, ahead, private=False, head="2026-11-22T15:50:00Z", dhead="2026-11-22T15:50:00Z", login="jonathanmckay"):
        return {"nameWithOwner": full, "isPrivate": private, "defaultBranchRef": {"name": default, "compare": {"aheadBy": ahead}, "target": {
                    "committedDate": dhead, "mine": {"totalCount": mine}}},
                "push": {"target": {"history": {"nodes": [{"committedDate": head, "author": {"user": {"login": login}}}]}}}}
    counted = {"jonathanmckay/i446-monorepo": 40} if counted is None else counted
    counts = [base] * 28 + (week or [300] * 7) + [5]            # 28 baseline days, the week, partial today
    d0 = dt.date(2026, 11, 22) - dt.timedelta(days=35)
    cal = [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "contributionCount": c} for i, c in enumerate(counts)]
    return {"viewer": {"login": "jonathanmckay", "cal": {"contributionCalendar": {"weeks": [
                {"contributionDays": cal[i:i + 7]} for i in range(0, len(cal), 7)]}}, "contributionsCollection": {"restrictedContributionsCount": restricted, "commitContributionsByRepository": [
                {"repository": {"nameWithOwner": k}, "contributions": {"totalCount": v}} for k, v in counted.items()]}},
            "r0": repo("jonathanmckay/i446-monorepo", "main", **{"mine": 40, "ahead": 12, **(mono or {})}),
            "r1": repo("jonathanmckay/vault", "main", **{"mine": 900, "ahead": 0, "private": True, **(vault or {})})}


_GH_NOW = dt.datetime(2026, 11, 22, 16, 0, tzinfo=dt.timezone.utc)


def test_github_contrib_ok_when_pushes_land_and_count():
    vh = _load()
    f = vh.github_contrib_finding(_gh_data(), _GH_NOW)
    assert f.status == "ok" and not f.review
    assert f.summary == "i446-monorepo 40 counted; vault 900 counted; 300/day on the graph in 7 days"
    assert any("900 private contributions counted (all private repos)" in d for d in f.detail)


def test_github_contrib_flags_wip_stranded_off_the_default_branch():
    # 2026-11-22: main last moved 09-24, 4,238 wip snapshots counted for nothing.
    vh = _load()
    data = _gh_data(counted={}, mono={"mine": 0, "ahead": 4238, "dhead": "2026-09-24T20:00:00Z"})
    f = vh.github_contrib_finding(data, _GH_NOW)
    assert f.status == "warn" and f.review
    assert any("4238 commits sit on wip" in d and "59 days" in d for d in f.detail)
    assert not any("vault:" in d and "credited 0" in d for d in f.detail)


def test_github_contrib_flags_uncounted_stale_and_unlinked():
    vh = _load()
    # A private repo is credited only through the anonymous private total (2026-11-22:
    # vault showed 0 until 'Include private contributions on my profile' was on).
    f = vh.github_contrib_finding(_gh_data(restricted=0), _GH_NOW)
    assert f.status == "warn" and any("Include private contributions" in d for d in f.detail) and any("vault: 900 of my commits reached main" in d and "credited 0" in d for d in f.detail)
    f = vh.github_contrib_finding(_gh_data(vault={"head": "2026-11-20T10:00:00Z"}), _GH_NOW)
    assert f.status == "warn" and any("nothing has reached main on GitHub in 2.2 days" in d for d in f.detail)
    f = vh.github_contrib_finding(_gh_data(mono={"login": "someone-else"}), _GH_NOW)
    assert f.status == "fail" and any("not attributed to jonathanmckay" in d for d in f.detail)


def test_github_contrib_dismissal_keeps_the_line_but_drops_the_review():
    vh = _load()
    data = _gh_data(restricted=0)
    f = vh.github_contrib_finding(data, _GH_NOW, [{"repo": "jonathanmckay/vault", "rule": "uncounted"}])
    assert f.status == "ok" and not f.review
    assert any("credited 0" in d and d.endswith("(dismissed)") for d in f.detail)


def test_github_contrib_never_ok_when_github_is_unreadable():
    vh = _load()
    f = vh.github_contrib_finding({"_error": "HTTP 401: Bad credentials"}, _GH_NOW)
    assert f.status == "warn" and f.review and "could not query GitHub" in f.summary


def test_github_contrib_check_is_registered_and_described():
    vh = _load()
    assert "github-contrib" in vh.CHECK_WHAT
    assert "check_github_contrib" in (HERE / "vault_health.py").read_text().split("def run_all")[1].split("def ")[0]
    q = vh._gh_query(_GH_NOW, "U_x")
    assert q.count("{") == q.count("}") and 'headRef:"wip"' in q and 'since:"2026-11-22T16:00:00Z"' in q
    assert 'cal: contributionsCollection(from:"2026-10-24T16:00:00Z",to:"2026-11-29T16:00:00Z")' in q


def test_github_contrib_flags_an_unreasonable_week():
    # JM 2026-11-22: "make sure that I'm getting a reasonable number of contributions".
    vh = _load()
    f = vh.github_contrib_finding(_gh_data(week=[300, 300, 0, 300, 0, 300, 300]), _GH_NOW)
    assert f.status == "warn" and f.review and any("no contribution at all on 11-17, 11-19" in d for d in f.detail)
    assert not any("is under" in d for d in f.detail)            # 1500 of a usual 2100 is fine
    f = vh.github_contrib_finding(_gh_data(week=[100] * 7), _GH_NOW)
    assert f.status == "warn" and any("700 contributions this week is under 50% of the usual 2100" in d for d in f.detail)
    # Credited for under half of what a repo sent (was: only a flat zero warned).
    f = vh.github_contrib_finding(_gh_data(counted={"jonathanmckay/i446-monorepo": 15}), _GH_NOW)
    assert f.status == "warn" and any("i446-monorepo: 40 of my commits reached main" in d and "credited 15" in d for d in f.detail)
    f = vh.github_contrib_finding(_gh_data(week=[100] * 7), _GH_NOW, [{"repo": "*", "rule": "low-week"}])
    assert f.status == "ok" and not f.review


def test_github_contrib_ignores_a_calendar_from_the_wrong_weeks():
    # 2026-11-22: asked without `to`, GitHub returned October days; the check
    # reported "0/day" and seven zero days on a 300-a-day week.
    vh = _load()
    data = _gh_data()
    for w in data["viewer"]["cal"]["contributionCalendar"]["weeks"]:
        for d in w["contributionDays"]:
            d["date"] = (dt.date.fromisoformat(d["date"]) - dt.timedelta(days=40)).isoformat()
    f = vh.github_contrib_finding(data, _GH_NOW)
    assert f.status == "warn" and any("returned no contribution calendar" in d for d in f.detail)
    assert not any("no contribution at all" in d for d in f.detail)


# ── onedrive-neon: other Macs' copies, from their hourly stamps (2026-11-22) ──

def _stamp(tmp_path, host, synced_at, neon="absent"):
    d = {"host": host, "synced_at": synced_at, "allow": [], "tombstones": [], "merged": {}}
    if neon != "absent":
        d["neon"] = neon
    (tmp_path / f"{host}.json").write_text(json.dumps(d))


def test_neon_peer_copy_six_weeks_behind_is_called_out(tmp_path, monkeypatch):
    """Straylight's copy stopped syncing on 10-12; the review on Ix said "fresh"."""
    vh = _load()
    monkeypatch.setattr(vh.socket, "gethostname", lambda: "ix.local")
    now = dt.datetime(2026, 11, 22, 8, 30, tzinfo=vh.TZ)
    stale = dt.datetime(2026, 10, 12, 12, 34, tzinfo=vh.TZ).timestamp()
    _stamp(tmp_path, "straylight", now.timestamp() - 600, {"mtime": int(stale), "error": None})
    r = vh._neon_peer_copies(now, now.timestamp() - 60, tmp_path)
    assert r["behind"] == ["straylight"] and r["unknown"] == []
    assert "10-12 12:34" in r["detail"][0] and "41 days behind ix" in r["detail"][0]


def test_neon_peer_copy_in_step_or_absent_is_fine(tmp_path, monkeypatch):
    vh = _load()
    monkeypatch.setattr(vh.socket, "gethostname", lambda: "ix.local")
    now = dt.datetime(2026, 11, 22, 8, 30, tzinfo=vh.TZ)
    _stamp(tmp_path, "straylight", now.timestamp() - 600, {"mtime": int(now.timestamp() - 3 * 3600), "error": None})
    assert vh._neon_peer_copies(now, now.timestamp(), tmp_path)["behind"] == []
    _stamp(tmp_path, "straylight", now.timestamp() - 600, {"mtime": None, "error": None})
    r = vh._neon_peer_copies(now, now.timestamp(), tmp_path)
    assert r["behind"] == [] and r["unknown"] == [] and "holds no copy" in r["detail"][0]


def test_neon_peer_without_a_usable_stamp_is_never_reported_in_step(tmp_path, monkeypatch):
    vh = _load()
    monkeypatch.setattr(vh.socket, "gethostname", lambda: "ix.local")
    now = dt.datetime(2026, 11, 22, 8, 30, tzinfo=vh.TZ)
    assert vh._neon_peer_copies(now, now.timestamp(), tmp_path)["unknown"] == ["straylight"]      # no file
    _stamp(tmp_path, "straylight", now.timestamp() - 600)                                          # old-format stamp
    assert vh._neon_peer_copies(now, now.timestamp(), tmp_path)["unknown"] == ["straylight"]
    _stamp(tmp_path, "straylight", now.timestamp() - 72 * 3600, {"mtime": int(now.timestamp()), "error": None})
    assert vh._neon_peer_copies(now, now.timestamp(), tmp_path)["unknown"] == ["straylight"]      # job dead
    _stamp(tmp_path, "straylight", now.timestamp() - 600, {"mtime": None, "error": "PermissionError: Operation not permitted"})
    r = vh._neon_peer_copies(now, now.timestamp(), tmp_path)
    assert r["unknown"] == ["straylight"] and "could not read" in r["detail"][0]


# --- meta-work + skill-prune (2026-11-24, jm-ai-risks risks 4 and 3) ---------
def _meta_store(tmp_path, rows):
    import sqlite3
    p = tmp_path / "toggl.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, day TEXT, seconds INTEGER, project TEXT)")
    c.executemany("INSERT INTO entries (day, seconds, project) VALUES (?, ?, ?)", rows)
    c.commit(); c.close()
    return p


def test_meta_work_share_excludes_sleep_and_ends_two_days_back(tmp_path):
    """JM 2026-11-24: 'do this as a reporting function'. The Saturday 04:40 run
    precedes the 05:10 Toggl sync, so the newest window ends the day before yesterday."""
    vh = _load()
    now = dt.datetime(2026, 11, 24, 18, tzinfo=vh.TZ)
    store = _meta_store(tmp_path, [
        ("2026-11-22", 3600, "i447"), ("2026-11-22", 3600, "infra"), ("2026-11-22", 8 * 3600, "i9"),
        ("2026-11-22", 7 * 3600, "睡觉"), ("2026-11-22", -5, "i447"),      # running timer: ignored
        ("2026-11-23", 9 * 3600, "i447"),                                   # yesterday: outside every window
        ("2026-11-15", 3600, "i447"), ("2026-11-15", 9 * 3600, "xk87"),
    ])
    weeks = vh.scan_meta_work(now, store)
    assert len(weeks) == vh.META_WEEKS
    assert (weeks[0]["start"].isoformat(), weeks[0]["end"].isoformat()) == ("2026-11-16", "2026-11-22")
    assert weeks[0]["meta_h"] == 2 and weeks[0]["wake_h"] == 10 and weeks[0]["share"] == 0.2
    assert weeks[1]["share"] == 0.1 and weeks[2]["share"] is None
    f = vh.meta_work_finding(weeks)
    assert f.check == "meta-work" and f.status == "ok" and not f.review
    assert "20.0%" in f.summary and "up from the prior 4-week mean 10.0%" in f.summary


def test_meta_work_warns_only_past_a_set_cap_and_never_asks_for_review(tmp_path, monkeypatch):
    vh = _load()
    weeks = [{"start": dt.date(2026, 11, 16), "end": dt.date(2026, 11, 22), "meta_h": 9.0, "wake_h": 100.0, "share": 0.09}]
    assert vh.meta_work_finding(weeks).status == "ok"
    monkeypatch.setattr(vh, "META_CAP", 0.06)
    f = vh.meta_work_finding(weeks)
    assert f.status == "warn" and not f.review and "EXCEEDED" in f.summary
    empty = [{"start": dt.date(2026, 11, 16), "end": dt.date(2026, 11, 22), "meta_h": 0.0, "wake_h": 0.0, "share": None}]
    assert vh.meta_work_finding(empty).status == "ok"


def test_skill_uses_parsed_from_all_three_transcript_signals():
    vh = _load()
    out = ('<command-name>/d</command-name>\n<command-name>-1l</command-name>\n'
           '"name":"Skill","input":{"skill":"auton156"\n"attributionSkill":"1-1n"\n')
    assert vh.parse_skill_uses(out) == {"d", "-1l", "auton156", "1-1n"}


def test_skill_prune_candidates_rules():
    vh = _load()
    day = 86400.0
    now_ts = 1000 * day
    texts = {"used": "run /helper first, see ~/tools/pathy/x.py", "helper": "h", "old": "o", "older": "o",
             "new": "n", "kept": "k", "back": "b", "pathy": "p", "untracked": "u"}
    created = {n: now_ts - 90 * day for n in texts if n != "untracked"}
    created["new"] = now_ts - 5 * day
    created["older"] = now_ts - 200 * day
    resurrected, cands = vh.skill_prune_candidates(texts, created, used={"used"}, keep={"kept"},
                                                   archived={"back"}, now_ts=now_ts)
    assert resurrected == ["back"]
    # helper is called by a used skill; new is under 30 days; kept was answered; untracked has no git date.
    # pathy only appears inside a file path in the used skill, which is not a call.
    assert cands == ["older", "old", "pathy"]


def test_skill_prune_finding_caps_the_list_and_reads_keep_answers(tmp_path, monkeypatch):
    vh = _load()
    skills = tmp_path / "skills"
    for n in ["d", "a1", "a2", "a3", "a4", "a5", "a6", "keepme", "gone"]:
        (skills / n).mkdir(parents=True); (skills / n / "SKILL.md").write_text(n)
    (skills / "_archive" / "gone").mkdir(parents=True)
    dismissed = tmp_path / "dismissed.json"; dismissed.write_text(json.dumps({"skill_keep": ["keepme"]}))
    now = dt.datetime(2026, 11, 24, 18, tzinfo=vh.TZ)
    monkeypatch.setattr(vh, "SKILLS", skills)
    monkeypatch.setattr(vh, "DISMISSED", dismissed)
    monkeypatch.setattr(vh, "_skill_created", lambda: {n: now.timestamp() - 90 * 86400 for n in
                                                       ["d", "a1", "a2", "a3", "a4", "a5", "a6", "keepme", "gone"]})
    monkeypatch.setattr(vh, "_agent_claims", lambda: type("AC", (), {"SKILL_CLAIMS": {}})())
    f = vh.check_skill_prune(now, grep_out="<command-name>/d</command-name>\n")
    assert f.check == "skill-prune" and f.status == "warn" and f.review
    assert "1 retired skill(s) live again" in f.summary and "6 of 9 skills unused" in f.summary
    assert sum("no use in 60 days" in d for d in f.detail) == vh.SKILL_PRUNE_MAX
    assert any("1 more unused" in d for d in f.detail) and not any("/keepme" in d for d in f.detail)
    # No usable transcript read: say so instead of calling every skill unused.
    assert "could not read" in vh.check_skill_prune(now, grep_out="ERROR: timeout").summary
