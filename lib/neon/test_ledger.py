"""lib/neon/ledger.py — semantic double-write of the Neon JSONL audit log."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

from neon import ledger

T = dt.datetime(2026, 10, 19, 8, 30, 0)


def _e(**kw):
    base = {"ts": "2026-10-19T08:30:00", "kind": "append", "sheet": "0分", "col": "R",
            "row": 290, "date": "10/19", "value": "+15", "before": "='0n'!BK290",
            "after": "='0n'!BK290+15", "after_value": "85.0", "before_value": "70.0",
            "src": "did stats", "chain": "ok", "source": "cli", "via": "did-fast"}
    base.update(kw)
    return base


@pytest.fixture()
def db(tmp_path):
    c = ledger.connect(tmp_path / "neon.db")
    yield c
    c.close()


# ── date / year inference ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,ts,expected", [
    ("10/19", dt.datetime(2026, 10, 19, 8), "2026-10-19"),
    ("10/18", dt.datetime(2026, 10, 19, 1), "2026-10-18"),     # post-midnight write
    ("12/31", dt.datetime(2027, 1, 1, 0, 5), "2026-12-31"),    # old year wins
    ("1/1", dt.datetime(2026, 12, 31, 23, 50), "2027-01-01"),  # new year wins
    ("2/29", dt.datetime(2029, 3, 1, 0), "2028-02-29"),        # invalid year skipped
    ("2026-09-18", dt.datetime(2026, 10, 1), "2026-09-18"),    # ISO passthrough
    (None, T, None),
    ("", T, None),
    ("r290", T, None),
])
def test_resolve_date(raw, ts, expected):
    assert ledger.resolve_date(raw, ts) == expected


# ── classification ───────────────────────────────────────────────────────────

def test_literal_append_is_credit_with_literal_points_not_cell_delta():
    # before→after moved 70→85 here, but the cell also carries 0n flow; the
    # credit is what the caller asked for, the values are observations only.
    r = ledger.to_row(_e(value="+15", before_value="0", after_value="85.0"))
    assert r["kind"] == "credit" and r["points"] == 15.0
    assert r["after_value"] == 85.0 and r["before_value"] == 0.0
    assert r["field"] == "i9" and r["date"] == "2026-10-19" and r["block"] == "巳"


def test_negative_append_is_negative_credit():
    r = ledger.to_row(_e(value="-7.5", col="W", before_value=None, after_value=None))
    assert r["kind"] == "credit" and r["points"] == -7.5 and r["field"] == "hcb"


def test_reference_append_resolves_points_from_values_when_present():
    r = ledger.to_row(_e(value="+'1n+'!C5", before_value="10", after_value="25.0"))
    assert r["kind"] == "credit_ref" and r["points"] == 15.0


def test_reference_append_without_values_has_null_points():
    e = _e(value="+'1n+'!C5", fallback=True)
    del e["before_value"]; del e["after_value"]
    r = ledger.to_row(e)
    assert r["kind"] == "credit_ref" and r["points"] is None and r["fallback"] == 1


def test_ritual_formula_write_is_set_not_credit():
    r = ledger.to_row(_e(kind="write", col="P", date=None, value="=13+13+7",
                         after_value="33.0", before_value="26.0", src="ritual 巳 -1n"))
    assert r["kind"] == "set" and r["points"] is None and r["after_value"] == 33.0
    assert r["field"] == "-1₦"


def test_block_turnover_lock_is_lock():
    r = ledger.to_row(_e(kind="write", col="I", value="278.64", after_value="278.64",
                         before_value="278.64", src="block-turnover lock I"))
    assert r["kind"] == "lock" and r["points"] is None


def test_text_append_on_hcbi_is_text():
    r = ledger.to_row(_e(sheet="hcbi", col="H", value=", eggs", after_value="eggs",
                         before_value=""))
    assert r["kind"] == "text" and r["points"] is None


@pytest.mark.parametrize("jk", ["reconcile", "baseline", "excel-edit"])
def test_observation_kinds_are_observe(jk):
    r = ledger.to_row(_e(kind=jk, value=None, after_value="159.0", before_value="0"))
    assert r["kind"] == "observe" and r["points"] is None and r["after_value"] == 159.0
    assert r["jsonl_kind"] == jk


@pytest.mark.parametrize("jk", ["ack", "structural"])
def test_ack_and_structural_are_ignored(jk):
    assert ledger.to_row(_e(kind=jk)) is None


def test_backfill_to_another_day_has_no_block():
    r = ledger.to_row(_e(date="10/12"))
    assert r["date"] == "2026-10-12" and r["block"] is None


def test_unmapped_column_has_null_field():
    r = ledger.to_row(_e(sheet="0s897", col="E", value=", hello"))
    assert r["field"] is None


# ── hash / idempotence ───────────────────────────────────────────────────────

def test_hash_is_key_order_independent():
    a = _e(); b = dict(reversed(list(a.items())))
    assert ledger.line_hash(a) == ledger.line_hash(b)


def test_record_is_idempotent_and_skips_ignored(db):
    e = _e()
    assert ledger.record(e, db) is not None
    assert ledger.record(e, db) is None            # same line again → no dup
    assert ledger.record(_e(kind="ack"), db) is None
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_two_ritual_writes_same_second_both_recorded(db):
    # second-resolution ts + ritual bursts produced 14 (ts,sheet,col,row)
    # collisions in the real files; the hash covers the whole line.
    a = _e(kind="write", col="P", date=None, value="=13", after="=13", after_value="13.0",
           before_value="0", src="ritual 卯 -1n")
    b = dict(a, value="=13+3", after="=13+3", after_value="16.0", before_value="13.0")
    assert ledger.record(a, db) and ledger.record(b, db)
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


# ── row → date learning (row-addressed ritual writes) ────────────────────────

def test_row_addressed_write_borrows_date_learned_live(db):
    ledger.record(_e(), db)                                   # 10/19 ↔ row 290
    r_id = ledger.record(_e(kind="write", col="P", date=None, value="=13",
                            after_value="13.0", src="ritual 巳 -1n"), db)
    date, block = db.execute("SELECT date, block FROM events WHERE id=?", (r_id,)).fetchone()
    assert date == "2026-10-19" and block == "巳"


def test_import_two_pass_lets_ritual_write_borrow_a_later_date(tmp_path, db):
    p = tmp_path / "2026-10.jsonl"
    ritual = _e(ts="2026-10-19T05:54:45", kind="write", col="P", date=None, value="=13",
                after_value="13.0", before_value="0", src="ritual 卯 -1n")
    later = _e(ts="2026-10-19T09:00:00")                      # date-addressed, same row
    p.write_text(json.dumps(ritual, ensure_ascii=False) + "\n"
                 + json.dumps(later, ensure_ascii=False) + "\n"
                 + "{torn line\n", encoding="utf-8")
    st = ledger.import_jsonl([p], db)
    assert st["inserted"] == 2 and st["seen"] == 2
    date = db.execute("SELECT date FROM events WHERE kind='set'").fetchone()[0]
    assert date == "2026-10-19"
    assert ledger.import_jsonl([p], db)["inserted"] == 0    # idempotent


# ── day view ─────────────────────────────────────────────────────────────────

def test_day_view_sums_credits_and_reports_last_observation(db):
    ledger.record(_e(ts="2026-10-19T08:00:00", value="+15", after_value="85"), db)
    ledger.record(_e(ts="2026-10-19T09:00:00", value="+10", after_value="95"), db)
    ledger.record(_e(ts="2026-10-19T10:00:00", kind="reconcile", value=None,
                     after_value="104", before_value="95"), db)
    ledger.record(_e(ts="2026-10-19T06:00:00", kind="write", col="P", date=None,
                     value="=13", after_value="13.0", src="ritual 卯 -1n"), db)
    rows = {r["col"]: r for r in ledger.day("2026-10-19", "0分", db)}
    assert rows["R"]["credits"] == 25.0 and rows["R"]["observed"] == 104.0
    assert rows["R"]["n_events"] == 3
    assert rows["P"]["last_set"] == 13.0 and rows["P"]["credits"] == 0


def test_status_counts_kinds(db):
    ledger.record(_e(), db)
    s = ledger.status(db)
    assert s["events"] == 1 and s["kinds"] == {"credit": 1}


# ── cols helpers added for the ledger / step 2 ───────────────────────────────

@pytest.mark.parametrize("n,letter", [(1, "A"), (26, "Z"), (27, "AA"), (31, "AE"), (32, "AF"),
                                      (52, "AZ"), (53, "BA"), (702, "ZZ"), (703, "AAA")])
def test_col_letter(n, letter):
    from neon import cols
    assert cols.col_letter(n) == letter


def test_col_letter_rejects_zero():
    from neon import cols
    with pytest.raises(ValueError):
        cols.col_letter(0)


def test_header_for_reverse_lookup():
    from neon import cols
    assert cols.header_for("0分", "R") == "i9"
    assert cols.header_for("0n", "AF") == "N color"
    with pytest.raises(KeyError):
        cols.header_for("0分", "ZZZ")
