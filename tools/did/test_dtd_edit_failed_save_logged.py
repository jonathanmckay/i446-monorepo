#!/usr/bin/env python3
"""Regression (2026-11-05): "edit-saved is stamped even when the save
failed, so a failed save leaves no record" (11-04 /bug session, T4/T6).

dtd's edit script ran edit-fast in the background, wrote its output to the
header (overwritten by the next action) and stamped `edit-saved` in the
timing log unconditionally. Now `edit-saved` is stamped only when edit-fast
exits 0; a failure stamps `edit-failed` and appends a kind=edit record (rc,
edit-fast's output, the form's answers) to the synced record log.

The test generates the real edit script from dtd.sh (same unquoted-heredoc
expansion dtd does), points /dev/tty at /dev/null, and runs it under a fake
HOME whose edit-form.py / edit-fast.py are stubs. Nothing is left running:
each run waits for its background writer's timing line.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()

FORM_STUB = """import sys, json
a = sys.argv
open(a[a.index("--out") + 1], "w").write(json.dumps({"name": "renamed task"}))
"""
FAST_STUB = """import os, sys
print(os.environ.get("STUB_OUT", ""))
sys.exit(int(os.environ.get("STUB_RC", "0")))
"""


def _heredoc_block(var, eof):
    lines = DTD.split("\n")
    i0 = next(i for i, l in enumerate(lines) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(lines)) if lines[i] == eof)
    return "\n".join(lines[i0:i1 + 1])


def _run_edit(tmp_path, rc, out):
    home = tmp_path / "home"
    did = home / "i446-monorepo/tools/did"
    did.mkdir(parents=True)
    (did / "edit-form.py").write_text(FORM_STUB)
    (did / "edit-fast.py").write_text(FAST_STUB)
    sid = f"t{os.getpid()}{int(time.time() * 1000) % 100000}"
    f = {k: str(tmp_path / k) for k in ("edit.sh", "hdr", "timing.log", "events.tsv", "rec.jsonl", "cache.json")}
    env = dict(os.environ, HOME=str(home), DTD_ID=sid, DTD_EDIT=f["edit.sh"], DTD_HDR=f["hdr"],
               DTD_TIMING=f["timing.log"], DTD_EVENTS=f["events.tsv"], DTD_RECLOG=f["rec.jsonl"],
               DTD_CACHE_FILE=f["cache.json"], DTD_HOSTSLUG="testhost", DTD_PORT=str(tmp_path / "noport"),
               STUB_RC=str(rc), STUB_OUT=out)
    subprocess.run(["zsh", "-c", _heredoc_block("DTD_EDIT", "EDITEOF")], env=env, check=True)
    script = Path(f["edit.sh"])
    script.write_text(script.read_text().replace("/dev/tty", "/dev/null"))
    try:
        subprocess.run(["zsh", str(script), "T42"], env=env, check=True, timeout=30,
                       stdin=subprocess.DEVNULL, capture_output=True)
        timing = Path(f["timing.log"])
        deadline = time.time() + 20
        while time.time() < deadline:
            t = timing.read_text() if timing.exists() else ""
            if "\tedit-saved\t" in t or "\tedit-failed\t" in t:
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"background writer never finished: {t!r}")
        time.sleep(0.1)  # the record-log append follows the timing line
        rec = Path(f["rec.jsonl"])
        return (t, Path(f["hdr"]).read_text().strip(),
                [json.loads(l) for l in rec.read_text().splitlines()] if rec.exists() else [])
    finally:
        for p in Path("/tmp").glob(f"dtd-{sid}.*"):
            p.unlink(missing_ok=True)


def test_successful_save_stamps_edit_saved_and_logs_nothing(tmp_path):
    timing, hdr, recs = _run_edit(tmp_path, 0, "✎ old task: name→\"renamed task\"")
    assert "\tedit-saved\tT42" in timing and "edit-failed" not in timing
    assert hdr.startswith("✎ old task")
    assert recs == []


def test_failed_save_stamps_edit_failed_and_writes_record(tmp_path):
    timing, hdr, recs = _run_edit(tmp_path, 1, "✗ Todoist update failed: HTTP 503")
    assert "\tedit-failed\tT42 rc=1" in timing
    assert "edit-saved" not in timing
    assert hdr == "✗ Todoist update failed: HTTP 503"
    assert len(recs) == 1
    r = recs[0]
    assert (r["kind"], r["status"], r["id"], r["rc"], r["host"]) == ("edit", "failed", "T42", 1, "testhost")
    assert "HTTP 503" in r["output"]
    assert json.loads(r["form"]) == {"name": "renamed task"}   # the typed edit survives


def test_crash_without_cross_line_gets_a_readable_header(tmp_path):
    tb = "Traceback (most recent call last):\n  File \"edit-fast.py\", line 1\nKeyError: 'today'"
    timing, hdr, recs = _run_edit(tmp_path, 1, tb)
    assert hdr == "✗ edit not saved (rc 1): KeyError: 'today'"
    assert recs and recs[0]["output"].endswith("KeyError: 'today'")


def test_dtd_log_lists_failed_edits(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dtd_log_ef", HERE / "dtd-log.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    s = m.summarize([{"ts": 1.0, "kind": "edit", "status": "failed", "id": "T42", "rc": 1,
                      "output": "✗ Todoist update failed: HTTP 503"}])
    assert s["edit_failed"] == [("T42", 1, ["✗ Todoist update failed: HTTP 503"])]
