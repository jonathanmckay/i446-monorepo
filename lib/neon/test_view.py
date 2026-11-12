"""lib/neon/view.py — ledger day view for readers (Neon off Excel step 4a)."""
from __future__ import annotations

import datetime as dt

import pytest

from neon import ledger, view


@pytest.fixture()
def db(tmp_path):
    c = ledger.connect(tmp_path / "neon.db")
    yield c
    c.close()


def _e(**kw):
    base = {"ts": "2026-10-18T08:30:00", "kind": "append", "sheet": "0分", "col": "R", "row": 289,
            "date": "10/18", "value": "+15", "before": "='0n'!BK292 + 0", "after": "='0n'!BK292 + 0+15",
            "after_value": "85.0", "before_value": "70.0", "src": "did x", "chain": "ok"}
    base.update(kw)
    return base


def _lock(col, value, ts):
    return _e(ts=ts, kind="write", sheet="0分", col=col, value=str(value), before="=D289-SUM(G289:H289)",
              after=str(value), after_value=str(value), before_value=str(value),
              src=f"block-turnover lock {col}")


def test_day_view_uses_derived_and_locks(db, monkeypatch):
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d,
        "cols": {"P": {"derived": 66.0, "unknown": []}, "Q": {"derived": 44.0, "unknown": []},
                 "R": {"derived": 403.0, "unknown": ["'1n+'!AB5"]}, "S": {"derived": None, "unknown": []},
                 "T": {"derived": 0.0, "unknown": []}, "U": {"derived": 0.0, "unknown": []},
                 "V": {"derived": 0.0, "unknown": []}, "W": {"derived": 0.0, "unknown": []},
                 "X": {"derived": 0.0, "unknown": []}, "Y": {"derived": 0.0, "unknown": []},
                 "D": {"derived": 513.0, "unknown": []}},
        "blocks": {"卯": {"lock": None, "observed": None}, "辰": {"lock": 12.0, "observed": 12.0},
                   "巳": {"lock": 395.571, "observed": 395.571}, "午": {"lock": None, "observed": 99.0}},
        "weights_known": 43, "strict": True})
    for e in (_lock("H", 12, "2026-10-18T08:00:10"), _lock("I", 395.571, "2026-10-18T10:00:10")):
        ledger.record(e, db)
    now = dt.datetime(2026, 10, 18, 11, 30)
    v = view.day_view("2026-10-18", db, now=now)
    assert v["total"] == 513.0
    assert v["cols"]["R"] == 403.0 and "S" in v["pending"]
    assert v["unknown"] == ["'1n+'!AB5"]
    # locked blocks at their lock value; the live block (午 at 11:30) is the residual
    assert v["blocks"]["辰"] == 12.0 and v["blocks"]["巳"] == 395.571
    assert v["live_block"] == "午"
    assert v["blocks"]["午"] == round(513.0 - 12.0 - 395.571, 3)
    assert "未" not in v["blocks"]
    assert v["through"] == "2026-10-18T10:00:10"


def test_past_day_has_no_live_residual(db, monkeypatch):
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d, "cols": {c: {"derived": 1.0, "unknown": []} for c in view.rules.POINT_COLS + ["D"]},
        "blocks": {"卯": {"lock": 1.0, "observed": 1.0}}, "weights_known": 1, "strict": True})
    v = view.day_view("2026-10-18", db, now=dt.datetime(2026, 10, 19, 11, 30))
    assert v["live_block"] is None and v["blocks"] == {"卯": 1.0}


def test_missing_db_returns_none_without_creating_it(tmp_path, monkeypatch):
    p = tmp_path / "nope" / "neon.db"
    monkeypatch.setattr(view.ledger, "DB_PATH", p)
    assert view.day_view("2026-10-18") is None
    assert not p.exists()


def test_derivation_error_returns_none(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(view.rules, "derive_day", boom)
    assert view.day_view("2026-10-18", db) is None


def test_pick_defaults_to_excel(monkeypatch):
    monkeypatch.delenv(view.READ_SOURCE_ENV, raising=False)
    assert view.pick(10, 12) == 10
    monkeypatch.setenv(view.READ_SOURCE_ENV, "ledger")
    assert view.pick(10, 12) == 12
    assert view.pick(10, None) == 10
