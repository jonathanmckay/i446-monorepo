"""Tests for excel-http server — structural checks via AST."""

import ast
import textwrap

import pytest


def _load_do_append_source() -> str:
    """Extract the do_append function source from server.py."""
    with open("server.py") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_append":
            return ast.get_source_segment(open("server.py").read(), node)
    raise AssertionError("do_append not found in server.py")


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
