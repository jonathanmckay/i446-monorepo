#!/usr/bin/env python3
"""Regression: dtd has three ctrl-t views — default, by-project, by-time — and
neither project nor time view prints the project name (color encodes the domain).

Change (2026-07-14): drop the project-name prefix in project view; add a 'time'
view that sorts by the (N) estimate ascending (pick off short tasks first);
ctrl-t cycles default -> project -> time -> default.
"""
import re
from pathlib import Path

DTD = Path(__file__).resolve().parent / "dtd.sh"
SRC = DTD.read_text()


def _block(anchor: str, eof: str) -> str:
    m = re.search(r"cat > \"\$%s\" << '%s'\n(.*?)\n%s" % (anchor, eof, eof), SRC, re.S)
    assert m, f"{anchor} block not found"
    return m.group(1)


def test_time_view_sorts_by_estimate_ascending():
    gen = _block("DTD_LIST", "LISTEOF")
    assert "def time_of(t):" in gen
    assert r"re.search(r'\((\d+)\)'" in gen, "time_of must parse the (N) estimate"
    assert "elif view == 'time':" in gen
    assert "unique.sort(key=lambda t: (time_of(t), prank(t.get('priority')))" in gen


def test_no_project_name_prefix_in_any_view():
    gen = _block("DTD_LIST", "LISTEOF")
    # the old project-view name prefix is gone
    assert "dom_tag = _dd" not in gen
    assert "if view == 'project':\n        _dd = domain_of(t)" not in gen


def test_ctrl_t_cycles_three_views():
    tog = _block("DTD_VIEWTOGGLE", "VTEOF")
    assert "views=(default project time)" in tog
    assert "time " in tog and "short first" in tog, "the time view needs a header label"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
