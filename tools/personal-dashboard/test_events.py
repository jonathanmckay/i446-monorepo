"""Tests for events.py, JM Dash's points/time event table and /slice query."""
from datetime import date, timezone
from zoneinfo import ZoneInfo

import events

COLS = {"P": "-1₦", "R": "i9", "T": "个"}
PT = ZoneInfo("America/Los_Angeles")


def _e(**kw):
    base = {"sheet": "0分", "row": 276, "date": "10/5", "ts": "2026-10-05T09:00:00"}
    base.update(kw)
    return base


def _by(rows, key="source"):
    out = {}
    for r in rows:
        out[r[key]] = round(out.get(r[key], 0) + r["value"], 6)
    return out


def test_row_and_date_addressed_writes_share_a_cell():
    # Ritual -1n writes are row-addressed (date null); the block-turnover
    # write is date-addressed. Same cell: deltas chain across both.
    rows = events.points_events([
        _e(kind="write", col="P", after_value="1.0", value="=1"),
        _e(kind="write", col="P", date=None, after_value="4.0", value="=1+3", src="ritual 辰 -1n"),
        _e(kind="write", col="P", after_value="4.0", value="=1+3", src="block-turnover"),
        _e(kind="write", col="P", date=None, after_value="7.0", value="=1+3+3", src="ritual 巳 -1n"),
    ], COLS)
    assert [r["value"] for r in rows] == [1.0, 3.0, 3.0]
    assert all(r["day"] == "2026-10-05" for r in rows)  # row-addressed day from the row


def test_append_without_history_uses_literal_not_template_value():
    # W-style cells start with a template formula worth 65; the first ledger
    # append "+30" must count 30, not 95.
    rows = events.points_events([
        _e(kind="append", col="T", after_value="95.0", value="+30", src="did x"),
    ], COLS)
    assert [r["value"] for r in rows] == [30.0]


def test_baseline_sets_start_without_points():
    rows = events.points_events([
        _e(kind="baseline", col="T", after_value="65.0"),
        _e(kind="append", col="T", after_value="95.0", value="+30", src="did x"),
    ], COLS)
    assert [(r["source"], r["value"]) for r in rows] == [("cli", 30.0)]


def test_reconcile_is_excel_3p_app():
    rows = events.points_events([
        _e(kind="append", col="T", after_value="123.0", value="+10", src="did x", source="cli", via="did-fast"),
        _e(kind="reconcile", col="T", before_value="123.0", after_value="170.0", source="3p-app", via="excel"),
    ], COLS)
    assert _by(rows) == {"cli": 10.0, "3p-app": 47.0}
    assert rows[-1]["via"] == "excel"


def test_broken_chain_with_before_value_splits_excel_edit():
    # Ledger last saw 40; JM typed +20 in Excel (60); then did-fast appends +10.
    rows = events.points_events([
        _e(kind="write", col="R", after_value="40.0", value="=40"),
        _e(kind="append", col="R", before_value="60.0", after_value="70.0", value="+10",
           chain="broken", source="cli", via="did-fast"),
    ], COLS)
    assert _by(rows) == {"cli": 50.0, "3p-app": 20.0}


def test_entry_source_and_via_carry_through():
    rows = events.points_events([
        _e(kind="append", col="R", after_value="5.0", value="+5", source="1p-app", via="janus-mobile"),
    ], COLS)
    assert (rows[0]["source"], rows[0]["via"]) == ("1p-app", "janus-mobile")


def test_ignores_other_sheets_and_columns():
    rows = events.points_events([
        _e(kind="append", sheet="hcbi", col="R", after_value="5.0", value="+5"),
        _e(kind="append", col="G", after_value="5.0", value="+5"),
    ], COLS)
    assert rows == []


def test_unattributed_makes_totals_match_cache():
    rows = events.points_events([_e(kind="append", col="R", after_value="5.0", value="+5")], COLS)
    rest = events.unattributed_points(rows, {"2026-10-05": {"i9": 12, "个": 7}}, ["i9", "个"], ["2026-10-05"])
    assert _by(rows + rest, "project") == {"i9": 12.0, "个": 7.0}
    assert {r["source"] for r in rest} == {"unknown"}


def _toggl(id_, start, dur, pid=1):
    return {"id": id_, "start": start, "duration": dur, "project_id": pid}


def test_time_source_from_log_cutover_or_unknown():
    entries = [_toggl(1, "2026-10-05T16:00:00Z", 1800), _toggl(2, "2026-10-05T17:00:00Z", 600),
               _toggl(3, "2026-10-04T17:00:00Z", 600), _toggl(4, "2026-10-05T18:00:00Z", -1)]
    log = {"1": {"source": "1p-app", "via": "janus-mobile"}}
    rows = events.time_events(entries, log, "2026-10-05T00:00:00+00:00", lambda pid: "i9", PT)
    assert [(r["source"], r["value"]) for r in rows] == [("1p-app", 30.0), ("3p-app", 10.0), ("unknown", 10.0)]
    assert rows[0]["ts"].startswith("2026-10-05T09:00")  # local time


def test_time_without_cutover_is_unknown():
    rows = events.time_events([_toggl(2, "2026-10-05T17:00:00Z", 600)], {}, None, lambda p: "i9", PT)
    assert rows[0]["source"] == "unknown"


def test_query_groups_filters_and_buckets():
    rows = [
        {"metric": "points", "ts": "2026-10-05T09:10:00", "day": "2026-10-05", "project": "i9", "source": "cli", "via": "", "value": 10},
        {"metric": "points", "ts": "2026-10-05T11:00:00", "day": "2026-10-05", "project": "个", "source": "3p-app", "via": "excel", "value": 5},
        {"metric": "points", "ts": None, "day": "2026-10-04", "project": "i9", "source": "unknown", "via": "", "value": 2},
        {"metric": "time", "ts": "2026-10-05T09:00:00", "day": "2026-10-05", "project": "i9", "source": "cli", "via": "", "value": 60},
    ]
    d0, d1 = date(2026, 10, 4), date(2026, 10, 5)
    q = events.query(rows, "points", d0, d1, "day", group_by="source")
    assert q["labels"] == ["2026-10-04", "2026-10-05"]
    assert q["series"] == {"cli": [0, 10], "3p-app": [0, 5], "unknown": [2, 0]}

    q = events.query(rows, "points", d0, d1, "day", {"project": ["i9"]}, "source")
    assert q["totals"] == {"cli": 10, "unknown": 2}

    q = events.query(rows, "points", d0, d1, "block", group_by="project")
    assert q["series"]["i9"][q["labels"].index("2026-10-05 巳")] == 10
    assert q["dropped"] == 2  # no time of day

    q = events.query(rows, "time", d0, d1, "week", group_by="project")
    assert q["labels"] == ["2026-10-04"] and q["series"] == {"i9": [60]}


def test_dimension_values():
    rows = [{"metric": "time", "project": "i9", "source": "cli"},
            {"metric": "points", "project": "个", "source": "excel"}]
    assert events.dimension_values(rows, "time") == {"project": ["i9"], "source": ["cli"]}
