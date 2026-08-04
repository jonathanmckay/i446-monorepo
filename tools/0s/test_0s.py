#!/usr/bin/env python3
"""Regression tests for the 0s daily-review Excel writer."""
import datetime as dt
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("zeros", Path(__file__).parent / "0s.py")
z = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(z)

TODAY = dt.date(2026, 7, 14)


def test_mdy_matches_sheet_format():
    assert z._mdy(TODAY) == "7/14/26"
    assert z._mdy(dt.date(2026, 1, 1)) == "1/1/26"


def test_motivation_targets_tomorrow_row():
    mot = [f for f in z.FIELDS if f[0] == "motivation"][0]
    assert mot[2] == "D" and mot[3] == "tomorrow"


def test_build_writes_today_and_tomorrow_and_skips_empty():
    ans = {"title": "Good day", "neon": "8", "motivation": "ship it", "win": ""}
    s = z.build_applescript(ans, TODAY)
    # date lookups for both rows present
    assert 'if bv = "7/14/26" then set todayRow to r' in s
    assert 'if bv = "7/15/26" then set tomRow to r' in s
    # text field → today row, quoted
    assert 'set value of range ("E" & todayRow) of ws to "Good day"' in s
    # num field → numeric, no quotes
    assert 'set value of range ("K" & todayRow) of ws to 8.0' in s
    # motivation → tomorrow row, guarded
    assert 'if tomRow > 0 then' in s
    assert 'set value of range ("D" & tomRow) of ws to "ship it"' in s
    # empty field never written
    assert '"H" &' not in s


def test_multiline_and_quotes_escaped():
    s = z.build_applescript({"thankful": 'a "b"\nc'}, TODAY)
    assert '"a \\"b\\"" & linefeed & "c"' in s


def test_non_numeric_num_field_skipped():
    s = z.build_applescript({"neon": "high"}, TODAY)
    assert '"K" &' not in s


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_review_date_defaults_to_yesterday():
    """0s is accrual/retrospective: with no date arg it reviews YESTERDAY, so the
    main fields land in yesterday's row (not today's)."""
    assert z._review_date(None) == dt.date.today() - dt.timedelta(days=1)


def test_review_date_arg_is_the_reviewed_day():
    assert z._review_date("2026-07-13") == dt.date(2026, 7, 13)


def test_default_run_writes_main_to_yesterday_motivation_to_today():
    rd = z._review_date(None)                 # yesterday
    s = z.build_applescript({"title": "x", "motivation": "m"}, rd)
    assert 'if bv = "%s" then set todayRow to r' % z._mdy(rd) in s          # main -> reviewed day
    assert 'if bv = "%s" then set tomRow to r' % z._mdy(rd + dt.timedelta(days=1)) in s  # motivation -> next day


def test_points_checked_is_last_and_has_no_column():
    last = z.FIELDS[-1]
    assert last[0] == "points_checked"
    assert last[2] is None and last[3] is None, "points_checked must not map to a neon column"


def test_points_checked_never_written_to_excel():
    s = z.build_applescript({"points_checked": "1", "title": "x"}, TODAY)
    assert "points_checked" not in s
    # only the real field (title) produces a write
    assert s.count("set value of range") == 1


def test_points_checked_1_marks_0l_done():
    import ast
    src = (z.__file__ and open(z.__file__).read()) or ""
    m = ast.get_source_segment(src, [n for n in ast.walk(ast.parse(src))
                                     if isinstance(n, ast.FunctionDef) and n.name == "main"][0])
    assert 'answers.get("points_checked")' in m
    assert '== "1"' in m
    assert 'str(DID_FAST), "0l"' in m, "points_checked=1 must run did-fast 0l"


def test_progress_line_printed_before_slow_neon_write():
    """User report 2026-07-25: after ^S the form restores the shell and the
    ssh Excel write runs silently for seconds — it looked like a frozen
    terminal. main() must announce the write (flushed) BEFORE write_answers
    runs, and announce the 0l mark before its did-fast call."""
    import pathlib
    src = pathlib.Path(__file__).with_name("0s.py").read_text()
    main_src = src[src.index("def main()"):]
    announce = main_src.index("writing %d fields to Neon")
    write = main_src.index("result = write_answers(")
    assert announce < write, "progress line must precede the Excel write"
    assert "flush=True" in main_src[:write]
    assert main_src.index("marking 0l done") < main_src.index("subprocess.run")


def test_did_fast_0l_timeout_has_headroom():
    """Bug 2026-08-04: 0s wrote to Neon but silently failed to mark 0l done
    because the did-fast subprocess call only got 60s — too little for 0l's
    own two sequential Excel round-trips (0n write ~30s + 0l-completion-time
    write ~15s) plus Todoist search/close. Must be raised well past 60."""
    import ast
    src = open(z.__file__).read()
    main_fn = [n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
    def _has_0l(arg):
        if isinstance(arg, ast.Constant):
            return arg.value == "0l"
        if isinstance(arg, ast.List):
            return any(_has_0l(e) for e in arg.elts)
        return False

    calls = [n for n in ast.walk(main_fn) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "run"
             and any(_has_0l(a) for a in n.args)]
    assert calls, "expected a subprocess.run(...) call passing \"0l\""
    timeout_kw = next(kw for kw in calls[0].keywords if kw.arg == "timeout")
    assert timeout_kw.value.value >= 120, "0l subprocess timeout must have headroom past 60s"


def test_marked_done_requires_actual_write_success():
    """The did-fast 0l subprocess always exits 0 and reports success/failure
    inside its JSON stdout — a clean subprocess return is not proof the 0n
    write succeeded. main() must parse and check 0n_write.ok before claiming
    '0l marked done', not assume success from the absence of an exception."""
    import pathlib
    src = pathlib.Path(__file__).with_name("0s.py").read_text()
    main_src = src[src.index("def main()"):]
    block_start = main_src.index('points_checked")')
    block = main_src[block_start:block_start + 2000]
    assert "0n_write" in block, "must inspect did-fast's 0n_write result, not just catch exceptions"
    assert '.get("ok")' in block
    marked_done_idx = block.index('"0l marked done"') if '"0l marked done"' in block else block.index("0l marked done")
    ok_check_idx = block.index('.get("ok")')
    assert ok_check_idx < marked_done_idx, "success message must be gated on the actual write result"
