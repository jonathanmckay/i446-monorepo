"""2026-10-22: "hitting enter on 1hcb should trigger the survey". The `1 hcb`
card opens tools/1hcb/1hcb.py from dtd, the way `1 i9` and `1 hcme` do.

Covers the dtd route, the hcb week's Thursday row, the answer shaping, and
the write contract: BG/BH/BJ of hcbi on that Thursday, nothing else."""
import importlib.util
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
SURVEY = HERE.parent / "1hcb" / "1hcb.py"


def _load():
    spec = importlib.util.spec_from_file_location("one_hcb_survey", SURVEY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dtd_routes_1_hcb_card_to_survey_form():
    src = (HERE / "dtd.sh").read_text()
    assert '"1 hcb")' in src and "tools/1hcb/1hcb.py" in src
    arm = src[src.index('"1 hcb")'):]
    assert arm.split("\n", 1)[0].rstrip().endswith(";;")
    assert "1hcme 1i9 1hcb: form" in src


def test_week_row_is_the_thursday_starting_the_thu_wed_week():
    m = _load()
    assert m.week_thursday(date(2026, 10, 22)) == date(2026, 10, 22)   # Thursday itself
    assert m.week_thursday(date(2026, 10, 28)) == date(2026, 10, 22)   # Wednesday
    assert m.week_thursday(date(2026, 10, 21)) == date(2026, 10, 15)
    assert m.md(date(2026, 10, 2)) == "10/2"


def test_answers_become_bullets_and_weights_are_sane():
    m = _load()
    assert m.bullets("ate greens\n\n* tea not coffee\n- 3 HIIT") == "• ate greens\n• tea not coffee\n• 3 HIIT"
    assert m.bullets("") == ""
    assert m.parse_weight("191.4") == 191.4 and m.parse_weight(" 190 ") == 190.0
    assert m.parse_weight("19.14") is None and m.parse_weight("heavy") is None


def test_writes_only_bg_bh_bj_on_the_thursday_row(monkeypatch):
    m = _load()
    calls = []
    monkeypatch.setattr(m.excel, "write",
                        lambda sheet, col, *, date=None, row=None, value, src=None:
                        calls.append((sheet, col, date, value, src)) or {"ok": True})
    m.write_week(date(2026, 10, 22), 191.4, "• learned", "")
    assert calls == [("hcbi", "BG", "10/22", "191.4", "1hcb survey"),
                     ("hcbi", "BH", "10/22", "• learned", "1hcb survey")]    # blank goals: untouched
