"""lib/fen_split.py: the "[N] @code [N] @code" grammar (JM 2026-11-01).

Run: python3 -m pytest lib/test_fen_split.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fen_split as fs  # noqa: E402


def test_single_bracket_unchanged():
    assert fs.parse_points("fix faucet (30) [8]") == [(8, None)]
    assert fs.total_points("fix faucet (30) [8]") == 8
    assert fs.has_split("fix faucet (30) [8]") is False
    assert fs.split_points("fix faucet (30) [8]", "家") == [("家", 8)]


def test_no_points():
    assert fs.parse_points("call dentist (10)") == []
    assert fs.total_points("call dentist (10)") is None
    assert fs.split_points("call dentist (10)", "xk87") == []


def test_split_two_domains():
    c = "mock WAU tree (30) [30] @h335 [10] @i9"
    assert fs.parse_points(c) == [(30, "h335"), (10, "i9")]
    assert fs.total_points(c) == 40
    assert fs.has_split(c)
    assert fs.split_points(c, "h335") == [("h335", 30), ("i9", 10)]
    assert fs.first_code(c) == "h335"


def test_bare_bracket_takes_default_and_merges():
    c = "prep call [20] @m5x2 [10]"
    assert fs.split_points(c, "m5x2") == [("m5x2", 30)]
    assert fs.split_points(c, "i9") == [("m5x2", 20), ("i9", 10)]
    assert fs.split_points(c, None) == [("m5x2", 20), (None, 10)]


def test_code_binds_forward_only():
    # "@i9" BEFORE a bracket is not bound to it (grammar: the @code after).
    c = "thing @i9 [20] [5] @hcb"
    assert fs.parse_points(c) == [(20, None), (5, "hcb")]


def test_rate_marker_and_curly_ignored():
    assert fs.parse_points("walk [1/m] {20} [15]") == [(15, None)]


def test_cjk_and_hyphen_codes():
    assert fs.parse_points("x [5] @家 [3] @-1g") == [(5, "家"), (3, "-1g")]


def test_set_total_single():
    assert fs.set_total("a (30) [8]", 12) == "a (30) [12]"
    assert fs.set_total("a (30) [8] @i9", 12) == "a (30) [12] @i9"
    assert fs.set_total("a (30)", 12) == "a (30) [12]"


def test_set_total_collapses_split():
    c = "mock WAU tree (30) [30] @h335 [10] @i9"
    assert fs.set_total(c, 25) == "mock WAU tree (30) [25]"
    assert fs.set_total("x [20] @m5x2 [10] tail", 5) == "x [5] tail"


def test_deduct_walks_pairs_in_order():
    c = "mock WAU tree (30) [30] @h335 [10] @i9"
    assert fs.deduct_points(c, 10) == "mock WAU tree (30) [20] @h335 [10] @i9"
    assert fs.deduct_points(c, 35) == "mock WAU tree (30) [0] @h335 [5] @i9"
    assert fs.deduct_points(c, 99) == "mock WAU tree (30) [0] @h335 [0] @i9"
    assert fs.deduct_points("a [30]", 10) == "a [20]"
    assert fs.deduct_points("a (10)", 10) == "a (10)"


def test_take_points_reports_what_came_off_which_domain():
    c = "prep call [20] @m5x2 [10]"
    assert fs.take_points(c, 15) == ("prep call [5] @m5x2 [10]", [(15, "m5x2")])
    assert fs.take_points(c, 25) == ("prep call [0] @m5x2 [5]", [(20, "m5x2"), (5, None)])
    assert fs.take_points("a (10)", 5) == ("a (10)", [])


def test_strip_points():
    assert fs.strip_points("mock WAU tree (30) [30] @h335 [10] @i9") == "mock WAU tree (30)"
    assert fs.strip_points("a [8] b") == "a b"


def test_typed_pair_re_is_token_anchored():
    assert [m.groups() for m in fs.TYPED_PAIR_RE.finditer("x [30] @h335 [10] @i9")] == [("30", "h335"), ("10", "i9")]
    assert list(fs.TYPED_PAIR_RE.finditer("x[30] @h335")) == []


def test_fullwidth_brackets_count_as_points():
    # A Chinese IME types [ ] as 【】 (JM 2026-11-08).
    assert fs.total_points("read COD deck 【30】") == 30
    assert fs.parse_points("x 【30】 @h335 [10] @i9") == [(30, "h335"), (10, "i9")]
    assert fs.set_total("a 【5】", 8) == "a [8]"
    assert fs.strip_points("a 【5】 b") == "a b"
    assert [m.groups() for m in fs.TYPED_PAIR_RE.finditer("x 【30】 @h335")] == [("30", "h335")]


def test_fullwidth_points_normalises_digits_only():
    assert fs.fullwidth_points("x 【12】 【notes】") == "x [12] 【notes】"
