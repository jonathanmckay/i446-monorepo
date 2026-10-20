"""Regression tests for 0g skill deduplication (SKILL.md)."""
import importlib.util
import sys
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"
SYNC_PY = Path.home() / "i446-monorepo" / "scripts" / "0g-sync.py"


def _load_sync_module():
    """Import 0g-sync.py despite its hyphen-and-leading-digit filename."""
    spec = importlib.util.spec_from_file_location("og_sync", SYNC_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["og_sync"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_todoist_add_step_requires_dedup_for_with_args():
    """
    Bug: /0g created duplicate Todoist tasks when run twice with the same goals,
    or when run with args then without args. e.g. two 'get through qz12' tasks.

    Fix: Step 3 (with args) must fetch existing tasks and skip duplicates.
    """
    text = SKILL_MD.read_text()
    # Find the "with arguments" add-to-todoist section
    with_args_section = text[text.index("With arguments"):text.index("Without arguments")]
    assert "dedup" in with_args_section.lower(), (
        "Step 3 (with args) must include dedup check before creating Todoist tasks"
    )
    assert "find-tasks" in with_args_section, (
        "Step 3 (with args) must use find-tasks to check for existing tasks"
    )
    assert "skip" in with_args_section.lower(), (
        "Step 3 (with args) must skip creation of duplicate tasks"
    )


def test_todoist_add_step_requires_dedup_for_sync():
    """
    Bug: /0g (no args, sync mode) created duplicate Todoist tasks when goals
    already existed from a previous /0g run.

    Fix: Step 2 (without args) must fetch existing tasks and skip duplicates.
    """
    text = SKILL_MD.read_text()
    # Find the "without arguments" section
    sync_section = text[text.index("Without arguments"):]
    assert "dedup" in sync_section.lower(), (
        "Step 2 (sync mode) must include dedup check before creating Todoist tasks"
    )
    assert "find-tasks" in sync_section, (
        "Step 2 (sync mode) must use find-tasks to check for existing tasks"
    )
    assert "skip" in sync_section.lower(), (
        "Step 2 (sync mode) must skip creation of duplicate tasks"
    )


def test_skill_md_no_longer_requires_critical_path_label():
    """
    Bug: 0g tasks were being labeled with the deprecated #关键径路 label.
    The label scheme in /0g now uses #0g + optional @code, no 关键径路.
    """
    text = SKILL_MD.read_text()
    # The deprecated label must not appear in label specs
    bad_specs = ['"#关键径路"', '"#关键径路"']
    for bad in bad_specs:
        assert bad not in text, (
            f"SKILL.md still references deprecated label spec {bad!r} — "
            "0g tasks should no longer be tagged with 关键径路."
        )


def test_sync_detect_labels_omits_critical_path():
    """
    Bug: 0g-sync.py auto-tagged every new Todoist task with #关键径路, which
    is deprecated. Verify the default label set is now [#0g, ...domain].
    """
    sync = _load_sync_module()
    labels = sync.detect_labels("ship the foo")
    assert "#关键径路" not in labels, (
        f"detect_labels still emits 关键径路: {labels}"
    )
    assert "#0g" in labels, f"detect_labels should always include #0g; got {labels}"


def test_sync_normalize_strips_annotation_tokens():
    """
    Bug: adopt-existing logic missed matches when Todoist content carried
    `{N}`/`<N>`/`(N)` annotations that the MD task.key did not. Result: a
    duplicate task was created on next sync. The normalizer strips these so
    'Foo {30}' adopts a 'Foo' MD task.
    """
    sync = _load_sync_module()
    cases = [
        ("Finish fund I m5x2 P&L {30}", "finish fund i m5x2 p&l"),
        ("weights exercise <20>", "weights exercise"),
        ("Read book (45)", "read book"),
        ("Plan trip @i9", "plan trip"),
        ("call mom", "call mom"),
    ]
    for raw, expected in cases:
        assert sync.normalize_content(raw) == expected, (
            f"normalize_content({raw!r}) = {sync.normalize_content(raw)!r}, "
            f"expected {expected!r}"
        )
