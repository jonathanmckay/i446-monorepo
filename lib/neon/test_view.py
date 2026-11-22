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
    ledger.record(_baseline("2026-10-18T03:50:15"), db)         # no baseline, no ledger view
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


def _baseline(ts, date="10/18"):
    return _e(ts=ts, kind="baseline", sheet="0分", col="T", date=date, value="4", before="", after="=4",
              after_value="4.0", before_value="", src="reconcile")


def test_past_day_has_no_live_residual(db, monkeypatch):
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d, "cols": {c: {"derived": 1.0, "unknown": []} for c in view.rules.POINT_COLS + ["D"]},
        "blocks": {"卯": {"lock": 1.0, "observed": 1.0}}, "weights_known": 1, "strict": True})
    ledger.record(_baseline("2026-10-18T03:50:15"), db)         # the day has its baseline (see the tests below)
    ledger.record(_lock("G", 1.0, "2026-10-18T06:00:10"), db)
    v = view.day_view("2026-10-18", db, now=dt.datetime(2026, 10, 19, 11, 30))
    assert v["live_block"] is None and v["blocks"] == {"卯": 1.0}


def test_day_with_no_events_yet_has_no_ledger_view(db, monkeypatch):
    """2026-11-14: from 00:00 until the 03:50 baseline the ledger has no event
    for the new day, derive_day yields 0, and Excel's row already evaluates
    to its formula floor (-46 = W -50 + T 4). dtd and janus logged that as a
    disagreement every midnight. No event for the date = no ledger value:
    day_view returns None, so pick() shows Excel and nothing is logged."""
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d, "cols": {c: {"derived": 0.0, "unknown": []} for c in view.rules.POINT_COLS + ["D"]},
        "blocks": {}, "weights_known": 43, "strict": True})
    assert view.day_view("2026-11-14", db, now=dt.datetime(2026, 11, 14, 0, 0, 7)) is None
    assert view.pick(-46, None) == -46
    assert view.log_reader_diff("dtd", "2026-11-14", -46, None) is False


def test_day_with_events_but_no_baseline_has_no_ledger_view(db, monkeypatch):
    """2026-11-19: the guard above never fired on real data, because a 0n set
    lands at 00:15 every night (allcolors handicap) and the readers kept
    logging -46 vs 0. The test is the 0分 baseline, not "any event"."""
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d, "cols": {c: {"derived": 0.0, "unknown": []} for c in view.rules.POINT_COLS + ["D"]},
        "blocks": {}, "weights_known": 43, "strict": True})
    ledger.record(_e(ts="2026-11-19T00:15:09", kind="write", sheet="0n", col="AH", row=324, date="11/19",
                     value="1", before="", after="1", after_value="1.0", before_value="",
                     src="allcolors auto: handicap"), db)
    assert db.execute("SELECT COUNT(*) FROM events WHERE date='2026-11-19'").fetchone()[0] == 1
    assert view.day_view("2026-11-19", db, now=dt.datetime(2026, 11, 19, 0, 15, 30)) is None
    ledger.record(_baseline("2026-11-19T03:50:15", date="11/19"), db)
    assert view.day_view("2026-11-19", db, now=dt.datetime(2026, 11, 19, 3, 51)) is not None


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


# ── watch_view (neg1n watch complications, 2026-11-22) ───────────────────────

def _flat_day(monkeypatch, total=20.0):
    monkeypatch.setattr(view.rules, "derive_day", lambda d, c=None, strict=False: {
        "date": d, "cols": {**{c: {"derived": 0.0, "unknown": []} for c in view.rules.POINT_COLS},
                            "D": {"derived": total, "unknown": []}},
        "blocks": {}, "weights_known": 43, "strict": True})


def _obs(sheet, col, row, formula, value, ts="2026-11-22T03:50:20"):
    return _e(ts=ts, kind="baseline", sheet=sheet, col=col, row=row, date="11/22", value=formula,
              before="", after=formula, after_value=str(value), before_value="", src="reconcile")


def _watch_day(db):
    ledger.record(_baseline("2026-11-22T03:50:15", date="11/22"), db)
    ledger.record(_obs("hcbi", "U", 328, "=SUM(AL328,AO328)", 0), db)
    ledger.record(_obs("hcbi", "Y", 328, "='0n'!AE327*2  + 0", 0), db)
    ledger.record(_obs("hcbi", "AA", 328, "=-50+W328", -50), db)
    ledger.record(_obs("0n", "AP", 327, "", ""), db)


def test_watch_view_before_anything_is_logged(db, monkeypatch):
    _flat_day(monkeypatch, total=-46.0)
    _watch_day(db)
    w = view.watch_view("2026-11-22", db)
    assert w["points"] == -46.0
    assert w["calories"] is None            # nothing eaten: no number, as the old cache had it
    assert w["hcbp_hcbc"] == -50.0
    assert w["prayers"] is None
    assert w["hcmp_minutes"] == 0.0         # the row is known, nothing logged


def test_watch_view_follows_writes_without_excel(db, monkeypatch):
    _flat_day(monkeypatch)
    _watch_day(db)
    ledger.record(_e(ts="2026-11-22T08:05:00", kind="append", sheet="hcbi", col="AL", row=328, date="11/22",
                     value="+420", before="", after="=420", after_value="420.0", before_value="",
                     src="ate mocha"), db)
    ledger.record(_e(ts="2026-11-22T08:06:00", kind="append", sheet="hcbi", col="W", row=328, date="11/22",
                     value="+15", before="", after="=15", after_value="15.0", before_value="",
                     src="did hcbp"), db)
    for ts, n in (("2026-11-22T06:10:00", 2), ("2026-11-22T12:40:00", 5)):
        ledger.record(_e(ts=ts, kind="write", sheet="0n", col="AP", row=327, date="11/22", value=str(n),
                         before="", after=str(n), after_value=f"{n}.0", before_value="", src="salat"), db)
    ledger.record(_e(ts="2026-11-22T07:00:00", kind="write", sheet="0n", col="AR", row=327, date="11/22",
                     value="20", before="", after="20", after_value="20.0", before_value="", src="did 冥想"), db)
    w = view.watch_view("2026-11-22", db)
    assert w["calories"] == 420.0
    assert w["hcbp_hcbc"] == -35.0          # AA = -50 + W(15), Y = 0
    assert w["prayers"] == 5.0              # the last set, not a sum of sets
    assert w["hcmp_minutes"] == 20.0
    assert w["through"] == "2026-11-22T12:40:00"


def test_watch_view_none_without_baseline_and_never_calls_the_daemon(db, monkeypatch):
    _flat_day(monkeypatch)
    monkeypatch.setattr(view, "pending_writes", lambda: (_ for _ in ()).throw(AssertionError("daemon call")))
    assert view.watch_view("2026-11-22", db) is None
    _watch_day(db)
    assert view.watch_view("2026-11-22", db) is not None


def test_watch_view_survives_an_unseeded_sheet(db, monkeypatch):
    """A partial 03:50 reconcile: 0分 has its baseline, hcbi and 0n have none."""
    _flat_day(monkeypatch)
    ledger.record(_baseline("2026-11-22T03:50:15", date="11/22"), db)
    w = view.watch_view("2026-11-22", db)
    assert w["points"] == 20.0
    assert (w["calories"], w["hcbp_hcbc"], w["prayers"], w["hcmp_minutes"]) == (None, None, None, None)


def test_watch_view_missing_db(tmp_path, monkeypatch):
    p = tmp_path / "nope" / "neon.db"
    monkeypatch.setattr(view.ledger, "DB_PATH", p)
    assert view.watch_view("2026-11-22") is None and not p.exists()
