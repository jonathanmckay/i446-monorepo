"""Regression (2026-07-19): `did-fast.py --ritual <tag>` printed its result
JSON (including a `p_credit_error` key when the 0分!P write failed, e.g. ix
unreachable over ssh) but always exited 0. Every skill that shells out to
`--ritual` (/-1g, /0g, /卯, /inbound) redirects with `>/dev/null 2>&1`, so a
completely failed point-credit was silently reported to the user as a
successful ritual completion — the exact symptom reported live: "I've done
most of -1n for this block but points aren't in neon" during an ix sshd
outage that made every ix_run() call fail.

The fix: the --ritual CLI handler now exits nonzero when p_credit_error is
present in the result, so a caller checking $ (or a human re-running with
output visible) can tell the ritual didn't fully land.
"""
import ast
from pathlib import Path

DID_FAST = Path(__file__).resolve().parent / "did-fast.py"


def _main_source() -> str:
    src = DID_FAST.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return ast.get_source_segment(src, node)
    raise AssertionError("main() not found in did-fast.py")


def _ritual_branch() -> str:
    src = _main_source()
    i_start = src.index('if sys.argv[1] == "--ritual":')
    # Next top-level branch after --ritual is the bare `argv = sys.argv[1:]`
    # fallthrough for the default (non-flag) completion path.
    i_end = src.index("\n    argv = sys.argv[1:]", i_start)
    return src[i_start:i_end]


def test_ritual_handler_exits_nonzero_on_p_credit_error():
    branch = _ritual_branch()
    assert 'result.get("p_credit_error")' in branch, (
        "the --ritual handler must check for p_credit_error in the result"
    )
    i_check = branch.index('result.get("p_credit_error")')
    tail = branch[i_check:]
    assert "sys.exit(1)" in tail, (
        "a p_credit_error must exit nonzero — otherwise every skill that "
        "shells out with `>/dev/null 2>&1` silently reports success on a "
        "totally failed point-credit (the ix-unreachable bug)"
    )


def test_result_json_is_still_printed_before_exiting():
    # The exit-on-error must not swallow the JSON output itself — a caller
    # that DOES check output (not just $?) still needs to see the error.
    branch = _ritual_branch()
    i_print = branch.index("print(json.dumps(result")
    i_exit = branch.index('result.get("p_credit_error")')
    assert i_print < i_exit, (
        "must print the result JSON before checking/exiting on p_credit_error"
    )


def test_success_path_unchanged_when_no_p_credit_error():
    # Sanity: the branch must not unconditionally exit nonzero — only on error.
    branch = _ritual_branch()
    assert "return" in branch, (
        "the normal (no p_credit_error) path must still return, not always exit"
    )
