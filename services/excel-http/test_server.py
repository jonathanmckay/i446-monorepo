"""Tests for excel-http server — structural checks via AST."""

import ast
import re
import textwrap

import pytest


def _load_func_source(name: str) -> str:
    """Extract a top-level function's source from server.py."""
    text = open("server.py").read()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} not found in server.py")


def _load_do_append_source() -> str:
    return _load_func_source("do_append")


class TestAppendStringValues:
    """Regression: /ate food names with '+' were silently dropped because
    do_append always set formula (=...) for empty cells, even for string values
    like ', mocha+yoghurt'. The '=' prefix made it an invalid formula and Excel
    rejected it silently."""

    def test_do_append_has_is_numeric_guard(self):
        """do_append must distinguish numeric (+N, =...) from string appends."""
        src = _load_do_append_source()
        assert "is_numeric" in src, (
            "do_append must check is_numeric to distinguish string vs formula appends"
        )

    def test_do_append_uses_set_value_for_strings(self):
        """For non-numeric values, empty cells should use 'set value of' (not 'set formula of')."""
        src = _load_do_append_source()
        assert "set value of theCell" in src, (
            "do_append must use 'set value of' for string values in empty cells"
        )

    def test_do_append_strips_leading_comma_for_empty_cells(self):
        """When the cell is empty, leading ', ' should be stripped from string values."""
        src = _load_do_append_source()
        assert 'lstrip(", ")' in src, (
            "do_append must strip leading ', ' from string values for empty cells"
        )


class TestThreadingAndTimeoutHardening:
    """Regression (2026-07-15, recurred twice same day): the server was a
    plain single-threaded HTTPServer with no socket timeout. A stalled client
    connection (our own curl hitting its own client-side timeout while the
    server's blocking rfile.read() waited forever for bytes that never came)
    wedged the single request-handling path permanently — every subsequent
    request queued forever, indistinguishable from 'daemon down' even though
    the process was alive and the port was LISTENing."""

    def test_uses_threading_http_server(self):
        src = open("server.py").read()
        assert "ThreadingHTTPServer" in src, (
            "must not use a plain single-threaded HTTPServer — one stalled "
            "connection must not block every other request"
        )

    def test_handler_has_socket_timeout(self):
        src = open("server.py").read()
        assert re.search(r"class Handler\(BaseHTTPRequestHandler\):\s*\n(\s*#.*\n)*\s*timeout\s*=\s*\d+", src), (
            "Handler must set a socket timeout, or a stalled read blocks forever"
        )

    def test_excel_calls_are_serialized_across_threads(self):
        # Threading fixes the stalled-read wedge, but concurrent AppleEvents
        # to Excel from multiple threads is its own hazard — must be
        # serialized with a lock around the actual Excel-touching call.
        src = open("server.py").read()
        assert "EXCEL_LOCK" in src and "threading.Lock()" in src
        assert "with EXCEL_LOCK:" in src


class TestPinnedWorkbook:
    """Regression (2026-07-14): every AppleScript block targeted 'active
    workbook' instead of the Neon workbook by name. When some other file was
    frontmost in Excel on ix (a real, observed state — ix runs unattended),
    every lookup/read/write/append silently searched the WRONG workbook and
    reported 'not found', even though the real row existed. Every cell target
    must be pinned to the actual workbook, never 'active workbook'."""

    def test_no_active_workbook_references(self):
        src = open("server.py").read()
        assert "active workbook" not in src, (
            "must not target Excel's frontmost workbook — pin to the named "
            "Neon workbook instead, or writes silently go to the wrong file"
        )

    def test_workbook_constant_defined_and_used(self):
        src = open("server.py").read()
        assert 'WORKBOOK = "Neon分v12.2.xlsx"' in src
        assert src.count('workbook "{WORKBOOK}"') >= 4, (
            "lookup_row, do_append, do_write, and do_read must all pin to "
            "the named workbook"
        )


class TestAppendNumericGuard:
    """Regression (2026-07-14): do_append's non-empty branch blindly did
    `oldFormula & "+N"`. When oldFormula was a bare number with no leading
    '=' (a plain value-set cell, not a formula — e.g. a Daily Dozen count
    cell), the result was the TEXT string '2+2' instead of the formula
    '=2+2', so the cell silently stopped summing. Observed live on hcbi via
    /ate."""

    def test_numeric_append_normalizes_bare_number_before_concatenating(self):
        src = _load_do_append_source()
        assert 'oldFormula does not start with "="' in src, (
            "numeric append must guard against a bare-number existing "
            "formula, or appending produces a non-computing text string"
        )

    def test_string_append_path_untouched(self):
        # String appends (e.g. food-name ", more food") must NOT gain the
        # numeric "=" guard — only the is_numeric branch should have it.
        src = _load_do_append_source()
        string_branch = src[src.index("else:", src.index("if is_numeric:")):]
        assert 'oldFormula does not start with "="' not in string_branch
        assert 'set formula of theCell to oldFormula & "{val_esc}"' in string_branch


class TestLookupRowDateMatching:
    """Regression: 0n's date column (C) holds real Excel DATE values, which
    AppleScript renders as 'Tuesday, June 30, 2026 …' — an exact '= "6/30"'
    string match misses every row, so /salat and any date= write to 0n failed.
    lookup_row must compare a real date cell by month/day and keep the text path
    for the M/D-text sheets (0分, hcbi, 1n+)."""

    def test_lookup_row_handles_real_date_cells(self):
        src = _load_func_source("lookup_row")
        assert '((class of cv) as text) is "date"' in src, (
            "lookup_row must detect real Excel date cells, not only M/D text"
        )
        assert "month of cv" in src and "day of cv" in src, (
            "a real date cell must be matched by its month/day"
        )

    def test_date_class_compared_as_text_not_bare_keyword(self):
        # Regression (2026-07-14): `if (class of cv) is date then` fails to
        # PARSE inside `tell application "Microsoft Excel"` — Excel's own
        # dictionary shadows the standard `date` type keyword there
        # (confirmed live: osascript exits non-zero on that exact line).
        # lookup_row swallows the failure as rc!=0 -> None, so every
        # date-typed date-column cell silently "wasn't found" even though
        # this branch's string pattern was present (the previous version of
        # this test only checked for the string, never executed it — that's
        # how the bug shipped and stayed invisible). Comparing the class as
        # text sidesteps the collision.
        src = _load_func_source("lookup_row")
        assert "(class of cv) is date" not in src, (
            "raw 'is date' comparison doesn't parse inside the Excel tell block"
        )

    def test_lookup_row_keeps_text_match_path(self):
        src = _load_func_source("lookup_row")
        assert "cv as text" in src, (
            "lookup_row must still match M/D-text date columns (0分, hcbi)"
        )

    def test_lookup_row_skips_empty_cells(self):
        src = _load_func_source("lookup_row")
        assert "missing value" in src, (
            "lookup_row must skip empty date cells (cv is missing value)"
        )
