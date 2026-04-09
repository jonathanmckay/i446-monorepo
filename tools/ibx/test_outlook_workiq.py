"""Regression tests for outlook_workiq."""
import ast
from pathlib import Path

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
