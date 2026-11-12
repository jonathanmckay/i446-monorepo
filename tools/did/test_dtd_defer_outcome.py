#!/usr/bin/env python3
"""Regression (2026-11-12): "? defer failed: 轩轩中文 (restored to list)" with
no reason. dtd's defer worker sent defer-fast's stderr to /dev/null and
reduced its stdout to pass/fail, discarding the {"error": ...} defer-fast
prints; nothing reached the synced record log either. Six defers failed that
morning (the machine's internet was dropping) and the cause had to be
reconstructed from other hosts' logs.

Now: dtd_defer_outcome.py produces the header tail or a one-line reason and
appends a kind=defer record; the worker keeps stderr and shows the reason.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
LINES = DTD.split("\n")


def _load():
    spec = importlib.util.spec_from_file_location("dtd_defer_outcome", HERE / "dtd_defer_outcome.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dtd_defer_outcome"] = mod
    spec.loader.exec_module(mod)
    return mod


def _heredoc(var, eof):
    i0 = next(i for i, l in enumerate(LINES) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(LINES)) if LINES[i] == eof)
    return "\n".join(LINES[i0 + 1:i1])


# --- outcome() -------------------------------------------------------------

def test_success_gives_header_tail_and_target():
    m = _load()
    raw = json.dumps({"target_date": "2026-11-13", "claimed_points": 2, "remaining_points": 10})
    assert m.outcome(raw, 0) == (True, "→ 2026-11-13 [2] today / [10] later", "2026-11-13")


def test_defer_fast_error_json_becomes_the_reason():
    m = _load()
    raw = json.dumps({"error": "task id 6hx4cvMHj438hWhP fetch failed: <urlopen error "
                               "[Errno 8] nodename nor servname provided, or not known>"})
    ok, line, target = m.outcome(raw, 1)
    assert not ok and target is None
    assert line.startswith("task id 6hx4cvMHj438hWhP fetch failed: <urlopen error")


def test_non_json_output_and_silence_still_explain():
    m = _load()
    assert m.outcome("Traceback (most recent call last):\n  boom\n", 1)[1] == "Traceback (most recent call last): boom"
    assert "exited 1 with no output" in m.outcome("", 1)[1]


def test_reason_is_one_line_and_bounded():
    m = _load()
    ok, line, _ = m.outcome(json.dumps({"error": "x\n" * 200}), 1)
    assert "\n" not in line and len(line) <= m.MAX_REASON


# --- record log ------------------------------------------------------------

def test_record_appended_for_fail_and_ok_as_valid_jsonl(tmp_path):
    log = tmp_path / "dtd-log" / "host.jsonl"
    script = HERE / "dtd_defer_outcome.py"
    fail = subprocess.run([sys.executable, str(script), "1", "TID1", str(log), "straylight", "sid1"],
                          input='{"error": "fetch failed: \\"quoted\\" \\\\ back\\nslash"}',
                          capture_output=True, text=True)
    assert fail.returncode == 1
    assert fail.stdout.strip() == 'fetch failed: "quoted" \\ back slash'
    ok = subprocess.run([sys.executable, str(script), "0", "TID2", str(log), "straylight", "sid1"],
                        input='{"target_date": "2026-11-13", "claimed_points": 2, "remaining_points": 10}',
                        capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.startswith("→ 2026-11-13")
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(r["kind"], r["status"], r["id"]) for r in recs] == [("defer", "fail", "TID1"), ("defer", "ok", "TID2")]
    assert recs[0]["error"] == 'fetch failed: "quoted" \\ back slash' and recs[0]["host"] == "straylight"
    assert recs[1]["target_date"] == "2026-11-13"


def test_no_record_path_is_fine():
    script = HERE / "dtd_defer_outcome.py"
    r = subprocess.run([sys.executable, str(script), "1", "TID"], input="", capture_output=True, text=True)
    assert r.returncode == 1 and "exited 1" in r.stdout


# --- dtd.sh worker (structural) --------------------------------------------

def test_defer_worker_keeps_stderr_and_shows_the_reason():
    body = _heredoc("DTD_DEFER", "DEFEREOF")
    run = next(l for l in body.split("\n") if '"\\$DEFER_FAST" --id' in l)
    assert "2>/dev/null" not in run, "defer-fast's stderr must not be discarded"
    assert '2>>"\\$ERRLOG"' in run and "rc=\\$?" in run
    assert '"\\$DEFER_OUTCOME" "\\$rc" "\\$tid" "$DTD_RECLOG" "$DTD_HOSTSLUG" "$DTD_ID"' in body
    hdr = next(l for l in body.split("\n") if "? defer failed:" in l)
    assert "\\${out:+ — \\$out}" in hdr, "failure header must carry the reason"
    assert re.search(r'ERRLOG="\$\{DTD_LOG:\+\$DTD_LOG\.err\}"; ERRLOG="\\\$\{ERRLOG:-/dev/null\}"', body)
