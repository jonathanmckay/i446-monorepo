#!/usr/bin/env python3
"""dtd's completion worker treats $DTD_PUSHED.log as its queue (2026-10-10).

Replaces test_dtd_fifo_invariant.py and test_dtd_recovery_stale_date.py,
which tested the previous design: FIFO lines carried the work, and an idle
tick diffed the durable log against processed ids to re-inject what the
killable fzf child dropped (19 of 54 completions in one 2026-10-09/10
session). Now done.sh's log line IS the work item and a FIFO line only wakes
the worker, so a dropped push costs one 2s tick and nothing is re-injected.

Kept guarantees from the old suites: an item that only reached the log is
processed exactly once; a delivered one is not processed twice; a previous
day's leftovers are never replayed against today (alerted once). New: the
cursor is by line, so the same recurring id queued twice in a day runs twice;
consecutive 0neon habits run as ONE did-fast batch with per-item ctrl-z
journal entries.
"""
import datetime as dt
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()
LINES = SRC.splitlines()
TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _worker_body() -> str:
    i0 = LINES.index("(")
    i1 = LINES.index(") &", i0)
    return "\n".join(LINES[i0:i1 + 1])


def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _run(tmp_path, pushed_lines, wake=0, cache=None, didfast=None):
    """Run the REAL worker body with $DTD_PUSHED.log seeded with
    `pushed_lines` and `wake` FIFO wake-ups. Returns (did-fast argv list per
    call, log text, journal entries)."""
    calls = tmp_path / "didfast.calls"
    did_fast = _stub(tmp_path, "did_fast_stub.py", didfast or (
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "argv = [a for a in sys.argv[1:]]\n"
        "if argv and argv[0] == '--task-id': argv = argv[2:]\n"
        "names = [n.strip() for n in ' '.join(argv).split(',')]\n"
        "names = [n.rsplit(' ', 1)[0] if n.rsplit(' ', 1)[-1].isdigit() else n for n in names]  # 'cpap 2' -> cpap, as did-fast names it\n"
        "print(json.dumps({'results': [{'name': n, 'step': '0n', 'todoist': {'closed': True}} for n in names],"
        " 'agent_needed': []}))\n"))
    undo_fast = _stub(tmp_path, "undo_fast_stub.py",
                      "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n")
    fifo, hdr, log = tmp_path / "fifo", tmp_path / "hdr", tmp_path / "log"
    pushed, processed = tmp_path / "pushed", tmp_path / "processed"
    processed_ids, journal, stop = tmp_path / "processed.ids", tmp_path / "journal", tmp_path / "stop"
    Path(str(pushed) + ".log").write_text("".join(pushed_lines))
    did = "DTDTEST"
    cache_path = Path(f"/tmp/dtd-{did}.cache.json")
    cache_path.write_text(json.dumps(cache or {"0neon": [], "夜neon": []}))
    home = tmp_path / "fakehome"
    home.mkdir(exist_ok=True)
    body = _worker_body()
    wakes = "printf 'wake\\n' >&3\n" * wake
    script = f"""#!/bin/zsh
zmodload zsh/datetime
DTD_ID={did}
DTD_FIFO={fifo}
DTD_HDR={hdr}
DTD_LOG={log}
DTD_PUSHED={pushed}
DTD_PROCESSED={processed}
DTD_PROCESSED_IDS={processed_ids}
DTD_JOURNAL={journal}
DTD_STOP={stop}
DID_FAST={did_fast}
UNDO_FAST={undo_fast}
DTD_OUTCOME={HERE / "dtd_outcome.py"}
export XDG_STATE_HOME={tmp_path / "state"}
mkfifo "$DTD_FIFO"
: > "$DTD_PROCESSED"; : > "$DTD_PROCESSED_IDS"; : > "$DTD_LOG"; : > "$DTD_LOG.err"; : > "$DTD_JOURNAL"
{body}
WORKER_PID=$!
exec 3>"$DTD_FIFO"
{wakes}sleep 3.5
touch "$DTD_STOP"
exec 3>&-
for _i in $(seq 1 60); do
  kill -0 $WORKER_PID 2>/dev/null || break
  sleep 0.1
done
"""
    (tmp_path / "harness.sh").write_text(script)
    try:
        subprocess.run(["zsh", str(tmp_path / "harness.sh")], capture_output=True, text=True, timeout=30)
    finally:
        cache_path.unlink(missing_ok=True)
    call_list = [json.loads(c) for c in calls.read_text().splitlines()] if calls.exists() else []
    call_list = [c for c in call_list if c != ["--refresh-cache"]]  # idle cache refresh, not a completion
    jl = [json.loads(l) for l in journal.read_text().splitlines() if l.strip()] if journal.exists() else []
    return call_list, log.read_text() if log.exists() else "", jl


def test_item_only_in_the_log_is_processed_once_without_any_fifo_push(tmp_path):
    """THE 2026-08-03 race: done.sh logged the completion but its FIFO push
    was killed. The log line alone must get it processed, exactly once."""
    calls, log, _ = _run(tmp_path, [f"{TODAY}T10:00:00\tdone\tLOSTID\t😈 -1l\n"])
    assert calls == [["--task-id", "LOSTID", "😈 -1l"]]
    assert "auto-recovered" not in log


def test_delivered_item_is_not_processed_twice(tmp_path):
    """The FIFO line is a wake-up, not a second copy of the work."""
    calls, _, _ = _run(tmp_path, [f"{TODAY}T10:00:00\tdone\tA\tdelivered\n"], wake=3)
    assert calls == [["--task-id", "A", "delivered"]]


def test_same_recurring_id_twice_in_a_day_runs_twice(tmp_path):
    """Cursor by line, not by id: a recurring card keeps its id all day."""
    calls, _, _ = _run(tmp_path, [f"{TODAY}T10:00:00\tdone\tFAM\tfamily 30\n",
                                  f"{TODAY}T15:00:00\tdone\tFAM\tfamily 20\n"])
    assert calls == [["--task-id", "FAM", "family 30"], ["--task-id", "FAM", "family 20"]]


def test_previous_days_leftover_is_never_replayed_and_alerted_once(tmp_path):
    calls, log, _ = _run(tmp_path, [f"{YESTERDAY}T20:55:04\tdone\tSTALE\t1st hci\n",
                                    "10:00:00\tdone\tLEGACY\told line\n",
                                    f"{TODAY}T10:00:00\tdone\tLIVE\t😈 -1l\n"], wake=2)
    assert calls == [["--task-id", "LIVE", "😈 -1l"]]
    assert log.count("1st hci") == 1 and "NOT replayed" in log
    assert "old line" in log


def test_consecutive_habits_run_as_one_batch_with_per_item_undo(tmp_path):
    cache = {"0neon": [{"id": "H1"}, {"id": "H2"}, {"id": "H3"}], "夜neon": []}
    lines = [f"{TODAY}T05:50:00\tdone\tH1\ttmrw\n",
             f"{TODAY}T05:50:01\tdone\tH2\tcharge\n",
             f"{TODAY}T05:50:02\tdone\tH3\tcpap 2\n",
             f"{TODAY}T05:50:03\tdone\tT9\tsend the memo\n"]
    calls, log, journal = _run(tmp_path, lines, cache=cache)
    assert calls[0] == ["tmrw, charge, cpap 2"], calls
    assert calls[1] == ["--task-id", "T9", "send the memo"]
    assert len(calls) == 2
    for n in ("tmrw", "charge", "cpap"):
        assert f"✓ {n} → 0n" in log
    # one ctrl-z entry per habit, each carrying only its own result
    assert [j["names"] for j in journal] == [["tmrw"], ["charge"], ["cpap"]]
    assert [j["task_ids"][0] for j in journal] == ["H1", "H2", "H3"]


def test_batch_item_missing_from_output_is_restored_not_silently_dropped(tmp_path):
    cache = {"0neon": [{"id": "H1"}, {"id": "H2"}], "夜neon": []}
    didfast = ("#!/usr/bin/env python3\nimport json\n"
               "print(json.dumps({'results': [{'name': 'tmrw', 'step': '0n'}], 'agent_needed': []}))\n")
    _, log, _ = _run(tmp_path, [f"{TODAY}T05:50:00\tdone\tH1\ttmrw\n",
                                f"{TODAY}T05:50:01\tdone\tH2\tcharge\n"], cache=cache, didfast=didfast)
    assert "✓ tmrw → 0n" in log
    assert "? charge (restored to list)" in log


def test_0t_and_comma_content_are_never_batched(tmp_path):
    cache = {"0neon": [{"id": "Z"}, {"id": "H1"}, {"id": "C"}], "夜neon": []}
    calls, _, _ = _run(tmp_path, [f"{TODAY}T05:50:00\tdone\tZ\t0t\n",
                                  f"{TODAY}T05:50:01\tdone\tH1\ttmrw\n",
                                  f"{TODAY}T05:50:02\tdone\tC\ta, b\n"], cache=cache)
    assert calls == [["--task-id", "Z", "0t"], ["--task-id", "H1", "tmrw"], ["--task-id", "C", "a, b"]]


def test_no_reinject_machinery_left():
    body = _worker_body()
    assert ">&4" not in body, "the worker must not write work back onto its own FIFO"
    assert "reinjected" not in body


def test_push_lines_carry_a_full_date():
    """done.sh's log line must timestamp with %Y-%m-%d (carried over from
    test_dtd_recovery_stale_date.py): the same-day gate above cannot tell
    yesterday's push from today's without it. 2026-10-03: built with the zsh
    strftime builtin (_now) instead of exec'ing date."""
    import re
    legacy = re.search(
        r"printf '%s\\tdone\\t%s\\t%s\\n' \"\\\$\(date \+%Y-%m-%dT%H:%M:%S\)\"", SRC)
    builtin = ("strftime -s _now '%Y-%m-%dT%H:%M:%S'" in SRC
               and re.search(r"printf '%s\\tdone\\t%s\\t%s\\n' \"\\\$_now\"", SRC))
    assert legacy or builtin, "pushed.log timestamps must include the date"
    assert 'date +%H:%M:%S' not in SRC.split("PUSHED.log")[0].rsplit("printf", 1)[-1]
