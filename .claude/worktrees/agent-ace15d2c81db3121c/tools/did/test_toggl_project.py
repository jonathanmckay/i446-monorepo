#!/usr/bin/env python3
"""Regression test: Toggl entries from did-fast must include the project code
resolved from Todoist task labels, not just from the hardcoded HABIT_PROJECT map.

Bug: tasks routed via Todoist (step "todoist") with labels like ["s897"] got no
project on their Toggl time_range entry because _resolve_toggl_project only
checked HABIT_PROJECT, not the task's labels.
"""
import ast
from pathlib import Path

SRC = Path(__file__).parent / "did-fast.py"


def test_resolve_toggl_project_checks_todoist_labels():
    """_resolve_toggl_project must check r.todoist_task labels as a fallback."""
    source = SRC.read_text()
    assert "_resolve_toggl_project" in source, (
        "did-fast.py must have a _resolve_toggl_project function"
    )
    # Find the function and verify it checks todoist_task labels
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_toggl_project":
            func_src = ast.get_source_segment(source, node)
            assert "todoist_task" in func_src, (
                "_resolve_toggl_project must check todoist_task for labels"
            )
            assert "LABEL_TO_0FEN" in func_src, (
                "_resolve_toggl_project must validate labels against LABEL_TO_0FEN"
            )
            return
    raise AssertionError("_resolve_toggl_project function not found in AST")


def test_toggl_items_uses_resolve_function():
    """toggl_items must use _resolve_toggl_project, not inline HABIT_PROJECT."""
    source = SRC.read_text()
    # The toggl_items list comprehension should call _resolve_toggl_project
    assert "_resolve_toggl_project(r)" in source, (
        "toggl_items must call _resolve_toggl_project(r), not inline HABIT_PROJECT.get()"
    )
