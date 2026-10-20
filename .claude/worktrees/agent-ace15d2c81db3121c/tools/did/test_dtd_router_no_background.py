#!/usr/bin/env python3
"""Regression (2026-08-03): "dtd invariant" recurred a 5th time. Task
6hCHH5g2FG7rw7X2 ("get to a conclusion on biowar {10}") wrote its
$PUSHED.log line (proving done.sh ran) but never reached the FIFO worker
(absent from $DTD_PROCESSED_IDS) and did-fast never ran (zero entries on
ix's durable Neon ledger for "biowar") — a clean, total loss, not a slow
tail. Traced to done-router.sh's execute-silent action for non-value-prompt
tasks backgrounding done.sh with a trailing `&`. fzf's own docs say
execute-silent blocks until the command completes and suggests
backgrounding as the fix for that — which is exactly the trap: a
`command &` grandchild inside execute-silent isn't part of the action's own
tracked lifetime and gets dropped once the action's synchronous portion
returns, before done.sh can finish its FIFO push. Reproduced directly
against fzf: a backgrounded child of an execute-silent action reliably
fails to survive, even with nohup or a real new session/process group.

Fix: drop the trailing `&` so done.sh runs as part of the execute-silent
action's own synchronous chain (matching how the cpap/xk20/xk22/xk26/i444
value-prompt tasks have always invoked done.sh, via bare execute(...), with
no history of this failure mode).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _generate_router(tmp_path, cache_path):
    """Extracts the exact DTD_DONE_ROUTER-generating lines from dtd.sh and
    sources them for real, mirroring test_dtd_router_jq_lookup.py's
    approach — heredoc escaping is easy to get subtly wrong by hand."""
    lines = SRC.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith('DTD_DONE_ROUTER='))
    end = next(i for i, l in enumerate(lines) if l.strip() == "ROUTEREOF")
    gen_script = "\n".join(lines[start:end + 1])

    dtd_id = "testrouternobg"
    setup = f"""#!/bin/zsh
DTD_ID="{dtd_id}"
DTD_CACHE_FILE="{cache_path}"
DTD_DONE_HIDE="/tmp/dtd-{dtd_id}.done-hide.sh"
DTD_DONE="/tmp/dtd-{dtd_id}.done.sh"
DTD_VAR1N_PAT="xxxNOMATCH"
"""
    script_path = tmp_path / "gen.zsh"
    script_path.write_text(setup + gen_script)
    subprocess.run(["zsh", str(script_path)], check=True, capture_output=True, text=True)
    router_path = Path(f"/tmp/dtd-{dtd_id}.done-router.sh")
    assert router_path.exists()
    router_path.chmod(0o755)
    return router_path


def _fake_cache(tmp_path):
    data = {"today": [{"id": "id-plain", "content": "2nd hci (15) [15]"}]}
    p = tmp_path / "cache.json"
    p.write_text(json.dumps(data))
    return p


def test_plain_habit_completion_is_not_backgrounded(tmp_path):
    cache = _fake_cache(tmp_path)
    router = _generate_router(tmp_path, cache)
    out = subprocess.run(["zsh", str(router), "id-plain"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert out.startswith("execute-silent("), f"expected execute-silent, got: {out!r}"
    assert not re.search(r"&\)\s*$", out), (
        "done.sh must NOT be backgrounded inside execute-silent — a `&` here "
        "detaches it as a grandchild that fzf drops before the FIFO push "
        f"completes (2026-08-03 incident, task 6hCHH5g2FG7rw7X2). Got: {out!r}")
    router.unlink()


def test_plain_habit_still_chains_hide_then_done(tmp_path):
    """The fix must not regress the 2026-08-01 instant-hide UI win — done-hide.sh
    still runs (and still runs first) before done.sh."""
    cache = _fake_cache(tmp_path)
    router = _generate_router(tmp_path, cache)
    out = subprocess.run(["zsh", str(router), "id-plain"],
                          capture_output=True, text=True, check=True).stdout.strip()
    hide_pos = out.find("done-hide.sh")
    done_pos = out.find("done.sh")
    assert hide_pos != -1 and done_pos != -1, f"expected both scripts chained: {out!r}"
    assert hide_pos < done_pos, f"done-hide.sh must run before done.sh: {out!r}"
    router.unlink()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
