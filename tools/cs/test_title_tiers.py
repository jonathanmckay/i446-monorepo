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


def test_free_session_reads_tilde_tilde(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "claim_record", lambda sid: None)
    monkeypatch.setattr(tt, "SESSION_TITLES", tmp_path / "titles")
    (tmp_path / "titles").mkdir()
    (tmp_path / "titles" / "S1").write_text("0l\n")
    monkeypatch.setattr(tt, "is_free", lambda sid: sid == "S1")
    t = tt.tiers("S1")
    assert t["source"] == "free" and tt.tab_text(t) == tt.FREE_INIT == "~~"
    assert tt.tiers("S2")["init"] is None   # no title, no claim, not free


# ── agent-done task keeps its initial on the tab (bug 2026-11-15) ────────────
# `agent_claims.py done` drops the id from by-session/<sid> so later hooks do
# not flip the state back; claim_record read only that index, so the tab fell
# to ❔ while dtd still led the ✅ row with the claim's 🔔.

def _claims_store(tmp_path, monkeypatch):
    ac = tt._claims()   # _claims() loads a fresh module per call: pin one
    monkeypatch.setattr(ac, "CLAIMS_DIR", tmp_path)
    monkeypatch.setattr(tt, "_claims", lambda: ac)
    (tmp_path / "by-session").mkdir()
    return ac


def _claim(root, tid, sid, state, ts, init="🔔"):
    (root / f"{tid}.json").write_text(json.dumps(
        {"task_id": tid, "task": "upgrade slack tasks [10]", "session": sid, "init": init}))
    (root / f"{tid}.state").write_text(f"{state} {int(ts)}\n")


def test_done_task_keeps_its_initial_on_the_tab(tmp_path, monkeypatch):
    import time
    _claims_store(tmp_path, monkeypatch)
    monkeypatch.setattr(tt, "TASK_SHORTS", tmp_path / "shorts.json")
    _claim(tmp_path, "T1", "S1", "done", time.time())
    t = tt.tiers("S1")
    assert t["source"] == "task" and t["init"] == "🔔" and t["task_id"] == "T1"


def test_done_fallback_ignores_released_other_sessions_and_expired(tmp_path, monkeypatch):
    import time
    ac = _claims_store(tmp_path, monkeypatch)
    now = time.time()
    _claim(tmp_path, "T1", "S1", "released", now)            # handed back to JM
    _claim(tmp_path, "T2", "S2", "done", now)                # another session's
    _claim(tmp_path, "T3", "S1", "done", now - ac.RELEASED_TTL - 60)  # dtd dropped it
    assert tt.claim_record("S1") is None


def test_held_task_wins_over_an_older_done_one(tmp_path, monkeypatch):
    import time
    _claims_store(tmp_path, monkeypatch)
    _claim(tmp_path, "T1", "S1", "done", time.time(), init="🔔")
    _claim(tmp_path, "T2", "S1", "working", time.time(), init="ab")
    (tmp_path / "by-session" / "S1").write_text("T2\n")
    assert tt.claim_record("S1")["task_id"] == "T2"
