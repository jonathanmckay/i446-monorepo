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
    assert 'string value of range "A2:A1000" of ws' in script
    assert script.count('string value of range "A2:A1000" of ws') == len(m.SHEETS)


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
    2026-08-11) at each page boundary onto the shared BackgroundWriter
    (lib/review_form_writer.py) -- the actual write is scoped to that one
    page's sheet (sheets=[cfg])."""
    import inspect
    m = _load()
    assert callable(m.run_paginated)
    src = inspect.getsource(m.run_paginated)
    assert "queue_write" in src, \
        "run_paginated must queue the write, not call write_answers directly (would block)"
    qw_src = inspect.getsource(m.queue_write)
    assert "write_answers" in qw_src and "sheets=[cfg]" in qw_src
    assert isinstance(m._writer, m.BackgroundWriter)
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


# ── finishing the survey closes the open family cards (2026-09-29) ──────────

def _queue():
    return {"1neon": [
        {"id": "a", "content": "1 xk88 (5) [15]", "labels": ["1neon", "xk88"], "due": "2026-09-29"},
        {"id": "b", "content": "1 xk87 wknd (20) [20]", "labels": ["1neon", "xk87"], "due": "2026-09-29"},
        {"id": "c", "content": "1 xk87 (20) [45]", "labels": ["1neon", "xk87"], "due": "2026-10-06"},
        {"id": "d", "content": "1 s897 (25) [30]", "labels": ["1neon", "s897"], "due": "2026-09-29"},
    ]}


def test_open_finish_cards_matches_exact_names_due_today_or_earlier():
    m = _load()
    assert m.open_finish_cards(_queue(), dt.date(2026, 9, 29)) == ["1 xk88"]
    # once the xk87 card's due date arrives it joins, in FINISH_CARDS order
    assert m.open_finish_cards(_queue(), dt.date(2026, 10, 6)) == ["1 xk88", "1 xk87"]
    # overdue still counts as on the list
    assert m.open_finish_cards(_queue(), dt.date(2026, 10, 9)) == ["1 xk88", "1 xk87"]


def test_open_finish_cards_never_matches_wknd_variant_or_empty_cache():
    m = _load()
    q = {"1neon": [{"content": "1 xk87 wknd (20) [20]", "due": "2026-09-01"}]}
    assert m.open_finish_cards(q, dt.date(2026, 9, 29)) == []
    assert m.open_finish_cards({}, dt.date(2026, 9, 29)) == []
    # a card with no due date is treated as open
    assert m.open_finish_cards({"1neon": [{"content": "1 xk88"}]}, dt.date(2026, 9, 29)) == ["1 xk88"]


def test_queue_finish_marks_queues_one_run_per_open_card(monkeypatch, tmp_path):
    m = _load()
    cache = tmp_path / "task-queue.json"
    import json as _json
    cache.write_text(_json.dumps(_queue()))
    monkeypatch.setattr(m, "TASK_QUEUE", cache)
    queued = []
    monkeypatch.setattr(m._writer, "queue", lambda fn, *a, **k: queued.append(k.get("tag")))
    assert m.queue_finish_marks(dt.date(2026, 10, 6)) == ["1 xk88", "1 xk87"]
    assert queued == ["did-1 xk88", "did-1 xk87", "dtd-refresh"]


def test_queue_finish_marks_missing_cache_is_a_noop(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setattr(m, "TASK_QUEUE", tmp_path / "absent.json")
    queued = []
    monkeypatch.setattr(m._writer, "queue", lambda fn, *a, **k: queued.append(k.get("tag")))
    assert m.queue_finish_marks(dt.date(2026, 9, 29)) == []
    assert queued == []


def test_run_paginated_marks_on_finish_unless_no_mark():
    import ast
    src = (HERE / "xk887-survey.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_paginated")
    body = ast.get_source_segment(src, fn)
    assert "queue_finish_marks()" in body and "no_mark" in body
    # the marks are queued BEFORE the final drain, so they are waited on
    assert body.index("queue_finish_marks()") < body.rindex("drain_writes()")
    # the cancel path never marks
    cancel = body[body.index("if action is None:"):body.index("if action == \"back\":")]
    assert "queue_finish_marks" not in cancel
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert "--no-mark" in ast.get_source_segment(src, main_fn)


def test_tail_row_lookup_is_one_range_read_not_a_per_row_loop():
    """Same defect class as 0s (2026-10-17): one Apple event per row over 1,000
    rows per sheet blew the 60 s write budget on a slow Excel."""
    src = (Path(__file__).resolve().with_name("xk887-survey.py")).read_text()
    assert 'string value of range "A2:A1000" of ws' in src
    assert 'set av to (string value of range ("A" & r)' not in src


# ── --detach: the form hands the save to a detached saver (2026-11-17) ───────
# "move saving the 1xk887 survey ... to the background so that the UI is more
# snappy, and then we can have the bottom notification letting me know when
# it is saved." Before this the END of the form held dtd's screen until four
# ix-osa writes and two card closes were done.

import json as _json
import subprocess as _sp


class _FakeProc:
    pid = 1


def _detach_env(m, monkeypatch, tmp_path, cache=None):
    """RECOVERY_DIR, a task cache, dtd's env files; spawn recorded, not run."""
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path / "rec")
    q = tmp_path / "task-queue.json"
    q.write_text(_json.dumps(cache if cache is not None else {"1neon": [
        {"id": "A1", "content": "1 xk88 (30) [60]", "due": "2026-11-15"},
        {"id": "B2", "content": "1 xk87 (30) [60]", "due": "2026-11-15"},
        {"id": "C3", "content": "1 xk87 wknd (60) [90]", "due": "2026-11-15"}]}))
    monkeypatch.setattr(m, "TASK_QUEUE", q)
    hide = tmp_path / "removed.ids"
    hdr = tmp_path / "hdr"
    monkeypatch.setenv("DTD_HIDE_IDS_FILE", str(hide))
    monkeypatch.setenv("DTD_TASK_ID", "C3")
    monkeypatch.setenv("DTD_HDR_FILE", str(hdr))
    monkeypatch.delenv("DTD_PORT_FILE", raising=False)
    spawned = []
    monkeypatch.setattr(m.form_detach, "spawn_detached",
                        lambda argv, log, env=None: spawned.append(argv) or _FakeProc())
    wrote = []
    monkeypatch.setattr(m, "write_answers",
                        lambda a, s, sheets=None: wrote.append(sheets[0]["sheet"]) or "OK")
    return hide, hdr, spawned, wrote


def _pages(m, monkeypatch, actions=None):
    """run_page stub: every page submits one field, or follow `actions`."""
    seq = iter(actions or [])

    def _rp(cfg, sunday, saturday, i, total, answers):
        act = next(seq, "submit")
        if act is None:
            return None, {}
        key = m.field_key(cfg["sheet"], cfg["fields"][0][0])
        return act, {key: "x%d" % i}
    monkeypatch.setattr(m, "run_page", _rp)


def _dead_pid():
    p = _sp.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_detach_finish_returns_at_once_without_writing(monkeypatch, tmp_path):
    m = _load()
    hide, hdr, spawned, wrote = _detach_env(m, monkeypatch, tmp_path)
    _pages(m, monkeypatch)
    monkeypatch.setattr(m, "drain_writes", lambda: (_ for _ in ()).throw(AssertionError("must not wait")))
    rc = m.run_paginated(dt.date(2026, 11, 8), dt.date(2026, 11, 14), detach=True)
    assert rc == 0
    assert wrote == [], "the form process must never touch Excel under --detach"
    assert len(spawned) == 1 and "--save-spool" in spawned[0], "exactly one saver child"
    spool = m.read_spool(Path(spawned[0][-1]))
    assert spool["state"] == "finished"
    assert list(spool["pages"]) == ["xk88", "xk20", "xk22", "xk26"]
    assert spool["close"] == [["1 xk88", "A1"], ["1 xk87", "B2"]]
    # every card the saver will close is hidden before this process exits;
    # the wknd row we were launched from is never closed, so never hidden
    assert hide.read_text().split() == ["A1", "B2"]
    assert hdr.read_text().startswith("⏳ xk887 week 11.2")


def test_detach_spawns_at_the_first_page_so_saving_starts_while_typing(monkeypatch, tmp_path):
    m = _load()
    _hide, _hdr, spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    s = m.DetachedSaver(dt.date(2026, 11, 8))
    assert s.page({"xk88_good": "a"}, m.SHEETS[0]) is True
    assert len(spawned) == 1
    assert m.read_spool(s.path)["state"] == "open"
    assert s.page({"xk88_good": "a", "xk20_is": "b"}, m.SHEETS[1]) is True
    assert len(spawned) == 1, "one saver for the whole form"
    assert m.read_spool(s.path)["pages"] == {"xk88": 1, "xk20": 2}
    # going back and re-submitting page 1 bumps its seq so the saver rewrites it
    assert s.page({"xk88_good": "edited", "xk20_is": "b"}, m.SHEETS[0]) is True
    assert m.read_spool(s.path)["pages"] == {"xk88": 3, "xk20": 2}


def test_detach_cancel_after_pages_saves_them_and_closes_nothing(monkeypatch, tmp_path):
    m = _load()
    hide, _hdr, spawned, wrote = _detach_env(m, monkeypatch, tmp_path)
    _pages(m, monkeypatch, ["submit", "submit", None])
    rc = m.run_paginated(dt.date(2026, 11, 8), dt.date(2026, 11, 14), detach=True)
    assert rc == 0, "handed-off pages are being saved: not a failed form (wrapper would hold the screen)"
    spool = m.read_spool(Path(spawned[0][-1]))
    assert spool["state"] == "cancelled" and spool["close"] == []
    assert list(spool["pages"]) == ["xk88", "xk20"]
    assert not hide.exists() and wrote == []


def test_detach_cancel_on_page_one_is_still_a_plain_cancel(monkeypatch, tmp_path):
    m = _load()
    _hide, _hdr, spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    _pages(m, monkeypatch, [None])
    assert m.run_paginated(dt.date(2026, 11, 8), dt.date(2026, 11, 14), detach=True) == 1
    assert spawned == []
    assert not list((tmp_path / "rec").glob("pending-*"))


def test_detach_no_mark_hides_and_closes_nothing(monkeypatch, tmp_path):
    m = _load()
    hide, _hdr, spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    _pages(m, monkeypatch)
    assert m.run_paginated(dt.date(2026, 11, 8), dt.date(2026, 11, 14), no_mark=True, detach=True) == 0
    assert m.read_spool(Path(spawned[0][-1]))["close"] == []
    assert not hide.exists()


def test_detach_falls_back_to_the_inline_writer_when_the_saver_cannot_start(monkeypatch, tmp_path):
    """No child, no hand-off: the answers must still be written, by the old
    path, and waited on."""
    m = _load()
    _hide, _hdr, _spawned, wrote = _detach_env(m, monkeypatch, tmp_path)

    def _no(argv, log, env=None):
        raise OSError("fork failed")
    monkeypatch.setattr(m.form_detach, "spawn_detached", _no)
    monkeypatch.setattr(m, "queue_finish_marks", lambda: [])
    _pages(m, monkeypatch)
    rc = m.run_paginated(dt.date(2026, 11, 8), dt.date(2026, 11, 14), detach=True)
    assert rc == 0
    assert wrote == ["xk88", "xk20", "xk22", "xk26"]
    assert not list((tmp_path / "rec").glob("pending-*")), "no orphan spool left behind"


def _spool(m, tmp_path, pages, state="finished", close=(), answers=None):
    path = tmp_path / "rec" / "pending-11.2-1.json"
    m.write_spool(path, {"week": "2026-11-08", "parent": _dead_pid(), "state": state,
                         "pages": pages, "close": [list(c) for c in close],
                         "answers": answers or {"xk88_good": "a"}})
    return path


def test_saver_writes_pages_in_order_reports_and_removes_the_spool(monkeypatch, tmp_path):
    m = _load()
    hide, hdr, _spawned, wrote = _detach_env(m, monkeypatch, tmp_path)
    monkeypatch.setattr(m, "close_card", lambda card: (True, card + ": ok"))
    monkeypatch.setattr(m, "run_bounded", lambda *a, **k: None)
    hide.write_text("A1\nB2\n")
    path = _spool(m, tmp_path, {"xk20": 2, "xk88": 1, "xk26": 4, "xk22": 3},
                  close=[("1 xk88", "A1"), ("1 xk87", "B2")])
    assert m.run_saver(path, poll=0.01, settle=0) == 0
    assert wrote == ["xk88", "xk20", "xk22", "xk26"]
    assert hdr.read_text().strip() == \
        "✓ xk887 week 11.2 saved (xk88, xk20, xk22, xk26) · closed 1 xk88, 1 xk87"
    assert not path.exists()
    assert hide.read_text().split() == ["A1", "B2"], "closed cards stay hidden"


def test_saver_continues_past_a_failed_sheet_and_says_where_the_answers_are(monkeypatch, tmp_path):
    m = _load()
    _hide, hdr, _spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    wrote = []

    def _w(answers, sunday, sheets=None):
        if sheets[0]["sheet"] == "xk20":
            raise RuntimeError("ERROR: workbook not open")
        wrote.append(sheets[0]["sheet"])
        return "OK"
    monkeypatch.setattr(m, "write_answers", _w)
    path = _spool(m, tmp_path, {"xk88": 1, "xk20": 2, "xk22": 3}, state="cancelled")
    assert m.run_saver(path, poll=0.01, settle=0) == 1
    assert wrote == ["xk88", "xk22"]
    dumps = list((tmp_path / "rec").glob("11.2-xk20-*.json"))
    assert len(dumps) == 1, "the failed sheet has a replayable recovery dump"
    line = hdr.read_text()
    assert line.startswith("✗ xk887 week 11.2: FAILED xk20") and str(dumps[0]) in line
    assert "saved xk88, xk22" in line


def test_saver_unhides_a_card_it_could_not_close(monkeypatch, tmp_path):
    """A card Todoist still shows open must come back in dtd, decided from
    run.py's own result: the cache keeps a closed weekly card (later due), so
    'still in the cache' proves nothing."""
    m = _load()
    hide, hdr, _spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    monkeypatch.setattr(m, "close_card",
                        lambda card: (card == "1 xk88", card + ": x"))
    monkeypatch.setattr(m, "run_bounded", lambda *a, **k: None)
    hide.write_text("A1\nB2\n")
    path = _spool(m, tmp_path, {"xk88": 1}, close=[("1 xk88", "A1"), ("1 xk87", "B2")])
    assert m.run_saver(path, poll=0.01, settle=0) == 1
    assert hide.read_text().split() == ["A1"]
    assert hdr.read_text().strip().endswith("· closed 1 xk88 · NOT closed: 1 xk87")


def test_saver_only_closes_cards_when_the_form_finished(monkeypatch, tmp_path):
    m = _load()
    _hide, _hdr, _spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(m, "close_card", lambda card: called.append(card) or (True, ""))
    # form died mid-survey: state still "open", parent gone
    path = _spool(m, tmp_path, {"xk88": 1}, state="open", close=[("1 xk88", "A1")])
    assert m.run_saver(path, poll=0.01, settle=0) == 0
    assert called == []


def test_saver_rewrites_a_page_resubmitted_while_it_was_saving(monkeypatch, tmp_path):
    m = _load()
    _hide, _hdr, _spawned, _wrote = _detach_env(m, monkeypatch, tmp_path)
    path = _spool(m, tmp_path, {"xk88": 1}, state="cancelled", answers={"xk88_good": "v1"})
    seen = []

    def _w(answers, sunday, sheets=None):
        seen.append(answers["xk88_good"])
        if len(seen) == 1:   # the form re-submits page 1 mid-write
            m.write_spool(path, dict(m.read_spool(path), pages={"xk88": 2},
                                     answers={"xk88_good": "v2"}))
        return "OK"
    monkeypatch.setattr(m, "write_answers", _w)
    assert m.run_saver(path, poll=0.01, settle=0) == 0
    assert seen == ["v1", "v2"]


def test_close_card_needs_a_confirmed_close(monkeypatch):
    m = _load()

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""
    monkeypatch.setattr(m, "run_bounded", lambda *a, **k: R(0, "  ✓ 1 xk88 → 1 + todoist closed\n"))
    assert m.close_card("1 xk88")[0] is True
    monkeypatch.setattr(m, "run_bounded", lambda *a, **k: R(0, "  ✓ 1 xk88 → 1\n"))
    assert m.close_card("1 xk88")[0] is False
    monkeypatch.setattr(m, "run_bounded", lambda *a, **k: R(1, "  ✗ Todoist close failed\n"))
    assert m.close_card("1 xk88")[0] is False

    def _t(*a, **k):
        raise _sp.TimeoutExpired("run.py", 120)
    monkeypatch.setattr(m, "run_bounded", _t)
    assert m.close_card("1 xk88")[0] is False


def test_finish_card_tasks_skips_closed_weekly_cards_still_in_the_cache():
    """A closed weekly card stays in the cache with next week's due: only
    id + due <= today means "still to close"."""
    m = _load()
    q = {"1neon": [{"id": "A1", "content": "1 xk88 (30) [60]", "due": "2026-11-24"},
                   {"id": "B2", "content": "1 xk87 (30) [60]", "due": "2026-11-17"},
                   {"id": "C3", "content": "1 xk87 wknd (60) [90]", "due": "2026-11-15"}]}
    assert m.finish_card_tasks(q, dt.date(2026, 11, 17)) == [("1 xk87", "B2")]
    assert m.open_finish_cards(q, dt.date(2026, 11, 17)) == ["1 xk87"]


def test_from_json_replays_a_spool_only_for_the_submitted_pages(tmp_path):
    m = _load()
    p = tmp_path / "pending.json"
    m.write_spool(p, {"week": "2026-11-08", "parent": 1, "state": "open",
                      "pages": {"xk20": 2, "xk88": 1}, "close": [], "answers": {"xk88_good": "a"}})
    answers, only, week = m.answers_from_file(p)
    assert answers == {"xk88_good": "a"} and week == "2026-11-08"
    assert [c["sheet"] for c in only] == ["xk88", "xk20"]
    flat = tmp_path / "flat.json"
    flat.write_text('{"xk88_good": "a"}')
    assert m.answers_from_file(flat) == ({"xk88_good": "a"}, None, None)
