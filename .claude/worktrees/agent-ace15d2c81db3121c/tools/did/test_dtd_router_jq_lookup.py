#!/usr/bin/env python3
"""Regression (2026-08-02): "invariant in dtd" recurred a 4th time the same
day, this time under completely normal use (no external contention) — three
rapid alt-enter presses 1-2s apart, the first succeeded, the next two
("-1l" task 6hC77PCj8M3VhGPc, "-1ibx" task 6hC77P8gJ2cgV8m6) were lost. The
2026-08-01 fast-hide/background split fixed done.sh's own tail latency, but
never touched a separate, still-synchronous cost in the SAME alt-enter
binding: $DTD_DONE_ROUTER runs as fzf's `transform` action on every single
alt-enter, blocking fzf from accepting the next key until it returns, and it
was shelling out to a fresh python3 interpreter (dtd_resolve.py) just to do
a one-id lookup in the cached task JSON.

Measured live: python3 dtd_resolve.py costs ~65-100ms per call (interpreter
startup, not the lookup itself); the equivalent jq query costs ~5ms, same
output. Swapping it out doesn't prove the rapid-fire race is fully closed,
but it's a real, measured cut to latency in the exact synchronous window
that's already been shown twice to matter.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _router_body() -> str:
    m = re.search(r'cat > "\$DTD_DONE_ROUTER" << ROUTEREOF\n(.*?)\nROUTEREOF', SRC, re.S)
    assert m, "could not find the done-router heredoc in dtd.sh"
    return m.group(1)


# ── Structural ────────────────────────────────────────────────────────────

def test_router_no_longer_shells_out_to_python3_for_the_lookup():
    body = _router_body()
    assert 'python3' not in body, (
        "the router runs synchronously on every alt-enter, blocking fzf "
        "from accepting the next key -- it must not pay for a python3 "
        "interpreter start just to look up one id in a JSON file")


def test_router_uses_jq_for_the_lookup():
    body = _router_body()
    assert re.search(r"jq -r --arg id .*\.content", body), (
        "expected a jq-based id -> content lookup in the router")


def test_router_still_falls_back_to_the_raw_id_when_unmatched():
    """dtd_resolve.py's own contract: echo the input back unchanged if no
    task in the cache has that id (defensive: legacy callers may pass text,
    not an id). The jq replacement must preserve this exact fallback."""
    body = _router_body()
    assert '// \\$id' in body or '// $id' in body, (
        "an unmatched id must fall back to itself, matching "
        "dtd_resolve.py's original behavior")


# ── Functional: generate the REAL router script and run it ────────────────

def _generate_router(tmp_path, cache_path):
    """Extracts the exact DTD_DONE_ROUTER-generating lines from dtd.sh and
    sources them for real (not a hand-reconstructed copy — heredoc escaping
    is easy to get subtly wrong by hand, so fidelity matters here), then
    returns the path to the resulting generated router script."""
    lines = SRC.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith('DTD_DONE_ROUTER='))
    end = next(i for i, l in enumerate(lines) if l.strip() == "ROUTEREOF")
    gen_script = "\n".join(lines[start:end + 1])

    dtd_id = "testrouter"
    setup = f"""#!/bin/zsh
DTD_ID="{dtd_id}"
DTD_CACHE_FILE="{cache_path}"
DTD_DONE_HIDE="/bin/echo"
DTD_DONE="/bin/echo"
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
    data = {
        "today": [
            {"id": "id-value-prompt", "content": "i444 (15) [5]"},
            {"id": "id-plain", "content": "2nd hci (15) [15]"},
        ]
    }
    p = tmp_path / "cache.json"
    p.write_text(json.dumps(data))
    return p


def test_value_prompt_habit_routes_to_execute(tmp_path):
    cache = _fake_cache(tmp_path)
    router = _generate_router(tmp_path, cache)
    out = subprocess.run(["zsh", str(router), "id-value-prompt"],
                          capture_output=True, text=True, check=True).stdout
    assert out.startswith("execute("), f"i444 must route to execute (tty), got: {out!r}"
    router.unlink()


def test_plain_habit_routes_to_execute_silent(tmp_path):
    cache = _fake_cache(tmp_path)
    router = _generate_router(tmp_path, cache)
    out = subprocess.run(["zsh", str(router), "id-plain"],
                          capture_output=True, text=True, check=True).stdout
    assert out.startswith("execute-silent("), f"plain habit must route to execute-silent, got: {out!r}"
    router.unlink()


def test_unknown_id_falls_back_to_itself_and_routes_silent(tmp_path):
    cache = _fake_cache(tmp_path)
    router = _generate_router(tmp_path, cache)
    out = subprocess.run(["zsh", str(router), "totally-unknown-id"],
                          capture_output=True, text=True, check=True).stdout
    assert out.startswith("execute-silent("), (
        "an id with no cache match must fall back to itself as the "
        "content, which won't match any value-prompt name, and route silent")
    router.unlink()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
