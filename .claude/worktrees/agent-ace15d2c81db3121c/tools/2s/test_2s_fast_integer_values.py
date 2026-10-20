"""/2s pastes Neon sums into the scorecard as str(float(v)) -> a genuine
integer like 306 wrote as "306.0", and a fractional one (e.g. variable-task
points divide by 7, so raw 分 totals in Neon can read like
323.714285714286) wrote its full repr into the scorecard cell (user report
2026-07-14: "adding decimals to the number of points it pulls"). 分 is
measured as integers everywhere else in this system (janus.py rounds at
every render site); normalize_value must round once here too."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("twos_fast", HERE / "2s-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["twos_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_whole_number_does_not_grow_a_decimal_point():
    mod = _load()
    assert mod.normalize_value("306") == "306"
    assert mod.normalize_value("306.0") == "306"


def test_fractional_value_rounds_to_nearest_int():
    mod = _load()
    assert mod.normalize_value("323.714285714286") == "324"
    assert mod.normalize_value("204.5") == str(round(204.5))  # banker's rounding


def test_missing_value_passes_through_as_empty_string_literal():
    mod = _load()
    assert mod.normalize_value("missing value") == '""'
    assert mod.normalize_value("") == '""'
    assert mod.normalize_value("  ") == '""'


def test_non_numeric_value_quoted_not_rounded():
    mod = _load()
    assert mod.normalize_value("N/A") == '"N/A"'


def test_negative_fractional_rounds_correctly():
    mod = _load()
    assert mod.normalize_value("-5.6") == "-6"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
