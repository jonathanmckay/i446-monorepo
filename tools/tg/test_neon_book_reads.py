"""neon_book_reads: janus's Excel replies rebuilt from the Neon book."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import neon_book_reads as nbr  # noqa: E402
from neon import calc  # noqa: E402
from neon.calc import col_index as ci  # noqa: E402

DAY = dt.date(2026, 11, 22)
S = calc.serial(DAY)


def _book(extra=None):
    cells = {("0分", ci("B"), 324): S, ("0分", ci("D"), 324): "=SUM(P324:Y324)",
             ("0分", ci("G"), 324): 16.0, ("0分", ci("H"), 324): "=D324-SUM(G324)",
             ("0分", ci("P"), 324): "=13+4", ("0分", ci("R"), 324): 40.0,
             ("0n", ci("C"), 327): S, ("0n", ci("D"), 1): "睡觉", ("0n", ci("E"), 1): "cpap", ("0n", ci("AP"), 1): "ص",
             ("0n", ci("E"), 327): 0.0, ("0n", ci("AP"), 327): 3.0,
             ("0n", ci("AQ"), 375): 156.0, ("0n", ci("AR"), 375): 299.0, ("0n", ci("AS"), 375): "=80+9",
             ("hcbi", ci("B"), 328): S, ("hcbi", ci("U"), 328): "=SUM(AL328,AO328)", ("hcbi", ci("AL"), 328): "=480",
             ("hcbi", ci("B"), 380): "Q4", ("hcbi", ci("E"), 380): -56.0, ("hcbi", ci("F"), 381): -21.0,
             ("hcbi", ci("Y"), 380): 755.0,
             ("hcbi", ci("B"), 376): "Q3", ("hcbi", ci("E"), 376): -112.0,
             ("hcbi", ci("B"), 384): 2026.0, ("hcbi", ci("E"), 384): -301.0,
             ("hcbi", ci("X"), 388): -1552.7}
    cells.update(extra or {})
    return calc.Book(cells)


def test_points_reply_has_the_shape_fetch_points_parses():
    parts = nbr.raw_points(_book(), DAY).split("|")
    assert len(parts) == 1 + 9 + 10 + 9
    assert float(parts[0]) == 57.0                                  # D
    assert parts[1] == "16.0" and parts[2] == "=D324-SUM(G324)"     # a lock is a literal, the residual a formula
    assert [float(p) for p in parts[10:20] if p] == [17.0, 40.0]
    assert float(parts[20]) == 16.0 and float(parts[21]) == 41.0    # G:O values: the residual is Σ − locks


def test_habits_reply_segments():
    raw = nbr.raw_habits(_book(), DAY, ["AQ375", "AR375", "AS375"], "X388")
    body, kcal = raw.rsplit("||kcal=", 1)
    assert float(kcal) == 480.0
    row, ytd, dozen = body.split("||")
    assert row.startswith("睡觉\t|cpap\t0.0|") and "ص\t3.0|" in row
    assert ytd.split("|")[:3] == ["156.0", "299.0", "89.0"]
    dozen, total = dozen.split("#")
    assert float(total) == -1552.7
    dozen, annual = dozen.split("~")
    cur, prev = dozen.split(";")
    r1, r2 = cur.split("|")
    assert r1.split(",")[0] == "-56.0" and r1.split(",")[20] == "755.0" and r2.split(",")[1] == "-21.0"
    assert prev.split("|")[0].split(",")[0] == "-112.0" and annual.split("|")[0].split(",")[0] == "-301.0"


def test_blanks_and_errors_read_empty():
    b = _book({("0分", ci("R"), 324): "=1/0"})
    parts = nbr.raw_points(b, DAY).split("|")
    assert parts[0] == "" and parts[12] == "" and parts[13] == ""      # D and R are errors, S is blank


def test_no_row_for_the_day_means_no_answer():
    assert nbr.raw_points(_book(), dt.date(2025, 1, 1)) is None
    assert nbr.raw_habits(_book(), dt.date(2025, 1, 1), [], "X388") is None


def test_under_pytest_the_live_book_is_never_read(monkeypatch):
    monkeypatch.delenv("NEON_TEST_DB_OK", raising=False)
    assert nbr.live_book() is None and nbr.points(DAY) is None
