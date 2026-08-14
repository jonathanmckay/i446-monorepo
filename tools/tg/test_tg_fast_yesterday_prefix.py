"""Regression (2026-08-14): "/tg yesterday run @hcbp #其他人 2005-2045,
2045-2058 1st hci @hci" had no handling anywhere for a leading 'yesterday'
token. tg-fast.py only ever recognized `--date YYYY-MM-DD` (janus's
past-day-view mechanism); a bare 'yesterday' word just rode along as literal
text into the first entry's description via resolve(), and BOTH entries (the
whole comma-separated batch) silently landed on TODAY's date instead of
yesterday's -- confirmed live: `toggl_date` showed both entries created on
2026-08-14 despite the user asking for yesterday (2026-08-13).

Fix: main() now recognizes a leading 'yesterday' the same way it already
recognizes '--date YYYY-MM-DD' -- strips it and sets the module-global
_DATE_OVERRIDE BEFORE the comma-split, so it covers every entry in the batch,
not just whichever one it happened to sit in.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("tg_fast_yesterday", HERE / "tg-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_fast_yesterday"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeTogglApi:
    def trim_range(self, start_dt, end_dt, exclude_ids=None):
        return []


def _yesterday_iso():
    return (datetime.now(TZ).date() - timedelta(days=1)).isoformat()


def test_yesterday_prefix_sets_date_override_and_is_stripped():
    mod = _load()
    mod._DATE_OVERRIDE = None
    fake = _FakeTogglApi()
    created = []

    def fake_cli(*args):
        created.append(args)
        return "Created: x [id:9001]"

    import unittest.mock as um
    with um.patch.object(mod, "_toggl_api", lambda: fake), \
         um.patch.object(mod, "_run_cli", fake_cli), \
         um.patch.object(sys, "argv",
                          ["tg-fast.py",
                           "yesterday run @hcbp #其他人 2005-2045， 2045-2058 1st hci @hci"]):
        mod.main()

    assert mod._DATE_OVERRIDE is not None
    assert mod._DATE_OVERRIDE.isoformat() == _yesterday_iso(), (
        "'yesterday' must resolve to (today - 1 day) in America/Los_Angeles")

    assert len(created) == 2, f"both batch entries must create, got: {created!r}"
    entry1, entry2 = created

    assert entry1[0] == "create"
    assert "yesterday" not in entry1[1].lower(), (
        "'yesterday' must be stripped, not leak into the first entry's "
        f"description: {entry1[1]!r}")
    assert "run" in entry1[1].lower()
    assert "hcbp" in entry1, f"project must resolve to hcbp: {entry1!r}"
    assert "--date" in entry1 and _yesterday_iso() in entry1, (
        f"first entry must carry --date <yesterday>: {entry1!r}")

    assert entry2[0] == "create"
    assert "hci" in entry2, f"second entry's project must resolve to hci: {entry2!r}"
    assert "--date" in entry2 and _yesterday_iso() in entry2, (
        "second entry must ALSO carry --date <yesterday> -- the whole batch "
        f"must share one date, not just the entry 'yesterday' sat in: {entry2!r}")


def test_explicit_date_flag_still_wins_over_yesterday_word():
    """If the user somehow gives both, the explicit --date must not be
    silently overridden by a coincidental leading 'yesterday' check."""
    mod = _load()
    mod._DATE_OVERRIDE = None
    fake = _FakeTogglApi()
    created = []

    import unittest.mock as um
    with um.patch.object(mod, "_toggl_api", lambda: fake), \
         um.patch.object(mod, "_run_cli",
                          lambda *a: (created.append(a), "Created: x [id:1]")[1]), \
         um.patch.object(sys, "argv",
                          ["tg-fast.py", "work 9-10 --date 2026-01-05"]):
        mod.main()

    assert mod._DATE_OVERRIDE.isoformat() == "2026-01-05"


def test_yesterday_word_inside_a_larger_word_is_not_stripped():
    """Only a standalone leading 'yesterday' token counts -- must not match
    e.g. a description that merely starts with 'yesterdays' or similar."""
    mod = _load()
    mod._DATE_OVERRIDE = None
    fake = _FakeTogglApi()
    created = []

    import unittest.mock as um
    with um.patch.object(mod, "_toggl_api", lambda: fake), \
         um.patch.object(mod, "_run_cli",
                          lambda *a: (created.append(a), "Created: x [id:1]")[1]), \
         um.patch.object(sys, "argv", ["tg-fast.py", "yesterdays plan 9-10"]):
        mod.main()

    assert mod._DATE_OVERRIDE is None, (
        "'yesterdays' is not the word 'yesterday' -- must not set an override")
    assert created and "yesterdays" in created[0][1].lower()


def test_bare_yesterday_word_regex_is_case_insensitive_and_word_bounded():
    import re
    pattern = re.compile(r"(?i)^yesterday\b\s*")
    assert pattern.match("yesterday run 9-10")
    assert pattern.match("Yesterday run 9-10")
    assert pattern.match("YESTERDAY run 9-10")
    # '\b' after 'yesterday' means it must NOT match inside 'yesterdays'
    assert pattern.match("yesterdays run 9-10") is None, \
        "must not match when followed immediately by another word char"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
