"""Tests for xk887-survey.py: /xk887 weekly review across 4 sheets
(xk88 marriage/social, xk20 Theo, xk22 Ren, xk26 Rori) in xk887.xlsx."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("xk887s", HERE / "xk887-survey.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xk887s"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_week_range_defaults_to_last_completed_week():
    m = _load()
    s, e = m.week_range(None, today=dt.date(2026, 7, 28))
    assert (s, e) == (dt.date(2026, 7, 19), dt.date(2026, 7, 25))


def test_week_range_with_date_arg():
    m = _load()
    s, e = m.week_range("2026-07-15")
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_week_row_label_matches_1s_convention():
    m = _load()
    assert m.week_row_label(dt.date(2026, 7, 19)) == "7.3"
    assert m.week_row_label(dt.date(2026, 7, 12)) == "7.2"
    assert m.week_row_label(dt.date(2026, 1, 4)) == "1.1"


def test_all_four_sheets_present_in_order():
    m = _load()
    assert [cfg["sheet"] for cfg in m.SHEETS] == ["xk88", "xk20", "xk22", "xk26"]


def test_field_keys_are_namespaced_and_unique():
    m = _load()
    keys = [m.field_key(cfg["sheet"], key) for cfg in m.SHEETS for key, *_ in cfg["fields"]]
    assert len(keys) == len(set(keys))


def test_applescript_targets_week_label_and_skips_blanks():
    m = _load()
    script = m.build_applescript(
        {"xk88_good": "Great week", "xk88_regrettable": "", "xk20_notes": "n"},
        dt.date(2026, 7, 19))
    assert '"7.3"' in script
    assert '("B" & weekRow_xk88)' in script          # good col
    assert '("C" & weekRow_xk88)' not in script      # blank regrettable skipped
    assert '("G" & weekRow_xk20)' in script          # xk20 notes col
    assert "save wb" in script


def test_applescript_appends_new_row_when_week_missing():
    m = _load()
    script = m.build_applescript({}, dt.date(2026, 7, 19))
    assert "set isNew_xk88 to (weekRow_xk88 = 0)" in script
    assert 'set weekRow_xk88 to lastRow_xk88 + 1' in script
    assert '("A" & weekRow_xk88) of ws to 7.3' in script


def test_col_a_matched_via_string_value_not_value():
    """Col A is a formula chain (=prev+0.1) with float-precision display
    artifacts (6.2 stores as 6.199999999999999) -- must compare the
    DISPLAYED text, same lesson 0s.py already learned for its date column."""
    m = _load()
    script = m.build_applescript({}, dt.date(2026, 7, 19))
    assert 'string value of range ("A" & r)' in script
    assert script.count('string value of range ("A" & r)') == len(m.SHEETS)


def test_age_auto_continues_on_new_row_when_blank():
    m = _load()
    script = m.build_applescript({}, dt.date(2026, 7, 19))
    assert "if isNew_xk26 then" in script
    assert 'prevVal_xk26 to (value of range ("B" & lastRow_xk26) of ws)' in script
    assert '("B" & weekRow_xk26) of ws to (prevVal_xk26 + 1)' in script


def test_age_explicit_value_overrides_auto_continue():
    m = _load()
    script = m.build_applescript({"xk26_age": "22"}, dt.date(2026, 7, 19))
    assert '("B" & weekRow_xk26) of ws to 22.0' in script
    assert "prevVal_xk26" not in script


def test_num_field_rejects_non_numeric():
    m = _load()
    script = m.build_applescript({"xk26_age": "twenty"}, dt.date(2026, 7, 19))
    assert "twenty" not in script


def test_print_script_from_json(tmp_path):
    m = _load()
    f = tmp_path / "answers.json"
    f.write_text('{"xk88_good": "Fine"}')
    import json
    answers = json.loads(f.read_text())
    script = m.build_applescript(answers, dt.date(2026, 7, 19))
    assert '("B" & weekRow_xk88) of ws to "Fine"' in script


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
