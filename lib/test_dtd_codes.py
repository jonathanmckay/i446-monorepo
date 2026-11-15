"""dtd_codes: every dtd task gets a two-letter handle computed from the queue alone."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd_codes as dc  # noqa: E402


def T(tid, content):
    return {"id": tid, "content": content}


TASKS = [
    T("6gA1", "0t (3) [10]"),
    T("6gA2", "1g (20) [20]"),
    T("6gA3", "ibx i9 (4) [20]"),
    T("6gA4", "ibx m5x2 ✉50 (4) [15]"),
    T("6gA5", "hiit (10) [23]"),
    T("6gA6", "1 f692 (10) [30]"),
    T("6gA7", "1 f693 (30) [30]"),
    T("6gA8", "业写 (60) [30]"),
    T("6gA9", "1 对身 (30) [30]"),
    T("6hB1", "fix claiming bug [2]"),
    T("6hB2", "Get appraisal, buy units (240) [1560] @m5x2"),
    T("6hB3", "review Omeed's review [(link)](https://example.com/x) (20) [20] @i9"),
    T("6hB4", "😈 -1g"),
]


def test_every_task_gets_a_unique_valid_code():
    c = dc.codes(TASKS)
    assert set(c) == {t["id"] for t in TASKS}
    assert len(set(c.values())) == len(c)
    assert all(dc.is_code(v) for v in c.values())


def test_a_two_character_name_is_its_own_code():
    c = dc.codes(TASKS)
    assert c["6gA1"] == "0t" and c["6gA2"] == "1g"


def test_initials_come_from_the_name():
    c = dc.codes(TASKS)
    assert c["6gA3"] == "ii"          # ibx i9
    assert c["6gA4"] == "im"          # ibx m5x2
    assert c["6gA5"] == "hi"          # hiit
    assert c["6hB1"] == "fc"          # fix claiming bug
    assert c["6hB2"] == "ga"          # estimates and @label are not part of the name
    assert c["6hB3"] == "ro"          # the link target is not part of the name
    assert c["6gA8"] == "业"          # one CJK character fills the two cells


def test_older_task_wins_a_collision_and_the_younger_takes_its_next_letter():
    c = dc.codes(TASKS)
    assert c["6gA6"] == "1f"          # 1 f692, older
    assert c["6gA7"] == "f6"          # 1 f693, younger: the second word's first two
    fresh = dc.codes(TASKS + [T("6zZ9", "fresh coat (5) [5]")])   # a newer task never renames an older one
    assert {k: v for k, v in fresh.items() if k != "6zZ9"} == c


def test_result_does_not_depend_on_input_order():
    base = dc.codes(TASKS)
    for seed in range(5):
        shuffled = TASKS[:]
        random.Random(seed).shuffle(shuffled)
        assert dc.codes(shuffled) == base


def test_codes_are_never_all_digits():
    c = dc.codes([T("6gC1", "10 pushups"), T("6gC2", "2026 plan"), T("6gC3", "42")])
    assert not any(v.isdigit() for v in c.values()) and len(c) == 3


def test_resolve_finds_the_task_and_ignores_non_codes():
    assert dc.resolve("FC", TASKS) == "6hB1"
    assert dc.resolve("业", TASKS) == "6gA8"
    assert dc.resolve("fix", TASKS) is None and dc.resolve("21", TASKS) is None
    assert dc.resolve("zz", TASKS) is None


def test_many_tasks_with_the_same_initials_still_get_distinct_codes():
    many = [T(f"6g{i:03d}", f"review item {i}") for i in range(80)]
    c = dc.codes(many)
    assert len(set(c.values())) == 80


def test_queue_tasks_flattens_sections_once():
    q = {"updated": "x", "today": [T("a1", "one"), T("a2", "two")], "0neon": [T("a1", "one"), T("a3", "three")]}
    assert [t["id"] for t in dc.queue_tasks(q)] == ["a1", "a2", "a3"]


def test_cell_pad():
    assert dc.cell_pad("fc") == "fc" and dc.cell_pad("业") == "业"
