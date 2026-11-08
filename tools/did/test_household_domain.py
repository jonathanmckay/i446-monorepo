"""家 = extended family only (JM 2026-11-05): household meals and kid teaching
route through HOUSEHOLD_DOMAIN, never 家 (Dream v180, option b)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("didfast_household", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["didfast_household"] = df
_spec.loader.exec_module(df)


def test_no_household_route_says_家():
    assert "家" not in df.HOUSEHOLD_DOMAIN.values()
    assert "家" not in df.HABIT_PROJECT.values()
    assert all(lbl != "家" for lbl, _ in df.VARIABLE_DOMAIN.values())


def test_option_b_breakfast_is_hcb_like_janus_the_rest_xk87():
    assert df.HOUSEHOLD_DOMAIN == {"早餐": "hcb", "问学": "xk87", "lunch": "xk87", "dinner": "xk87"}
    assert df.HABIT_PROJECT["早餐"] == "hcb" and df.HABIT_PROJECT["问学"] == "xk87"
    assert df.VARIABLE_DOMAIN["lunch"][0] == df.VARIABLE_DOMAIN["dinner"][0] == "xk87"


@pytest.mark.parametrize("name", ["lunch", "dinner"])
def test_a_meals_0fen_column_is_its_domains_column(name):
    label, col = df.VARIABLE_DOMAIN[name]
    assert df.LABEL_TO_0FEN[label] == col


@pytest.mark.parametrize("habit", ["早餐", "问学"])
def test_every_habit_domain_has_a_0fen_column(habit):
    """[N] / +N on the habit lands in its domain's 0分 column; 家 had none."""
    assert df.HABIT_PROJECT[habit] in df.LABEL_TO_0FEN
