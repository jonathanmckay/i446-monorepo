"""Regression: marking a daily inbox habit (ibx s897 / ibx i9 / ibx m5x2 /
slack i9 / slack m5x2 / slack github / teams) stamps 📧 on the current block's
header the way ص stamps ☀️ (2026-11-09, 1-1n idea 3: the week of Nov 2-8 had
the inbox habits marked every day but the heatmap saw 📧 in 5% of blocks).
Names must be compared through header_normalize, the path 0n routing uses, so
the sheet's "ibx - s897" spelling matches.
"""
import ast
from pathlib import Path

SRC = (Path(__file__).parent / "did-fast.py").read_text()


def _set_literal(name: str) -> set:
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name and isinstance(node.value, ast.Set):
                    return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"{name} set not found")


def test_inbox_habits_are_the_0n_inbox_columns_only():
    names = _set_literal("INBOX_HABITS")
    assert {"ibx s897", "ibx i9", "ibx m5x2", "slack i9", "slack m5x2", "slack github", "teams"} <= names
    for bad in ("ص", "冥想", "o314", "新闻", "0g"):
        assert bad not in names


def test_inbox_match_goes_through_header_normalize():
    i = SRC.index("INBOX_HABITS = {")
    seg = SRC[i:i + 600]
    assert "header_normalize(" in seg and "ZERO_N_ALIASES.get(" in seg
    assert '_stamp_current_block("📧")' in seg


def test_prayer_and_inbox_share_the_locked_stamp_helper():
    assert '_stamp_current_block("☀️")' in SRC
    assert "def _stamp_current_block(emoji: str) -> None:" in SRC
    body = SRC[SRC.index("def _stamp_current_block"):SRC.index("def _stamp_on_ix")]
    assert "build_order_lock()" in body and "_stamp_on_ix(_bname, emoji)" in body
    assert "except Exception:" in body, "a habit mark must never fail on a stamp"


def test_header_normalize_bridges_sheet_spelling():
    import importlib.machinery, importlib.util, sys
    # pure function: load without executing main
    for node in ast.parse(SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name == "header_normalize":
            ns = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "did-fast", "exec"), ns)
            hn = ns["header_normalize"]
            assert hn("ibx - s897") == "ibx s897"
            assert hn("Slack  M5x2") == "slack m5x2"
            return
    raise AssertionError("header_normalize not found")
