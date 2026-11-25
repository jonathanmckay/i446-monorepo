#!/usr/bin/env python3
"""/0n (2026-09-21): mark a 0₦ habit on a past date, move its points to today.
Pure-helper tests: date parsing, formula-append number hygiene, delta
measurement and the Σdelta == weight×value guard (with the 0g +9 exception),
and the AppleScript output parser."""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("zn_backfill", _HERE / "0n-backfill.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zn_backfill"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def zb():
    return _load()


TODAY = date(2026, 9, 21)


@pytest.mark.parametrize("raw,expect", [
    ("09.18", date(2026, 9, 18)), ("9/18", date(2026, 9, 18)), ("9.18", date(2026, 9, 18)),
    ("2026-09-18", date(2026, 9, 18)), ("yesterday", date(2026, 9, 20)),
    ("12/31", date(2025, 12, 31)),  # M/D after today → last year
    ("09.21", date(2026, 9, 21)),   # today's own M/D → today (in-place credit)
    ("09.22", date(2026, 9, 22)),   # tomorrow's own M/D → tomorrow, not last year
    ("9/22", date(2026, 9, 22)),
    ("tomorrow", date(2026, 9, 22)),
    ("today", date(2026, 9, 21)),
    ("09.23", date(2025, 9, 23)),   # day after tomorrow → last year, as before
])
def test_parse_target_date(zb, raw, expect):
    assert zb.parse_target_date(raw, TODAY) == expect


@pytest.mark.parametrize("args,expect", [
    (("nvc", "15", None), ("nvc", "today", "15")),       # the 2026-11-06 crash
    (("15", "nvc", None), ("nvc", "today", "15")),
    (("hiit", "+15", None), ("hiit", "today", "+15")),
    (("hiit", "=15", None), ("hiit", "today", "=15")),
    (("0t", "10/18", None), ("0t", "10/18", "1")),       # dates stay dates
    (("0t", "9.18", None), ("0t", "9.18", "1")),
    (("11.02", "hiit", "+15"), ("hiit", "11.02", "+15")),
    (("hiit", "11.02", "+15"), ("hiit", "11.02", "+15")),
    (("nvc", "today", "15"), ("nvc", "today", "15")),
])
def test_bare_number_with_no_date_is_todays_value(zb, args, expect):
    assert zb.split_args(*args) == expect


def test_unparseable_date_is_an_error_not_a_traceback(zb, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["0n-backfill.py", "nvc", "15", "3"])
    with pytest.raises(SystemExit, match="isn't a date"):
        zb.main()


def test_parse_target_date_tomorrow_across_year(zb):
    nye = date(2026, 12, 31)
    assert zb.parse_target_date("1/1", nye) == date(2027, 1, 1)
    assert zb.parse_target_date("tomorrow", nye) == date(2027, 1, 1)


# ── 戌/亥 advance window (2026-10-17): tomorrow allowed only 18:00-22:00 ─────

@pytest.mark.parametrize("hour,open_", [
    (17, False),  # 酉
    (18, True), (19, True),   # 戌
    (20, True), (21, True),   # 亥
    (22, False),  # 子
    (4, False), (12, False),
])
def test_advance_window_open(zb, hour, open_):
    from datetime import datetime
    assert zb.advance_window_open(datetime(2026, 10, 17, hour, 30)) is open_


def test_col_letter(zb):
    assert [zb.col_letter(n) for n in (1, 19, 26, 27, 32, 58)] == ["A", "S", "Z", "AA", "AF", "BF"]


def test_number_hygiene(zb):
    assert zb.fmt_num(26.0) == "26"
    assert zb.fmt_num(7.5) == "7.5"
    assert zb.signed(20) == "+20" and zb.signed(-20) == "-20"
    assert zb.signed(-(-9)) == "+9"  # no '--9'
    assert zb.parse_num("missing value") == 0.0 and zb.parse_num("") == 0.0
    with pytest.raises(ValueError):
        zb.parse_num("#REF!")


def test_deltas_and_guard(zb):
    pre = {c: 0.0 for c in zb.FEN_COLS}
    pre[20] = 11.0
    post = dict(pre)
    post[20] = 31.0  # 0l (weight 20) landed on 个
    d = zb.compute_deltas(pre, post)
    assert d == {20: 20.0}
    assert zb.deltas_match_expected(d, 20, habit_col=19)
    # 0g: +8 on 个 and +9 on 0g column is the one allowed extra
    post2 = dict(pre); post2[20] = 19.0; post2[17] = 9.0
    d2 = zb.compute_deltas(pre, post2)
    assert zb.deltas_match_expected(d2, 8, habit_col=20)
    assert not zb.deltas_match_expected(d2, 8, habit_col=19)
    # something else moved (e.g. the 111 all-colors bonus) → refuse
    post3 = dict(post); post3[20] = 142.0
    assert not zb.deltas_match_expected(zb.compute_deltas(pre, post3), 20, habit_col=19)


def test_fen_col_for_habit(zb):
    assert zb.fen_col_for_habit(19) == 20   # 0l → 个
    assert zb.fen_col_for_habit(16) == 21   # night hcmc → 媒
    assert zb.fen_col_for_habit(28) == 19   # ibx m5x2 → m5
    assert zb.fen_col_for_habit(21) == 18   # stats i9 → i9


def test_parse_script_output(zb):
    out = "ROWS\t262\t259\nWEIGHT\t20.0\nPRE0N\t\nPRE\t16\t7.0\nPRE\t20\t11.0\nPOST0N\t1.0\nPOST\t16\t7.0\nPOST\t20\t31.0\n"
    r = zb.parse_script_output(out)
    assert r["n_row"] == 262 and r["f_row"] == 259
    assert r["WEIGHT"] == "20.0" and r["PRE0N"] == "" and r["POST0N"] == "1.0"
    assert zb.compute_deltas(r["PRE"], r["POST"]) == {20: 20.0}


def test_week_start_sunday_anchored(zb):
    assert zb.week_start(date(2026, 9, 21)) == date(2026, 9, 20)  # Mon → Sun
    assert zb.week_start(date(2026, 9, 20)) == date(2026, 9, 20)  # Sun stays
    assert zb.week_start(date(2026, 9, 18)) == date(2026, 9, 13)


# ── already-credited guard (2026-09-28) ─────────────────────────────────────

@pytest.mark.parametrize("cell,expect", [
    ("", False), ("   ", False), ("0", False), ("0.0", False),
    ("1", True), ("1.0", True), ("25", True), ("x", True),
])
def test_already_credited(zb, cell, expect):
    assert zb.already_credited(cell) is expect


# --- 2026-10-22: stalled recalc measured {}, "reverted" while the cell stayed 1 ---

def test_remeasure_uses_calculate_full_and_rereads_the_post_image(zb):
    s = zb.build_remeasure_script(6, 3)
    assert "calculate full" in s
    assert s.index("calculate full") < s.index('"POST"')
    assert "set value" not in s and "clear contents" not in s      # read-only besides the recalc


def test_mismatch_triggers_remeasure_before_any_revert():
    src = (_HERE / "0n-backfill.py").read_text()
    i = src.index('deltas = compute_deltas(r["PRE"], r["POST"])')
    block = src[i:i + 1600]
    assert block.index("build_remeasure_script") < block.index("revert_or_die")


@pytest.mark.parametrize("out,pre,ok", [
    ("REVERTED\t", "", True), ("REVERTED\t0.0", "", True),
    ("REVERTED\t1.0", "", False),                 # the 10/22 failure: still marked
    ("REVERTED\t2.0", "2", True), ("", "", False),  # no report at all = not landed
])
def test_revert_landed(zb, out, pre, ok):
    assert zb.revert_landed(out, pre) is ok


def test_failed_revert_is_reported_not_claimed(zb, monkeypatch):
    def down(*a, **k):  # daemon unreachable, as with Excel wedged
        raise RuntimeError("ix unreachable (timeout)")
    monkeypatch.setattr(zb.neon_excel, "batch_write", down)
    with pytest.raises(SystemExit) as e:
        zb.revert_or_die(30, 155, "", "measured 0分 change {} != expected 15", "6/3")
    msg = str(e.value)
    assert "REVERT DID NOT LAND" in msg and "--move-only" in msg and "0n write reverted" not in msg


def test_landed_revert_says_so(zb, monkeypatch):
    monkeypatch.setattr(zb.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": True, "results": [{"value": ""}]})
    with pytest.raises(SystemExit) as e:
        zb.revert_or_die(30, 155, "", "x", "6/3")
    assert "0n write reverted, nothing moved" in str(e.value)


def test_main_never_reads_stdin():
    """Run inside `while read d`, a child (save_and_refresh's ssh) ate the
    loop's input and dates were skipped; main points fd 0 at /dev/null."""
    src = (_HERE / "0n-backfill.py").read_text()
    body = src[src.index("def main() -> int:"):]
    assert "os.dup2(os.open(os.devnull, os.O_RDONLY), 0)" in body[:600]


# --- variable habits + either-order args (2026-11-03: '/0n 11.02 hiit +15') ---
@pytest.mark.parametrize("a,b,expect", [
    ("11.02", "hiit", ("hiit", "11.02")),
    ("hiit", "11.02", ("hiit", "11.02")),
    ("yesterday", "0l", ("0l", "yesterday")),
    ("ibx i9", "9/18", ("ibx i9", "9/18")),
])
def test_order_args(zb, a, b, expect):
    assert zb.order_args(a, b) == expect


@pytest.mark.parametrize("raw,cum,expect", [
    ("+15", False, ("add", 15)), ("=15", False, ("set", 15)),
    ("15", False, ("set", 15)), ("15", True, ("add", 15)),
    ("+7.5", True, ("add", 7.5)), ("-5", False, ("set", -5)),
])
def test_parse_value(zb, raw, cum, expect):
    assert zb.parse_value(raw, cum) == expect


def test_parse_value_rejects_text(zb):
    with pytest.raises(ValueError):
        zb.parse_value("+abc", False)


def test_deltas_follow_change(zb):
    assert zb.deltas_follow_change({23: 30.0}, 15)
    assert zb.deltas_follow_change({}, 15)               # weekly-1n+ habit: nothing to move
    assert zb.deltas_follow_change({23: -10.0}, -5)      # =N lowering the cell
    assert not zb.deltas_follow_change({23: 30.0, 20: -4.0}, 15)
    assert not zb.deltas_follow_change({23: 30.0}, 0)


# --- the mark goes through the daemon (2026-11-04, Neon off Excel) ----------
# The AppleScript used to set the 0n cell itself, invisible to the ledger, so
# the strict check read every /0n mark as typed into 0n. Now the script only
# reads; daemon_set writes the mark (and any revert) through excel-http.

def test_planned_value_guards_marks_and_adds_minutes(zb):
    assert zb.planned_value("", 1, None) == 1
    assert zb.planned_value("1.0", 1, None) is None          # already marked: never overwrite
    assert zb.planned_value("15", 10, "add") == 25
    assert zb.planned_value("", 10, "add") == 10
    assert zb.planned_value("15", 10, "set") == 10


def test_daemon_set_writes_a_journaled_set(zb, monkeypatch):
    calls = []

    def bw(sheet, writes, **kw):
        calls.append((sheet, writes, kw))
        return {"ok": True, "results": [{"value": "1.0"}]}
    monkeypatch.setattr(zb.neon_excel, "batch_write", bw)
    assert zb.daemon_set(41, "10/29", 1.0, "0n backfill nvc 2026-10-29") == (True, "1.0", "")
    assert calls == [("0n", [{"col": "AO", "value": 1, "mode": "set"}],
                      {"date": "10/29", "src": "0n backfill nvc 2026-10-29"})]


def test_daemon_set_never_raises(zb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("daemon down")
    monkeypatch.setattr(zb.neon_excel, "batch_write", boom)
    assert zb.daemon_set(41, "10/29", 1, "s") == (False, "", "daemon down")


def test_revert_goes_through_the_daemon_and_checks_it_landed(zb, monkeypatch):
    calls = []
    monkeypatch.setattr(zb.neon_excel, "batch_write",
                        lambda sheet, writes, **kw: calls.append(writes) or {"ok": True, "results": [{"value": ""}]})
    with pytest.raises(SystemExit, match="0n write reverted, nothing moved"):
        zb.revert_or_die(41, 0, "", "mismatch", "10/29", "src")
    assert calls == [[{"col": "AO", "value": "", "mode": "set"}]]
    monkeypatch.setattr(zb.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": True, "results": [{"value": "1.0"}]})
    with pytest.raises(SystemExit, match="REVERT DID NOT LAND"):
        zb.revert_or_die(41, 0, "", "mismatch", "10/29", "src")


# --- the read half goes through the daemon too (2026-11-25, Neon off Excel) ---
# build_script (one raw AppleScript: both date rows, row-2 weight, the cell,
# 0分 P..Z) is gone; read_pre_image asks excel-http and returns the dict
# parse_script_output gave for that script.

class FakeExcel:
    """neon.excel stand-in over a tiny sheet model. Records every call; any
    write endpoint fails the test."""

    def __init__(self, rows=None, cells=None, fen=None, refuse=None):
        self.rows = rows if rows is not None else {("0n", "10/29"): 302, ("0分", "10/29"): 299}
        self.cells = cells if cells is not None else {("0n", "AO", 2): "25.0", ("0n", "AO", 302): ""}
        self.fen = fen if fen is not None else ["81.0", "218.0", "107.0", "163.0", "172.0", "42.0",
                                                "10.0", "83.785714285714", "839.0", "0.0", ""]
        self.refuse = refuse or {}       # {"lookup"|"read"|"read_range": reply}
        self.calls = []

    def lookup(self, sheet, md):
        self.calls.append(("lookup", sheet, md))
        if "lookup" in self.refuse:
            return self.refuse["lookup"]
        row = self.rows.get((sheet, md))
        return {"ok": True, "row": row} if row else {"ok": False, "error": "date_not_found"}

    def read(self, sheet, col, *, date=None, row=None):
        self.calls.append(("read", sheet, col, row))
        if "read" in self.refuse:
            return self.refuse["read"]
        v = self.cells.get((sheet, col, row), "")
        return {"ok": True, "row": row, "col": col, "value": v, "formula": v}

    def read_range(self, sheet, rng):
        self.calls.append(("read_range", sheet, rng))
        if "read_range" in self.refuse:
            return self.refuse["read_range"]
        return {"ok": True, "range": rng, "rows": [list(self.fen)]}

    def _write(self, *a, **k):
        raise AssertionError("the read half called a write endpoint")
    append = write = batch_append = batch_write = _write


@pytest.fixture
def fake(zb, monkeypatch):
    def install(**kw):
        fx = FakeExcel(**kw)
        monkeypatch.setattr(zb, "neon_excel", fx)
        monkeypatch.setattr(zb, "ix_run", lambda *a, **k: pytest.fail("raw AppleScript read"))
        return fx
    return install


def test_read_pre_image_gives_the_old_script_shape(zb, fake):
    fx = fake()
    r = zb.read_pre_image(41, 10, 29)
    assert r == {"n_row": 302, "f_row": 299, "WEIGHT": "25.0", "PRE0N": "",
                 "PRE": {16: 81.0, 17: 218.0, 18: 107.0, 19: 163.0, 20: 172.0, 21: 42.0,
                         22: 10.0, 23: 83.785714285714, 24: 839.0, 25: 0.0, 26: 0.0},
                 "POST": {}}
    assert fx.calls == [("lookup", "0n", "10/29"), ("lookup", "0分", "10/29"),
                        ("read", "0n", "AO", 2), ("read", "0n", "AO", 302),
                        ("read_range", "0分", "P299:Z299")]   # P..Z only: 0分!D (Σ) never read


def test_read_pre_image_keeps_the_cell_text_the_guards_compare(zb, fake):
    """PRE0N is compared as text ('' = unmarked) and reused as the revert
    value, so it must stay what Excel's value coerces to."""
    fake(cells={("0n", "AO", 2): "25.0", ("0n", "AO", 302): "1.0"})
    r = zb.read_pre_image(41, 10, 29)
    assert r["PRE0N"] == "1.0" and zb.already_credited(r["PRE0N"])
    assert zb.planned_value(r["PRE0N"], 1, None) is None           # never overwrites a mark
    fake(cells={("0n", "AO", 2): "25.0", ("0n", "AO", 302): "missing value"})
    assert zb.read_pre_image(41, 10, 29)["PRE0N"] == ""


@pytest.mark.parametrize("rows,msg", [
    ({("0分", "10/29"): 299}, "ERROR: date 10/29 not found in 0n"),
    ({("0n", "10/29"): 302}, "ERROR: date 10/29 not found in 0分"),
    # the old scan covered rows 3..500 on both sheets: 0分's first date row (2) stays out
    ({("0n", "10/29"): 5, ("0分", "10/29"): 2}, "ERROR: date 10/29 not found in 0分"),
    ({("0n", "10/29"): 501, ("0分", "10/29"): 299}, "ERROR: date 10/29 not found in 0n"),
])
def test_read_pre_image_date_not_found_keeps_the_old_message(zb, fake, rows, msg):
    fake(rows=rows)
    with pytest.raises(zb.ExcelReadError) as e:
        zb.read_pre_image(41, 10, 29)
    assert str(e.value) == msg


@pytest.mark.parametrize("refuse,needle", [
    ({"lookup": None}, "excel-http unreachable"),                          # daemon down
    ({"lookup": {"ok": False, "error": "excel_error", "detail": "busy"}}, "excel_error (busy)"),
    ({"read": {"ok": False, "error": "queue_pending", "pending": 2}}, "queue_pending"),
    ({"read": {"ok": False, "error": "date_not_found_fallback", "fallback": True}}, "0n!AO2"),
    ({"read_range": {"ok": False, "error": "excel-http unreachable"}}, "0分!P299:Z299"),
    ({"read_range": {"ok": True, "rows": [["1.0", "2.0"]]}}, "expected 1 row of 11 cells"),
    ({"read_range": {"ok": True, "rows": []}}, "expected 1 row of 11 cells"),
])
def test_read_pre_image_failed_read_is_an_error_never_a_blank(zb, fake, refuse, needle):
    """A refused read must not come back as '' / 0: PRE0N '' would let the
    mark be set over an existing one, and a zero pre-image would be moved."""
    fake(refuse=refuse)
    with pytest.raises(zb.ExcelReadError) as e:
        zb.read_pre_image(41, 10, 29)
    assert needle in str(e.value)
    assert "not found" not in str(e.value)        # Excel busy is not "date not found"


def test_read_pre_image_transport_exception_is_a_read_error(zb, fake):
    fx = fake()
    fx.lookup = lambda *a, **k: (_ for _ in ()).throw(OSError("ssh died"))
    with pytest.raises(zb.ExcelReadError, match="ssh died"):
        zb.read_pre_image(41, 10, 29)


def _main(zb, monkeypatch, argv, today=date(2026, 11, 25)):
    stub = SimpleNamespace(header_normalize=lambda h: h.lower(),
                           load_headers=lambda: {"0n": {"nvc": 41, "hiit": 31}},
                           refresh_headers=lambda: pytest.fail("headers refreshed"),
                           CUMULATIVE_0N=set(), VARIABLE_0N={"hiit"})
    monkeypatch.setattr(zb, "_did", lambda: stub)
    monkeypatch.setattr(zb.daytime, "today", lambda: today)
    monkeypatch.setattr(sys, "argv", ["0n-backfill.py"] + argv)
    return zb.main()


def test_main_dry_run_reads_through_the_daemon_and_writes_nothing(zb, fake, monkeypatch, capsys):
    fx = fake()
    assert _main(zb, monkeypatch, ["nvc", "10/29", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == [302, 299] and out["weight"] == 25.0 and out["pre_0n"] == ""
    assert out["moves"] == [{"col": "X", "name": "xk", "pts": 25.0}] and out["dry_run"] is True
    assert {c[0] for c in fx.calls} == {"lookup", "read", "read_range"}


@pytest.mark.parametrize("kw,needle", [
    ({"rows": {("0分", "10/29"): 299}}, "ERROR: excel read failed: ERROR: date 10/29 not found in 0n"),
    ({"refuse": {"read": {"ok": False, "error": "queue_pending"}}}, "ERROR: excel read failed: 0n!AO2: queue_pending"),
    ({"refuse": {"lookup": None}}, "ERROR: excel read failed: 0n row lookup for 10/29: excel-http unreachable"),
])
def test_main_failed_read_exits_like_a_failed_script_before_any_write(zb, fake, monkeypatch, kw, needle):
    """Real run (no --dry-run): the FakeExcel write endpoints assert, so
    reaching one after a failed read fails the test."""
    fake(**kw)
    with pytest.raises(SystemExit) as e:
        _main(zb, monkeypatch, ["nvc", "10/29"])
    assert str(e.value) == needle


def test_no_raw_read_or_write_script_is_left(zb):
    """Only the post-write re-measure (`calculate full`) and the workbook
    save still talk AppleScript; neither sets a cell."""
    src = (_HERE / "0n-backfill.py").read_text()
    assert not hasattr(zb, "build_script") and not hasattr(zb, "build_revert_script")
    assert src.count('tell application "Microsoft Excel"') == 2
    assert "set value of cell" not in src and "clear contents" not in src
    assert "set value" not in zb.build_remeasure_script(6, 3)
