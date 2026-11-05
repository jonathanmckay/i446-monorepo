import datetime as dt
import json

import pytest

import evidence

DAY = dt.date(2026, 11, 5)


def test_parse_ranges_takes_both_clock_forms():
    assert evidence.parse_ranges(["0612-0631", "6:33-6:38"], DAY)[1][0] == dt.datetime(2026, 11, 5, 6, 33)


def test_parse_ranges_rejects_a_bare_time():
    with pytest.raises(SystemExit):
        evidence.parse_ranges(["0612"], DAY)


def test_range_end_is_exclusive():
    r = evidence.parse_ranges(["0612-0631"], DAY)
    assert evidence.inside(dt.datetime(2026, 11, 5, 6, 12), r)
    assert not evidence.inside(dt.datetime(2026, 11, 5, 6, 31), r)


def test_prompts_keep_jm_and_drop_subagents_and_skill_bodies(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    stamp = dt.datetime(2026, 11, 5, 6, 20).astimezone().astimezone(dt.timezone.utc)
    base = {"type": "user", "timestamp": stamp.isoformat().replace("+00:00", "Z")}
    rows = [
        {**base, "message": {"content": "do the fixes"}},
        {**base, "isSidechain": True, "message": {"content": "subagent brief"}},
        {**base, "message": {"content": "Base directory for this skill: x"}},
        {**base, "message": {"content": "<command-name>/0n</command-name>\n<command-args>0g 11.04</command-args>"}},
    ]
    (proj / "abcdef12.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = evidence.prompt_events(evidence.parse_ranges(["0600-0700"], DAY), DAY, tmp_path)
    assert [e[2] for e in got] == ["[abcdef] do the fixes", "[abcdef] /0n 0g 11.04"]
