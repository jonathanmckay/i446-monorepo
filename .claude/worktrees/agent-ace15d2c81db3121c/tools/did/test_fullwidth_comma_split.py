"""Regression test (2026-06-24): a fullwidth CJK comma "，" between items was not
a separator, so "睡觉，0744-0810 xk22" parsed as ONE bogus item. parse_input must
split on ，； as well as ,;.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
_SPEC = importlib.util.spec_from_file_location("did_fast_fw", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_fw"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]


def test_fullwidth_comma_splits_items():
    items = df.parse_input("0000-0744 睡觉，0744-0810 xk22")
    names = [it.name for it in items]
    assert len(items) == 2, f"fullwidth comma must split into 2 items, got {names}"
    assert names[0] == "睡觉" or names[0].endswith("睡觉")
    assert "xk22" in names[1]


def test_ascii_comma_still_splits():
    items = df.parse_input("1st hci, 一起饭")
    assert len(items) == 2


def test_fullwidth_semicolon_splits():
    items = df.parse_input("睡觉；xk22")
    assert len(items) == 2
