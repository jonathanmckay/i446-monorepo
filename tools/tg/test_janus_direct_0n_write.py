"""Regression (2026-10-25): "janus CLI won't let me write to 其他人, that's
something I should be able to write to directly on the 0n page."

Typed "其他人 3" went to tg-fast, whose bare trailing-N rule ("doing this for
the last N minutes") made a 3-minute Toggl entry and never wrote the 0n
cell. It now routes to did-fast, which writes the value into today's 0n
其他人 cell. The bare name (no number) still starts a timer."""
import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_direct_0n", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_direct_0n"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_qitaren_with_a_number_is_a_direct_0n_write():
    j = _load()
    assert j._is_direct_0n_write("其他人 3")
    assert j._is_direct_0n_write(" 其他人 12 ")
    assert j._is_direct_0n_write("其他人 1.5")


def test_bare_name_and_other_habits_still_go_to_toggl():
    j = _load()
    assert not j._is_direct_0n_write("其他人")          # starts a timer
    assert not j._is_direct_0n_write("冥想 20")         # minute habits keep tg-fast's last-N-minutes rule
    assert not j._is_direct_0n_write("其他人 1815-1843")


def test_resolve_part_routes_direct_writes_to_did_fast():
    tree = ast.parse((HERE / "janus.py").read_text())
    src = next(ast.get_source_segment((HERE / "janus.py").read_text(), n)
               for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_resolve_part")
    assert "use_did = use_did or _is_direct_0n_write(part)" in src
