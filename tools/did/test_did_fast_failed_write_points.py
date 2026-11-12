"""Regression (2026-11-12): "janus is showing at least 140 logged points, but
巳 shows only 98" / "it should have gotten 100 points but didn't".

"Jon/Sarah connect 0800-0830 @g245 [100]" ran did-fast from desktop janus on
Straylight at 08:52. The Toggl entry and the posthoc Todoist task landed; the
0分 batch append never reached the excel-http daemon (no ledger entry). did-fast
exited 2 as designed, but step 7 had already written completed-today with
points=100, so janus-mobile showed the entry as logged for 100 while Excel's
巳 cell held 58 + 40 (Metrics meeting) = 98.

Fix: completed-today only carries 分 for names whose 0分 / hcbi batch landed,
and a batch with the rc-0 "ERROR: date … not found" echo counts as not landed
(0fen_write.ok was rc-only before, so that miss was reported as success).
"""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

DID_FAST = Path(__file__).resolve().parent / "did-fast.py"
SRC = DID_FAST.read_text(encoding="utf-8")


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_failed_write", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _cp(rc, out="", err=""):
    return subprocess.CompletedProcess("excel-http", rc, stdout=out, stderr=err)


def test_batch_landed_rules():
    df = _load()
    assert df._batch_landed(None) is True                       # no batch = nothing to lose
    assert df._batch_landed(_cp(0, "OK:0fen row=314")) is True
    assert df._batch_landed(_cp(1, "", "ssh timeout")) is False
    # The old AppleScript shape: rc 0 but nothing written.
    assert df._batch_landed(_cp(0, "ERROR: date 11/12 not found in 0分")) is False


def _step7() -> str:
    main = next(n for n in ast.walk(ast.parse(SRC))
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(SRC, main)
    i = body.index("# 7. Update completed-today")
    j = body.index("mc.append_names(completed_names", i)
    return body[i:j]


def test_completed_today_points_gated_on_landed_batches():
    step7 = _step7()
    assert "_batch_landed(fen_result)" in step7, "0分 batch outcome must gate completed_points"
    assert "_batch_landed(hcbi_result)" in step7, "hcbi batch outcome must gate completed_points"
    assert "uncredited" in step7 and "completed_points = {" in step7, (
        "names whose batch failed must be dropped from completed_points before append_names")


def test_0fen_write_ok_is_not_rc_only():
    main = next(n for n in ast.walk(ast.parse(SRC))
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(SRC, main)
    i = body.index('output["0fen_write"] = {')
    blk = body[i:i + 400]
    assert '"ok": fen_ok' in blk, "0fen_write.ok must use the landed check, not returncode == 0"
    assert "fen_result.returncode == 0" not in blk
