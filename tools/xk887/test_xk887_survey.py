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


def test_applescript_opens_workbook_if_not_already_open():
    """2026-08-04: xk887.xlsx isn't kept open like Neon分v12.2.xlsx -- it
    drifts closed between infrequent /xk887 runs, and referencing `workbook
    "xk887.xlsx"` while closed threw uncaught all the way up through
    main(), crashing the whole process (and its cmux pane) right after the
    user submitted a page, silently losing what they'd typed. Confirmed
    live: the workbook was closed, the last real week of data across all
    four sheets was ~3 months old."""
    m = _load()
    script = m.build_applescript({"xk88_good": "x"}, dt.date(2026, 7, 19))
    open_idx = script.index('wbNames does not contain "%s"' % m.WORKBOOK)
    ref_idx = script.index('set wb to workbook "%s"' % m.WORKBOOK)
    assert open_idx < ref_idx, "must check/open the workbook BEFORE referencing it"
    assert str(m.WORKBOOK_PATH) in script


def test_workbook_path_uses_cloudstorage_convention():
    """ix's OneDrive mount is Library/CloudStorage/OneDrive-Personal/..., not
    a plain ~/OneDrive symlink (confirmed via mdfind on ix) -- a guessed
    ~/OneDrive/vault-excel path silently fails to find the file."""
    m = _load()
    assert "Library/CloudStorage/OneDrive-Personal" in str(m.WORKBOOK_PATH)


def test_dump_recovery_persists_answers_and_is_replayable(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    answers = {"xk88_good": "great week", "xk20_notes": "n"}
    path = m.dump_recovery(answers, dt.date(2026, 7, 19), sheet="xk88")
    assert path.exists()
    assert path.parent == tmp_path
    assert "7.3" in path.name and "xk88" in path.name
    import json
    assert json.loads(path.read_text()) == answers


def test_dump_recovery_does_not_collide_on_repeated_failures(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    p1 = m.dump_recovery({"a": "1"}, dt.date(2026, 7, 19), sheet="xk88")
    import time
    time.sleep(1.1)  # timestamp resolution is seconds
    p2 = m.dump_recovery({"a": "2"}, dt.date(2026, 7, 19), sheet="xk88")
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_run_paginated_survives_a_write_failure_without_crashing(monkeypatch, tmp_path):
    """The old behavior: an uncaught RuntimeError from write_answers()
    propagated straight out of run_paginated() -- a bare traceback, and (via
    cmux respawn-pane) the whole pane getting reaped. It must instead return
    a plain exit code and leave a recovery file behind."""
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(m, "run_page",
                        lambda cfg, sunday, saturday, i, total, answers:
                            ("submit", {"%s_good" % cfg["sheet"]: "x"}))

    def _boom(answers, sunday, sheets=None):
        raise RuntimeError("Invalid object specifier (workbook not open)")
    monkeypatch.setattr(m, "write_answers", _boom)

    rc = m.run_paginated(dt.date(2026, 7, 19), dt.date(2026, 7, 25))
    assert rc == 1
    assert list(tmp_path.glob("*xk88*.json")), "expected a recovery dump for the failed page"


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
    assert 'set isNew_xk88 to (tailLabel_xk88 is not "7.3")' in script
    assert 'set weekRow_xk88 to lastRow_xk88 + 1' in script
    assert '("A" & weekRow_xk88) of ws to 7.3' in script


def test_week_row_only_matches_the_tail_not_any_historical_row():
    """The M.W label has no year component (e.g. "7.3" recurs every year),
    so matching must be scoped to the sheet's tail row only -- scanning the
    whole column for a string match risks landing on an old year's row with
    the same label well before the real tail, silently overwriting stale
    history instead of appending a new one."""
    m = _load()
    script = m.build_applescript({}, dt.date(2026, 7, 19))
    for cfg in m.SHEETS:
        v = cfg["sheet"]
        assert 'set isNew_%s to (tailLabel_%s is not "7.3")' % (v, v) in script
        # the old buggy pattern (match anywhere in rows 2-1000) must be gone
        assert 'if av = "7.3" then set weekRow_%s to r' % v not in script


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


def test_applescript_sheets_subset_writes_only_that_sheet():
    """Paginated form writes one sheet per page via sheets=[cfg]."""
    m = _load()
    cfg = next(c for c in m.SHEETS if c["sheet"] == "xk20")
    script = m.build_applescript({"xk20_is": "curious"}, dt.date(2026, 7, 19), sheets=[cfg])
    assert 'worksheet "xk20"' in script
    for other in ("xk88", "xk22", "xk26"):
        assert 'worksheet "%s"' % other not in script


def test_applescript_default_still_covers_all_sheets():
    m = _load()
    script = m.build_applescript({}, dt.date(2026, 7, 19))
    for s in ("xk88", "xk20", "xk22", "xk26"):
        assert 'worksheet "%s"' % s in script


def test_paginated_entrypoint_exists_and_form_is_paged():
    """The interactive path pages per sheet and writes at each page boundary."""
    import inspect
    m = _load()
    assert callable(m.run_paginated)
    src = inspect.getsource(m.run_paginated)
    assert "write_answers" in src and "sheets=[cfg]" in src
    page_src = inspect.getsource(m.run_page)
    assert "Dimension(min=1" in page_src  # compact rows: no reserved blank height
