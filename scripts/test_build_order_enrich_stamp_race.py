"""Regression (2026-08-01): build-order-enrich read the file BEFORE its slow
Toggl/Todoist gathering and rewrote it at the end with a bare write_text —
any ritual stamp landing in that window was silently erased (午's manually
claimed ⏱️✅ vanished minutes after stamping; same class ate 未's 🎯). The
write must hold the shared build-order.lock and union-merge the CURRENT
on-disk copy's block-header stamps into the rewritten text."""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent / "build-order-enrich.py"


def _write_region():
    src = SRC.read_text()
    i = src.index("def enrich_build_order")
    return src[i:]


def test_final_write_is_lock_guarded_and_merge_aware():
    body = _write_region()
    assert "merge_stamps" in body, "must union current on-disk stamps before writing"
    assert "LOCK_EX" in body, "must hold the shared build-order.lock for the write"
    # The merge + write must happen together: merge_stamps before write_text,
    # both after the flock acquisition.
    lock_i = body.index("LOCK_EX")
    merge_i = body.index("merge_stamps", lock_i)
    write_i = body.index("BUILD_ORDER.write_text", lock_i)
    assert lock_i < merge_i < write_i, \
        "order must be: acquire lock → merge current stamps → write"


def test_no_unguarded_build_order_write_remains():
    """Every BUILD_ORDER.write_text in the enrich body must come after the
    lock acquisition — a second bare write would reintroduce the race."""
    body = _write_region()
    lock_i = body.index("LOCK_EX")
    first_write = body.index("BUILD_ORDER.write_text")
    assert first_write > lock_i, "found a BUILD_ORDER write before the lock"
