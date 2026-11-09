import datetime as dt
import json
from collections import Counter

import suggest_eval as se


def test_normalize_and_similar():
    assert se.normalize_goal("prep for Standup (09:00) {10}") == se.normalize_goal("Prep for standup")
    assert se.similar("1532 分 「50」 {15} @g245", "1532 分 「50」") == 1.0
    assert se.similar("write the memo", "prep for Standup") < 0.6


def _rec(**kw):
    base = {"ts": "2026-11-09T08:05:00-08:00", "date": "2026-11-09", "weekday": "Mon",
            "block": "巳", "host": "ix", "mode": "inbound", "existing_goals": 0,
            "suggestions": [
                {"content": "prep for Standup (09:00)", "source": "cal", "domain": "i9"},
                {"content": "Progress with hiring {15}", "source": "0g", "domain": ""},
                {"content": "ship v3 [30]", "source": "todoist", "domain": "i9"}],
            "picks": [], "custom": [], "reason": None, "raw": ""}
    base.update(kw)
    return base


def test_rollup_math(tmp_path):
    recs = [
        _rec(response_kind="pick", picks=[2], raw="2"),
        _rec(response_kind="custom", custom=["progress on hiring"], raw="progress on hiring"),
        _rec(response_kind="custom", custom=["call the plumber"], block="酉"),
        _rec(response_kind="skip", reason="wrong-domain", raw="skip -w"),
    ]
    log = tmp_path / "-1g-suggest.ix.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    loaded = se.load_records(dt.date(2026, 11, 9), dt.date(2026, 11, 9), str(tmp_path))
    assert len(loaded) == 4
    shown, picked = se.pick_rates_by_source(loaded)
    assert shown["0g"] == 4 and picked["0g"] == 1 and picked["cal"] == 0
    cov, _ = se.coverage(loaded)
    assert cov == Counter({"outranked": 1, "absent": 1})

    snap = {"巳": [(True, "Progress with hiring {15}"), (False, "something else")]}
    oc = se.outcomes(loaded, snapshot_loader=lambda d: snap)
    assert oc == Counter({"done": 1})

    out = se.render(loaded, dt.date(2026, 11, 9), dt.date(2026, 11, 9))
    assert "4 cards shown" in out and "wrong-domain 1" in out


def test_block_goal_lines():
    text = "## -1₲\n\n- 巳 ☀️ 🎯 (81min)\n    - [x] 200分 {10}\n    - [ ] \n    **Time**\n- 午\n    - [ ] prep {5}\n## next\n- 巳\n    - [x] not this\n"
    g = se.block_goal_lines(text)
    assert g["巳"] == [(True, "200分 {10}")]
    assert g["午"] == [(False, "prep {5}")]
