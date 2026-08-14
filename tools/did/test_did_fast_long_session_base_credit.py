"""Regression (2026-08-14): "neon shows +30 points 冥想, jm dash shows -4".

Root cause: 长冥想 ("long meditation") is a weekly Todoist card (the ONLY
Todoist-surfaced reminder for meditation at all -- there is no separate daily
"冥想" 0neon task) that, when completed with >=30 Toggl minutes for the base
"冥想" activity, correctly computes and credits the long-session BONUS
(0.5 x minutes, into the 1n+ week cell + 0分 column V) via the THRESHOLD_1N
path in route_items(). But nothing in that path ever wrote to the BASE
habit's own 0n column (AR, header "冥想") -- so a real 35-minute session
correctly triggered the bonus (0.5*35=17.5->18) while the base "冥想" 0₦
column stayed at 0 all day. The dashboard's 冥想 tile sums that column
(0n!AR375, a running total), so it stayed unaffected/negative despite the
session happening and clearing the bonus threshold.

Fix: when a THRESHOLD_1N item (长冥想/长o314) clears its minimum-minutes
threshold, route_items() now also appends a step="0n" RouteResult crediting
the base habit (resolved from THRESHOLD_1N[...]["toggl"], the same name
toggl_minutes_for() was already queried with) with the same minutes.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_long_bonus", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


HEADERS = {"0n": {"冥想": 44, "o314": 43}, "1n": {"长冥想": "V", "长o314": "W"}}


def test_long_meditation_above_threshold_also_credits_base_habit(monkeypatch):
    mod = _load_did_fast()
    monkeypatch.setattr(mod, "toggl_minutes_for", lambda name: 35)

    items = mod.parse_input("长冥想")
    results = mod.route_items(items, headers=HEADERS, tq={})

    base_credits = [r for r in results
                    if r.step == "0n" and r.item.name == "冥想"]
    assert len(base_credits) == 1, (
        f"expected exactly one base-habit (冥想) 0n credit, got {len(base_credits)}: {results}")
    assert base_credits[0].write_value == 35, \
        "base habit must be credited with the SAME minutes the bonus used"
    assert base_credits[0].col_num == HEADERS["0n"]["冥想"]

    bonus = [r for r in results if r.step == "1n"]
    assert len(bonus) == 1, f"expected the long-session bonus itself to still route, got {results}"


def test_long_meditation_below_threshold_does_not_credit_base_habit(monkeypatch):
    """Below the 30m minimum, 长冥想 is skipped entirely -- no bonus AND no
    base-habit credit should be synthesized from a session that never
    cleared the threshold."""
    mod = _load_did_fast()
    monkeypatch.setattr(mod, "toggl_minutes_for", lambda name: 10)

    items = mod.parse_input("长冥想")
    results = mod.route_items(items, headers=HEADERS, tq={})

    base_credits = [r for r in results if r.step == "0n" and r.item.name == "冥想"]
    assert not base_credits, \
        f"a sub-threshold session must not synthesize a base-habit credit: {results}"
    skipped = [r for r in results if r.step == "skipped"]
    assert len(skipped) == 1


def test_long_o314_uses_the_first_toggl_alias_as_the_base_habit(monkeypatch):
    """长o314's threshold minutes are summed across ('o314', 'hcmr') --
    the base credit must resolve to 'o314' (the real 0n column), not the
    alias tuple itself or 'hcmr' (which shares o314's column via
    ZERO_N_ALIASES but isn't its own header)."""
    mod = _load_did_fast()
    monkeypatch.setattr(mod, "toggl_minutes_for", lambda name: 40)

    items = mod.parse_input("长o314")
    results = mod.route_items(items, headers=HEADERS, tq={})

    base_credits = [r for r in results if r.step == "0n"]
    assert len(base_credits) == 1, f"expected exactly one base-habit credit: {results}"
    assert base_credits[0].item.name == "o314"
    assert base_credits[0].col_num == HEADERS["0n"]["o314"]
    assert base_credits[0].write_value == 40


def test_base_credit_skipped_when_base_header_not_in_registry(monkeypatch):
    """If the base habit's header isn't present in the live 0n headers dict
    (a plumbing gap, not this bug), the synthesized credit must be skipped
    rather than crash route_items with a KeyError."""
    mod = _load_did_fast()
    monkeypatch.setattr(mod, "toggl_minutes_for", lambda name: 35)

    headers_missing_base = {"0n": {}, "1n": {"长冥想": "V"}}
    items = mod.parse_input("长冥想")
    results = mod.route_items(items, headers=headers_missing_base, tq={})

    assert not [r for r in results if r.step == "0n"]
    assert [r for r in results if r.step == "1n"], \
        "the bonus itself must still route even if the base credit can't"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
