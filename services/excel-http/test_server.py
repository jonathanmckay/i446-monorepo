"""Tests for excel-http server — structural checks via AST."""

import ast
import os
import re
import textwrap

import pytest


def _load_func_source(name: str) -> str:
    """Extract a top-level function's source from server.py."""
    text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} not found in server.py")


def _load_do_append_source() -> str:
    # The append normalization lives in _append_setters (shared with
    # /batch_write mode "append" since 1.9.0); the guards are asserted on both.
    return _load_func_source("do_append") + "\n" + _load_func_source("_append_setters")


class TestAppendStringValues:
    """Regression: /ate food names with '+' were silently dropped because
    do_append always set formula (=...) for empty cells, even for string values
    like ', mocha+yoghurt'. The '=' prefix made it an invalid formula and Excel
    rejected it silently."""

    def test_do_append_has_is_numeric_guard(self):
        """do_append must distinguish numeric (+N, =...) from string appends."""
        src = _load_do_append_source()
        assert "is_numeric" in src, (
            "do_append must check is_numeric to distinguish string vs formula appends"
        )

    def test_do_append_uses_set_value_for_strings(self):
        """For non-numeric values, empty cells should use 'set value of' (not 'set formula of')."""
        src = _load_do_append_source()
        assert "set value of theCell" in src, (
            "do_append must use 'set value of' for string values in empty cells"
        )

    def test_do_append_strips_leading_comma_for_empty_cells(self):
        """When the cell is empty, leading ', ' should be stripped from string values."""
        src = _load_do_append_source()
        assert 'lstrip(", ")' in src, (
            "do_append must strip leading ', ' from string values for empty cells"
        )


class TestThreadingAndTimeoutHardening:
    """Regression (2026-07-15, recurred twice same day): the server was a
    plain single-threaded HTTPServer with no socket timeout. A stalled client
    connection (our own curl hitting its own client-side timeout while the
    server's blocking rfile.read() waited forever for bytes that never came)
    wedged the single request-handling path permanently — every subsequent
    request queued forever, indistinguishable from 'daemon down' even though
    the process was alive and the port was LISTENing."""

    def test_uses_threading_http_server(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
        assert "ThreadingHTTPServer" in src, (
            "must not use a plain single-threaded HTTPServer — one stalled "
            "connection must not block every other request"
        )

    def test_handler_has_socket_timeout(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
        assert re.search(r"class Handler\(BaseHTTPRequestHandler\):\s*\n(\s*#.*\n)*\s*timeout\s*=\s*\d+", src), (
            "Handler must set a socket timeout, or a stalled read blocks forever"
        )

    def test_excel_calls_are_serialized_across_threads(self):
        # Threading fixes the stalled-read wedge, but concurrent AppleEvents
        # to Excel from multiple threads is its own hazard — must be
        # serialized with a lock around the actual Excel-touching call.
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
        assert "EXCEL_LOCK" in src and "threading.Lock()" in src
        assert "with EXCEL_LOCK:" in src


class TestPinnedWorkbook:
    """Regression (2026-07-14): every AppleScript block targeted 'active
    workbook' instead of the Neon workbook by name. When some other file was
    frontmost in Excel on ix (a real, observed state — ix runs unattended),
    every lookup/read/write/append silently searched the WRONG workbook and
    reported 'not found', even though the real row existed. Every cell target
    must be pinned to the actual workbook, never 'active workbook'."""

    def test_no_active_workbook_references(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
        assert "active workbook" not in src, (
            "must not target Excel's frontmost workbook — pin to the named "
            "Neon workbook instead, or writes silently go to the wrong file"
        )

    def test_workbook_constant_defined_and_used(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
        assert 'WORKBOOK = "Neon分v12.2.xlsx"' in src
        assert src.count('workbook "{WORKBOOK}"') >= 4, (
            "lookup_row, do_append, do_write, and do_read must all pin to "
            "the named workbook"
        )


class TestAppendNumericGuard:
    """Regression (2026-07-14): do_append's non-empty branch blindly did
    `oldFormula & "+N"`. When oldFormula was a bare number with no leading
    '=' (a plain value-set cell, not a formula — e.g. a Daily Dozen count
    cell), the result was the TEXT string '2+2' instead of the formula
    '=2+2', so the cell silently stopped summing. Observed live on hcbi via
    /ate."""

    def test_numeric_append_normalizes_bare_number_before_concatenating(self):
        src = _load_do_append_source()
        assert 'oldFormula does not start with "="' in src, (
            "numeric append must guard against a bare-number existing "
            "formula, or appending produces a non-computing text string"
        )

    def test_string_append_path_untouched(self):
        # String appends (e.g. food-name ", more food") must NOT gain the
        # numeric "=" guard — only the is_numeric branch should have it.
        src = _load_do_append_source()
        string_branch = src[src.index("else:", src.index("if is_numeric:")):]
        assert 'oldFormula does not start with "="' not in string_branch
        assert 'set formula of theCell to oldFormula & "{val_esc}"' in string_branch


class TestLookupRowDateMatching:
    """Regression: 0n's date column (C) holds real Excel DATE values, which
    AppleScript renders as 'Tuesday, June 30, 2026 …' — an exact '= "6/30"'
    string match misses every row, so /salat and any date= write to 0n failed.
    lookup_row must compare a real date cell by month/day and keep the text path
    for the M/D-text sheets (0分, hcbi, 1n+)."""

    def test_lookup_row_handles_real_date_cells(self):
        src = (_load_func_source("lookup_row") + "\n" + _load_func_source("_date_match_as"))
        assert '((class of cv) as text) is "date"' in src, (
            "lookup_row must detect real Excel date cells, not only M/D text"
        )
        assert "month of cv" in src and "day of cv" in src, (
            "a real date cell must be matched by its month/day"
        )

    def test_date_class_compared_as_text_not_bare_keyword(self):
        # Regression (2026-07-14): `if (class of cv) is date then` fails to
        # PARSE inside `tell application "Microsoft Excel"` — Excel's own
        # dictionary shadows the standard `date` type keyword there
        # (confirmed live: osascript exits non-zero on that exact line).
        # lookup_row swallows the failure as rc!=0 -> None, so every
        # date-typed date-column cell silently "wasn't found" even though
        # this branch's string pattern was present (the previous version of
        # this test only checked for the string, never executed it — that's
        # how the bug shipped and stayed invisible). Comparing the class as
        # text sidesteps the collision.
        src = (_load_func_source("lookup_row") + "\n" + _load_func_source("_date_match_as"))
        assert "(class of cv) is date" not in src, (
            "raw 'is date' comparison doesn't parse inside the Excel tell block"
        )

    def test_lookup_row_keeps_text_match_path(self):
        src = (_load_func_source("lookup_row") + "\n" + _load_func_source("_date_match_as"))
        assert "cv as text" in src, (
            "lookup_row must still match M/D-text date columns (0分, hcbi)"
        )

    def test_lookup_row_skips_empty_cells(self):
        src = (_load_func_source("lookup_row") + "\n" + _load_func_source("_date_match_as"))
        assert "missing value" in src, (
            "lookup_row must skip empty date cells (cv is missing value)"
        )


def test_append_treats_minus_as_formula_term():
    """The 0t sleep dock appends "-7.5"; without "-" in the numeric check a
    negative append takes the string branch and produces non-computing text
    on bare-number cells (same class as the 2026-07-14 "+2" regression)."""
    text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
    assert 'startswith(("+", "=", "-"))' in text


class TestExplicitSaveWatchdog:
    """Regression (2026-07-19, 07-20, 08-13, 08-14/15): the daemon never
    called Save — every /write and /append kept returning 200 OK (the cell
    really updated in the open workbook) while Excel's AutoSave-to-OneDrive
    silently wedged for up to a full day, so nothing reached disk/cloud until
    a human noticed the workbook's on-disk mtime hadn't moved. A periodic
    explicit save bounds that wedge to SAVE_INTERVAL instead of 'however long
    until someone checks'."""

    def test_save_workbook_function_exists_and_pins_workbook(self):
        src = _load_func_source("save_workbook")
        assert 'save workbook "{WORKBOOK}"' in src, (
            "save_workbook must explicitly save the pinned Neon workbook, "
            "not rely on AutoSave"
        )

    def test_save_loop_runs_on_a_timer_under_the_lock(self):
        src = _load_func_source("save_loop")
        assert "time.sleep(SAVE_INTERVAL)" in src, (
            "save_loop must force a save periodically, not just once at startup"
        )
        assert "with EXCEL_LOCK:" in src, (
            "save_loop must serialize its save call with other Excel-touching "
            "requests via EXCEL_LOCK"
        )
        assert "save_workbook()" in src

    def test_main_starts_save_loop_as_daemon_thread(self):
        src = _load_func_source("main")
        assert re.search(r"threading\.Thread\(target=save_loop,\s*daemon=True\)\.start\(\)", src), (
            "main() must start save_loop as a daemon thread, or the watchdog "
            "never runs"
        )


class TestIdemDedupe:
    """Regression 2026-10-12: a watchdog-killed did-fast was retried by dtd
    after its +25 had already landed: +25 three times in 0分!U. Appends
    carrying an already-journaled idem key are dropped while the earlier
    append is still in the cell; an undone one (stripped or negated) credits
    again."""

    @pytest.fixture()
    def srv(self, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location("excel_http_server", os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.IDEM_INDEX.clear()
        m.IDEM_INDEX["0fen:T1@10/12:U"] = "='0n'!BG286+25"
        return m

    def test_replay_is_dup(self, srv):
        assert srv.idem_dup("0fen:T1@10/12:U", "='0n'!BG286+25", "+25")
        # later unrelated appends to the same cell don't hide the replay
        assert srv.idem_dup("0fen:T1@10/12:U", "='0n'!BG286+25+10", "+25")

    def test_unknown_or_missing_key_is_not_dup(self, srv):
        assert not srv.idem_dup("0fen:T2@10/12:U", "='0n'!BG286+25", "+25")
        assert not srv.idem_dup(None, "='0n'!BG286+25", "+25")

    def test_undone_append_credits_again(self, srv):
        # undo-fast stripped the tail
        assert not srv.idem_dup("0fen:T1@10/12:U", "='0n'!BG286", "+25")
        # undo-fast appended the negation
        assert not srv.idem_dup("0fen:T1@10/12:U", "='0n'!BG286+25+10-25", "+25")

    def test_do_append_short_circuits_dup_before_writing(self, srv, monkeypatch):
        scripts = []

        def fake_osa(script):
            scripts.append(script)
            return 0, "62\t='0n'!BG286+25", ""

        monkeypatch.setattr(srv, "osascript", fake_osa)
        monkeypatch.setattr(srv, "cell_addr", lambda req: ("U", 283))
        out = srv.do_append({"sheet": "0分", "col": "U", "date": "10/12",
                             "value": "+25", "idem": "0fen:T1@10/12:U"})
        assert out["ok"] and out.get("dup")
        assert len(scripts) == 1 and "set formula" not in scripts[0]

    def test_journaled_idem_is_indexed(self, srv, monkeypatch, tmp_path):
        monkeypatch.setattr(srv, "LEDGER_DIR", str(tmp_path))
        srv.journal({"sheet": "0分", "col": "U", "date": "10/12", "row": 283,
                     "after": "=5+25", "idem": "0fen:T9@10/12:U"})
        assert srv.IDEM_INDEX["0fen:T9@10/12:U"] == "=5+25"
        srv.seed_chain_index()
        assert srv.IDEM_INDEX["0fen:T9@10/12:U"] == "=5+25"


class TestSemanticDoubleWrite:
    """Neon-off-Excel step 1: journal() mirrors every entry into the SQLite
    ledger, after the JSONL append, and a ledger failure can only log."""

    @pytest.fixture()
    def srv(self, tmp_path, monkeypatch):
        import importlib.util
        monkeypatch.setenv("NEON_DB", str(tmp_path / "neon.db"))
        import os, sys
        for m in [k for k in sys.modules if k == "neon" or k.startswith("neon.")]:
            del sys.modules[m]
        spec = importlib.util.spec_from_file_location(
            "excel_http_server_dw", os.path.join(os.path.dirname(__file__), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.LEDGER_DIR = str(tmp_path / "jsonl")
        return m

    def test_journal_lands_in_jsonl_and_sqlite(self, srv):
        assert srv.neon_ledger is not None and srv.LEDGER_DB_STATE["ok"]
        srv.journal({"ts": "2026-10-19T08:30:00", "kind": "append", "sheet": "0分", "col": "R",
                     "row": 290, "date": "10/19", "value": "+15", "before": "=1",
                     "after": "=1+15", "after_value": "16.0", "before_value": "1.0",
                     "src": "test", "chain": "ok"})
        import glob, json
        lines = [l for p in glob.glob(srv.LEDGER_DIR + "/*.jsonl") for l in open(p)]
        assert len(lines) == 1 and json.loads(lines[0])["value"] == "+15"
        rows = srv.neon_ledger.recent(5)
        assert len(rows) == 1 and rows[0][5] == "credit" and rows[0][6] == 15.0

    def test_ledger_failure_only_logs(self, srv, monkeypatch, capsys):
        def boom(entry):
            raise RuntimeError("db locked")
        monkeypatch.setattr(srv.neon_ledger, "record", boom)
        srv.journal({"ts": "2026-10-19T08:30:00", "kind": "append", "sheet": "0分", "col": "R",
                     "row": 290, "date": "10/19", "value": "+15", "before": "=1",
                     "after": "=1+15", "after_value": "16.0", "src": "t", "chain": "ok"})
        assert srv.LEDGER_DB_STATE["errors"] == 1
        assert "db locked" in srv.LEDGER_DB_STATE["last_error"]
        assert "semantic ledger write failed" in capsys.readouterr().err


class TestBatchWrite:
    """/batch_write: N 0n-style value writes in one osascript call, each
    journaled + double-written, with pre-images echoed back (step 2)."""

    @pytest.fixture()
    def srv(self, tmp_path, monkeypatch):
        import importlib.util, os, sys
        monkeypatch.setenv("NEON_DB", str(tmp_path / "neon.db"))
        for m in [k for k in sys.modules if k == "neon" or k.startswith("neon.")]:
            del sys.modules[m]
        spec = importlib.util.spec_from_file_location(
            "excel_http_server_bw", os.path.join(os.path.dirname(__file__), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.LEDGER_DIR = str(tmp_path / "jsonl")
        m.CHAIN_INDEX.clear()
        return m

    def _run(self, srv, monkeypatch, req, echo):
        captured = {}

        def fake_osa(script):
            captured["script"] = script
            return 0, echo, ""
        monkeypatch.setattr(srv, "osascript", fake_osa)
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 293)
        return srv.do_batch_write(req), captured["script"]

    def test_blank_cell_echo_keeps_leading_empty_fields(self, srv, monkeypatch):
        """A blank cell echoes "\\t\\t<new value>\\t<formula>". osascript() must
        not strip those leading tabs: with a full strip() every field shifted
        left, the ledger logged before=<new value> / after="", and did-fast's
        verify read "" for the value it had just written, so every first 0n
        mark of the day failed and only the retry passed (2026-10-21)."""
        import contextlib
        import subprocess as sp
        monkeypatch.setattr(srv, "quiet_excel", lambda: contextlib.nullcontext())
        monkeypatch.setattr(srv.subprocess, "run",
                            lambda *a, **k: sp.CompletedProcess(a, 0, "\t\t1.0\t1\x1e\n", ""))
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 293)
        resp = srv.do_batch_write({"sheet": "0n", "date": "10/21",
                                   "writes": [{"col": "Q", "value": 1}]})
        assert resp["ok"], resp
        r = resp["results"][0]
        assert (r["before"], r["before_value"], r["value"], r["formula"]) == ("", "", "1.0", "1")
        assert "chain_broken_cols" not in resp

    def test_numbers_land_unquoted_and_add_mode_accumulates(self, srv, monkeypatch):
        req = {"sheet": "0n", "date": "10/19", "src": "did hiit, xk20",
               "writes": [{"col": "AE", "value": 1}, {"col": "AJ", "value": 15, "mode": "add"}]}
        echo = "\t\t1\t1\x1e\t30\t45\t45\x1e"
        resp, script = self._run(srv, monkeypatch, req, echo)
        assert 'cell ("AE293")' in script and "set value of theCell to 1\n" in script
        assert 'set value of theCell to "1"' not in script               # number, not text
        assert "(oldValue as number) + 15" in script                     # cumulative add
        assert 'if oldValue = "" or oldValue = "0" then' in script       # blank → plain set
        assert resp["ok"] and resp["row"] == 293 and len(resp["results"]) == 2
        ae, aj = resp["results"]
        assert ae["value"] == "1" and ae["before_value"] == "" and ae["mode"] == "set"
        assert aj["value"] == "45" and aj["before_value"] == "30" and aj["mode"] == "add"

    def test_each_cell_is_journaled_and_in_the_semantic_ledger(self, srv, monkeypatch):
        req = {"sheet": "0n", "date": "10/19", "src": "did hiit",
               "writes": [{"col": "AE", "value": 1}, {"col": "AF", "value": 1830}]}
        resp, _ = self._run(srv, monkeypatch, req, "\t\t1\t1\x1e\t\t1830\t1830\x1e")
        import glob, json
        lines = [json.loads(l) for p in glob.glob(srv.LEDGER_DIR + "/*.jsonl") for l in open(p)]
        assert [(e["kind"], e["sheet"], e["col"], e["value"], e["src"]) for e in lines] == [
            ("write", "0n", "AE", "1", "did hiit"), ("write", "0n", "AF", "1830", "did hiit")]
        rows = srv.neon_ledger.recent(5)
        assert sorted((r[3], r[4], r[5], r[7]) for r in rows) == [
            ("AE", "hiit", "set", 1.0), ("AF", "N color", "set", 1830.0)]

    def test_formula_and_string_values(self, srv, monkeypatch):
        req = {"sheet": "0n", "row": 293,
               "writes": [{"col": "V", "value": "=1+1"}, {"col": "V", "value": "a \"q\""}]}
        _, script = self._run(srv, monkeypatch, req, "\t\t2\t=1+1\x1e\t\ta \"q\"\ta \"q\"\x1e")
        assert 'set formula of theCell to "=1+1"' in script
        assert 'set value of theCell to "a \\"q\\""' in script

    def test_echo_mismatch_and_bad_input_fail_closed(self, srv, monkeypatch):
        resp, _ = self._run(srv, monkeypatch,
                            {"sheet": "0n", "row": 293, "writes": [{"col": "AE", "value": 1}]},
                            "\t\t1\t1\x1e\t\t2\t2\x1e")
        assert not resp["ok"] and resp["error"].startswith("echo_mismatch")
        assert not srv.do_batch_write({"sheet": "0n", "row": 293, "writes": []})["ok"]
        assert not srv.do_batch_write({"sheet": "0n", "row": 293,
                                       "writes": [{"col": "AE1", "value": 1}]})["ok"]

    def test_route_registered(self, srv):
        assert srv.ROUTES["/batch_write"] is srv.do_batch_write
        assert "/batch_write" not in srv.NO_EXCEL


class TestBatchWriteAppendMode:
    """/batch_write mode "append" (step 2b): shares /append's normalization
    fragment, journals as kind append, echoes `before`, and anchors 1n+ by
    row with a `week` label instead of polluting `date`."""

    @pytest.fixture()
    def srv(self, tmp_path, monkeypatch):
        import importlib.util, os, sys
        monkeypatch.setenv("NEON_DB", str(tmp_path / "neon.db"))
        for m in [k for k in sys.modules if k == "neon" or k.startswith("neon.")]:
            del sys.modules[m]
        spec = importlib.util.spec_from_file_location(
            "excel_http_server_bwa", os.path.join(os.path.dirname(__file__), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.LEDGER_DIR = str(tmp_path / "jsonl")
        m.CHAIN_INDEX.clear()
        return m

    def _run(self, srv, monkeypatch, req, echo):
        captured = {}

        def fake_osa(script):
            captured["script"] = script
            return 0, echo, ""
        monkeypatch.setattr(srv, "osascript", fake_osa)
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 20)
        return srv.do_batch_write(req), captured["script"]

    def test_append_mode_uses_shared_fragment(self, srv, monkeypatch):
        req = {"sheet": "1n+", "date": "10.3", "src": "did family",
               "writes": [{"col": "D", "value": "+45", "mode": "append"},
                          {"col": "K", "value": 1, "mode": "set"}]}
        echo = "\t\t45\t=45\x1e\t\t1\t1\x1e"
        resp, script = self._run(srv, monkeypatch, req, echo)
        assert 'if oldFormula = "" or oldFormula = "0" then' in script
        assert 'set formula of theCell to "=45"' in script                 # empty-cell append
        assert 'if oldFormula does not start with "="' in script           # bare-number guard
        assert 'set formula of theCell to "=" & oldFormula & "+45"' in script
        assert resp["ok"] and resp["results"][0]["mode"] == "append"
        assert resp["results"][0]["before"] == "" and resp["results"][0]["value"] == "45"

    def test_append_mode_matches_do_append_fragment(self, srv):
        e1, n1 = srv._append_setters("+45")
        assert "=45" in e1 and 'oldFormula & "+45"' in n1
        e2, n2 = srv._append_setters(", eggs")                            # string append
        assert 'set value of theCell to "eggs"' == e2

    def test_1n_cells_journal_row_anchored_with_week(self, srv, monkeypatch):
        req = {"sheet": "1n+", "date": "10.3", "src": "did family",
               "writes": [{"col": "D", "value": "+45", "mode": "append"},
                          {"col": "K", "value": 1}]}
        self._run(srv, monkeypatch, req, "\t\t45\t=45\x1e\t\t1\t1\x1e")
        import glob, json
        lines = [json.loads(l) for p in glob.glob(srv.LEDGER_DIR + "/*.jsonl") for l in open(p)]
        assert [(e["kind"], e["col"], e["date"], e.get("week"), e["row"]) for e in lines] == [
            ("append", "D", None, "10.3", 20), ("write", "K", None, "10.3", 20)]
        # semantic ledger: the "+45" is a credit (same as a /batch append), the mark a set
        rows = sorted((r[3], r[5], r[6]) for r in srv.neon_ledger.recent(5))
        assert rows == [("D", "credit", 45.0), ("K", "set", None)]
        # chain index anchored on the row, like every pre-existing 1n+ entry
        assert ("1n+", "D", "r20") in srv.CHAIN_INDEX and ("1n+", "D", "10.3") not in srv.CHAIN_INDEX

    def test_0n_cells_still_journal_by_date(self, srv, monkeypatch):
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 293)
        req = {"sheet": "0n", "date": "10/19", "writes": [{"col": "AE", "value": 1}]}
        resp, _ = self._run(srv, monkeypatch, req, "\t\t1\t1\x1e")
        assert ("0n", "AE", "10/19") in srv.CHAIN_INDEX


class TestReviewSheetSupport:
    """Step 2c: year-aware date lookup, review-sheet DATE_COL, prose literals,
    text mode, US-separated /read, label-sheet journaling."""

    @pytest.fixture()
    def srv(self, tmp_path, monkeypatch):
        import importlib.util, os, sys
        monkeypatch.setenv("NEON_DB", str(tmp_path / "neon.db"))
        for m in [k for k in sys.modules if k == "neon" or k.startswith("neon.")]:
            del sys.modules[m]
        spec = importlib.util.spec_from_file_location(
            "excel_http_server_rs", os.path.join(os.path.dirname(__file__), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.LEDGER_DIR = str(tmp_path / "jsonl")
        m.CHAIN_INDEX.clear()
        return m

    @pytest.mark.parametrize("raw,expected", [
        ("10/19", ("10/19", None)), ("1/5/27", ("1/5", 2027)), ("01/05/2027", ("1/5", 2027)),
        ("2026-10-19", ("10/19", 2026)), ("10.3", ("10.3", None)), (10.3, ("10.3", None)),
    ])
    def test_date_target(self, srv, raw, expected):
        assert srv.date_target(raw) == expected

    def test_lookup_script_checks_year_only_when_given(self, srv, monkeypatch):
        scripts = []
        monkeypatch.setattr(srv, "osascript", lambda s: (scripts.append(s), (0, "5", ""))[1])
        assert srv.lookup_row("0s897", "1/5/27") == 5
        assert "((year of cv) as integer) = 2027" in scripts[-1] and 'md = "1/5"' in scripts[-1]
        srv.lookup_row("0n", "10/19")
        assert "year of cv" not in scripts[-1]
        srv.lookup_row("1分+1s", "10.3")
        assert '(cv as text) = "10.3"' in scripts[-1]
        assert srv.DATE_COL["0s897"] == "B" and srv.DATE_COL["1分+1s"] == "A" and srv.DATE_COL["i9+m5x2"] == "A"
        # the cache verifier uses the same predicate
        srv._row_still_matches("0s897", 5, "1/5/27")
        assert "((year of cv) as integer) = 2027" in scripts[-1]

    def test_as_literal_and_text_mode(self, srv):
        lit = srv.as_literal('line "one"\r\nline two\n= not a formula')
        assert lit == 'line \\"one\\"" & linefeed & "line two" & linefeed & "= not a formula'
        assert srv._as_text_setter("=SUM(A1)") == 'set value of theCell to "=SUM(A1)"'
        assert srv._as_setter("=SUM(A1)").startswith("set formula")
        assert srv._as_setter("a\nb") == 'set value of theCell to "a" & linefeed & "b"'

    def test_read_uses_unit_separator(self, srv, monkeypatch):
        monkeypatch.setattr(srv, "osascript", lambda s: (0, "a | b\x1f=X", ""))
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 7)
        r = srv.do_read({"sheet": "0s897", "col": "E", "date": "10/18/26"})
        assert r["value"] == "a | b" and r["formula"] == "=X"

    def test_label_sheets_journal_row_anchored_with_week(self, srv, monkeypatch):
        monkeypatch.setattr(srv, "osascript", lambda s: (0, "\t\t1\t1\x1e", ""))
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 44)
        srv.do_batch_write({"sheet": "1分+1s", "date": "10.3", "writes": [{"col": "AM", "value": 1, "mode": "add"}]})
        import glob, json
        e = [json.loads(l) for p in glob.glob(srv.LEDGER_DIR + "/*.jsonl") for l in open(p)][0]
        assert e["date"] is None and e["week"] == "10.3" and e["row"] == 44
        assert ("1分+1s", "AM", "r44") in srv.CHAIN_INDEX

    def test_text_mode_never_appends(self, srv, monkeypatch):
        scripts = []
        monkeypatch.setattr(srv, "osascript", lambda s: (scripts.append(s), (0, "\t\t- item\t- item\x1e", ""))[1])
        monkeypatch.setattr(srv, "lookup_row_cached", lambda sheet, d: 9)
        srv.do_batch_write({"sheet": "0s897", "date": "10/18/26",
                            "writes": [{"col": "E", "value": "- item\nmore", "mode": "text"}]})
        assert 'set value of theCell to "- item" & linefeed & "more"' in scripts[-1]
        assert 'set formula of theCell to oldFormula &' not in scripts[-1]   # never the append branch


def test_save_loop_survives_a_save_timeout():
    """Regression (2026-10-22): a save past osascript's 15 s timeout raised
    TimeoutExpired out of save_loop, killing the thread and every later save;
    the web copy of Neon froze at 13:26 while Ix's Excel kept taking writes."""
    import subprocess
    import threading

    calls = {"save": 0, "sleep": 0}

    class Stop(Exception):
        pass

    def sleep(_):
        calls["sleep"] += 1
        if calls["sleep"] > 3:
            raise Stop

    def save_workbook():
        calls["save"] += 1
        if calls["save"] == 1:
            raise subprocess.TimeoutExpired("osascript", 15)
        return 0, "", ""

    ns = {"time": type("T", (), {"sleep": staticmethod(sleep)}), "SAVE_INTERVAL": 0,
          "EXCEL_LOCK": threading.Lock(), "save_workbook": save_workbook,
          "ensure_events_on": lambda: None, "ping_macro": lambda: None,
          "recalc_guard": lambda: "ok", "sys": __import__("sys")}
    exec(textwrap.dedent(_load_func_source("save_loop")), ns)
    with pytest.raises(Stop):
        ns["save_loop"]()
    assert calls["save"] == 3      # the timeout on pass 1 did not end the loop



def test_save_loop_runs_the_recalc_guard_and_survives_its_failure():
    """2026-10-22: Excel stopped recalculating dependents for 5.5 h (Σ stuck at
    1099 while P:Y summed to 1382). Every save pass checks Σ against the live
    sum; a guard that raises must not end the loop either."""
    import threading
    calls = {"guard": 0, "sleep": 0}

    class Stop(Exception):
        pass

    def sleep(_):
        calls["sleep"] += 1
        if calls["sleep"] > 3:
            raise Stop

    def guard():
        calls["guard"] += 1
        if calls["guard"] == 1:
            raise RuntimeError("osascript timed out")
        return "STALE 1099.0 1382.0"

    ns = {"time": type("T", (), {"sleep": staticmethod(sleep)}), "SAVE_INTERVAL": 0,
          "EXCEL_LOCK": threading.Lock(), "save_workbook": lambda: (0, "", ""),
          "ensure_events_on": lambda: None, "ping_macro": lambda: None,
          "recalc_guard": guard, "sys": __import__("sys")}
    exec(textwrap.dedent(_load_func_source("save_loop")), ns)
    with pytest.raises(Stop):
        ns["save_loop"]()
    assert calls["guard"] == 3


def test_recalc_guard_compares_sigma_to_the_live_sum_and_only_then_recalcs():
    src = _load_func_source("recalc_guard")
    src = src[src.index('tell application "Microsoft Excel"'):]       # the script, not the docstring
    assert 'is not "=SUM(P{row}:Y{row})" then return "skip"' in src      # only when D is the Σ formula
    assert "repeat with i from 1 to count of row1" in src                  # indexed (refs break `as real`)
    i_cmp, i_calc = src.index("(d - s) > 0.5 or (s - d) > 0.5"), src.index("calculate full")
    assert i_cmp < i_calc < src.index('return "STALE ')                    # full recalc only on mismatch


class TestReadRange:
    """/read_range (2026-10-30, the 1hcb report): one Apple Event for a block."""

    @pytest.fixture()
    def srv(self, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location("excel_http_server", os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    @pytest.mark.parametrize("rng", ['A1:B2" & (do shell script "x")', "B9:A1", "B9:B1", "A1", "", "A1:ZZ99999"])
    def test_rejects_bad_or_huge_ranges_without_running_osascript(self, srv, monkeypatch, rng):
        monkeypatch.setattr(srv, "osascript", lambda s: pytest.fail("osascript ran for " + rng))
        assert srv.do_read_range({"sheet": "hcbi", "range": rng})["ok"] is False

    def test_splits_rows_and_cells_and_blanks_missing_values(self, srv, monkeypatch):
        seen = {}

        def fake(script):
            seen["s"] = script
            return 0, "Th\x1f2340.0\x1fmissing value\x1eF\x1f\x1f1.2345E+4", ""
        monkeypatch.setattr(srv, "osascript", fake)
        r = srv.do_read_range({"sheet": "hcbi", "range": "c297:e298"})
        assert r == {"ok": True, "range": "C297:E298", "row": 297,
                     "rows": [["Th", "2340.0", ""], ["F", "", "1.2345E+4"]]}
        assert 'range ("C297:E298") of sheet "hcbi"' in seen["s"]
        assert "/read_range" in srv.ROUTES
