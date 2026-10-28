#!/usr/bin/env python3
"""f695 grades live in the doc (2026-10-28): m5x2 link lines end with
` — grade: X`, i9 entries carry **JM (X):**; set/clear round-trips."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("grades", Path(__file__).parent / "grades.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)

DOC = """---
title: x
---
## Weekly updates

### Week of 2026.10.18

#### Bei Lu

stuff

**JM (C-):** go higher level.

#### Data Infra (late)

more stuff

## m5x2 weekly updates

### Week of 2026.10.25
- [Andie · Leasing](https://mail.google.com/mail/u/0/#all/1a17b08a) — Leasing Weekly Update (10/26)
- [Stef · TRS](https://mail.google.com/mail/u/0/#all/1a17a955) — TRS Recap (10/26) — grade: B

## Notes
tail
"""


def test_read_grades_both_sections():
    r = g.read_grades(DOC)
    assert r["m5x2"] == {"1a17a955": "B"}
    assert r["i9"] == {"2026-10-18|Bei Lu": "C-"}


def test_m5x2_set_replace_clear():
    t = g.set_m5x2(DOC, "1a17b08a", "a")
    assert "Leasing Weekly Update (10/26) — grade: A\n" in t
    t = g.set_m5x2(t, "1a17a955", "D")
    assert "TRS Recap (10/26) — grade: D\n" in t and "grade: B" not in t
    t = g.set_m5x2(t, "1a17a955", "")
    assert "TRS Recap (10/26)\n" in t
    assert g.read_grades(t)["m5x2"] == {"1a17b08a": "A"}


def test_m5x2_unknown_thread_and_bad_grade():
    with pytest.raises(KeyError):
        g.set_m5x2(DOC, "ffff", "A")
    with pytest.raises(ValueError):
        g.set_m5x2(DOC, "1a17b08a", "E+x")


def test_i9_replace_keeps_comment_and_clear():
    t = g.set_i9(DOC, "2026-10-18", "Bei Lu", "B")
    assert "**JM (B):** go higher level." in t
    t = g.set_i9(t, "2026-10-18", "Bei Lu", "")
    assert "**JM:** go higher level." in t
    assert g.read_grades(t)["i9"] == {}


def test_i9_adds_jm_line_when_missing_and_late_heading_matches():
    t = g.set_i9(DOC, "2026-10-18", "Data Infra", "A")
    entry = t[t.index("#### Data Infra"):t.index("## m5x2")]
    assert "more stuff\n\n**JM (A):**\n" in entry
    assert g.read_grades(t)["i9"]["2026-10-18|Data Infra"] == "A"
    assert t.count("## m5x2 weekly updates") == 1


def test_i9_unknown_entry():
    with pytest.raises(KeyError):
        g.set_i9(DOC, "2026-10-11", "Bei Lu", "A")
