"""lib/neon/rates.py session pricing, shared by did-fast and the janus preview."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neon import rates  # noqa: E402


def test_session_points_are_base_plus_a_point_a_minute():
    assert rates.session_points("中文", 18) == 23
    assert rates.session_points("Math", 40) == 45
    assert rates.session_points("一起饭", 20) == 35


def test_session_points_is_none_for_anything_else():
    assert rates.session_points("work", 30) is None
