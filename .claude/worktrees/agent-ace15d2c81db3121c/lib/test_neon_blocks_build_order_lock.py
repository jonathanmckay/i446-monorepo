"""Tests for neon_blocks.build_order_lock() and flip_goal_checkboxes().

build_order_lock() is the centralized primitive added 2026-08-02 after three
separate incidents of the same unlocked build-order.md read-modify-write
race (each in a different call site: did-fast.py's run_ritual, then its
prayer-marker/checkbox-flip steps, then build-order-daemon.py's marker
writers) — every writer now shares this one lock instead of re-implementing
its own inline fcntl.flock."""
import importlib.util
import multiprocessing
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("neon_blocks_lock", HERE / "neon_blocks.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["neon_blocks_lock"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_lock_path_is_derived_from_given_build_order_not_hardcoded(tmp_path):
    """A caller passing a tmp_path copy must lock THAT file's own .lock
    sibling, not the production BUILD_ORDER's — otherwise tests either
    contend with real production locking or silently skip protection."""
    nb = _load()
    bo = tmp_path / "build-order.md"
    bo.write_text("## -1₲\n\n- 卯\n")
    with nb.build_order_lock(bo):
        assert (tmp_path / "build-order.lock").exists()


def _locked_stamp_worker(build_order_path: str, block: str, emoji: str) -> None:
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("nb_w", str(Path(__file__).resolve().parent / "neon_blocks.py"))
    nb = _ilu.module_from_spec(spec)
    spec.loader.exec_module(nb)
    bo = Path(build_order_path)
    with nb.build_order_lock(bo):
        t = bo.read_text(encoding="utf-8")
        nt, ch = nb.stamp_emoji(t, block, emoji)
        if ch:
            bo.write_text(nt, encoding="utf-8")


def _unlocked_stamp_worker(build_order_path: str, block: str, emoji: str) -> None:
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("nb_w2", str(Path(__file__).resolve().parent / "neon_blocks.py"))
    nb = _ilu.module_from_spec(spec)
    spec.loader.exec_module(nb)
    bo = Path(build_order_path)
    t = bo.read_text(encoding="utf-8")
    nt, ch = nb.stamp_emoji(t, block, emoji)
    if ch:
        bo.write_text(nt, encoding="utf-8")


def test_build_order_lock_prevents_concurrent_stamp_loss(tmp_path):
    build_order = tmp_path / "build-order.md"
    build_order.write_text("## -1₲\n\n- 巳\n    - [ ] goal\n", encoding="utf-8")
    emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
    procs = [multiprocessing.Process(target=_locked_stamp_worker,
                                     args=(str(build_order), "巳", e))
             for e in emojis]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
    final = build_order.read_text(encoding="utf-8")
    header = next(l for l in final.split("\n") if l.startswith("- 巳"))
    missing = [e for e in emojis if e not in header]
    assert not missing, f"lock failed to prevent lost updates: missing {missing} from {header!r}"


def test_without_the_lock_concurrent_stamps_can_still_be_lost(tmp_path):
    """Sanity check the race is real (flaky-by-nature, same rationale as the
    equivalent check in tools/did/test_ritual_single_writer_stamp.py)."""
    losses = 0
    for i in range(5):
        build_order = tmp_path / f"bo-{i}.md"
        build_order.write_text("## -1₲\n\n- 巳\n    - [ ] goal\n", encoding="utf-8")
        emojis = ["☀️", "🎯", "📧", "⏱️", "✅"]
        procs = [multiprocessing.Process(target=_unlocked_stamp_worker,
                                         args=(str(build_order), "巳", e))
                 for e in emojis]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
        final = build_order.read_text(encoding="utf-8")
        header = next(l for l in final.split("\n") if l.startswith("- 巳"))
        if any(e not in header for e in emojis):
            losses += 1
    assert losses > 0, "expected the unlocked path to lose at least one stamp across 5 runs"


def test_flip_goal_checkboxes_flips_matching_unchecked_goal():
    nb = _load()
    text = "## -1₲\n\n- 巳\n    - [ ] finish the report\n    - [ ] call mom\n"
    new_text, changed = nb.flip_goal_checkboxes(text, ["finish the report"])
    assert changed
    assert "- [x] finish the report" in new_text
    assert "- [ ] call mom" in new_text


def test_flip_goal_checkboxes_each_match_consumes_one_line():
    """Two completions that bare-match the SAME goal text must only flip one
    line each pass -- once flipped to [x] it can't match a later entry in
    the same call, mirroring the original inline loop's behavior."""
    nb = _load()
    text = "## -1₲\n\n- 巳\n    - [ ] standup\n    - [ ] standup\n"
    new_text, changed = nb.flip_goal_checkboxes(text, ["standup", "standup"])
    assert changed
    assert new_text.count("- [x] standup") == 2


def test_flip_goal_checkboxes_no_match_is_a_noop():
    nb = _load()
    text = "## -1₲\n\n- 巳\n    - [ ] finish the report\n"
    new_text, changed = nb.flip_goal_checkboxes(text, ["something unrelated"])
    assert not changed
    assert new_text == text


def test_flip_goal_checkboxes_no_neg1_section_is_a_noop():
    nb = _load()
    text = "no section here\n"
    new_text, changed = nb.flip_goal_checkboxes(text, ["anything"])
    assert not changed
    assert new_text == text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
