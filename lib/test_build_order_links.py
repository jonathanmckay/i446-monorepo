"""Tests for build_order_links: daily build-order archives link to the prior day."""
import datetime
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bol", Path(__file__).parent / "build_order_links.py")
bol = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bol)


def test_link_inserted_after_frontmatter():
    t = "---\ntitle: x\n---\n\n**1₲**\n## 0₲\n"
    out = bol.with_prev_day_link(t, datetime.date(2026, 6, 29))
    assert "◀ Previous: [[2026.06.28-build-order|2026.06.28]]" in out
    # link comes after the closing frontmatter fence, before the body heading
    assert out.index("Previous") > out.index("---", 3)
    assert out.index("Previous") < out.index("## 0₲")


def test_idempotent():
    t = "---\na\n---\nbody\n"
    once = bol.with_prev_day_link(t, datetime.date(2026, 6, 29))
    assert bol.with_prev_day_link(once, datetime.date(2026, 6, 29)) == once


def test_month_and_year_rollover():
    assert "[[2026.05.31-build-order" in bol.prev_day_link_line(datetime.date(2026, 6, 1))
    assert "[[2025.12.31-build-order" in bol.prev_day_link_line(datetime.date(2026, 1, 1))


def test_no_frontmatter_prepends():
    out = bol.with_prev_day_link("## 0₲\n", datetime.date(2026, 6, 29))
    assert out.startswith("◀ Previous:")
