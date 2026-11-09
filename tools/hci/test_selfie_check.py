"""selfie_check.py: decide selfie / none / no-source from a minimal Photos.sqlite."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import selfie_check as sc  # noqa: E402

NOW = dt.datetime(2026, 11, 9, 15, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))


def _apple(ts: dt.datetime) -> float:
    return ts.timestamp() - sc.APPLE_EPOCH


def make_db(path: Path, assets):
    """assets: [(created, added, kind, trashed, lens, faces)]"""
    con = sqlite3.connect(path)
    con.executescript("""
        create table ZASSET (Z_PK integer primary key, ZDATECREATED real, ZADDEDDATE real,
            ZKIND integer, ZTRASHEDSTATE integer, ZHIDDEN integer, ZFILENAME text,
            ZEXTENDEDATTRIBUTES integer);
        create table ZEXTENDEDATTRIBUTES (Z_PK integer primary key, ZLENSMODEL text);
        create table ZDETECTEDFACE (Z_PK integer primary key, ZASSETFORFACE integer);
    """)
    for i, (created, added, kind, trashed, lens, faces) in enumerate(assets, 1):
        con.execute("insert into ZEXTENDEDATTRIBUTES values (?, ?)", (i, lens))
        con.execute("insert into ZASSET values (?,?,?,?,?,0,?,?)",
                    (i, _apple(created), _apple(added), kind, trashed, f"IMG_{i}.HEIC", i))
        for _ in range(faces):
            con.execute("insert into ZDETECTEDFACE (ZASSETFORFACE) values (?)", (i,))
    con.commit(); con.close()
    return path


def _run(db, tmp_path, *extra):
    log = tmp_path / "log.jsonl"
    rc = sc.main(["--db", str(db), "--log", str(log), *extra])
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    return rc, recs[-1]


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch):
    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)
    monkeypatch.setattr(sc.dt, "datetime", _DT)


def test_selfie_found(tmp_path, monkeypatch):
    recent = NOW - dt.timedelta(minutes=40)
    db = make_db(tmp_path / "p.sqlite", [
        (recent, recent, 0, 0, "iPhone 15 Pro front camera 2.69mm f/1.9", 1),
    ])
    called = []
    monkeypatch.setattr(sc, "dock", lambda *a, **k: called.append(a))
    rc, rec = _run(db, tmp_path, "--apply")
    assert rc == 0 and rec["status"] == "selfie" and rec["docked"] == 0
    assert "front" in rec["match"]["lens"] and not called


def test_none_docks_when_library_live(tmp_path, monkeypatch):
    old = NOW - dt.timedelta(hours=5)             # outside the 2h window
    fresh_add = NOW - dt.timedelta(hours=1)       # library is receiving assets
    db = make_db(tmp_path / "p.sqlite", [
        (old, fresh_add, 0, 0, "front camera", 1),
        (NOW - dt.timedelta(minutes=10), fresh_add, 0, 0, "back camera", 0),   # no face
        (NOW - dt.timedelta(minutes=5), fresh_add, 1, 0, "front camera", 1),   # video
        (NOW - dt.timedelta(minutes=5), fresh_add, 0, 1, "front camera", 1),   # trashed
    ])
    called = []
    monkeypatch.setattr(sc, "dock", lambda pts, d: called.append((pts, d)) or {"ok": True})
    rc, rec = _run(db, tmp_path, "--apply")
    assert rec["status"] == "none" and rec["docked"] == 5
    assert called == [(5, "11/9")]


def test_no_source_when_library_stale(tmp_path, monkeypatch):
    stale = NOW - dt.timedelta(days=12)
    db = make_db(tmp_path / "p.sqlite", [(stale, stale, 0, 0, "front camera", 1)])
    called = []
    monkeypatch.setattr(sc, "dock", lambda *a, **k: called.append(a))
    rc, rec = _run(db, tmp_path, "--apply")
    assert rc == 0 and rec["status"] == "no-source" and not called
    rc, rec = _run(db, tmp_path, "--apply", "--dock-when-stale")
    assert rec["docked"] == 5 and called


def test_missing_db_logs_error(tmp_path):
    rc, rec = _run(tmp_path / "nope.sqlite", tmp_path)
    assert rc == 1 and rec["status"] == "error" and "FileNotFoundError" in rec["error"]
