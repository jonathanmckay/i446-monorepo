"""Tests for the excel-http fallback's AppleScript generation (lib/neon/excel.py).

Regression (2026-07-14): appending a numeric value to a cell whose existing
content is a bare number with no leading "=" (e.g. a plain value-set "2", not
a formula) produced the TEXT string "2+2" instead of the formula "=2+2" — the
cell silently stopped summing. Observed live on hcbi Daily Dozen count cells
via /ate.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("excel", HERE / "excel.py")
excel = importlib.util.module_from_spec(spec)
sys.modules["excel"] = excel
spec.loader.exec_module(excel)


def _generated_script(monkeypatch, *, existing_formula, value):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "0|0"
        stderr = ""

    def fake_run(cmd, **kwargs):
        # cmd is ["ssh", host, "osascript", "-e", script]
        captured["script"] = cmd[-1]
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    excel._ssh_fallback("append", "hcbi", "I", None, 197, value)
    return captured["script"]


def test_numeric_append_normalizes_bare_number_before_concatenating(monkeypatch):
    # We can't control the AppleScript-side `f` value from here (it's read live
    # on ix), so assert the GENERATED script contains the guard that normalizes
    # a non-"=" existing formula before appending — this is what prevents the
    # "2" + "+2" -> text "2+2" bug.
    script = _generated_script(monkeypatch, existing_formula="2", value="+2")
    assert 'f does not start with "="' in script, (
        "numeric append must guard against a bare-number existing formula, "
        "or appending produces a non-computing text string")
    assert 'set formula of theCell to "=" & f & "+2"' in script


def test_string_append_does_not_add_numeric_guard(monkeypatch):
    # String appends (e.g. food-name ", more food") must NOT get the "="
    # guard — that would corrupt plain text into a bogus formula.
    script = _generated_script(monkeypatch, existing_formula="existing food",
                                value=", more food")
    assert 'f does not start with "="' not in script
    assert 'set formula of theCell to f & ", more food"' in script


def test_no_active_workbook_references():
    # Regression (2026-07-14): every AppleScript block targeted 'active
    # workbook' instead of the Neon workbook by name. When some other file
    # was frontmost in Excel on ix (unattended machine — a real observed
    # state), every lookup/read/write/append silently searched the WRONG
    # workbook and reported "not found", even though the real row existed.
    src = (HERE / "excel.py").read_text()
    assert "active workbook" not in src, (
        "must not target Excel's frontmost workbook — pin to the named "
        "Neon workbook instead, or writes silently go to the wrong file")
    assert src.count('workbook "{WORKBOOK}"') >= 5


def test_lookup_compares_date_class_as_text_not_bare_keyword():
    # Regression (2026-07-14): `if (class of cv) is date then` fails to
    # PARSE inside `tell application "Microsoft Excel"` — Excel's own
    # AppleScript dictionary shadows the standard `date` type keyword there
    # (confirmed live: osascript exits non-zero with a syntax error at that
    # line). lookup_row swallows the non-zero exit as "not found", so every
    # date-typed date-column cell (hcbi/0分/1n+ can hold a real Excel date,
    # not just 0n col C) silently failed to look up. Comparing the class as
    # text sidesteps the terminology collision.
    src = (HERE / "excel.py").read_text()
    assert "(class of cv) is date" not in src, (
        "raw 'is date' comparison doesn't parse inside the Excel tell block")
    assert '((class of cv) as text) is "date"' in src


def test_fallback_append_treats_minus_as_formula_term():
    """The 0t sleep dock appends "-7.5"; the ssh fallback must route it down
    the numeric branch or it lands as non-computing text on bare-number cells."""
    src = (HERE / "excel.py").read_text()
    assert 'startswith(("+", "=", "-"))' in src
