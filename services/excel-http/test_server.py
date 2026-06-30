"""Tests for excel-http server — structural checks via AST."""

import ast
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


class TestLookupRowDateMatching:
    """Regression: 0n's date column (C) holds real Excel DATE values, which
    AppleScript renders as 'Tuesday, June 30, 2026 …' — an exact '= "6/30"'
    string match misses every row, so /salat and any date= write to 0n failed.
    lookup_row must compare a real date cell by month/day and keep the text path
    for the M/D-text sheets (0分, hcbi, 1n+)."""

    def test_lookup_row_handles_real_date_cells(self):
        src = _load_func_source("lookup_row")
        assert "(class of cv) is date" in src, (
            "lookup_row must detect real Excel date cells, not only M/D text"
        )
        assert "month of cv" in src and "day of cv" in src, (
            "a real date cell must be matched by its month/day"
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
