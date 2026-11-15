"""title_tiers (2026-11-14): tab initial / tmux short / statusline full."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import title_tiers as tt  # noqa: E402


def test_prose_strips_estimates_labels_and_mark():
    assert tt.prose("😈 fix -1n points (30) [20] {5} @i447") == "fix -1n points"
    assert tt.prose("W225 billboard lease [0G]") == "W225 billboard lease"


def test_trim_cuts_at_a_word_boundary():
    assert tt.trim("auto-claim sessions + 3-tier cmux/tmux/statusline titles") == "auto-claim sessions + 3-tier"
    assert tt.trim("short") == "short"


def test_valid_initial_is_two_cells_at_most():
    assert tt.valid_initial("Xb") and tt.valid_initial("税") and tt.valid_initial("🎮") and tt.valid_initial("❔")
    assert not tt.valid_initial("Xbox") and not tt.valid_initial("税务") and not tt.valid_initial("a b")
    assert not tt.valid_initial("")


def test_initial_for_caches_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "INITIALS", tmp_path / "initials.json")
    calls = []
    gen = lambda text: calls.append(text) or "ac"
    assert tt.initial_for("auto-claim sessions", generate=False, compute=gen) is None
    assert tt.initial_for("auto-claim sessions", generate=True, compute=gen) == "ac"
    assert tt.initial_for("auto-claim sessions", generate=True, compute=gen) == "ac"
    assert calls == ["auto-claim sessions"]
    assert json.loads((tmp_path / "initials.json").read_text()) == {tt._hash("auto-claim sessions"): "ac"}


def test_unclaimed_session_leads_with_the_question_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "claim_record", lambda sid: None)
    monkeypatch.setattr(tt, "SESSION_TITLES", tmp_path)
    monkeypatch.setattr(tt, "SESSION_SHORTS", tmp_path / "none.json")
    (tmp_path / "S1").write_text("PlayStation 5 and Xbox sales figures\n")
    t = tt.tiers("S1")
    assert t == {"init": "❔", "short": "PlayStation 5 and Xbox sales", "full": "PlayStation 5 and Xbox sales figures",
                 "source": "session"}
    assert tt.tab_text(t) == "❔ PlayStation 5 and Xbox sales"
    # No recorded title yet: the caller's fallback title is used; none at all → no tab text.
    assert tt.tiers("S2", title="Happy crane")["init"] == "❔"
    assert tt.tab_text(tt.tiers("S2")) == ""


def test_claimed_session_uses_the_task_and_its_recorded_initial(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "TASK_SHORTS", tmp_path / "shorts.json")
    (tmp_path / "shorts.json").write_text(json.dumps({"T1": {"h": "x", "short": "auto-claim + 3-tier titles (120) [60]"}}))
    rec = {"task_id": "T1", "task": "auto-claim sessions + 3-tier cmux/tmux/statusline titles (120) [60]",
           "init": "ac", "_dir": str(tmp_path)}
    monkeypatch.setattr(tt, "claim_record", lambda sid: dict(rec))
    t = tt.tiers("S1")
    assert (t["init"], t["short"], t["full"], t["source"]) == (
        "ac", "auto-claim + 3-tier titles", "auto-claim sessions + 3-tier cmux/tmux/statusline titles", "task")
    assert tt.tab_text(t) == "ac auto-claim + 3-tier titles"
