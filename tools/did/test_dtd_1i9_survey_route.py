"""Bug 2026-10-19: the `1 i9` card started its timer and nothing else. It
should open the /1i9 survey from dtd, the way `1 hcme` opens its form.

Covers the dtd route (AST-free text check on the case arm), the survey's
pure helpers (points parse, AppleScript builders), and the write contract:
B/D/E/F of the week row, never C (a formula into 1分+1s)."""
import importlib.util
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
SURVEY = HERE.parent / "1i9" / "1i9.py"


def _load():
    spec = importlib.util.spec_from_file_location("one_i9_survey", SURVEY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dtd_routes_1_i9_card_to_survey_form():
    src = (HERE / "dtd.sh").read_text()
    assert '"1 i9")' in src and "tools/1i9/1i9.py" in src
    arm = src[src.index('"1 i9")'):]
    assert arm.split("\n", 1)[0].rstrip().endswith(";;")


def test_parse_points_sums_only_the_week_rows():
    m = _load()
    raw = "\n".join([
        "1\t10/10\t999",    # previous week, excluded
        "2\t10/11\t90",
        "3\t10/17\t198",
        "4\t10/18\t70",     # next day, excluded
        "5\tnot a date\t5",
    ])
    assert m.parse_points(raw, date(2026, 10, 11), date(2026, 10, 17)) == 288


def test_points_script_uses_ascii_tab_not_tab_constant():
    m = _load()
    script = m.points_script(1, 10)
    assert "ASCII character 9" in script
    assert "& tab &" not in script and "& TAB &" not in script


def test_write_script_touches_only_bdef_never_formula_column_c():
    m = _load()
    script = m.write_script(43, "MA", "shipped X", "more Y", 1832)
    for col in ("2", "4", "5", "6"):
        assert f"cell {col} of row 43" in script
    assert "cell 3 of" not in script and "cell 1 of" not in script
    assert "save wb" in script
