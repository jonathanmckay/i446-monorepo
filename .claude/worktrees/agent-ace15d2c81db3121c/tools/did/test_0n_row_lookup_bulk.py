"""0n row lookup must be ONE bulk range read, not a per-cell scan.

Bug 2026-10-12 ("the 0s python script takes a long time to mark 0l done"):
build_0n_script and build_0l_time_script each found today's 0n row by asking
Excel for `value of cell 3 of row r` for r = 3..500, one Apple Event per row.
On Ix that took ~50s by October (row ~286), and marking 0l pays it twice.
The bulk read of C3:C500 returns the same row in under a second.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_rowlookup", HERE / "did-fast.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_rowlookup"] = m
    spec.loader.exec_module(m)
    return m


class _It:
    name = "0l"


class _W:
    item = _It()
    col_num = 20
    write_value = 1


def _scripts():
    m = _load()
    return [m.build_0n_script([_W()], "10/12"), m.build_0l_time_script("10/12")]


def test_0n_and_0l_scripts_read_the_date_column_in_one_bulk_read():
    for s in _scripts():
        assert "value of cell 3 of row r" not in s      # the per-row Apple Event scan
        assert 'value of range "C3:C500"' in s
        assert "set todayRow to i + 2" in s             # list index 1 -> sheet row 3


def test_lookup_loop_runs_outside_the_excel_tell_block():
    # `date` is an Excel property inside `tell application "Microsoft Excel"`,
    # so `class of cellDate is date` only compiles outside it.
    for s in _scripts():
        loop = s.index("repeat with i from 1 to count of dateVals")
        first_end_tell = s.index("end tell")
        assert first_end_tell < loop < s.index('tell application "Microsoft Excel"', first_end_tell)
