"""tools/ate/hcbi_day.py via /out (hcbi AI) and /hiit (hcbi AE ff)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from out import OUT as out  # noqa: E402
from hiit import HIIT  # noqa: E402

TODAY = dt.date(2026, 11, 3)   # Tuesday


@pytest.mark.parametrize("text,score,day", [
    ("10", 10, TODAY),
    ("-1 yesterday", -1, dt.date(2026, 11, 2)),
    ("yesterday 10", 10, dt.date(2026, 11, 2)),
    ("10 10/30", 10, dt.date(2026, 10, 30)),
    ("10 10.30", 10, dt.date(2026, 10, 30)),
    ("10 2026-10-28", 10, dt.date(2026, 10, 28)),
    ("out 10 on sunday", 10, dt.date(2026, 11, 1)),
    ("10 3 days ago", 10, dt.date(2026, 10, 31)),
])
def test_parse(text, score, day):
    p = out.parse(text, TODAY)
    assert (p["value"], p["date"]) == (score, day)


def test_parse_errors():
    with pytest.raises(ValueError):
        out.parse("yesterday", TODAY)
    with pytest.raises(ValueError):
        out.parse("10 someday", TODAY)


class _Excel:
    def __init__(self):
        self.calls = []

    def batch_write(self, sheet, writes, date=None, src=None):
        self.calls.append((sheet, writes, date, src))
        return {"ok": True, "row": 307, "results": [{"before_value": "-1.0"}]}


class _Cols:
    MAP = {"AI": "AI", "ff": "AE"}

    def col(self, sheet, header):
        assert sheet == "hcbi"
        return self.MAP[header]


def test_write_sets_named_day():
    ex = _Excel()
    r = out.write("10 yesterday", TODAY, excel=ex, cols=_Cols())
    assert ex.calls == [("hcbi", [{"col": "AI", "value": 10, "mode": "set"}], "11/2",
                         "out 10 2026-11-02")]
    assert out.fmt(r) == "out → 10 (2026-11-02, hcbi AI307, was -1.0)"


def test_hiit_writes_ff_column():
    ex = _Excel()
    r = HIIT.write("hiit 2 10/30", TODAY, excel=ex, cols=_Cols())
    assert ex.calls == [("hcbi", [{"col": "AE", "value": 2, "mode": "set"}], "10/30",
                         "hiit 2 2026-10-30")]
    assert HIIT.fmt(r) == "hiit → 2 (2026-10-30, hcbi AE307, was -1.0)"


def test_hiit_parse_words():
    assert HIIT.parse("1 future-fit workout yesterday", TODAY)["date"] == dt.date(2026, 11, 2)
