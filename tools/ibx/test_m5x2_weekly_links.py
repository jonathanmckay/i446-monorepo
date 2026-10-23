#!/usr/bin/env python3
"""/ibxm5x2 (2026-10-23): weekly-update links land in their own f695 section,
newest week first, never twice, and never inside `## Weekly updates` (the
dash's i9 parser reads that section)."""
import datetime as dt
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("wl", Path(__file__).parent / "m5x2_weekly_links.py")
wl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wl)

DOC = """---
title: x
---
## Weekly updates

### Week of 2026.09.13
#### Bei Lu
stuff

## Notes
tail
"""


def _u(thread, who, d):
    return {"thread": thread, "who": who, "topic": "T", "subject": f"{who} update",
            "date": dt.date.fromisoformat(d), "sender": "x@m5c7.com"}


def test_links_go_in_their_own_section_newest_first_and_idempotent():
    ups = [_u("t1", "LX", "2026-10-07"), _u("t2", "LX", "2026-10-21"), _u("t3", "Andie", "2026-10-19")]
    out, added = wl.insert_links(DOC, ups)
    assert len(added) == 3
    i9 = out[out.index("## Weekly updates"):out.index("## m5x2 weekly updates")]
    assert "t1" not in i9                                  # i9 section untouched
    assert out.index("## m5x2 weekly updates") < out.index("## Notes")
    sec = out[out.index("## m5x2 weekly updates"):out.index("## Notes")]
    assert sec.index("Week of 2026.10.18") < sec.index("Week of 2026.10.04")
    assert "#all/t2" in sec and "#all/t3" in sec
    again, added2 = wl.insert_links(out, ups)
    assert added2 == [] and again == out


def test_new_link_joins_an_existing_week():
    out, _ = wl.insert_links(DOC, [_u("t2", "LX", "2026-10-21")])
    out, _ = wl.insert_links(out, [_u("t9", "Ian", "2026-10-22")])
    assert out.count("### Week of 2026.10.18") == 1
    wk = out[out.index("### Week of 2026.10.18"):]
    assert wk.index("#all/t2") < wk.index("#all/t9")


def test_matches_only_weekly_updates():
    es = [{"sender": "lx@m5c7.com", "subject": "m5x2 Weekly Update", "thread": "a", "date": dt.date(2026, 10, 21)},
          {"sender": "lx@m5c7.com", "subject": "lunch?", "thread": "b", "date": dt.date(2026, 10, 21)},
          {"sender": "ian@m5c7.com", "subject": "Weekly Update delay", "thread": "c", "date": dt.date(2026, 10, 21)}]
    assert [u["thread"] for u in wl.match(es)] == ["a"]
