"""Tests for xk887-survey.py: /xk887 weekly review across 4 sheets
(xk88 marriage/social, xk20 Theo, xk22 Ren, xk26 Rori) in xk887.xlsx."""
import datetime as dt
import importlib.util
import sys
import threading
import time
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


def test_week_range_accepts_week_label_directly():
    """2026-08-04: 'xk887 7.4' should target that week directly, without
    needing to know/guess a date that falls inside it."""
    m = _load()
    s, e = m.week_range("7.4")
    assert m.week_row_label(s) == "7.4"
    assert (e - s).days == 6


def test_sunday_for_week_label_round_trips_with_week_row_label():
    m = _load()
    for d in (dt.date(2026, 7, 19), dt.date(2026, 1, 4), dt.date(2026, 12, 27),
              dt.date(2026, 2, 1)):
        label = m.week_row_label(d)
        assert m.sunday_for_week_label(label, year=d.year) == d


def test_sunday_for_week_label_defaults_to_current_year():
    m = _load()
    label = m.week_row_label(dt.date.today())
    # Whatever week we're actually in right now, asking for its own label
    # back (no explicit year) must resolve to a Sunday in the current year.
    assert m.sunday_for_week_label(label).year == dt.date.today().year


def test_sunday_for_week_label_rejects_nonexistent_week():
    m = _load()
    import pytest
    with pytest.raises(ValueError):
        m.sunday_for_week_label("2.6", year=2026)  # Feb never has a 6th Sunday-week


def test_week_range_still_accepts_iso_dates():
    """Must not regress the existing 'any date in the week' arg form."""
    m = _load()
    s, e = m.week_range("2026-07-15")
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_free_page_navigation_bound_from_any_field():
    """2026-08-04: previously the only way off a page was Tab/Enter at the
    LAST field (forward) or S-Tab at the FIRST field (back) -- revisiting an
    earlier or later page meant tabbing through every field in between.
    ^Right/^PageDown must submit-and-advance, ^Left/^PageUp must go back,
    from anywhere on the page."""
    src = (Path(__file__).parent / "xk887-survey.py").read_text()
    start = src.index("def run_page(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    for key in ('"c-right"', '"c-pagedown"'):
        idx = body.index(key)
        # the next kb.add-decorated function after this key must call _submit
        fn_start = body.index("def _(e):", idx)
        fn_end = body.index("\n\n", fn_start)
        assert "_submit(e.app)" in body[fn_start:fn_end], (
            "%s must trigger the same submit-and-advance as Tab/Enter" % key)
    for key in ('"c-left"', '"c-pageup"'):
        idx = body.index(key)
        fn_start = body.index("def _(e):", idx)
        fn_end = body.index("\n\n", fn_start)
        assert 'result="back"' in body[fn_start:fn_end], (
            "%s must trigger the same back-navigation as S-Tab" % key)


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
    """The interactive path pages per sheet and QUEUES a write (non-blocking,
    2026-08-11) at each page boundary; the background worker performs the
    actual write scoped to that one page's sheet (sheets=[cfg])."""
    import inspect
    m = _load()
    assert callable(m.run_paginated)
    src = inspect.getsource(m.run_paginated)
    assert "queue_write" in src, \
        "run_paginated must queue the write, not call write_answers directly (would block)"
    worker_src = inspect.getsource(m._writer_loop)
    assert "write_answers" in worker_src and "sheets=[cfg]" in worker_src
    page_src = inspect.getsource(m.run_page)
    assert "Dimension(min=1" in page_src  # compact rows: no reserved blank height


# ── Non-blocking background writer (2026-08-11) ─────────────────────────────
# "the save function takes way too long, it should be non-blocking while I go
# on to the next field" — write_answers() is an ix-osa round trip (Excel +
# AppleScript on ix, 15s+ when the daemon is under load) that used to run
# synchronously between run_page() calls. It now queues onto a single
# background worker thread so the next page renders immediately.

def test_queue_write_returns_immediately_even_if_write_is_slow(monkeypatch):
    m = _load()
    release = threading.Event()
    started = threading.Event()

    def _slow(answers, sunday, sheets=None):
        started.set()
        release.wait(timeout=2)
        return "OK"
    monkeypatch.setattr(m, "write_answers", _slow)

    t0 = time.monotonic()
    m.queue_write({"xk88_good": "x"}, dt.date(2026, 7, 19), m.SHEETS[0])
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, "queue_write must return immediately, not block on write_answers"

    assert started.wait(timeout=1), "the background worker should pick up the queued write"
    release.set()
    assert m.drain_writes() is True


def test_writes_never_run_concurrently(monkeypatch):
    """Two overlapping ix-osa writes to the same open workbook is a real
    corruption risk, not just a style concern — the background worker must
    process queued writes one at a time even though queuing itself doesn't
    block the caller."""
    m = _load()
    lock = threading.Lock()
    in_flight = []
    max_concurrent = [0]

    def _tracked(answers, sunday, sheets=None):
        with lock:
            in_flight.append(1)
            max_concurrent[0] = max(max_concurrent[0], len(in_flight))
        time.sleep(0.05)
        with lock:
            in_flight.pop()
        return "OK"
    monkeypatch.setattr(m, "write_answers", _tracked)

    for cfg in m.SHEETS:
        m.queue_write({}, dt.date(2026, 7, 19), cfg)
    assert m.drain_writes() is True
    assert max_concurrent[0] == 1, "queued writes must be serialized, never concurrent"


def test_queue_write_snapshots_answers_not_a_live_reference(monkeypatch):
    """answers is one growing dict mutated by every subsequent page
    (answers.update(page)) — queue_write must snapshot it at queue time, or
    a background write for page 1 could pick up page 3's data by the time
    the worker actually gets to it."""
    m = _load()
    captured = {}
    release = threading.Event()

    def _capture(answers, sunday, sheets=None):
        release.wait(timeout=2)
        captured.update(answers)
        return "OK"
    monkeypatch.setattr(m, "write_answers", _capture)

    answers = {"xk88_good": "before"}
    m.queue_write(answers, dt.date(2026, 7, 19), m.SHEETS[0])
    answers["xk88_good"] = "after-mutation"  # simulates a later page's answers.update()
    answers["xk20_notes"] = "page 3 data"
    release.set()
    m.drain_writes()

    assert captured["xk88_good"] == "before", \
        "queue_write must snapshot answers at queue time, not read a live-mutated dict later"
    assert "xk20_notes" not in captured


def test_drain_writes_reports_failure_and_dumps_recovery(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)

    def _boom(answers, sunday, sheets=None):
        raise RuntimeError("Invalid object specifier (workbook not open)")
    monkeypatch.setattr(m, "write_answers", _boom)

    m.queue_write({"xk88_good": "x"}, dt.date(2026, 7, 19), m.SHEETS[0])
    assert m.drain_writes() is False
    assert list(tmp_path.glob("*xk88*.json")), "a failed background write must still dump recovery"
