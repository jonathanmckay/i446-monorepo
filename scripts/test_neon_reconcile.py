"""scripts/neon-reconcile.py — backfilled days get re-snapshotted."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
_SPEC = importlib.util.spec_from_file_location("neon_reconcile", ROOT / "scripts/neon-reconcile.py")
nr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nr)

from neon import ledger  # noqa: E402


def _ev(ts, date, col="W", kind="append", value="+90", sheet="0分", row=303):
    return {"ts": ts, "kind": kind, "sheet": sheet, "col": col, "row": row, "date": date,
            "value": value, "before": "", "after": "=90", "after_value": "90", "src": "caf 90"}


def test_backfilled_days_are_reconciled_newest_first_capped(tmp_path, monkeypatch):
    db = tmp_path / "neon.db"
    monkeypatch.setattr(ledger, "DB_PATH", db)
    monkeypatch.setattr(ledger, "JSONL_DIR", tmp_path)          # catch_up reads nothing real
    now = dt.datetime.now()
    today = now.date()
    fresh = (now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    stale = (now - dt.timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%S")
    md = lambda d: f"{d.month}/{d.day}"  # noqa: E731
    c = ledger.connect(db)
    for back in (2, 3, 4, 5, 6, 7, 30):                         # 30 days back: out of window
        ledger.record(_ev(fresh, md(today - dt.timedelta(days=back)), row=300 - back), c)
    ledger.record(_ev(stale, md(today - dt.timedelta(days=8)), row=290), c)   # write too old
    ledger.record(_ev(fresh, md(today - dt.timedelta(days=1)), row=299), c)   # yesterday: already covered
    c.commit()
    c.close()
    got = nr.backfilled_dates(today)
    assert got == [today - dt.timedelta(days=b) for b in (2, 3, 4, 5, 6)]


def test_main_posts_backfilled_dates_before_yesterday_and_today(monkeypatch):
    today = dt.date.today()
    monkeypatch.setattr(nr, "backfilled_dates", lambda t: [today - dt.timedelta(days=3)])
    posted = []
    monkeypatch.setattr(nr, "post", lambda body: posted.append(body) or {"ok": True, "changes": []})
    assert nr.main() == 0
    dates = [b["date"] for b in posted if "date" in b]
    d3, d1 = today - dt.timedelta(days=3), today - dt.timedelta(days=1)
    assert dates[:3] == [f"{d3.month}/{d3.day}"] * 3
    assert f"{d1.month}/{d1.day}" in dates and f"{today.month}/{today.day}" in dates


def test_backfilled_dates_failure_falls_back_to_two_days(monkeypatch):
    from neon import ledger as lg
    monkeypatch.setattr(lg, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    assert nr.backfilled_dates(dt.date.today()) == []
