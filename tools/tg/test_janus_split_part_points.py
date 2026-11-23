"""2026-11-23: a dtd split's 分 sit on the time entry as a part
(lib/entry_points.py). The row shows them, but they are not a completion: the
row can still be logged, and a ^G / pin keeps the row's total exact."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_janus_block_view import _load_tui  # noqa: E402

DAY = dt.date(2026, 11, 23)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(tmp_path / "ep"))
    m = _load_tui()
    monkeypatch.setattr(m, "_ENTRY_PTS_DIR", tmp_path / "state")
    return m


def test_a_part_shows_on_the_row_but_does_not_block_logging_it(mod):
    mod.entry_points.record_part(DAY, "50", "PH1", 100)
    assert mod._recorded_entry_pts(DAY, [50]) == 100
    assert mod._entry_logged(DAY, [50]) is False


def test_a_completed_entry_blocks_a_second_log_and_an_undone_one_does_not(mod):
    mod.entry_points.record_part(DAY, "50", "PH1", 100)
    mod._entry_pts_add(DAY, {"50": 420})                 # the close, via janus
    assert mod._entry_logged(DAY, [50]) is True
    assert mod._recorded_entry_pts(DAY, [50]) == 520
    mod.entry_points.record(DAY, {"50": None}, "undo")
    assert mod._entry_logged(DAY, [50]) is False
    assert mod._recorded_entry_pts(DAY, [50]) == 100


def test_a_points_delta_lands_on_the_shown_total_once(mod):
    mod.entry_points.record_part(DAY, "50", "PH1", 100)
    mod._entry_pts_add(DAY, {"50": 420})
    mod._entry_pts_add(DAY, {"50": 30}, add=True)        # ^G +30
    assert mod._recorded_entry_pts(DAY, [50]) == 550


def test_a_pin_keeps_the_rows_total_with_parts_on_a_merged_id(mod):
    mod.entry_points.record_part(DAY, "51", "PH1", 100)  # part on the row's 2nd id
    mod._entry_pts_add(DAY, {"50": 420})
    mod._entry_pts_pin(DAY, [50, 51], 600)
    assert mod._recorded_entry_pts(DAY, [50, 51]) == 600
