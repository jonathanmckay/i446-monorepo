#!/usr/bin/env python3
"""Regression (2026-10-02): "dtd is still taking too long to clear tasks --
the path from opt-enter to the row disappearing."

No network call is on that path (did-fast runs in the FIFO worker). The row
only vanished after fzf had synchronously run: the router (zsh+jq), done-hide,
done.sh (python3 re-resolve of the same id, sed pipelines, tty drain) and
then the list regen + header -- 1-3s, stretching under CPU load. done.sh
cannot be backgrounded (see the 2026-08-03 data-loss note in dtd.sh).

Fix, three parts:
  1. the router leads its emitted action chain with fzf's `exclude`, so the
     row disappears client-side as soon as the ~0.1s router returns;
  2. alt-enter's reload is `reload-sync`, so the already-excluded list stays
     on screen instead of blanking while the regen runs;
  3. the router passes the jq-resolved content to done.sh base64-encoded
     (action-string safe), so done.sh skips its own python3 re-resolve.
"""
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _heredoc(var: str, eof: str) -> str:
    lines = DTD.split("\n")
    i0 = next(i for i, l in enumerate(lines) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(lines)) if lines[i] == eof)
    return "\n".join(lines[i0 + 1:i1])


# --- structural ------------------------------------------------------------

def test_alt_enter_uses_reload_sync():
    m = re.search(r'--bind "alt-enter:[^"]*"', DTD)
    assert m and "reload-sync($DTD_RELOAD)" in m.group(0), m and m.group(0)
    assert "transform($DTD_DONE_ROUTER {2})" in m.group(0)


def test_router_emits_exclude_first_on_both_branches():
    body = _heredoc("DTD_DONE_ROUTER", "ROUTEREOF")
    assert '_ex="exclude+"' in body
    assert '[[ "\\$_id" == BLOCK:* ]] && _ex=""' in body, "picker rows must not be excluded"
    assert body.count("printf '%sexecute(") == 1 and body.count("printf '%sexecute-silent(") == 1


def test_router_passes_base64_content_and_keeps_jq_lookup():
    body = _heredoc("DTD_DONE_ROUTER", "ROUTEREOF")
    assert re.search(r"jq -r --arg id .*\n.*\.content", body)
    assert "@base64" in body, "base64 must come from the single jq pass"
    for tool in ("python3", "sed ", "| base64", "| tr "):
        assert tool not in body, f"router must stay a single jq exec; found {tool!r}"


def test_done_sh_decodes_router_content_and_only_falls_back_to_python():
    body = _heredoc("DTD_DONE", "DONEEOF")
    dec = body.index('task=\\$(printf \'%s\' "\\$2" | base64 -d')
    fb = body.index('[[ -n "\\$task" ]] || task=\\$(python3 "$DTD_RESOLVE"')
    assert dec < fb, "decode first, python resolve only when no content was passed"


# --- functional: generate the real router and run it --------------------------

def _generate_router(tmp_path, cache_path):
    lines = DTD.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("DTD_DONE_ROUTER="))
    end = next(i for i, l in enumerate(lines) if l.strip() == "ROUTEREOF")
    dtd_id = "testinstanthide"
    setup = f"""#!/bin/zsh
DTD_ID="{dtd_id}"
DTD_CACHE_FILE="{cache_path}"
DTD_DONE_HIDE="/HIDE.sh"
DTD_DONE="/DONE.sh"
DTD_VAR1N_PAT="xxxNOMATCH"
"""
    gen = tmp_path / "gen.zsh"
    gen.write_text(setup + "\n".join(lines[start:end + 1]))
    subprocess.run(["zsh", str(gen)], check=True, capture_output=True, text=True)
    router = Path(f"/tmp/dtd-{dtd_id}.done-router.sh")
    assert router.exists()
    router.chmod(0o755)
    return router


CONTENT = "2nd hci (15) [15]"


def _cache(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"today": [
        {"id": "id-plain", "content": CONTENT},
        {"id": "id-prompt", "content": "i444 (15) [5]"},
    ]}))
    return p


def _run(router, arg):
    return subprocess.run(["zsh", str(router), arg], capture_output=True, text=True, check=True).stdout


def test_plain_task_action_excludes_then_hides_then_completes(tmp_path):
    router = _generate_router(tmp_path, _cache(tmp_path))
    try:
        out = _run(router, "id-plain")
    finally:
        router.unlink()
    m = re.fullmatch(r"exclude\+execute-silent\(/HIDE\.sh id-plain; /DONE\.sh id-plain (\S+) >/dev/null 2>&1\)", out)
    assert m, out
    assert base64.b64decode(m.group(1)).decode() == CONTENT
    assert not re.search(r"&\)\s*$", out), "done.sh must stay synchronous (2026-08-03 data-loss note)"


def test_value_prompt_task_still_gets_a_tty_and_is_excluded(tmp_path):
    router = _generate_router(tmp_path, _cache(tmp_path))
    try:
        out = _run(router, "id-prompt")
    finally:
        router.unlink()
    assert re.fullmatch(r"exclude\+execute\(/DONE\.sh id-prompt \S+\)", out), out


def test_block_picker_row_is_not_excluded(tmp_path):
    router = _generate_router(tmp_path, _cache(tmp_path))
    try:
        out = _run(router, "BLOCK:戌")
    finally:
        router.unlink()
    assert out.startswith("execute-silent("), out
    assert "exclude" not in out


def test_base64_content_survives_shell_and_action_metachars(tmp_path):
    """Content with quotes, parens, $ and ; must not break the fzf action
    string -- that is the whole reason for base64 over passing raw text."""
    nasty = "it's (weird) [10] $x; y) {20}"
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"today": [{"id": "id-nasty", "content": nasty}]}))
    router = _generate_router(tmp_path, p)
    try:
        out = _run(router, "id-nasty")
    finally:
        router.unlink()
    m = re.fullmatch(r"exclude\+execute-silent\(/HIDE\.sh id-nasty; /DONE\.sh id-nasty ([A-Za-z0-9+/=]+) >/dev/null 2>&1\)", out)
    assert m, out
    assert base64.b64decode(m.group(1)).decode() == nasty


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
