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
    _write(tmp_path, "weird-d359", "fortnightly", "2026-01-01")  # unknown cadence → skip
    assert sc.overdue_contacts(date(2026, 6, 14), tmp_path) == []


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
