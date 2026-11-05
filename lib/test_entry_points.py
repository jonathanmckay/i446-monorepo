"""Shared per-entry 分 (lib/entry_points.py), JM 2026-11-05."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_points as ep  # noqa: E402

DAY = "2026-11-05"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(tmp_path))
    return tmp_path


def _line(path, ts, id_, pts, day=DAY):
    with open(path, "a") as f:
        f.write(json.dumps({"ts": ts, "day": day, "id": id_, "pts": pts}) + "\n")


def test_a_point_recorded_on_one_host_is_read_on_every_host(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T08:00:00", "7", 19)
    assert ep.for_day(DAY) == {"7": 19}


def test_the_newest_line_wins_across_hosts(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T09:00:00", "7", 25)
    _line(store / "entry-points.straylight.jsonl", "2026-11-05T08:00:00", "7", 19)
    assert ep.for_day(DAY) == {"7": 25}


def test_a_tombstone_removes_the_local_sidecar_value_too(store):
    ep.record(DAY, {"7": 19})
    ep.record(DAY, {"7": None})
    assert ep.overlay({"7": 19, "8": 5}, DAY) == {"8": 5}


def test_overlay_beats_a_stale_local_value(store):
    ep.record(DAY, {"7": 25})
    assert ep.overlay({"7": 19}, DAY) == {"7": 25}


def test_other_days_stay_out(store):
    ep.record("2026-11-04", {"7": 19})
    assert ep.for_day(DAY) == {}


def test_a_half_synced_line_is_skipped(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T08:00:00", "7", 19)
    with open(store / "entry-points.ix.jsonl", "a") as f:
        f.write('{"ts": "2026')
    assert ep.for_day(DAY) == {"7": 19}


def test_a_new_line_is_seen_through_the_cache(store):
    ep.record(DAY, {"7": 19})
    assert ep.for_day(DAY) == {"7": 19}
    ep.record(DAY, {"8": 5})
    assert ep.for_day(DAY) == {"7": 19, "8": 5}


def test_tests_never_touch_the_real_store(monkeypatch):
    monkeypatch.delenv("ENTRY_POINTS_DIR", raising=False)
    assert ep._dir() is None
    assert ep.for_day(DAY) == {}
    ep.record(DAY, {"7": 19})        # no-op, no write


def test_record_never_raises_on_an_unwritable_dir(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(blocker / "sub"))
    ep.record(DAY, {"7": 19})
