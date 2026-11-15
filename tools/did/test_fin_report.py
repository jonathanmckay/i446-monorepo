"""fin_report (2026-11-14): the /fin final report's numbers."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fin_report_t", HERE / "fin_report.py")
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)


def _line(**o):
    return json.dumps(o) + "\n"


def test_model_usage_dedupes_streamed_lines_and_counts_jm_turns(tmp_path):
    u = {"input_tokens": 2, "output_tokens": 300, "cache_read_input_tokens": 1000,
         "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 50}}
    t = tmp_path / "s.jsonl"
    t.write_text(
        _line(type="user", timestamp="2026-11-14T20:00:00Z", message={"content": "fix the thing"}) +
        _line(type="assistant", timestamp="2026-11-14T20:00:05Z", message={"id": "m1", "model": "claude-fable-5-1", "usage": u}) +
        _line(type="assistant", timestamp="2026-11-14T20:00:06Z", message={"id": "m1", "model": "claude-fable-5-1", "usage": u}) +
        _line(type="user", timestamp="2026-11-14T20:00:07Z", message={"content": [{"type": "tool_result", "content": "ok"}]}) +
        _line(type="user", timestamp="2026-11-14T20:01:00Z", message={"content": "<command-name>/d</command-name>"}) +
        _line(type="user", timestamp="2026-11-14T20:02:00Z", message={"content": "thanks, ship it"}) +
        _line(type="assistant", timestamp="2026-11-14T20:03:00Z", message={"id": "m2", "model": "claude-haiku-5-5", "usage": {"input_tokens": 10, "output_tokens": 20}}))
    m = fr.model_usage(t, since=0, now=1e12)
    assert m["turns"] == 2 and m["messages"] == 2 and m["active_minutes"] == 3
    assert m["tokens"] == {"input": 12, "output": 320, "cache_read": 1000, "cache_write_5m": 100, "cache_write_1h": 50}
    fable = (2 * 10 + 300 * 50 + 1000 * 0.25 + 100 * 12.5 + 50 * 20) / 1e6
    haiku = (10 * 0.10 + 20 * 0.50) / 1e6
    assert m["cost_usd"] == round(fable + haiku, 2)


def test_model_usage_respects_since_and_unpriced_model(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(
        _line(type="assistant", timestamp="2026-11-14T19:00:00Z", message={"id": "old", "model": "claude-fable-5-1", "usage": {"output_tokens": 999}}) +
        _line(type="assistant", timestamp="2026-11-14T21:00:00Z", message={"id": "new", "model": "claude-zeta-9", "usage": {"output_tokens": 1}}))
    since = fr._ts({"timestamp": "2026-11-14T20:00:00Z"})
    m = fr.model_usage(t, since=since)
    assert m["messages"] == 1 and m["tokens"]["output"] == 1 and m["cost_usd"] is None


def test_jm_minutes_links_by_id_then_by_name_and_counts_running():
    entries = [
        {"id": 1, "description": "0l", "duration": 600},                       # same name
        {"id": 2, "description": "renamed card", "duration": 300},             # linked
        {"id": 3, "description": "other", "duration": 9999},                   # unrelated
        {"id": 4, "description": "0l [20]", "duration": -1, "start": "2026-11-14T20:00:00+00:00"},  # running
    ]
    now = fr._ts({"timestamp": "2026-11-14T20:10:00Z"})
    assert fr.jm_minutes(entries, "😈 0l (8) [20]", linked_entry_id=2, now=now) == (25, 3)
    assert fr.jm_minutes(entries, "nothing like it", None, now=now) == (0, 0)


def test_task_points_and_prices():
    assert fr.task_points("auto-claim sessions (120) [60]") == 60
    assert fr.task_points("no value here") == 0
    assert fr.prices_for("claude-opus-5-5")["output"] == 20.0 and fr.prices_for("claude-opus-5")["output"] == 25.0
    assert fr.prices_for("gpt-9") is None
