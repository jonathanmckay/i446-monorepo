"""-1g goal helpers in neon_blocks (pure logic; the locked writer is exercised on a tmp copy)."""
from pathlib import Path

import neon_blocks as nb

BO = """# build order
## -1₲
- 巳 ☀️ 🎯
    - [ ] I have an agenda {10}
    - [x] Chinese or math with kids {10}
    **Time**
    - 07:40-08:03 get $300 for bag
- 午 ☀️
    - [ ] 
    **Time**
- 未
    - [ ] 
## next section
- 巳 not this one
"""


def test_parse_goals_text_variants():
    assert nb.parse_goals_text("200分 【15】") == ["200分 {15}"]
    assert nb.parse_goals_text("- a {5}\n- [ ] b") == ["a {5}", "b"]
    assert nb.parse_goals_text("ship dashboard, call plumber; tidy desk") == ["ship dashboard", "call plumber", "tidy desk"]
    assert nb.parse_goals_text("") == []


def test_split_goal_domain_at_code_keyword_default():
    assert nb.split_goal_domain("fix window @m5x2", "巳") == ("fix window", "m5x2")
    assert nb.split_goal_domain("prep standup notes", "酉") == ("prep standup notes", "i9")
    assert nb.split_goal_domain("something unrelated", "酉") == ("something unrelated", "m5x2")
    assert nb.split_goal_domain("200分 {15}", "申") == ("200分 {15}", "g245")


def test_points_and_plain_todo_rules():
    assert nb.ensure_goal_points("tidy desk") == "tidy desk {10}"
    assert nb.ensure_goal_points("tidy desk {5}") == "tidy desk {5}"
    assert nb.is_plain_todo("buy milk [3]") is True
    assert nb.is_plain_todo("buy milk [3] {5}") is False
    assert nb.is_plain_todo("buy milk") is False


def test_append_replaces_blank_placeholder_and_stamps():
    text, added = nb.append_block_goals_text(BO, "午", ["200分 {15}"])
    assert added == ["200分 {15}"]
    lines = text.split("\n")
    i = lines.index("- 午 ☀️ 🎯")
    assert lines[i + 1] == "    - [ ] 200分 {15}"
    assert lines[i + 2] == "    **Time**"
    assert "    - [ ] \n" not in text.split("- 午")[1].split("- 未")[0]


def test_append_keeps_existing_goals_and_is_idempotent():
    text, added = nb.append_block_goals_text(BO, "巳", ["new goal {5}", "I have an agenda {10}"])
    assert added == ["new goal {5}"]
    seg = text.split("- 巳 ☀️ 🎯")[1].split("- 午")[0]
    assert seg.count("- [ ] I have an agenda {10}") == 1
    assert seg.count("- [x] Chinese or math with kids {10}") == 1
    assert seg.index("new goal {5}") > seg.index("Chinese or math")
    assert seg.index("new goal {5}") < seg.index("**Time**")
    again, added2 = nb.append_block_goals_text(text, "巳", ["new goal {5}"])
    assert added2 == [] and again == text


def test_append_missing_block_raises():
    try:
        nb.append_block_goals_text(BO, "亥", ["x {5}"])
    except ValueError as e:
        assert "亥" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_locked_writer_on_tmp_copy(tmp_path: Path):
    bo = tmp_path / "build-order.md"
    bo.write_text(BO, encoding="utf-8")
    added = nb.append_block_goals("未", ["walk 20m {5}"], build_order=bo)
    assert added == ["walk 20m {5}"]
    out = bo.read_text(encoding="utf-8")
    assert "- 未 🎯\n    - [ ] walk 20m {5}\n" in out
    assert (tmp_path / "build-order.lock").exists()


BO_DAILY = """# build order
 
 **1₲**

## 0₲
- [ ] first goal {20}
- [x] done goal {10}

### 以后的目标
- [ ] later thing {5}

## -1₲
- 巳 ☀️
    - [ ] block goal {10}
"""


def test_append_daily_goals_after_last_checkbox_before_yihou():
    text, added = nb.append_daily_goals_text(BO_DAILY, ["new goal {15}", "first goal {20}"])
    assert added == ["new goal {15}"]
    lines = text.split("\n")
    i = lines.index("- [x] done goal {10}")
    assert lines[i + 1] == "- [ ] new goal {15}"
    assert lines[i + 2] == ""                       # blank line before ### kept
    assert lines[i + 3] == "### 以后的目标"
    # 以后的目标 and -1₲ untouched, and idempotent on retry
    assert text.count("later thing {5}") == 1 and text.count("block goal {10}") == 1
    again, added2 = nb.append_daily_goals_text(text, ["new goal {15}"])
    assert added2 == [] and again == text


def test_append_daily_goals_empty_section_inserts_after_header():
    bo = "## 0₲\n\n### 以后的目标\n- [ ] later {5}\n"
    text, added = nb.append_daily_goals_text(bo, ["a goal {15}"])
    assert added == ["a goal {15}"]
    assert text.split("\n")[1] == "- [ ] a goal {15}"
    # the ### subsection's own checkboxes never count as 0₲ content
    assert "later {5}" in text and text.count("- [ ]") == 2


def test_append_daily_goals_missing_section_raises():
    try:
        nb.append_daily_goals_text("# build order\n## -1₲\n", ["x {5}"])
    except ValueError as e:
        assert "0₲" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_daily_locked_writer_on_tmp_copy(tmp_path: Path):
    bo = tmp_path / "build-order.md"
    bo.write_text(BO_DAILY, encoding="utf-8")
    added = nb.append_daily_goals(["walk 20m {15}"], build_order=bo)
    assert added == ["walk 20m {15}"]
    assert "- [ ] walk 20m {15}\n" in bo.read_text(encoding="utf-8")
    assert (tmp_path / "build-order.lock").exists()
