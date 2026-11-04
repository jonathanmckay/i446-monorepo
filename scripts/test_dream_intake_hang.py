"""dream-intake must not outlive its timeouts (2026-11-03, Dream v177).

Bug: duration_ms was 922k-7.2M ms for weeks while every source's elapsed_ms
summed to ~1 min. The outlook sources called Agency on ix (no work auth),
which sat ~15 min per server on an interactive sign-in; fut.result(timeout)
reported "timeout" but `with ThreadPoolExecutor` then waited for the hung
thread anyway, and the interpreter joined it again at exit.
"""
import importlib.util
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dream_intake", HERE / "dream-intake.py")
di = importlib.util.module_from_spec(spec)
spec.loader.exec_module(di)


def test_run_sources_does_not_wait_for_a_hung_source(monkeypatch):
    monkeypatch.setattr(di, "_SOURCES", {
        "fast": {"fn": lambda: {"x": 1}, "desc": ""},
        "hung": {"fn": lambda: time.sleep(30), "desc": ""},
    })
    t0 = time.time()
    res = di.run_sources(timeout=1)
    assert time.time() - t0 < 5, "run_sources waited for the hung thread"
    assert res["fast"]["status"] == "ok"
    assert res["hung"]["status"] == "timeout"


def test_process_exits_promptly_with_a_hung_source(tmp_path):
    # The interpreter joins non-daemon executor threads at exit; main() must
    # hard-exit once the snapshot is written.
    script = textwrap.dedent(f"""
        import importlib.util, sys, time
        spec = importlib.util.spec_from_file_location("di", {str(HERE / 'dream-intake.py')!r})
        di = importlib.util.module_from_spec(spec); spec.loader.exec_module(di)
        di._SOURCES.clear()
        di._SOURCES["hung"] = {{"fn": lambda: time.sleep(60), "desc": ""}}
        di.load_credentials = lambda: None
        orig = di.run_sources
        di.run_sources = lambda selected=None, timeout=30: orig(selected, 1)
        sys.argv = ["x", "--output", {str(tmp_path / 'out.json')!r}]
        di.main()
    """)
    t0 = time.time()
    subprocess.run([sys.executable, "-c", script], timeout=30, check=True,
                   capture_output=True)
    assert time.time() - t0 < 15
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["sources"]["hung"]["status"] == "timeout"
