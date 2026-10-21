import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merge3 import merge3  # noqa: E402


def test_desktop_cleared_phone_appended_keeps_only_the_new_lines():
    """The 2026-10-21 incident: /notes cleared the inbox, a stale phone put
    appended one line. The live file must be the cleared header + that line."""
    base = "hdr\n\nA\nB\nC\n"
    assert merge3(base, "hdr\n", "hdr\n\nA\nB\nC\nD\n") == ("hdr\nD\n", False)


def test_one_sided_changes_apply_cleanly():
    assert merge3("h\na\nb\nc\n", "h\na\nB!\nc\n", "h\na\nb\nc\nd\n") == ("h\na\nB!\nc\nd\n", False)
    assert merge3("h\nA\n", "h\n", "h\nA\n") == ("h\n", False)          # phone unchanged
    assert merge3("h\nA\n", "h\nA\n", "h\nA\nB\n") == ("h\nA\nB\n", False)  # desktop unchanged


def test_both_appended_keeps_both_ix_first():
    assert merge3("hdr\n\nA\n", "hdr\n\nA\nX\n", "hdr\n\nA\nY\n") == ("hdr\n\nA\nX\nY\n", False)


def test_phone_text_extends_its_own_landed_append_without_duplicating():
    # the optimistic append landed (ours has it), the phone then typed more
    assert merge3("h\n", "h\nnote\n", "h\nnote\nmore\n") == ("h\nnote\nmore\n", False)


def test_identical_changes_apply_once():
    assert merge3("h\na\n", "h\na\nz\n", "h\na\nz\n") == ("h\na\nz\n", False)


def test_missing_trailing_newline_is_not_a_change():
    assert merge3("h\na", "h\na", "h\na\nb") == ("h\na\nb\n", False)


def test_real_conflict_keeps_both_texts_between_markers():
    text, conflicted = merge3("h\nx\n", "h\nx1\n", "h\nx2\n")
    assert conflicted
    assert text == "h\n<<<<<<< ix\nx1\n=======\nx2\n>>>>>>> phone\n"


def test_empty_sides():
    assert merge3("hdr\nA\n", "", "hdr\nA\nB\n") == ("B\n", False)
    assert merge3("", "a\n", "b\n") == ("a\nb\n", False)
