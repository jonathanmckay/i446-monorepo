"""Regression tests for -2n time block configuration."""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent / "-2n.py"


def _parse_blocks():
    """Extract BLOCKS constant via AST to avoid importing the module."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "BLOCKS":
                    return ast.literal_eval(node.value)
    raise AssertionError("BLOCKS constant not found in -2n.py")


def _parse_index_formula():
    """Extract the block index formula offset from get_current_block."""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_current_block":
            for child in ast.walk(node):
                if isinstance(child, ast.BinOp) and isinstance(child.op, ast.FloorDiv):
                    # (hour - N) // 2 — extract N
                    left = child.left  # (hour - N)
                    if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Sub):
                        if isinstance(left.right, (ast.Constant, ast.Num)):
                            return getattr(left.right, "value", getattr(left.right, "n", None))
    raise AssertionError("Block index formula not found in get_current_block")


def test_blocks_use_dizhi_not_arabic():
    """地支 names should be used, not Arabic prayer time names."""
    blocks = _parse_blocks()
    arabic = {"فجر", "شروق", "صباح", "ظهر", "عصر", "آصيل", "غروب", "غسق", "زلة"}
    dizhi = {"卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"}
    names = {b[0] for b in blocks}
    assert names == dizhi, f"Expected 地支 names, got {names}"
    assert names.isdisjoint(arabic), "Arabic names should not appear in BLOCKS"


def test_blocks_start_on_even_hours():
    """All time blocks should start on even-numbered hours."""
    blocks = _parse_blocks()
    for name, start, end in blocks:
        start_hour = int(start.split(":")[0])
        assert start_hour % 2 == 0, f"Block {name} starts at {start} (odd hour {start_hour})"


def test_block_index_formula_uses_offset_4():
    """Formula should be (hour - 4) // 2 for even-hour blocks."""
    offset = _parse_index_formula()
    assert offset == 4, f"Expected offset 4, got {offset}"
