"""dtd + a multi-domain split task ("[N] @code [N] @code", lib/fen_split.py):
the list shows and sorts by the total; quick-add keeps the pairs in the
content and labels only the first code (JM 2026-11-01).

Run: python3 -m pytest tools/dtd/test_dtd_fen_split.py -v
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
spec = importlib.util.spec_from_file_location("dtd_split_under_test", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dtd
spec.loader.exec_module(dtd)


def test_parse_est_sums_split():
    assert dtd.parse_est("mock WAU tree (30) [30] @h335 [10] @i9") == ("(30) [40]", 40)
    assert dtd.parse_est("fix faucet (30) [8]") == ("(30) [8]", 8)
    assert dtd.parse_est("goal {20}") == ("{20}", 20)


def test_parse_add_input_split_keeps_pairs_and_labels_first_code():
    content, labels = dtd.parse_add_input("mock WAU tree (30) [30] @h335 [10] @i9")
    assert content == "mock WAU tree (30) [30] @h335 [10] @i9"
    assert labels == ["h335"]


def test_parse_add_input_split_lone_tag_stays_a_label():
    content, labels = dtd.parse_add_input("x [30] @h335 [10] @i9 @卯")
    assert content == "x [30] @h335 [10] @i9"
    assert labels == ["卯", "h335"]


def test_parse_add_input_single_unchanged():
    assert dtd.parse_add_input("fix faucet (30) [8] @家") == ("fix faucet (30) [8]", ["家"])
    assert dtd.parse_add_input("x [8] @家 @i9") == ("x [8]", ["家", "i9"])
