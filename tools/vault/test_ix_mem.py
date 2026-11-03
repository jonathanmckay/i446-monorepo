#!/usr/bin/env python3
"""ix-memory: sampler parsing (ps/top/jetsam), 7-day summary, and the vault_health finding."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod


PS = """47751     1 Mon Nov  2 03:00:03 2026     /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
47790 47751 Mon Nov  2 03:00:04 2026     /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer
51000 50990 Tue Nov  3 01:00:00 2026     /Users/mckay/Library/Caches/ms-playwright/chromium-1/chrome-mac/Chromium.app/Contents/MacOS/Chromium --user-data-dir=/Users/mckay/.config/m5x2/appfolio_browser_state --remote-debugging-pipe
52000 1 Tue Nov  3 02:00:00 2026     /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/login-xyz
53000 1 Tue Nov  3 02:00:00 2026     /usr/bin/grep Google Chrome
"""


def test_parse_browsers_keeps_mains_only_and_reads_user_data_dir():
    m = _load("ix_mem_sample", "ix_mem_sample.py")
    bs = m.parse_browsers(PS)
    assert [(b["pid"], b["exe"], b["udd"]) for b in bs] == [
        (47751, "Google Chrome", "default"),
        (51000, "Chromium", "/Users/mckay/.config/m5x2/appfolio_browser_state"),
        (52000, "Google Chrome", "/tmp/login-xyz")]


def test_parse_top_sums_chrome_footprint_across_helpers():
    m = _load("ix_mem_sample", "ix_mem_sample.py")
    out = "PID    MEM   COMMAND\n1  2.5G+ Google Chrome He\n2  512M  Google Chrome\n3  100M- node\n4  900K  zsh\n"
    mb, n, top = m.parse_top(out)
    assert (mb, n) == (3072, 2) and top[0] == ["Google Chrome", 3072] and top[1] == ["node", 100]


def test_summary_flags_restart_extra_instance_jetsam_and_ignores_old(tmp_path, monkeypatch):
    m = _load("ix_mem_sample", "ix_mem_sample.py")
    monkeypatch.setattr(m, "_run", lambda cmd: "")
    now = dt.datetime(2026, 11, 7, 4, 40).astimezone()
    main = lambda pid, st: {"pid": pid, "ppid": 1, "start": st, "exe": "Google Chrome", "udd": "default"}
    rows = [
        {"ts": "2026-10-20T10:00:00-07:00", "browsers": [main(1, "Mon Oct 19 09:00:00 2026")], "chrome_mb": 20000},
        {"ts": "2026-11-05T10:00:00-08:00", "browsers": [main(2, "Wed Oct 28 09:00:00 2026")], "chrome_mb": 800,
         "swap_mb": 7500, "swap_total_mb": 8192, "boot": 1},
        {"ts": "2026-11-05T10:15:00-08:00", "chrome_mb": 7000, "top": [["Google Chrome", 7000]], "boot": 1,
         "browsers": [main(3, "Thu Nov  5 10:10:00 2026"),
                      {"pid": 9, "ppid": 4, "start": "x", "exe": "Google Chrome", "udd": "/tmp/login-xyz"}]},
    ]
    log = tmp_path / "ix-mem.jsonl"; log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    diag = tmp_path / "diag"; diag.mkdir()
    ips = {"memoryStatus": {"pageSize": 16384}, "largestProcess": "R",
           "processes": [{"name": "R", "rpages": 65536}, {"name": "R", "rpages": 65536}, {"name": "node", "rpages": 10}]}
    (diag / "JetsamEvent-2026-11-04-100000.ips").write_text('{"bug_type":"298"}\n' + json.dumps(ips))
    sm = m.summarize(now, log, diag)
    assert sm["samples"] == 2 and sm["chrome_restarts"] == ["Thu Nov  5 10:10:00 2026"]
    assert list(sm["extra_browsers"]) == ["Google Chrome udd=/tmp/login-xyz"]
    assert sm["chrome_peak_mb"] == 7000 and sm["swap_peak_pct"] == 92
    assert sm["reports"][0]["kind"] == "jetsam" and sm["reports"][0]["top"] == "R 2.0 GB ×2"


def test_ix_memory_finding_quiet_week_is_ok_and_events_are_review_items():
    vh = _load("vault_health", "vault_health.py")
    now = dt.datetime(2026, 11, 7, 4, 40, tzinfo=vh.TZ)
    quiet = {"last": "2026-11-07T04:30:00-08:00", "top": [["claude.exe", 5000]], "automation_browsers": {"Chromium udd=x": {"n": 1}}}
    f = vh.ix_memory_finding(quiet, now)
    assert f.status == "ok" and not f.review
    bad = dict(quiet, reports=[{"kind": "jetsam", "at": "2026-11-04T10:00", "top": "R 23.6 GB ×17"}],
               chrome_restarts=["Thu Nov  5 10:10:00 2026"], boots_in_window=[1793800000],
               extra_browsers={"Google Chrome udd=/tmp/l": {"n": 2, "first": "2026-11-05T10:15", "ppid": 4}})
    f = vh.ix_memory_finding(bad, now)
    assert f.status == "fail" and f.review and f.summary == "4 memory event(s) this week"
    assert any("23.6 GB" in d for d in f.detail) and any("rebooted" in d for d in f.detail)


def test_ix_memory_finding_flags_stale_sampler():
    vh = _load("vault_health", "vault_health.py")
    f = vh.ix_memory_finding({"last": None}, dt.datetime(2026, 11, 7, tzinfo=vh.TZ))
    assert f.status == "warn" and "sampler stale" in f.detail[0]
