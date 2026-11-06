"""Tests for the excel-http fallback's AppleScript generation (lib/neon/excel.py).

Regression (2026-07-14): appending a numeric value to a cell whose existing
content is a bare number with no leading "=" (e.g. a plain value-set "2", not
a formula) produced the TEXT string "2+2" instead of the formula "=2+2" — the
cell silently stopped summing. Observed live on hcbi Daily Dozen count cells
via /ate.
"""
import importlib.util
import subprocess

import pytest
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
        # The fallback now makes TWO ssh calls: the osascript write (wrapped
        # in neon-osa-quiet.sh + shlex-quoted) and then the printf that
        # journals it to the JSONL ledger. Capture the AppleScript one.
        if "tell application" in cmd[-1]:
            captured["script"] = cmd[-1]
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("NEON_TEST_TRANSPORT_OK", "1")   # subprocess is faked above
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


# ---------------------------------------------------------------------------
# Step 2b (2026-10-19): batch_write fallback + numeric write fallback
# ---------------------------------------------------------------------------

def _capture_scripts(monkeypatch):
    scripts = []

    class FakeResult:
        returncode = 0
        stdout = "45|45"
        stderr = ""

    def fake_run(cmd, **kwargs):
        if "tell application" in cmd[-1]:
            scripts.append(cmd[-1])
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("NEON_TEST_TRANSPORT_OK", "1")   # subprocess is faked above
    return scripts


def test_fallback_write_keeps_numbers_numeric(monkeypatch):
    scripts = _capture_scripts(monkeypatch)
    excel._ssh_fallback("write", "0n", "AF", None, 293, 1830)
    excel._ssh_fallback("write", "0n", "AF", None, 293, "1830")
    excel._ssh_fallback("write", "1n+", "D", None, 20, "=0+30")
    assert "set value of theCell to 1830\n" in scripts[0]          # number unquoted
    assert 'set value of theCell to "1830"' in scripts[1]          # string stays quoted
    assert 'set formula of theCell to "=0+30"' in scripts[2]


def test_batch_write_fallback_maps_modes(monkeypatch):
    monkeypatch.setattr(excel, "_curl_ex", lambda *a, **k: (None, False))      # daemon down
    calls = []

    def fake_fallback(op, sheet, col, date, row, value, src=None):
        calls.append((op, sheet, col, date, row, value, src))
        return {"ok": True, "row": 293, "col": col, "value": str(value), "formula": "", "fallback": True}

    monkeypatch.setattr(excel, "_ssh_fallback", fake_fallback)
    out = excel.batch_write("0n", [{"col": "AE", "value": 1},
                                   {"col": "D", "value": "+45", "mode": "append"},
                                   {"col": "AJ", "value": 15, "mode": "add"}],
                            date="10/19", src="did x")
    assert out["fallback"] is True and out["ok"] is False           # add mode cannot be reproduced
    assert out["row"] == 293
    assert calls == [("write", "0n", "AE", "10/19", None, 1, "did x"),
                     ("append", "0n", "D", "10/19", 293, "+45", "did x")]  # row reused after lookup
    assert out["results"][2] == {"ok": False, "col": "AJ", "error": "add_mode_needs_daemon",
                                 "fallback": True}


def test_batch_write_prefers_daemon(monkeypatch):
    monkeypatch.setattr(excel, "_curl_ex", lambda path, body=None, **k: ({"ok": True, "row": 1, "path": path}, False))
    monkeypatch.setattr(excel, "_ssh_fallback", lambda *a, **k: pytest.fail("fallback must not run"))
    assert excel.batch_write("0n", [{"col": "AE", "value": 1}], date="10/19")["path"] == "/batch_write"


def test_live_calls_are_refused_under_pytest(monkeypatch):
    """Regression (2026-10-19): a test that forgot to mock batch_write cleared
    two real cells on Ix. Any transport call from inside pytest must raise
    unless the test explicitly opts in with subprocess faked."""
    monkeypatch.delenv("NEON_TEST_TRANSPORT_OK", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("transport must not run"))
    with pytest.raises(RuntimeError, match="live Neon call from a test"):
        excel.batch_write("0n", [{"col": "H", "value": 1}], row=156)
    with pytest.raises(RuntimeError):
        excel.read("0n", "H", row=156)
    with pytest.raises(RuntimeError):
        excel._ssh_fallback("write", "0n", "H", None, 156, 1)


def test_batch_write_fallback_false_reports_unreachable_without_ssh(monkeypatch):
    monkeypatch.setattr(excel, "_curl_ex", lambda *a, **k: (None, False))
    monkeypatch.setattr(excel, "_ssh_fallback", lambda *a, **k: pytest.fail("no per-cell ssh fallback"))
    out = excel.batch_write("0s897", [{"col": "E", "value": "x", "mode": "text"}], date="10/18/26",
                            fallback=False)
    assert out == {"ok": False, "error": "daemon_unreachable", "fallback": True}
    assert excel._DATE_COL["0s897"] == "B" and excel._DATE_COL["1分+1s"] == "A"


# --- a daemon TIMEOUT is not "daemon down" (2026-11-04) -----------------------
# Excel lagging 45s+: the client gave up, fell back to ssh+osascript, and the
# daemon's request landed too, so one /ate appended twice (hcbi BD =550+550).

class _Proc:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def _transport(monkeypatch, rc):
    import subprocess as _sp
    from neon import excel
    calls = []
    monkeypatch.setenv("NEON_TEST_TRANSPORT_OK", "1")
    monkeypatch.setattr(excel.subprocess, "run", lambda *a, **k: _Proc(rc))
    monkeypatch.setattr(excel, "_ssh_fallback", lambda *a, **k: calls.append(a) or {"ok": True})
    return excel, calls


def test_timed_out_batch_append_is_not_retried_by_fallback(monkeypatch):
    excel, calls = _transport(monkeypatch, 28)          # curl: operation timed out
    r = excel.batch_append("hcbi", [("BD", "+550"), ("BE", "+32")], date="11/4", src="t")
    assert r["ok"] is False and r["timeout"] is True and calls == []


def test_python_side_timeout_is_also_not_retried(monkeypatch):
    import subprocess as _sp
    from neon import excel
    calls = []
    monkeypatch.setenv("NEON_TEST_TRANSPORT_OK", "1")
    def slow(*a, **k):
        raise _sp.TimeoutExpired("curl", 25)
    monkeypatch.setattr(excel.subprocess, "run", slow)
    monkeypatch.setattr(excel, "_ssh_fallback", lambda *a, **k: calls.append(a) or {"ok": True})
    assert excel.append("0分", "Q", date="11/4", value="+50")["timeout"] is True
    assert calls == []


def test_timed_out_add_mode_batch_write_is_not_retried(monkeypatch):
    excel, calls = _transport(monkeypatch, 28)
    r = excel.batch_write("0n", [{"col": "AJ", "value": 15, "mode": "add"}], date="11/4")
    assert r["timeout"] is True and calls == []


def test_refused_daemon_still_falls_back(monkeypatch):
    excel, calls = _transport(monkeypatch, 7)            # curl: couldn't connect
    r = excel.batch_append("hcbi", [("BD", "+550")], date="11/4", src="t")
    assert r.get("fallback") is True and len(calls) == 1


@pytest.mark.parametrize("rc", [52, 56])
def test_daemon_dying_mid_request_is_not_retried(monkeypatch, rc):
    excel, calls = _transport(monkeypatch, rc)
    assert excel.batch_append("hcbi", [("BD", "+1")], date="11/4")["timeout"] is True and calls == []


# Fallback lines reach the ledger (2026-11-05): 11/4's `did 1s` fallback had no
# points and no idem, so strict called the real journal line "typed into Excel".
def _fallback_journal(monkeypatch, stdout):
    lines = []

    class FakeResult:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        res = FakeResult()
        res.stdout = stdout if "tell application" in cmd[-1] else ""
        if "printf" in cmd[-1]:
            lines.append(cmd[-1])
        return res

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("NEON_TEST_TRANSPORT_OK", "1")   # subprocess is faked above
    return lines


def test_fallback_journals_values_and_idem(monkeypatch):
    lines = _fallback_journal(monkeypatch, "213.0|=1+'1n+'!C5\x1f168.0\n")
    out = excel._ssh_fallback("append", "0分", "T", None, 306, "+'1n+'!C5", src="did 1s", idem="k")
    assert out["value"] == "213.0" and out["formula"] == "=1+'1n+'!C5"
    assert '"before_value": "168.0"' in lines[0] and '"after_value": "213.0"' in lines[0]
    assert '"idem": "k"' in lines[0]


def test_fallback_append_to_empty_cell_starts_from_zero(monkeypatch):
    lines = _fallback_journal(monkeypatch, "15.0|=15\x1f\n")
    excel._ssh_fallback("append", "0分", "T", None, 306, "+15")
    assert '"before_value": "0"' in lines[0]


def test_fallback_without_formula_drops_idem(monkeypatch):
    """idem_dup matches any formula against an empty `after`, so every replay
    of the key would read as a duplicate."""
    lines = _fallback_journal(monkeypatch, "15.0|\x1f0\n")
    excel._ssh_fallback("append", "0分", "T", None, 306, "+15", idem="k")
    assert '"idem"' not in lines[0]


def test_batch_append_fallback_passes_idem(monkeypatch):
    seen = {}
    monkeypatch.setattr(excel, "_curl_ex", lambda *a, **k: (None, False))

    def fake_fallback(op, sheet, col, date, row, value, src=None, idem=None):
        seen[col] = idem
        return {"ok": True, "fallback": True}

    monkeypatch.setattr(excel, "_ssh_fallback", fake_fallback)
    excel.batch_append("0分", [{"col": "T", "value": "+1", "idem": "k"}], row=306)
    assert seen == {"T": "k"}
