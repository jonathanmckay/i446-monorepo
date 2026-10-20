"""Regression tests for outlook_workiq."""
import ast
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

OUTLOOK_PY = Path(__file__).parent / "outlook_workiq.py"


def test_fetch_outlook_detects_workiq_refusal():
    """workiq sometimes returns AI refusal text instead of emails — must be caught."""
    source = OUTLOOK_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "refusal" in func_source.lower(), (
                "fetch_outlook_items must detect workiq refusal responses"
            )
            assert "I won't do that" in func_source or "won.t do that" in func_source, (
                "fetch_outlook_items must check for common refusal phrases"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_fetch_outlook_skips_placeholder_entries():
    """workiq sometimes returns ellipsis/template placeholders — must be filtered out."""
    source = OUTLOOK_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "placeholder" in func_source.lower(), (
                "fetch_outlook_items must filter placeholder/template entries"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_workiq_prompt_no_iso8601():
    """
    Bug: Requesting ISO 8601 timestamps causes workiq to refuse the entire request.
    workiq only has relative timestamps ("about an hour ago"), so asking for exact
    ISO format triggers a full refusal with zero emails returned.

    Fix: prompt must NOT request ISO 8601 or exact timestamps.
    """
    source = OUTLOOK_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "ISO 8601" not in func_source, (
                "workiq prompt must not request ISO 8601 timestamps — "
                "this causes workiq to refuse the entire request"
            )
            assert "YYYY-MM-DD" not in func_source, (
                "workiq prompt must not request exact date formats — "
                "workiq only has relative timestamps"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")
