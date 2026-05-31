"""Regression: prayer habits (冥想, o314, 其他人) should write ☀️ to the
current block's header line in the build order file.

Previously, the ☀️ only flashed in tg-tui (6s) via SIGUSR1 but was never
persisted to build order, so it disappeared immediately.
"""

import ast
import textwrap
from pathlib import Path


def test_prayer_marker_section_exists_in_did_fast():
    """did-fast.py must contain a prayer marker write block (step 5c)."""
    src = Path(__file__).parent / "did-fast.py"
    text = src.read_text()
    assert "PRAYER_HABITS" in text, "PRAYER_HABITS constant missing"
    assert "☀️" in text, "☀️ marker write missing"
    assert "## -1₲" in text, "build order section check missing"


def test_prayer_habits_include_all_hcm_habits():
    """The PRAYER_HABITS set should include 冥想, o314, 其他人."""
    src = Path(__file__).parent / "did-fast.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PRAYER_HABITS":
                    if isinstance(node.value, ast.Set):
                        names = {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)}
                        assert "冥想" in names
                        assert "o314" in names
                        assert "其他人" in names
                        return
    raise AssertionError("PRAYER_HABITS set not found as AST assignment")


def test_prayer_marker_write_logic(tmp_path):
    """Simulate the prayer marker write on a build order file."""
    bo = tmp_path / "build-order.md"
    bo.write_text(textwrap.dedent("""\
        ## -1₲

        - 卯
            - [ ]
        - 辰
            - [ ]
        - 巳
            - [ ]
        - 午
            - [ ]
        - 未
            - [ ]
        - 申
            - [ ]
        - 酉
            - [ ]
        - 戌
            - [ ]
        - 亥
            - [ ]
    """))

    # Simulate the write logic from did-fast.py for block 申
    bname = "申"
    text = bo.read_text()
    lines = text.split("\n")
    new = []
    for l in lines:
        if l.startswith(f"- {bname}") and "☀️" not in l:
            new.append(f"{l.rstrip()} ☀️")
        else:
            new.append(l)
    bo.write_text("\n".join(new))

    result = bo.read_text()
    assert "- 申 ☀️" in result
    # Other blocks untouched
    assert "- 午\n" in result or "- 午 \n" in result.replace("☀️", "")
    assert result.count("☀️") == 1


def test_prayer_marker_idempotent(tmp_path):
    """Writing the marker twice should not duplicate it."""
    bo = tmp_path / "build-order.md"
    bo.write_text("## -1₲\n\n- 申 ☀️\n    - [ ] \n")

    bname = "申"
    text = bo.read_text()
    lines = text.split("\n")
    new = []
    for l in lines:
        if l.startswith(f"- {bname}") and "☀️" not in l:
            new.append(f"{l.rstrip()} ☀️")
        else:
            new.append(l)
    bo.write_text("\n".join(new))

    assert bo.read_text().count("☀️") == 1
