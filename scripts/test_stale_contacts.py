"""Regression tests for stale-contacts.py — overdue detection, the 😈 marker,
and the outreach_task override.

Context: parent calls (call mom/dad) were moved off ad-hoc recurring/defer tasks
onto the cadence system. Auto-generated tasks must carry 😈 so JM can tell them
from hand-made ones, and parents use a custom task body via `outreach_task`.
"""

import importlib.util
import textwrap
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sc", Path(__file__).resolve().parent / "stale-contacts.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _write(d, name, cadence, last_contact, title=None, outreach=None):
    fm = [f'title: "{title or name}"', f"cadence: {cadence}", f"last_contact: {last_contact}"]
    if outreach:
        fm.append(f'outreach_task: "{outreach}"')
    (d / f"{name}.md").write_text("---\n" + "\n".join(fm) + "\n---\n\nbody\n")


def test_overdue_uses_cadence_threshold(tmp_path):
    _write(tmp_path, "stale-d359", "weekly", "2026-05-25")     # 20d ago > 10 → overdue
    _write(tmp_path, "fresh-d359", "weekly", "2026-06-12")     # 2d ago ≤ 10 → not
    res = {c["slug"]: c for c in sc.overdue_contacts(date(2026, 6, 14), tmp_path)}
    assert "stale" in res and "fresh" not in res


def test_threshold_is_strict(tmp_path):
    # exactly at threshold (10 days for weekly) is NOT overdue; 11 is
    _write(tmp_path, "at-d359", "weekly", "2026-06-04")   # exactly 10d
    _write(tmp_path, "over-d359", "weekly", "2026-06-03")  # 11d
    slugs = {c["slug"] for c in sc.overdue_contacts(date(2026, 6, 14), tmp_path)}
    assert slugs == {"over"}


def test_future_and_unknown_cadence_skipped(tmp_path):
    _write(tmp_path, "future-d359", "weekly", "2026-12-01")     # future → skip
    _write(tmp_path, "weird-d359", "whenever-ish", "2026-01-01")  # unknown → not overdue...
    overdue, problems = sc.scan(date(2026, 6, 14), tmp_path)
    assert overdue == []
    # ...but reported, not silently dropped
    assert [(p["file"], p["issue"]) for p in problems] == [("weird-d359.md", "unknown cadence")]


def test_skip_files_ignored(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("---\ncadence: weekly\nlast_contact: 2026-01-01\n---\n")
    assert sc.overdue_contacts(date(2026, 6, 14), tmp_path) == []


def test_default_task_content_is_auto_marked():
    c = {"name": "Stuart Bowers", "cadence": "monthly",
         "last_contact": "2026-02-15", "outreach_task": None}
    body = sc.task_content(c)
    assert body.startswith("😈 ")
    assert "Reach out to Stuart Bowers" in body
    assert "overdue monthly" in body


def test_default_body_carries_default_points():
    # The default "Reach out to <name>" body defaults to [10] pts (2026-07-13).
    c = {"name": "Stuart Bowers", "cadence": "monthly",
         "last_contact": "2026-02-15", "outreach_task": None}
    body = sc.task_content(c)
    assert body.rstrip().endswith(f"[{sc.DEFAULT_POINTS}]")
    assert sc.DEFAULT_POINTS == 10


def test_custom_override_not_given_extra_points():
    # A custom outreach_task sets its own points — no default [10] appended.
    c = {"name": "Carol Bryan", "cadence": "weekly",
         "last_contact": "2026-06-11", "outreach_task": "call mom (20) [20]"}
    assert sc.task_content(c) == "😈 call mom (20) [20]"  # unchanged, single [20]


def test_outreach_task_override_wins_and_is_marked():
    c = {"name": "Carol Bryan", "cadence": "weekly",
         "last_contact": "2026-06-11", "outreach_task": "call mom (20) [20]"}
    assert sc.task_content(c) == "😈 call mom (20) [20]"


def test_slug_strips_d359_suffix():
    assert sc.slug_of(Path("mark-mckay-d359.md")) == "mark-mckay"
    assert sc.slug_of(Path("carol-bryan-d359.md")) == "carol-bryan"


# ---- v175: cadence parser, subfolders, missing last_contact ----

import pytest


@pytest.mark.parametrize("raw,days", [
    ("weekly", 10), ("monthly", 38), ("quarterly", 100), ("semi-annual", 200),
    ("annual", 400), ("yearly", 400), ("Yearly", 400), ("biennial", 800),
    ("6 months", 200), ("six months", 200), ("fortnightly", 17),
    ("every-5-days", 8), ("every 5 days", 8), ("3 weeks", 24), ("every 2 months", 66),
    ("monthly        # weekly / monthly / quarterly", 38), ('"quarterly"', 100),
])
def test_cadence_threshold_forms(raw, days):
    assert sc.cadence_threshold(raw) == days


@pytest.mark.parametrize("raw", ["none", "as-needed", "ad-hoc", "", None, "None"])
def test_no_cadence_values_return_none(raw):
    assert sc.cadence_threshold(raw) is None


def test_unknown_cadence_raises():
    with pytest.raises(ValueError):
        sc.cadence_threshold("when the moon is full")


def test_yearly_family_doc_is_evaluated(tmp_path):
    _write(tmp_path, "kelly-mckay-d359", "yearly", "2025-01-01")
    assert [c["slug"] for c in sc.overdue_contacts(date(2026, 6, 14), tmp_path)] == ["kelly-mckay"]


def test_every_5_days_and_6_months(tmp_path):
    _write(tmp_path, "ian-d359", "every-5-days", "2026-06-05")      # 9d > 8
    _write(tmp_path, "jenn-d359", "6 months", "2025-11-01")         # 225d > 200
    _write(tmp_path, "arman-d359", "biennial", "2025-01-01")        # 529d ≤ 800
    slugs = {c["slug"] for c in sc.overdue_contacts(date(2026, 6, 14), tmp_path)}
    assert slugs == {"ian", "jenn"}


def test_subfolder_folder_note_is_scanned(tmp_path):
    # LX's doc lives at d359/louisa-xu-d359/louisa-xu-d359.md; the old glob missed it.
    sub = tmp_path / "louisa-xu-d359"
    sub.mkdir()
    _write(sub, "louisa-xu-d359", "weekly", "2026-05-01", title="Louisa Xu")
    # a sibling meeting note with cadence-like frontmatter is NOT a person doc
    _write(sub, "2026-05-01-dinner", "weekly", "2026-01-01")
    res = sc.overdue_contacts(date(2026, 6, 14), tmp_path)
    assert [(c["slug"], c["name"], c["file"]) for c in res] == [
        ("louisa-xu", "Louisa Xu", "louisa-xu-d359/louisa-xu-d359.md")]


def test_missing_last_contact_is_reported(tmp_path):
    (tmp_path / "hadi-d359.md").write_text("---\ntitle: Hadi\ncadence: quarterly\n---\n")
    _write(tmp_path, "jay-d359", "quarterly", "null")
    _write(tmp_path, "bad-d359", "quarterly", "last spring")
    _write(tmp_path, "adhoc-d359", "ad-hoc", "null")   # no cadence → not a problem
    overdue, problems = sc.scan(date(2026, 6, 14), tmp_path)
    assert overdue == []
    assert {(p["file"], p["issue"]) for p in problems} == {
        ("hadi-d359.md", "no last_contact"),
        ("jay-d359.md", "no last_contact"),
        ("bad-d359.md", "bad last_contact"),
    }
