#!/usr/bin/env python3
"""Tests for neon-task-checksum.py (pure logic; no Excel/Todoist/ssh).

Feature 2026-07-26: the 1st-hci disappearance showed the only daily-card
checker lived inside the dead -2n loop and nothing checked weeklies at all.
The checksum derives weekly expectations straight from the 1n+ sheet and is
scheduled on Ix via launchd (com.jm.neon-task-checksum).
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cs():
    return _load("ntc", "neon-task-checksum.py")


# ── norm_name ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("AoS (15) [15]", "aos"),
    ("relax (60)", "relax"),
    ("relax {60}", "relax"),
    ("family (120) [1/m]", "family"),
    ("😈 1st hci (15) [15]", "1st hci"),
    ("1 kids nature (120) [1/m] [30]", "1 kids nature"),
    ("长o314", "长o314"),
])
def test_norm_name(cs, raw, expected):
    assert cs.norm_name(raw) == expected


# ── 1n+ expectation parsing ──────────────────────────────────────────────────

ROW1 = ["1s", "AoS", "长冥想", "一起饭", "∑", ""]
ROW2 = ["20.0", "15.0", "60.0", "90.0", "1027.0", ""]
ROW3 = ["1.0", "7.0", "6.0", "7.0", "", ""]
ROW5 = ["45.0", "15+1/m", ".5/m", "15+1/m", "", ""]


def test_parse_1n_expectations(cs):
    exp = cs.parse_1n_expectations(ROW1, ROW2, ROW3, ROW5)
    assert [e["header"] for e in exp] == ["1s", "AoS", "长冥想", "一起饭"]
    assert exp[0] == {"header": "1s", "time": 20, "day": 1, "pts": 45, "rate": None}
    assert exp[1]["pts"] is None and exp[1]["rate"] == "15+1/m"
    # ∑ (no day) and blank tail dropped


# ── weekly matching ──────────────────────────────────────────────────────────

def _exp(cs):
    return cs.parse_1n_expectations(ROW1, ROW2, ROW3, ROW5)


def test_match_weekly_aliases_and_annotations(cs):
    present, missing = cs.match_weekly(_exp(cs), [
        "1s (20) [45]",
        "AoS (15) [15]",            # case-insensitive vs header "AoS"
        "long o314 (30) [10]",      # alias — irrelevant here, not expected
        "一起饭 (90)",
    ])
    assert [e["header"] for e in missing] == ["长冥想"]
    assert len(present) == 3


def test_match_weekly_relax_alias(cs):
    # Header "relax {60}" ↔ card "relax (60)" via the relax alias
    exp = cs.parse_1n_expectations(["relax {60}"], ["60.0"], ["7.0"], ["1/m"])
    present, missing = cs.match_weekly(exp, ["relax (60)"])
    assert not missing


# ── weekday warnings ─────────────────────────────────────────────────────────

def test_weekday_warning_for_wrong_day(cs):
    exp = cs.parse_1n_expectations(["AoS"], ["15.0"], ["7.0"], ["15.0"])
    warns = cs.weekday_warnings(exp, [{"content": "AoS (15) [15]",
                                       "due_string": "every Sunday"}])
    assert len(warns) == 1 and "saturday" in warns[0]


def test_no_warning_when_day_matches_or_monthly(cs):
    exp = cs.parse_1n_expectations(["AoS", "2 hci"], ["15.0", "15.0"],
                                   ["7.0", "6.0"], ["15.0", "15.0"])
    warns = cs.weekday_warnings(exp, [
        {"content": "AoS (15) [15]", "due_string": "every Saturday"},
        {"content": "2 hci (15) [15]", "due_string": "every month"},
    ])
    assert warns == []


# ── create payloads ──────────────────────────────────────────────────────────

def test_weekly_create_payload_fixed_points(cs):
    e = {"header": "1 s897", "time": 25, "day": 4, "pts": 30, "rate": None}
    body = cs.weekly_create_payload(e)
    assert body["content"] == "1 s897 (25) [30]"
    assert body["due_string"] == "every Wednesday"
    assert body["labels"] == ["1neon", "s897"]
    assert "😈" not in body["content"]


def test_weekly_create_payload_rate_has_no_points(cs):
    e = {"header": "长冥想", "time": 60, "day": 6, "pts": None, "rate": ".5/m"}
    body = cs.weekly_create_payload(e)
    assert body["content"] == "长冥想 (60)"


def test_weekly_create_payload_strips_curly_from_name(cs):
    # {N} in a card name triggers the 0g bonus write on completion
    e = {"header": "relax {60}", "time": 60, "day": 7, "pts": None, "rate": "1/m"}
    assert cs.weekly_create_payload(e)["content"] == "relax (60)"


# ── manifest drift ───────────────────────────────────────────────────────────

def test_manifest_drift(cs):
    manifest = {"habits": {"a": {"match": "1st hci"}, "b": {"match": "gone col"}}}
    assert cs.manifest_drift(manifest, ["1st hci", "2nd hci"]) == ["b"]


# ── sync with did-fast (source of truth for aliases) ─────────────────────────

def test_aliases_match_did_fast(cs):
    df = _load("df_sync", str(Path.home()
               / "i446-monorepo/tools/did/did-fast.py"))
    assert cs.ALIASES == df.ONENEON_ALIASES, (
        "neon-task-checksum's ALIASES copy drifted from did-fast's "
        "ONENEON_ALIASES — sync them")


# ── validator creates clean names (the 😈 mis-route fix) ─────────────────────

def test_validate_daily_habits_creates_clean_names():
    vdh = _load("vdh_sync", "validate-daily-habits.py")
    body = vdh.recreate_payload({"content": "1st hci (15) [15]",
                                 "due_string": "every day",
                                 "labels": ["0neon", "hci"], "priority": 2})
    assert body["content"] == "1st hci (15) [15]"
    assert "😈" not in body["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
