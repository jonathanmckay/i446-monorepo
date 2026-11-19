"""Write queue (Neon off Excel step 4b, 2026-11-19): a write request is recorded
before Excel is touched, applied at once when Excel answers, and replayed in
order, exactly once, when it does not.

FakeExcel stands in for osascript: it keeps cell formulas, applies the append
scripts the real handlers build, and can be told to hang before the write, or
after it (the write landed but the daemon never saw the echo)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROW = 321


class FakeExcel:
    def __init__(self, replay):
        self.replay = replay
        self.cells: dict[tuple[str, str], str] = {}
        self.up = True
        self.hang_after_set = False      # apply the write, then time out
        self.hang_cols: set[str] = set() # time out before writing these columns
        self.error = None                # (rc, stderr) to return instead of running
        self.sets: list[tuple[str, str, str]] = []   # (sheet, col, appended value) actually applied

    def osascript(self, script):
        if "return name of workbook" in script:
            return (0, "Neon", "") if self.up else (1, "", "Microsoft Excel got an error: Connection is invalid. (-609)")
        if not self.up:
            raise subprocess.TimeoutExpired("osascript", 15)
        if self.error:
            return self.error[0], "", self.error[1]
        sheet = re.search(r'sheet "([^"]+)"', script).group(1)
        col = re.search(r'cell \("([A-Z]+)\d+"\)', script).group(1)
        if "oldFormula &" not in script:                    # a read
            f = self.cells.get((sheet, col), "")
            return 0, f"{f}\t{f}", ""
        if col in self.hang_cols:
            raise subprocess.TimeoutExpired("osascript", 15)
        val = re.search(r'oldFormula & "([^"]*)"', script).group(1)
        old = self.cells.get((sheet, col), "")
        new = self.replay.apply(old, "append", val)
        self.cells[(sheet, col)] = new
        self.sets.append((sheet, col, val))
        if self.hang_after_set:
            raise subprocess.TimeoutExpired("osascript", 15)
        return 0, f"{old}\t0\t1\t{new}", ""


@pytest.fixture()
def q(tmp_path, monkeypatch):
    monkeypatch.setenv("NEON_DB", str(tmp_path / "neon.db"))
    for m in [k for k in sys.modules if k == "neon" or k.startswith("neon.")]:
        del sys.modules[m]
    spec = importlib.util.spec_from_file_location("excel_http_server_q", os.path.join(HERE, "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.LEDGER_DIR = str(tmp_path / "jsonl")
    m.CHAIN_INDEX.clear(); m.IDEM_INDEX.clear(); m.APPLIED.clear()
    m.QUEUE_ENABLED = True
    m.INTENTS_PATH = str(tmp_path / "intents.jsonl")
    m.INTENTS = m.intents_store.Intents(m.INTENTS_PATH)
    m.STABLE_DELAY = 0
    m.alerts = []
    monkeypatch.setattr(m, "alert", lambda reason, detail: m.alerts.append((reason, detail)))
    m.xl = FakeExcel(m.neon_replay)
    monkeypatch.setattr(m, "osascript", m.xl.osascript)
    monkeypatch.setattr(m, "lookup_row_cached", lambda sheet, d: ROW)
    return m


def journal_lines(m):
    out = []
    d = m.LEDGER_DIR
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        out += [json.loads(l) for l in open(os.path.join(d, name)) if l.strip()]
    return out


def append(col, value, **kw):
    return {"sheet": "0分", "col": col, "date": "11/19", "value": value, "src": "t", **kw}


def baseline(m, col, formula):
    """The 03:50 reconcile's line: what the journal believes the cell holds."""
    m.xl.cells[("0分", col)] = formula
    m.journal({"ts": "2026-11-19T03:50:15", "kind": "baseline", "sheet": "0分", "col": col, "row": ROW,
               "date": "11/19", "value": None, "before": None, "after": formula, "after_value": "0"})


def test_healthy_write_is_unchanged_and_carries_its_intent(q):
    baseline(q, "R", "='0n'!BK324")
    status, res = q.queued_write("/append", append("R", "+15"))
    assert status == 200 and res["ok"] and res["formula"] == "='0n'!BK324+15" and "queued" not in res
    e = journal_lines(q)[-1]
    assert e["kind"] == "append" and e["intent"] and e["item"] == 0 and "requested_ts" not in e
    assert q.INTENTS.stats() == {"pending": 0, "held": 0, "oldest_ts": None}


def test_excel_down_queues_with_todays_status_and_keeps_order(q):
    baseline(q, "R", "=1")
    q.xl.up = False
    s1, r1 = q.queued_write("/append", append("R", "+15"))
    s2, r2 = q.queued_write("/append", append("R", "+7"))
    # legacy status and ok:false, so callers that predate the queue do not fall back to a raw write
    assert (s1, r1["ok"], r1["error"], r1["queued"]) == (504, False, "osascript_timeout", True)
    assert (s2, r2["ok"], r2["queued"]) == (503, False, True)
    assert q.xl.sets == [] and q.INTENTS.stats()["pending"] == 2
    q.xl.up = True
    assert q.drain_locked() == 2
    assert q.xl.sets == [("0分", "R", "+15"), ("0分", "R", "+7")]          # in order, once each
    assert q.xl.cells[("0分", "R")] == "=1+15+7"
    assert q.INTENTS.stats()["pending"] == 0
    replayed = [e for e in journal_lines(q) if e.get("intent")]
    assert [e["value"] for e in replayed] == ["+15", "+7"] and all(e["requested_ts"] for e in replayed)


def test_a_new_write_drains_the_backlog_first_then_applies_normally(q):
    baseline(q, "R", "=1")
    q.xl.up = False
    q.queued_write("/append", append("R", "+15"))
    q.xl.up = True
    status, res = q.queued_write("/append", append("R", "+7"))
    assert status == 200 and res["formula"] == "=1+15+7"
    assert q.xl.sets == [("0分", "R", "+15"), ("0分", "R", "+7")]


def test_write_that_landed_before_the_timeout_is_not_applied_twice(q):
    """The dangerous case: Excel took the +15, then osascript timed out, so
    nothing was journaled. The replay must recognise the cell already holds it."""
    baseline(q, "R", "=1")
    q.xl.hang_after_set = True
    status, res = q.queued_write("/append", append("R", "+15"))
    assert status == 504 and res["queued"]
    assert q.xl.cells[("0分", "R")] == "=1+15" and not [e for e in journal_lines(q) if e.get("intent")]
    q.xl.hang_after_set = False
    assert q.drain_locked() == 1
    assert q.xl.sets == [("0分", "R", "+15")]                              # still exactly one
    assert q.xl.cells[("0分", "R")] == "=1+15"
    e = [e for e in journal_lines(q) if e.get("intent")][-1]
    assert (e["before"], e["after"], e["recovered"], e["chain"]) == ("=1", "=1+15", True, "ok")
    assert q.INTENTS.stats()["pending"] == 0


def test_unexplained_cell_content_holds_the_intent_and_lets_later_ones_through(q):
    baseline(q, "R", "=1")
    baseline(q, "S", "=2")
    q.xl.up = False
    q.queued_write("/append", append("R", "+15"))
    q.queued_write("/append", append("S", "+3"))
    q.xl.up = True
    q.xl.cells[("0分", "R")] = "=1+99"                                     # someone typed into Excel meanwhile
    assert q.drain_locked() == 2
    assert q.xl.cells[("0分", "R")] == "=1+99"                             # not touched
    assert q.xl.cells[("0分", "S")] == "=2+3"                              # later intent still applied
    st = q.INTENTS.stats()
    assert st["held"] == 1 and st["pending"] == 0
    assert q.alerts and q.alerts[0][0] == "held" and "cannot tell" in q.alerts[0][1]


def test_batch_that_failed_midway_replays_only_what_is_missing(q):
    for c in ("R", "S", "T"):
        baseline(q, c, "=1")
    body = {"sheet": "0分", "date": "11/19", "src": "did x",
            "appends": [{"col": "R", "value": "+1"}, {"col": "S", "value": "+2"}, {"col": "T", "value": "+3"}]}
    q.xl.hang_cols = {"S"}
    status, res = q.queued_write("/batch", body)
    assert status == 504 and q.xl.sets == [("0分", "R", "+1")]             # R landed and was journaled
    q.xl.hang_cols = set()
    assert q.drain_locked() == 1
    assert q.xl.sets == [("0分", "R", "+1"), ("0分", "S", "+2"), ("0分", "T", "+3")]
    assert [q.xl.cells[("0分", c)] for c in "RST"] == ["=1+1", "=1+2", "=1+3"]
    items = sorted((e["item"], e["col"]) for e in journal_lines(q) if e.get("intent"))
    assert items == [(0, "R"), (1, "S"), (2, "T")]


def test_bad_request_is_rejected_not_queued(q, monkeypatch):
    monkeypatch.setattr(q, "lookup_row_cached", lambda sheet, d: None)
    status, res = q.queued_write("/append", append("R", "+15"))
    assert status == 400 and res["error"] == "date_not_found_or_missing_target" and "queued" not in res
    assert q.INTENTS.stats()["pending"] == 0


def test_deterministic_script_error_is_final_but_excel_trouble_is_not(q):
    baseline(q, "R", "=1")
    q.xl.error = (1, "Microsoft Excel got an error: Can’t get sheet \"nope\". (-1728x)")
    q.xl.error = (1, "execution error: Microsoft Excel got an error: The object you are trying to access does not exist (-50)")
    status, res = q.queued_write("/append", append("R", "+15"))
    assert status == 400 and q.INTENTS.stats()["pending"] == 0             # would wedge the queue if kept
    q.xl.error = (1, "Microsoft Excel got an error: Connection is invalid. (-609)")
    status, res = q.queued_write("/append", append("R", "+7"))
    assert status == 503 and res["queued"] and q.INTENTS.stats()["pending"] == 1


def test_an_intent_that_keeps_failing_while_excel_answers_is_held(q):
    baseline(q, "R", "=1")
    baseline(q, "S", "=2")
    q.xl.hang_cols = {"R"}
    q.queued_write("/append", append("R", "+15"))
    for _ in range(q.MAX_ATTEMPTS + 1):
        q.drain_locked()
    assert q.INTENTS.stats() == {"pending": 0, "held": 1, "oldest_ts": None}
    assert q.xl.sets == [] and q.alerts
    status, res = q.queued_write("/append", append("S", "+3"))             # the queue is not wedged
    assert status == 200 and res["formula"] == "=2+3"


def test_duplicates_are_recognised_by_idem_key_only(q):
    baseline(q, "R", "=1")
    q.xl.up = False
    q.queued_write("/append", append("R", "+15", idem="0fen:abc@11/19"))
    s, r = q.queued_write("/append", append("R", "+15", idem="0fen:abc@11/19"))   # dtd retry
    assert s == 503 and r["dup"] and q.INTENTS.stats()["pending"] == 1
    q.queued_write("/append", append("R", "+10"))
    q.queued_write("/append", append("R", "+10"))                          # two deliberate +10s, no key
    assert q.INTENTS.stats()["pending"] == 3
    q.xl.up = True
    q.drain_locked()
    assert q.xl.cells[("0分", "R")] == "=1+15+10+10"


def test_restart_between_excel_and_bookkeeping_does_not_reapply(q):
    """A crash after the journal line but before the intent is marked done."""
    baseline(q, "R", "=1")
    intent, _ = q.INTENTS.add("/append", append("R", "+15"))
    q.INTENTS.attempting(intent["id"])
    q.do_append({**append("R", "+15"), "_intent": intent["id"]})           # landed and journaled…
    assert q.xl.cells[("0分", "R")] == "=1+15"
    # …then the daemon dies. On restart both indexes are rebuilt from disk.
    q.INTENTS = q.intents_store.Intents(q.INTENTS_PATH)
    q.seed_chain_index()
    assert q.INTENTS.stats()["pending"] == 1 and (intent["id"], 0) in q.APPLIED
    assert q.drain_locked() == 1
    assert q.xl.sets == [("0分", "R", "+15")] and q.xl.cells[("0分", "R")] == "=1+15"


def test_restart_mid_write_with_nothing_journaled_resolves_against_the_cell(q):
    baseline(q, "R", "=1")
    intent, _ = q.INTENTS.add("/append", append("R", "+15"))
    q.INTENTS.attempting(intent["id"])                                     # died here; did Excel take it?
    q.INTENTS = q.intents_store.Intents(q.INTENTS_PATH)
    assert q.INTENTS.pending()[0]["attempts"] == 1
    assert q.drain_locked() == 1                                           # cell untouched → applied once
    assert q.xl.sets == [("0分", "R", "+15")]


def test_cell_the_journal_has_never_seen_is_held_after_a_failed_attempt(q):
    q.xl.cells[("0分", "Z")] = ""
    q.xl.hang_after_set = True
    q.queued_write("/append", append("Z", "+5"))
    q.xl.hang_after_set = False
    q.drain_locked()
    assert q.INTENTS.stats()["held"] == 1 and "no journaled content" in q.alerts[0][1]


def test_reads_drain_first_so_they_never_see_excel_behind_the_queue(q):
    baseline(q, "R", "=1")
    q.xl.up = False
    q.queued_write("/append", append("R", "+15"))
    q.xl.up = True
    # what do_POST does for a non-write Excel path with the queue on
    with q.EXCEL_LOCK:
        q.drain_locked()
        value, formula = q._read_cell("0分", "R", ROW)
    assert formula == "=1+15"


def test_health_reports_the_queue(q):
    assert q.queue_state() == {"enabled": True, "pending": 0, "held": 0, "oldest_ts": None}
    q.QUEUE_ENABLED = False
    assert q.queue_state() == {"enabled": False}


def _add_intent(q, value=15):
    body = {"sheet": "0n", "date": "11/19", "src": "did xk20",
            "writes": [{"col": "AE", "value": 1}, {"col": "AJ", "value": value, "mode": "add"}]}
    intent, _ = q.INTENTS.add("/batch_write", body)
    q.INTENTS.attempting(intent["id"])                    # dispatched once, outcome unknown
    q.journal({"ts": "2026-11-19T03:50:19", "kind": "baseline", "sheet": "0n", "col": "AJ", "row": ROW,
               "date": "11/19", "value": None, "before": None, "after": "30", "after_value": "30.0"})
    return q.INTENTS.get(intent["id"])


@pytest.mark.parametrize("cell,verdict", [("30", "apply"), ("45", "apply"), ("52", "held")])
def test_cumulative_add_is_resolved_on_the_cell_value(q, monkeypatch, cell, verdict):
    """mode add (xk20 minutes): 30 → still 30 means replay; 45 means the +15
    landed (journal it, replay skips it); anything else cannot be explained."""
    intent = _add_intent(q)
    monkeypatch.setattr(q, "_read_cell", lambda sheet, col, row: (cell, cell))
    got, note = q.resolve_gaps(intent)
    assert got == verdict
    recovered = [e for e in journal_lines(q) if e.get("recovered")]
    if cell == "45":
        e = recovered[0]
        assert (e["kind"], e["col"], e["value"], e["mode"], e["before"], e["after"], e["item"]) == \
               ("write", "AJ", "15", "add", "30", "45", 1)
        assert (intent["id"], 1) in q.APPLIED and (intent["id"], 0) not in q.APPLIED   # the set still replays
    else:
        assert not recovered
    if verdict == "held":
        assert "cannot tell" in note


def test_set_reapplied_after_a_lost_echo_is_not_a_chain_break(q):
    """A plain set that landed before the timeout is simply set again; the
    journal must not call the cell's own value an outside edit."""
    q.journal({"ts": "2026-11-19T03:50:19", "kind": "baseline", "sheet": "0n", "col": "F", "row": ROW,
               "date": "11/19", "value": None, "before": None, "after": "", "after_value": ""})
    req = {"sheet": "0n", "col": "F", "date": "11/19", "value": "1", "src": "did wake up",
           "_intent": "i1", "_reapply": True}
    r = q._journal_and_respond("write", req, ROW, "1", "1.0", "1", "1.0")   # before is already "1"
    assert r["chain"] == "ok"
    e = journal_lines(q)[-1]
    assert e["chain"] == "ok" and e["recovered"] is True and "chain_expected" not in e


def test_over_http_with_the_queue_on(q):
    """The real request path: status codes and bodies as callers see them."""
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    baseline(q, "R", "=1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), q.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert post("/append", append("R", "+15")) [0] == 200
        q.xl.up = False
        status, body = post("/append", append("R", "+7"))
        assert (status, body["ok"], body["error"], body["queued"]) == (504, False, "osascript_timeout", True)
        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            assert json.loads(r.read())["queue"]["pending"] == 1
        q.xl.up = True
        status, body = post("/read", {"sheet": "0分", "col": "R", "date": "11/19"})   # a read drains first
        assert status == 200 and q.xl.cells[("0分", "R")] == "=1+15+7"
        assert q.INTENTS.stats()["pending"] == 0
    finally:
        httpd.shutdown()
