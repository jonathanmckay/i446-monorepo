"""Regression (2026-10-03): "did put a link to 1n+ AF5, which is just a
formula ... for the times it's a formula, it needs to ask me for how many
minutes then multiply by the formula."

Variable 1n+ habits hold a RATE string in row 5 ("1/m", ".5/m", "15+1/m"),
not points. did-fast's 1n+ -> 0分 step appended "+'1n+'!<col>5" whenever a
variable habit had no minutes (or 0 points), so 0分 summed a text cell. The
ledger shows four: s897 9/20 and 9/26, 业写 10/2, family 10/3. run.py (the
/did skill fast path) parsed "1/m" as 0 points and silently assumed 1 minute.

Now: a no-base rate habit with no minutes is refused (asked for), a known
minute count is multiplied by the rate, and row 5 is never referenced.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from neon import rates  # noqa: E402

DF_SRC = (HERE / "did-fast.py").read_text()


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── shared rate parser ──────────────────────────────────────────────────────

def test_parse_rate_reads_every_row5_rate_form_on_the_sheet():
    assert rates.parse_rate("1/m") == (0.0, 1.0)
    assert rates.parse_rate(".5/m") == (0.0, 0.5)
    assert rates.parse_rate("15+1/m") == (15.0, 1.0)
    assert rates.parse_rate("20") is None and rates.parse_rate("") is None
    assert rates.parse_rate(None) is None and rates.parse_rate(20) is None
    assert rates.rate_points("1/m", 45) == 45
    assert rates.rate_points("15+1/m", 30) == 45
    assert rates.rate_points(".5/m", 31) == 16
    assert rates.rate_points("20", 30) is None


# ── did-fast ────────────────────────────────────────────────────────────────

HEADERS = {"0n": {}, "1n": {"family": "AF", "一起饭": "AC"}}


def _route(df, raw):
    with patch.object(df, "toggl_minutes_for", return_value=None):
        return df.route_items(df.parse_input(raw), HEADERS, {"1neon": []})


def test_did_fast_family_with_no_minutes_is_asked_not_credited():
    df = _load("df_rate_ask", "did-fast.py")
    [r] = _route(df, "family")
    assert r.step == "needs_agent", r
    assert "how many minutes" in r.error


def test_did_fast_family_with_minutes_multiplies_the_rate():
    df = _load("df_rate_mul", "did-fast.py")
    [r] = _route(df, "family 45")
    assert r.step == "1n" and r.is_variable_1n and r.variable_value == 45


def test_did_fast_explicit_zero_minutes_is_an_answer_not_a_question():
    df = _load("df_rate_zero", "did-fast.py")
    [r] = _route(df, "family 0")
    assert r.step == "1n" and not r.variable_value


def test_did_fast_based_habit_still_completes_blank_at_base_points():
    df = _load("df_rate_base", "did-fast.py")
    [r] = _route(df, "一起饭")
    assert r.step == "1n" and r.variable_value == 15


def test_did_fast_never_references_row5_for_a_variable_habit():
    body = DF_SRC[DF_SRC.index("# 4c. Batch 1n+"):DF_SRC.index("# 5. Batch 0分 appends")]
    assert "if r.is_variable_1n:" in body, body
    assert "r.is_variable_1n and r.variable_value" not in body, \
        "a variable habit with 0/None points must not fall through to the row-5 reference"
    i_var = body.index("if r.is_variable_1n:")
    i_ref = body.index("'1n+'!{r.col_letter}5")
    assert i_var < i_ref, "the variable branch must be checked before the row-5 reference branch"


# ── run.py (/did skill fast path) ──────────────────────────────────────────

class _FakeExcel:
    def __init__(self, row5):
        self.row5 = row5
        self.writes, self.appends = [], []

    def read(self, sheet, col, row=None, **kw):
        return {"value": self.row5 if row == 5 else "0"}

    def write(self, sheet, col, row=None, **kw):
        self.writes.append((sheet, col, row, kw))
        return {"ok": True}

    def append(self, sheet, col, **kw):
        self.appends.append((sheet, col, kw))
        return {"ok": True}


def _run_1n(run, fake, **kw):
    d = {"habit_name": "family", "neon_col": "AF", "fen_col": "X", "toggl": {"desc": "family"}}
    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(9.5, 44)), \
         patch.object(run, "_auto_detect_minutes", side_effect=lambda *a, default=1, **k: default), \
         patch.object(run, "_find_and_close_todoist", return_value=(None, None)), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"):
        return run.run_1n(d, "10/3", **kw)


def test_run_py_rate_habit_without_minutes_defers_to_agent_and_writes_nothing():
    run = _load("run_rate_ask", "run.py")
    fake = _FakeExcel("1/m")
    rc = _run_1n(run, fake, time_range=None, explicit_minutes=None)
    assert rc == 2
    assert fake.writes == [] and fake.appends == []


def test_run_py_rate_habit_with_minutes_credits_rate_times_minutes():
    run = _load("run_rate_mul", "run.py")
    fake = _FakeExcel("1/m")
    rc = _run_1n(run, fake, time_range=None, explicit_minutes=45)
    assert rc == 0
    assert fake.appends and fake.appends[0][2]["value"] == "+45"
    assert all("'1n+'!" not in str(a[2]["value"]) for a in fake.appends)


def test_run_py_numeric_row5_unchanged():
    run = _load("run_rate_num", "run.py")
    fake = _FakeExcel("20")
    rc = _run_1n(run, fake, time_range=None, explicit_minutes=None)
    assert rc == 0 and fake.appends[0][2]["value"] == "+20"


# ── dtd ─────────────────────────────────────────────────────────────────────

def test_dtd_reasks_blank_for_no_base_rate_habits_and_shows_the_reason():
    src = (HERE / "dtd.sh").read_text()
    assert "DTD_VAR1N_NOBASE_PAT=$(python3" in src
    i = src.index('case "\\$clean_base" in\n    ${DTD_VAR1N_NOBASE_PAT})')
    assert 'while [[ -z "\\$_iv" ]]' in src[i:i + 400], "blank answer must re-ask for no-base habits"
    assert ".agent_needed[0].reason" in src, "dtd must show did-fast's reason when it restores a card"
