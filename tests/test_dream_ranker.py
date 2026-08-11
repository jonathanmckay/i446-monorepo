"""Tests for scripts/dream-ranker."""
import importlib.util, sys
from datetime import date
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "dream_ranker", Path(__file__).parent.parent / "scripts" / "dream-ranker.py")
m = importlib.util.module_from_spec(SPEC); sys.modules["dream_ranker"] = m
SPEC.loader.exec_module(m)


def test_due_urgency_overdue():
    assert m.due_score("2026-05-01", date(2026, 5, 23)) == 3

def test_due_urgency_far():
    assert m.due_score("2026-12-01", date(2026, 5, 23)) == 0

def test_risk_buy_forbidden():
    assert m.risk("Buy nightshade and bike rack") == "forbidden"

def test_risk_send_approval():
    assert m.risk("Send out SLT slide deck to Jay") == "approval-required"

def test_doability_implement_high():
    assert m.dream_doability("implement s897 scoring") == 5

def test_weekly_match_i9_xbox():
    t = {"content": "Roadmaps for FYQ1 Xbox Growth", "labels": ["i9"]}
    s = m.score(t, date(2026, 5, 23))
    assert s["weekly_goal_match"] == 5
