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


def test_code_follows_the_short_name_dtd_displays():
    c = dc.codes([{"id": "6hC7", "content": "figure out allergy avoiding plan - last year was brutal [600] (82)",
                   "short": "Allergy avoiding plan (82) [600]"}])
    assert c["6hC7"] == "aa"


def test_older_task_wins_a_collision_and_the_younger_takes_its_next_letter():
    c = dc.codes(TASKS)
    assert c["6gA6"] == "1f"          # 1 f692, older
    assert c["6gA7"] == "f9"          # 1 f693, younger: f6 is reserved (the f69x domain), so f + 9
    assert c["6gA9"] == "对"          # 1 对身: nothing left of the name but the CJK
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


def test_lookup_finds_the_task_and_ignores_non_codes():
    assert dc.lookup("FC", TASKS) == ["6hB1"]
    assert dc.lookup("业", TASKS) == ["6gA8"]
    assert dc.lookup("fix", TASKS) == [] and dc.lookup("21", TASKS) == []
    assert dc.lookup("zz", TASKS) == []


def test_a_task_keeps_its_code_when_an_older_one_leaves():
    """Without `prev`, closing `1 f692` (1f) handed 1f to `1 f693`: a code typed
    from memory then named a different card."""
    before = dc.codes(TASKS)
    rest = [t for t in TASKS if t["id"] != "6gA6"]
    assert dc.codes(rest)["6gA7"] == "1f"                      # the churn, with no memory
    assert dc.codes(rest, prev=before)["6gA7"] == before["6gA7"] == "f9"


def test_a_card_named_like_a_code_takes_it_back_from_a_holder():
    prev = {"6gA3": "0t"}                                       # some row once showed 0t
    c = dc.codes(TASKS, prev=prev)
    assert c["6gA1"] == "0t" and c["6gA3"] != "0t"


def test_reserved_codes_go_only_to_the_card_with_that_name():
    """`/d i9` must stay a name search over the i9 cards, and `1s` must never
    drift onto `1 s897` while the 1s card is closed."""
    tasks = [T("6gD1", "1 s897 (25) [30]"), T("6gD2", "i9 2000分 this week"), T("6gD3", "hcmc")]
    c = dc.codes(tasks)
    assert c["6gD1"] not in dc.RESERVED and c["6gD2"] not in dc.RESERVED and c["6gD3"] not in dc.RESERVED
    assert dc.codes(tasks + [T("6gD4", "1s (20) [45]")])["6gD4"] == "1s"


def test_render_publishes_the_map_and_keeps_it_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("DTD_CODES_DIR", str(tmp_path))
    monkeypatch.setenv("DTD_HOSTSLUG", "straylight")
    first = dc.render_codes(TASKS, now=1000.0)
    f = tmp_path / "dtd-codes-straylight.json"
    assert f.exists() and dc.published(now=1001.0) == [first]
    stamp = f.stat().st_mtime_ns
    assert dc.render_codes(TASKS, now=1010.0) == first and f.stat().st_mtime_ns == stamp   # unchanged: not rewritten
    rest = [t for t in TASKS if t["id"] != "6gA6"]
    assert dc.render_codes(rest, now=1020.0)["6gA7"] == first["6gA7"]                       # sticky across renders
    dc.render_codes(rest, now=1020.0 + dc.HEARTBEAT + 1)
    assert dc.published(now=1020.0 + dc.HEARTBEAT + 2)                                      # heartbeat keeps it fresh


def test_lookup_reads_what_a_dtd_drew_and_asks_when_two_disagree(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("DTD_CODES_DIR", str(tmp_path))
    (tmp_path / "dtd-codes-straylight.json").write_text(json.dumps({"ts": 1000, "codes": {"6hB1": "qq", "6gA3": "ii"}}))
    assert dc.lookup("qq", TASKS, now=1001) == ["6hB1"]         # the drawn code, not the computed fc
    assert dc.lookup("fc", TASKS, now=1001) == []
    assert dc.code_for("6hB1", TASKS, now=1001) == "qq"
    (tmp_path / "dtd-codes-ix.json").write_text(json.dumps({"ts": 1000, "codes": {"6gA5": "qq", "6gA3": "ii"}}))
    assert sorted(dc.lookup("qq", TASKS, now=1001)) == ["6gA5", "6hB1"]   # two dtds, two rows: ask
    assert dc.lookup("ii", TASKS, now=1001) == ["6gA3"]                   # they agree
    assert dc.code_for("6gA3", TASKS, now=1001) == "ii"
    late = 1000 + dc.MIRROR_MAX_AGE + 5                          # both dtds gone: maps are stale
    assert dc.published(now=late) == []
    assert dc.lookup("qq", TASKS, now=late) == [] and dc.lookup("fc", TASKS, now=late) == ["6hB1"]   # computed


def test_tests_never_touch_the_real_vault(monkeypatch):
    monkeypatch.delenv("DTD_CODES_DIR", raising=False)
    assert dc.mirror_dir() is None


def test_many_tasks_with_the_same_initials_still_get_distinct_codes():
    many = [T(f"6g{i:03d}", f"review item {i}") for i in range(80)]
    c = dc.codes(many)
    assert len(set(c.values())) == 80


def test_queue_tasks_flattens_sections_once():
    q = {"updated": "x", "today": [T("a1", "one"), T("a2", "two")], "0neon": [T("a1", "one"), T("a3", "three")]}
    assert [t["id"] for t in dc.queue_tasks(q)] == ["a1", "a2", "a3"]


def test_cell_pad():
    assert dc.cell_pad("fc") == "fc" and dc.cell_pad("业") == "业"
