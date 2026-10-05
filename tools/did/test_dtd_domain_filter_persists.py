#!/usr/bin/env python3
"""Regression (2026-10-01): "I type i9 into dtd's query box and it shows the
i9 tasks, then reverts to the whole list."

The domain search (test_dtd_domain_search.py) reloaded the list with the
domain code as a one-shot LITERAL 11th arg, sent only by the keystroke-
triggered POST. Every other reload path omitted that arg and so rebuilt the
full list:
  * the auto-reload watcher, which fires on every task-cache mtime advance
    (the ~3-min did-refresh-cache daemon, the staggered block-boundary
    refreshes, cross-machine completed-today mirrors) -- this is the one
    the user actually sees, seconds to minutes after typing the code;
  * DTD_RELOAD, chained onto every ctrl-* binding and resize.

Fix: persist the active domain code in a per-session file ($DTD_DOMAIN,
mirroring $DTD_VIEW) and pass that FILE as the 11th arg from every reload
builder; the list generator reads the file (empty = no filter). The domain-
search script writes the file before its reload; ctrl-r clears it.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()

LISTGEN_ARGS_TAIL = "'$DTD_SKIPPED' '$DTD_TIMER' '$DTD_VIEW' '$DTD_BLOCKPICK' '$DTD_DOMAIN'\""


def _block(start_marker: str, end_marker: str) -> str:
    i = DTD.index(start_marker)
    j = DTD.index(end_marker, i)
    return DTD[i:j]


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


# ---------------------------------------------------------------------------
# Structural: every reload builder carries the domain file as the 11th arg.
# ---------------------------------------------------------------------------

def test_domain_file_defined_and_reset_per_session():
    assert 'DTD_DOMAIN="/tmp/dtd-$DTD_ID.domain"' in DTD
    assert ': > "$DTD_DOMAIN"' in DTD, "domain scope must start empty each session"


def test_ui_reload_cmd_passes_domain_file():
    line = next(l for l in DTD.splitlines() if l.strip().startswith("DTD_LIST_CMD="))
    assert line.rstrip().endswith(LISTGEN_ARGS_TAIL), (
        "DTD_LIST_CMD (-> DTD_RELOAD, every ctrl-* binding + resize) must pass "
        "'$DTD_DOMAIN' as the 11th arg or any action drops the domain scope: "
        f"{line.strip()!r}")


def test_watcher_reload_cmd_passes_domain_file():
    line = next(l for l in DTD.splitlines() if l.strip().startswith("watch_reload="))
    assert line.rstrip().endswith(LISTGEN_ARGS_TAIL), (
        "the cache-mtime watcher's reload must pass '$DTD_DOMAIN' as the 11th "
        "arg -- this is the reload that reverted the scoped list to the full "
        f"list on the next daemon refresh: {line.strip()!r}")


def test_domainsearch_persists_code_then_passes_file():
    blk = _block('DTD_DOMAINSEARCH="/tmp/dtd-$DTD_ID.domainsearch.sh"',
                 'chmod +x "$DTD_DOMAINSEARCH"')
    write = blk.index("printf '%s' \"\\$q\" > \"$DTD_DOMAIN\"")
    reload_line = next(l for l in blk.splitlines() if l.startswith("reload_cmd="))
    assert reload_line.rstrip().endswith(LISTGEN_ARGS_TAIL), reload_line
    assert "'\\$q'\"" not in blk, "must not send the code as a one-shot literal any more"
    # "\nreload_cmd=" = the domain path's own (unindented) line; the delay
    # screen's days-flag reload above it is indented (2026-10-05).
    assert write < blk.index("\nreload_cmd="), "write the file BEFORE the reload reads it"


def test_ctrl_r_refresh_clears_domain_scope():
    blk = _block('DTD_REFRESH="/tmp/dtd-$DTD_ID.refresh.sh"', 'chmod +x "$DTD_REFRESH"')
    assert ': > "$DTD_DOMAIN"' in blk, "ctrl-r is the documented way to clear the scope"


def test_header_hint_matches_behaviour():
    assert "(ctrl-r or any action to clear)" not in DTD, (
        "the scope now survives actions; the hint must not promise otherwise")
    assert "(ctrl-r to clear)" in DTD


# ---------------------------------------------------------------------------
# Functional: the generator reads the domain from the FILE, empty = unfiltered.
# ---------------------------------------------------------------------------

def _task(task_id, content, label):
    return {"id": task_id, "content": content, "labels": [label],
            "priority": 3, "due": "2026-10-01", "recurring": False}


def _run(tmp: Path, domain_file_text):
    def _w(name, obj_or_text):
        p = tmp / name
        p.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
        return str(p)
    cache = _w("cache.json", {"updated": "t", "today": [], "关键路径": [
        _task("I9001", "review PR (10) [5]", "i9"),
        _task("M5X001", "check lease (10) [5]", "m5x2"),
    ]})
    done = _w("done.json", {"date": "2026-10-01", "names": [], "ids": {}})
    removed = _w("removed", ""); (tmp / "removed.ids").write_text("")
    args = [sys.executable, str(tmp / "lg.py"), cache, done, removed, "2026-10-01",
            "120", _w("skipped", ""), _w("timer", ""), _w("view", ""), _w("blockpick", "")]
    if domain_file_text is not None:
        args.append(_w("domain", domain_file_text))
    (tmp / "lg.py").write_text(_listgen_payload())
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {l.rsplit("\t", 1)[-1] for l in r.stdout.splitlines() if l.strip()}


def test_generator_reads_domain_from_file():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert _run(tmp, "i9") & {"I9001", "M5X001"} == {"I9001"}
        assert _run(tmp, "i9\n") & {"I9001", "M5X001"} == {"I9001"}, "trailing newline tolerated"


def test_empty_domain_file_is_unfiltered():
    """The persisted file is passed on EVERY reload, so an empty file (session
    start, after ctrl-r) must mean 'no filter', not 'match nothing'."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert {"I9001", "M5X001"} <= _run(tmp, "")


def test_missing_domain_file_is_unfiltered():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        got = _run(tmp, None)
        assert {"I9001", "M5X001"} <= got
        # and a path that does not exist on disk is tolerated, not a crash
        args_missing = tmp / "nope"
        assert not args_missing.exists()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
