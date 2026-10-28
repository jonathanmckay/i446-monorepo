"""Regression (2026-10-28): "I don't see block points in Janus". WIDTH_HINT
was computed once at startup; janus launched in a wider pane that later
narrowed to 45 columns kept laying lines out ~50 wide, so the right-edge
block 分 (`7 133分`) was clipped to `7 1`. The width now follows the live
pane on every render_all()."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_width_resize", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_narrowed_pane_shrinks_width_hint(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_pane_cols", lambda: 52)
    m._sync_width()
    assert m.WIDTH_HINT == 51
    monkeypatch.setattr(m, "_pane_cols", lambda: 45)
    m._sync_width()
    assert m.WIDTH_HINT == 44
    assert m.DESC_MAX == 28


def test_headless_keeps_startup_width(monkeypatch):
    m = _load()
    before = m.WIDTH_HINT
    monkeypatch.setattr(m, "_pane_cols", lambda: 0)
    m._sync_width()
    assert m.WIDTH_HINT == before


def test_render_all_syncs_width_first():
    tree = ast.parse((HERE / "janus.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_all")
    first = fn.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert getattr(first.value.func, "id", None) == "_sync_width"
