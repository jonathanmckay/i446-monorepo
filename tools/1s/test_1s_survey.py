"""Tests for 1s-survey.py (feature 2026-07-21: /1s asks the 1分+1s manual
questions as a full-screen form, surfacing the week's daily 0s897 answers so
answering is selecting rather than composing de novo)."""
import pytest
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("s1", HERE / "1s-survey.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["s1"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_week_range_defaults_to_last_completed_week():
    m = _load()
    # Tue 2026-07-21 → last completed Sun-Sat is 7/12-7/18.
    s, e = m.week_range(None, today=dt.date(2026, 7, 21))
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))
    # A Sunday reviews the week that JUST ended, not the one starting.
    s, e = m.week_range(None, today=dt.date(2026, 7, 19))
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_week_range_with_date_arg():
    m = _load()
    s, e = m.week_range("2026-07-15")
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_week_row_label_is_sunday_anchored_month_week():
    m = _load()
    assert m.week_row_label(dt.date(2026, 7, 12)) == "7.2"
    assert m.week_row_label(dt.date(2026, 1, 4)) == "1.1"
    assert m.week_row_label(dt.date(2026, 6, 28)) == "6.4"


def test_expand_selections_picks_day_answers():
    m = _load()
    ctx = {"titles": [(0, "Reclaiming sunday"), (2, "Strong Start")],
           "wins": [(1, "Points")]}
    out = m.expand_selections({"title": "3", "win": "2"}, ctx)
    assert out["title"] == "Strong Start"
    assert out["win"] == "Points"


def test_expand_selections_comma_list_joins():
    m = _load()
    ctx = {"titles": [(0, "A"), (1, "B"), (2, "C")]}
    out = m.expand_selections({"title": "1,3"}, ctx)
    assert out["title"] == "A; C"


def test_expand_selections_leaves_prose_alone():
    m = _load()
    ctx = {"titles": [(0, "A")]}
    out = m.expand_selections({"title": "Big week 3"}, ctx)
    assert out["title"] == "Big week 3"   # prose untouched


def test_expand_selection_missing_day_kept_verbatim():
    m = _load()
    out = m.expand_selections({"title": "5"}, {"titles": [(0, "A")]})
    assert out["title"] == "5"


def test_parse_context_offsets_and_blanks():
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    cells = [""] * 16
    cells[0], cells[3], cells[4] = "Title", "Win", "Learn"
    cells[11], cells[12], cells[13] = "8", "5", "6.5"
    cells[14], cells[15] = "Proud", "LearnO"
    raw = "<<ROW 2>>" + "".join("<<F>>" + c for c in cells)
    ctx = m.parse_context(raw, dates)
    assert ctx["titles"] == [(2, "Title")]
    assert ctx["wins"] == [(2, "Win")]
    assert ctx["ceil"] == [(2, "8")] and ctx["mean"] == [(2, "6.5")]
    assert ctx["proud"] == [(2, "Proud")] and ctx["learn_others"] == [(2, "LearnO")]


def test_no_formula_fields_in_form():
    """Regression (2026-07-21): Rating (P), High (W), Low (X), Avg (Y) hold
    live sheet formulas (P='i9'!B{row}; W/X/Y aggregate the week's 0s897
    ⌈/⌊/x̄) — they compute themselves from the daily surveys. A form write
    would clobber the formulas, so those columns must never be FIELDS."""
    m = _load()
    cols = {c for _k, _l, c, _kind, _ck in m.FIELDS}
    assert not cols & {"P", "W", "X", "Y"}


def test_applescript_targets_week_row_and_skips_blanks():
    m = _load()
    script = m.build_applescript({"title": "T", "win": "", "missed": "n"},
                                 dt.date(2026, 7, 12))
    assert '"7.2"' in script
    assert '("R" & weekRow)' in script          # title col
    assert '("T" & weekRow)' in script          # missed col
    assert '("AO" & weekRow)' not in script     # AO belongs to /1hcmc
    assert '("S" & weekRow)' not in script      # blank win skipped
    assert "save wb" in script


def _fake_0l(m, monkeypatch, dates, done):
    """0n through a faked excel-http client (2026-11-25: the 0l read left raw
    ix-osa): col C = the week's days as read_range returns date cells, the 0l
    column = 1.0 on the `done` day indexes. Any raw call fails the test."""
    long = ["%s, %s %d, %d at 12:00:00\u202fAM" % (d.strftime("%A"), d.strftime("%B"), d.day, d.year)
            for d in dates]
    marks = ["1.0" if i in set(done) else "" for i in range(7)]

    def read_range(sheet, rng):
        assert sheet == "0n"
        col = long if rng.startswith("C") else marks
        return {"ok": True, "rows": [[v] for v in col]}
    monkeypatch.setattr(m.neon_excel, "read_range", read_range)

    def no_raw(*a, **k):
        raise AssertionError("raw ix-osa must not run")
    monkeypatch.setattr(m, "run_bounded", no_raw)


def test_check_week_flags_missing_days(monkeypatch):
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    ctx = {"titles": [(0, "t"), (1, "t"), (3, "t"), (4, "t"), (5, "t"), (6, "t")]}
    # 0l done every day except day 2; tracking fine except day 6.
    _fake_0l(m, monkeypatch, dates, [i for i in range(7) if i != 2])
    monkeypatch.setattr(m, "_toggl_minutes_by_day",
                        lambda ds: {d: (600 if i == 6 else 1400) for i, d in enumerate(ds)})
    b = m.check_week(dates, ctx)
    assert b["missing_0s"] == [dates[2]]
    assert b["missing_0l"] == [dates[2]]
    assert b["low_tracking"] == [(dates[6], 600)]
    text = m.format_blockers(b)
    assert "/0s 2026-07-14" in text and "/did 0l 7/14" in text and "/tg" in text


def test_check_week_clear_when_complete(monkeypatch):
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    ctx = {"titles": [(i, "t") for i in range(7)]}
    _fake_0l(m, monkeypatch, dates, range(7))
    monkeypatch.setattr(m, "_toggl_minutes_by_day",
                        lambda ds: {d: 1400 for d in ds})
    b = m.check_week(dates, ctx)
    assert not any(b.values())


def test_survey_save_marks_1s_done_in_source():
    """User decision 2026-07-21: completing the survey completes the weekly
    1s task — the tool runs did-fast's runner after a successful write (with
    a --no-mark escape hatch), and the /1s skill no longer marks it."""
    src = (HERE / "1s-survey.py").read_text()
    assert 'run.py"), "1s"' in src.replace("'", '"')
    assert "--no-mark" in src


def test_daily_context_matches_date_value_not_display(monkeypatch):
    """Col B holds real date cells. A date's `as text` is the long locale form
    (bug found live 2026-07-21), and matching the displayed string broke when
    the display moved m/d/yy -> yyyy.mm.dd (2026-11-03). Match the date VALUE,
    year included (2026-11-25: read through excel-http, see test_1s_reads.py)."""
    m = _load()
    rows = [["Sunday, July 12, 2026 at 12:00:00\u202fAM"] + [""] * 18,
            ["Saturday, July 12, 2025 at 12:00:00\u202fAM", "", "", "last year"] + [""] * 15,
            ["2026.07.12"] + [""] * 18]
    rows[0][3] = "Title"
    monkeypatch.setattr(m.neon_excel, "read_range", lambda sheet, rng: {"ok": True, "rows": rows})
    assert m.fetch_context([dt.date(2026, 7, 12)])["titles"] == [(0, "Title")]


# ── Shared BackgroundWriter adoption (2026-08-11) ───────────────────────────
# 1s had NO recovery mechanism before this, same gap 0s had. Adopting
# lib/review_form_writer.py's BackgroundWriter (the same module xk887/0s use)
# backfills that. No wall-clock speedup here either (single-page form, no
# "next page" to advance into) — the Neon write and the 1s-mark-done call
# stay serialized on purpose (JM 2026-08-11), same reasoning as 0s.

def test_main_uses_shared_background_writer():
    m = _load()
    assert isinstance(m._writer, m.BackgroundWriter)


def test_drain_happens_before_cmux_close_in_source():
    """close-surface tears down the pane; it must never fire while a queued
    write might still need the terminal to report its result."""
    import inspect
    m = _load()
    src = inspect.getsource(m.main)
    drain_idx = src.index("_writer.drain()")
    close_idx = src.index('"cmux", "close-surface"')
    assert drain_idx < close_idx, "drain() must run before cmux close-surface"


def _daemon(m, monkeypatch, replies):
    calls = []
    def fake(sheet, items, **kw):
        calls.append((sheet, items, kw))
        return replies.pop(0) if len(replies) > 1 else replies[0]
    monkeypatch.setattr(m.neon_excel, "batch_write", fake)
    def no_raw(*a, **k):
        raise AssertionError("raw ix-osa must not run")
    monkeypatch.setattr(m, "run_bounded", no_raw)
    return calls


def test_daemon_write_chunks_and_skips_blanks(monkeypatch):
    m = _load()
    calls = _daemon(m, monkeypatch, [{"ok": True, "row": 40, "results": [1, 1, 1, 1]},
                                     {"ok": True, "row": 40, "results": [1]}])
    answers = {f[0]: "a" for f in m.FIELDS}
    out = m.write_answers(answers, dt.date(2026, 7, 12))
    assert [len(c[1]) for c in calls] == [4, 1]
    assert all(i["mode"] == "text" for c in calls for i in c[1])
    assert calls[0][2]["date"] == "7.2" and calls[0][2]["fallback"] is False
    assert out == "OK: weekRow=40 wrote=5"


def test_no_answers_skips_the_daemon(monkeypatch):
    m = _load()
    calls = _daemon(m, monkeypatch, [{"ok": True}])
    assert m.write_answers({"title": "  "}, dt.date(2026, 7, 12)) == "OK: nothing to write"
    assert calls == []


def test_daemon_refusal_raises_and_skips_raw(monkeypatch):
    m = _load()
    _daemon(m, monkeypatch, [{"ok": False, "error": "label_not_found"}])
    with pytest.raises(RuntimeError, match="label_not_found"):
        m.write_answers({"title": "T"}, dt.date(2026, 7, 12))


def test_stalled_excel_retries_once(monkeypatch):
    m = _load()
    calls = _daemon(m, monkeypatch, [{"ok": False, "error": "osascript_timeout"},
                                     {"ok": True, "row": 40, "results": [1]}])
    assert m.write_answers({"title": "T"}, dt.date(2026, 7, 12)).startswith("OK")
    assert len(calls) == 2


def test_daemon_down_uses_raw(monkeypatch):
    m = _load()
    _daemon(m, monkeypatch, [{"ok": False, "error": "daemon_unreachable", "fallback": True}])
    seen = {}
    class P:
        stdout, stderr, returncode = "OK: weekRow=40 wrote=1", "", 0
    def raw(cmd, input=None, timeout=None):
        seen["script"] = input
        return P
    monkeypatch.setattr(m, "run_bounded", raw)
    assert m.write_answers({"title": "T"}, dt.date(2026, 7, 12)) == "OK: weekRow=40 wrote=1"
    assert "save wb" in seen["script"]


def test_test_guard_error_never_reaches_raw(monkeypatch):
    m = _load()
    def guard(*a, **k):
        raise RuntimeError("neon client called from a test")
    monkeypatch.setattr(m.neon_excel, "batch_write", guard)
    def no_raw(*a, **k):
        raise AssertionError("raw ix-osa must not run")
    monkeypatch.setattr(m, "run_bounded", no_raw)
    with pytest.raises(RuntimeError, match="from a test"):
        m.write_answers({"title": "T"}, dt.date(2026, 7, 12))


def test_failed_write_does_not_mark_1s_done(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(m, "fetch_context", lambda dates: {k: [] for k in m.DAILY_COLS})
    def _boom(answers, sunday):
        raise RuntimeError("no")
    monkeypatch.setattr(m, "write_answers", _boom)
    ran = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: ran.append(a))
    f = tmp_path / "answers.json"
    f.write_text('{"title": "typed"}')
    monkeypatch.setattr("sys.argv", ["1s-survey.py", "--from-json", str(f)])
    assert m.main() == 1
    assert ran == []


def test_write_failure_dumps_recovery_and_returns_nonzero(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(m, "fetch_context", lambda dates: {k: [] for k in m.DAILY_COLS})

    def _boom(answers, sunday):
        raise RuntimeError("Invalid object specifier (workbook not open)")
    monkeypatch.setattr(m, "write_answers", _boom)

    f = tmp_path / "answers.json"
    f.write_text('{"good": "typed answer"}')
    monkeypatch.setattr("sys.argv", ["1s-survey.py", "--from-json", str(f), "--no-mark"])

    rc = m.main()
    assert rc == 1, "a failed Neon write must make main() exit non-zero"
    assert list(tmp_path.glob("recovery*.json")), "expected a recovery dump for the failed write"


# ── --detach (2026-11-17): the form process saves nothing itself ────────────
# "move saving ... (all surveys) to the background so that the UI is more
# snappy, and then we can have the bottom notification letting me know when it
# is saved." The form writes the answers to RECOVERY_DIR, starts one detached
# child (--from-json ... --report-hdr) and exits; the child reports in dtd's
# status line.

SUN = dt.date(2026, 7, 12)


def _detach_env(m, monkeypatch, tmp_path, result, argv=("--detach", "--force")):
    """Interactive main() with the form, context fetch and every writer faked.
    Returns a log of everything that would have touched live data."""
    log = {"spawn": [], "writes": [], "runs": [], "hdr": [], "order": []}
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path / "rec")
    monkeypatch.setattr(m, "LOG", tmp_path / "1s.log")
    monkeypatch.setattr(m, "fetch_context", lambda dates: {k: [] for k in m.DAILY_COLS})
    seen = {}
    def form(sunday, dates, ctx, no_mark=False, detach=False):
        seen.update(no_mark=no_mark, detach=detach)
        return result
    monkeypatch.setattr(m, "run_form", form)
    log["form"] = seen
    def write(answers, sunday):
        log["writes"].append((dict(answers), sunday))
        return "OK: weekRow=40 wrote=1"
    monkeypatch.setattr(m, "write_answers", write)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: log["runs"].append(a))
    def bounded(*a, **k):
        log["runs"].append(a)
        raise AssertionError("no subprocess in the form process")
    monkeypatch.setattr(m, "run_bounded", bounded)
    def spawn(argv, logf, env=None):
        log["order"].append("spawn")
        log["spawn"].append((list(argv), logf, Path(argv[argv.index("--from-json") + 1]).read_text()))
    monkeypatch.setattr(m.form_detach, "spawn_detached", spawn)
    monkeypatch.setattr(m.form_detach, "hdr",
                        lambda msg, poke=True: log["hdr"].append((msg, poke)) or True)
    monkeypatch.setattr(m.dtd_hide, "hide_card",
                        lambda tid=None: log["order"].append("hide") or True)
    monkeypatch.setattr(m.dtd_hide, "unhide_card",
                        lambda tid=None: log["order"].append("unhide") or True)
    monkeypatch.setattr("sys.argv", ["1s-survey.py", SUN.isoformat(), *argv])
    return log


def _pending(answers, filled=1):
    return {"answers": answers, "all_ok": None, "filled": filled, "pending": True}


def test_detach_spawns_one_child_and_writes_nothing(monkeypatch, tmp_path):
    import json
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "Big week", "win": ""}),
                      argv=("--detach", "--force", "--no-mark"))
    assert m.main() == 0
    assert log["form"] == {"no_mark": True, "detach": True}
    assert log["writes"] == [] and log["runs"] == []
    assert len(log["spawn"]) == 1
    argv, logf, content = log["spawn"][0]
    assert argv[1].endswith("1s-survey.py") and argv[2] == "2026-07-12"
    for flag in ("--from-json", "--unlink-json", "--expanded", "--report-hdr", "--no-mark"):
        assert flag in argv
    assert "--detach" not in argv            # the child must not detach again
    path = Path(argv[argv.index("--from-json") + 1])
    assert path.parent == tmp_path / "rec" and path.exists()   # recovery dir, not /tmp
    assert json.loads(content) == {"title": "Big week", "win": "", "_week": "2026-07-12"}
    assert logf == tmp_path / "1s.log"
    (msg, poke), = log["hdr"]
    assert msg.startswith("⏳ 1s week 7.2: saving in the background …") and poke is False
    assert log["order"] == ["spawn"]         # --no-mark closes no card: nothing hidden


def test_detach_hides_card_before_spawn_when_it_will_close_it(monkeypatch, tmp_path):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "T"}))
    assert m.main() == 0
    assert log["order"] == ["hide", "spawn"]
    assert "--no-mark" not in log["spawn"][0][0]
    assert log["writes"] == [] and log["runs"] == []


def test_detach_spawn_failure_falls_back_inline(monkeypatch, tmp_path, capsys):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "T"}))
    def no_spawn(argv, logf, env=None):
        raise OSError("fork: resource temporarily unavailable")
    monkeypatch.setattr(m.form_detach, "spawn_detached", no_spawn)
    assert m.main() == 0
    assert log["writes"] == [({"title": "T"}, SUN)]            # saved inline
    assert len(log["runs"]) == 1 and log["runs"][0][0][-1] == "1s"   # and marked, as today
    assert not any(a[0][:1] == ["cmux"] for a in log["runs"])  # dtd's pane stays open
    assert log["order"] == ["hide", "unhide"]                  # row back: no child will close it
    assert log["hdr"] == []                                    # no "saving in the background" claim
    assert list((tmp_path / "rec").glob("pending-*")) == []    # Neon has them: file gone
    assert "saving inline" in capsys.readouterr().out


def test_detach_fallback_failure_keeps_the_answers_file(monkeypatch, tmp_path):
    import json
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "T"}),
                      argv=("--detach", "--force", "--no-mark"))
    monkeypatch.setattr(m.form_detach, "spawn_detached",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    def boom(answers, sunday):
        raise RuntimeError("excel down")
    monkeypatch.setattr(m, "write_answers", boom)
    assert m.main() == 1
    kept, = (tmp_path / "rec").glob("pending-*.json")
    assert json.loads(kept.read_text())["title"] == "T"


def test_detach_unwritable_answers_file_saves_inline(monkeypatch, tmp_path):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "T"}),
                      argv=("--detach", "--force", "--no-mark"))
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(m, "RECOVERY_DIR", blocker / "rec")    # mkdir fails: not a directory
    assert m.main() == 0
    assert log["spawn"] == [] and log["writes"] == [({"title": "T"}, SUN)]


def test_detach_cancel_spawns_nothing(monkeypatch, tmp_path):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, None)
    assert m.main() == 1
    assert log["spawn"] == [] and log["writes"] == [] and log["runs"] == []
    assert log["order"] == [] and log["hdr"] == []
    assert not (tmp_path / "rec").exists()


def test_detach_empty_form_spawns_nothing(monkeypatch, tmp_path):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path, _pending({"title": "", "win": ""}, filled=0))
    assert m.main() == 0
    assert log["spawn"] == [] and log["writes"] == [] and log["runs"] == []
    assert log["order"] == [] and not (tmp_path / "rec").exists()


def test_without_detach_form_is_not_asked_to_detach(monkeypatch, tmp_path):
    m = _load()
    log = _detach_env(m, monkeypatch, tmp_path,
                      {"answers": {"title": "T"}, "all_ok": False, "filled": 1},
                      argv=("--force", "--no-mark"))
    assert m.main() == 1
    assert log["form"] == {"no_mark": True, "detach": False}
    assert log["spawn"] == [] and log["hdr"] == [] and log["order"] == []


def _child_env(m, monkeypatch, tmp_path, answers, extra=(), run_out=None, write=None):
    """The detached child: main() with --from-json ... --report-hdr."""
    import json
    log = {"writes": [], "runs": [], "announce": [], "order": []}
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path / "rec")
    def no_ctx(dates):
        raise AssertionError("the child must not re-read the daily context")
    monkeypatch.setattr(m, "fetch_context", no_ctx)
    def ok_write(a, sunday):
        log["order"].append("write")
        log["writes"].append((dict(a), sunday))
        return "OK: weekRow=40 wrote=1"
    monkeypatch.setattr(m, "write_answers", write or ok_write)
    def bounded(cmd, timeout=None, **k):
        log["order"].append("mark")
        log["runs"].append(cmd)
        if isinstance(run_out, BaseException):
            raise run_out
        return type("P", (), {"returncode": 0, "stdout": run_out or "", "stderr": ""})()
    monkeypatch.setattr(m, "run_bounded", bounded)
    monkeypatch.setattr(m.form_detach, "announce",
                        lambda msg, **k: log["announce"].append(msg) or True)
    monkeypatch.setattr(m.dtd_hide, "hide_card",
                        lambda tid=None: log["order"].append("hide") or True)
    monkeypatch.setattr(m.dtd_hide, "unhide_card",
                        lambda tid=None: log["order"].append("unhide") or True)
    f = tmp_path / "pending-7.2-1.json"
    f.write_text(json.dumps(dict(answers, _week="2026-07-12")))
    monkeypatch.setattr("sys.argv", ["1s-survey.py", "2026-07-12", "--from-json", str(f),
                                     "--unlink-json", "--expanded", "--report-hdr", *extra])
    log["file"] = f
    return log


def test_child_success_header_and_unlink(monkeypatch, tmp_path):
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "3", "win": "W"}, extra=("--no-mark",))
    assert m.main() == 0
    # "3" stays "3": the form already expanded day picks, the child must not.
    assert log["writes"] == [({"title": "3", "win": "W"}, SUN)]
    assert log["runs"] == []
    assert log["announce"] == ["✓ 1s week 7.2 saved (2 fields)"]
    assert not log["file"].exists()
    assert "unhide" not in log["order"]


def test_child_write_then_mark_and_confirmed_close(monkeypatch, tmp_path):
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"},
                     run_out="  ✓ 1s → 1n+ + todoist closed\n")
    assert m.main() == 0
    assert log["order"] == ["write", "mark"]
    assert log["runs"][0][-2:] == [str(m.RUN_PY), "1s"]
    assert log["announce"] == ["✓ 1s week 7.2 saved (1 field) · 1s closed"]


def test_child_already_done_this_week_counts_as_closed(monkeypatch, tmp_path):
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"},
                     run_out="  = 1s already done this week\n")
    assert m.main() == 0
    assert "unhide" not in log["order"]


def test_child_failure_header_names_a_replayable_file(monkeypatch, tmp_path):
    import json
    m = _load()
    def boom(a, sunday):
        raise RuntimeError("Invalid object specifier\n(workbook not open)")
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"}, write=boom)
    assert m.main() == 1
    assert log["runs"] == []                     # a failed write never marks
    msg, = log["announce"]
    assert msg.startswith("✗ 1s week 7.2 save FAILED (1分+1s: Invalid object specifier (workbook not open)) answers in ")
    kept = Path(msg.rsplit(" answers in ", 1)[1])
    assert kept.exists() and kept.parent == tmp_path / "rec"
    assert json.loads(kept.read_text()) == {"title": "T"}
    assert log["order"] == ["unhide"]            # card was hidden by the form, still open


@pytest.mark.parametrize("run_out", [
    "  ✗ 1s: todoist error\n",                   # ran, close not confirmed
    "",                                          # no output at all
    __import__("subprocess").TimeoutExpired("run.py", 120),
])
def test_child_unconfirmed_close_unhides_and_reports(monkeypatch, tmp_path, run_out):
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"}, run_out=run_out)
    assert m.main() == 1
    assert log["order"] == ["write", "mark", "unhide"]
    msg, = log["announce"]
    assert msg.startswith("✗ 1s week 7.2 save FAILED (1s card NOT closed")
    assert msg.endswith("answers are in Neon")
    assert not log["file"].exists()              # the answers did land


def test_child_crash_keeps_file_unhides_and_reports(monkeypatch, tmp_path):
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"})
    def crash(*a, **k):
        raise KeyError("boom")
    monkeypatch.setattr(m, "queue_save", crash)
    with pytest.raises(KeyError):
        m.main()
    assert log["order"] == ["unhide"]
    assert log["announce"] == ["✗ 1s save FAILED (KeyError: 'boom') answers in %s" % log["file"]]
    assert log["file"].exists()


def test_child_never_ticks_when_the_writer_died_silently(monkeypatch, tmp_path):
    """drain() is True when the worker recorded nothing (it died inside the
    recovery dump). That must not read as saved, nor delete the answers."""
    m = _load()
    log = _child_env(m, monkeypatch, tmp_path, {"title": "T"}, extra=("--no-mark",))
    monkeypatch.setattr(m, "queue_save", lambda *a, **k: None)
    assert m.main() == 1
    msg, = log["announce"]
    assert msg.startswith("✗ 1s week 7.2 save FAILED") and msg.endswith(str(log["file"]))
    assert log["file"].exists()


def test_week_key_replays_into_the_recorded_week(monkeypatch, tmp_path):
    """A leftover answers file replayed with a bare --from-json lands in the
    week it was typed for, and the key never reaches the sheet."""
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(m, "fetch_context", lambda dates: {k: [] for k in m.DAILY_COLS})
    got = []
    monkeypatch.setattr(m, "write_answers", lambda a, s: got.append((dict(a), s)) or "OK")
    f = tmp_path / "pending.json"
    f.write_text('{"title": "T", "_week": "2026-07-12"}')
    monkeypatch.setattr("sys.argv", ["1s-survey.py", "--from-json", str(f), "--no-mark"])
    assert m.main() == 0
    assert got == [({"title": "T"}, SUN)]
    assert f.exists()                            # no --unlink-json: left alone


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
