"""Regression tests for build-order-daemon.py's 0分 block-lock.

2026-06-12 bug: the 06:00 fire locked 卯 (column G) at -46 because D's live
0n penalty rollups sit negative until morning habits are logged. The frozen
negative then inflated every later block's residual (=D-SUM(locked)) by the
same amount — janus showed 巳 at 173分 of a 127分 day, disagreeing with the
points the user actually had. Locks must clamp negative residuals to 0 so the
transient stays in the unlocked tail and self-corrects.
"""
import ast
import importlib.util
import pathlib
import sys

SRC = pathlib.Path(__file__).parent / "build-order-daemon.py"


def _load():
    spec = importlib.util.spec_from_file_location("bod_lock", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bod_lock"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_is_parseable():
    ast.parse(SRC.read_text())


def test_lock_clamps_negative_residual_before_writing():
    """The lock must clamp v to 0 when negative, after reading the formula
    value (via the excel-http client) and before writing the literal back."""
    import inspect
    mod = _load()
    src = inspect.getsource(mod.neon_lock_cell)
    read_i = src.index("neon_excel.read(")
    clamp_i = src.index("if v < 0")
    write_i = src.index("neon_excel.write(")
    assert read_i < clamp_i < write_i, (
        "negative-residual clamp must sit between the read and the write"
    )
    assert "v = 0" in src


def test_lock_columns_follow_block_convention():
    """LOCK_AT_FIRE_HOUR must lock the column of the block that just ended,
    using the 卯=04-06 convention shared with janus and the 0分 sheet writer.
    A drift here silently shifts every block's points by one column."""
    mod = _load()
    for i, (branch, lo, hi) in enumerate(mod.BRANCH_HOURS):
        fire_hour = hi + 1
        assert mod.HOUR_TO_BRANCH_BLOCK[fire_hour] == branch
        assert mod.LOCK_AT_FIRE_HOUR[fire_hour] == chr(ord("G") + i)


def test_block_convention_consistent_across_tools():
    """One convention, everywhere: 2026-06-12 found lib/neon/blocks.py, the
    /1-1n heatmap, and -1g-check.py all one block off (卯=06-08) from what
    the sheet writer actually records (卯=04-06), so every consumer of 0分's
    G:O columns mislabelled blocks by two hours."""
    daemon = _load()
    daemon_blocks = [(lo, b) for b, lo, hi in daemon.BRANCH_HOURS]

    sys.path.insert(0, str(pathlib.Path.home() / "i446-monorepo" / "lib"))
    from neon import blocks as neon_blocks
    assert neon_blocks.BLOCKS[:9] == daemon_blocks

    heatmap_src = (pathlib.Path.home() / "i446-monorepo" / "skills"
                   / "claude-skills" / "1-1n" / "make_heatmap.py").read_text()
    hm_ns: dict = {}
    for line in heatmap_src.splitlines():
        if line.startswith("BLOCKS ="):
            exec(line, hm_ns)
            break
    assert hm_ns.get("BLOCKS") == daemon_blocks

    spec = importlib.util.spec_from_file_location(
        "_1g_check_conv", pathlib.Path(__file__).parent / "-1g-check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert [(lo, b) for lo, hi, b, _ in check.BLOCKS] == daemon_blocks


def test_run_lock_and_mark_uses_travel_aware_now():
    """2026-08-26 bug: run_lock_and_mark computed 'now' via raw
    dt.datetime.now() (ix's own fixed-PT system clock) instead of
    daytime.local_now(), unlike every other 'now'/'today' read in this
    file. During international travel this pins the -1neon ritual-card
    create/retire cadence (and block scoring) to ix's home timezone even
    once an explicit /travel override is active, so a traveling user's
    current 地支 block never gets a fresh set of ritual cards."""
    import inspect
    mod = _load()
    src = inspect.getsource(mod.run_lock_and_mark)
    assert "daytime.local_now()" in src, (
        "run_lock_and_mark must resolve 'now' via daytime.local_now() so it "
        "honors an active /travel override, like the rest of this module"
    )
    assert "dt.datetime.now()" not in src


def test_neon_blocks_build_order_path_matches_daemon():
    """lib/neon/blocks.py must point at the live build-order.md, not the old
    missing '-1₦ , 0₦ - Neon {Build Order}.md' — flip_checkbox/parse_block_goals
    were silently reading a nonexistent file (2026-06-14)."""
    daemon = _load()
    sys.path.insert(0, str(pathlib.Path.home() / "i446-monorepo" / "lib"))
    from neon import blocks as neon_blocks
    assert neon_blocks.BUILD_ORDER.name == "build-order.md"
    assert neon_blocks.BUILD_ORDER == daemon.BUILD_ORDER
