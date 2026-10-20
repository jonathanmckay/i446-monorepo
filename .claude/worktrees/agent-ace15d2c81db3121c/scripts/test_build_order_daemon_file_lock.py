"""Regression (2026-08-02): build-order-daemon.py's build-order.md
read-modify-write functions had NO locking at all, unlike the (by-then
twice-fixed) ritual-stamping path in did-fast.py — the third of three
independent unlocked call sites found for the same lost-update race. Every
writer here must now hold neon_blocks.build_order_lock() for its full
read-modify-write, not just the write."""
import pathlib

SRC = pathlib.Path(__file__).parent / "build-order-daemon.py"

LOCKED_FUNCTIONS = [
    "_write_block_marker",
    "_strip_unearned_markers",
    "run_link_meetings",
    "annotate_block_fired",
    "defer_unchecked_neg1",
]


def _function_source(name: str) -> str:
    src = SRC.read_text()
    start = src.index(f"def {name}(")
    # Next top-level `def ` after this one marks the end of this function.
    next_def = src.index("\ndef ", start + 1)
    return src[start:next_def]


def test_neon_blocks_imported():
    src = SRC.read_text()
    assert "import neon_blocks as nb" in src


def test_every_known_writer_holds_the_shared_lock():
    for name in LOCKED_FUNCTIONS:
        body = _function_source(name)
        assert "nb.build_order_lock()" in body, (
            f"{name} does a build-order.md read-modify-write with no lock"
        )


def test_write_block_marker_locks_around_its_own_has_marker_check():
    """The check-then-write (including the nested _has_block_marker call)
    must be atomic as a whole -- locking only around the final write leaves
    the check itself racy."""
    body = _function_source("_write_block_marker")
    lock_i = body.index("nb.build_order_lock()")
    check_i = body.index("_has_block_marker(")
    write_i = body.index("BUILD_ORDER.write_text(")
    assert lock_i < check_i < write_i, (
        "lock must be acquired before the _has_block_marker check, not just before the write"
    )


def test_strip_unearned_markers_locks_before_its_read():
    body = _function_source("_strip_unearned_markers")
    lock_i = body.index("nb.build_order_lock()")
    read_i = body.index("BUILD_ORDER.read_text(")
    write_i = body.index("BUILD_ORDER.write_text(")
    assert lock_i < read_i < write_i


def test_run_link_meetings_locks_around_load_and_save():
    body = _function_source("run_link_meetings")
    lock_i = body.index("nb.build_order_lock()")
    load_i = body.index("load_lines()")
    save_i = body.index("save_lines(")
    assert lock_i < load_i < save_i


def test_annotate_block_fired_locks_around_load_and_save():
    body = _function_source("annotate_block_fired")
    lock_i = body.index("nb.build_order_lock()")
    load_i = body.index("load_lines()")
    save_i = body.index("save_lines(")
    assert lock_i < load_i < save_i


def test_defer_unchecked_neg1_locks_around_load_and_save():
    body = _function_source("defer_unchecked_neg1")
    lock_i = body.index("nb.build_order_lock()")
    load_i = body.index("load_lines()")
    save_i = body.index("save_lines(")
    assert lock_i < load_i < save_i


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
