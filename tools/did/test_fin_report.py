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


def _call(ts, mid, read, write=0, out=0, model="claude-fable-5-2"):
    return _line(type="assistant", timestamp=ts, message={"id": mid, "model": model, "usage": {
        "input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": read,
        "cache_creation": {"ephemeral_1h_input_tokens": write}}})


def test_windows_partition_a_session_with_several_claims(tmp_path):
    """2026-11-15: three claims in one session reported 41.6M / 23M / 9.8M
    tokens, each including every later task. Windows must not overlap, and a
    streamed message straddling a boundary belongs to one window only."""
    t = tmp_path / "s.jsonl"
    t.write_text(
        _line(type="user", timestamp="2026-11-15T10:00:00Z", message={"content": "task one"}) +
        _call("2026-11-15T10:00:10Z", "a", 1000, out=10) +
        _call("2026-11-15T10:59:59Z", "b", 2000, out=10) +     # starts in window 1
        _call("2026-11-15T11:00:01Z", "b", 2000, out=10) +     # ...finishes in window 2
        _line(type="user", timestamp="2026-11-15T11:00:02Z", message={"content": "task two"}) +
        _call("2026-11-15T11:00:10Z", "c", 4000, out=10))
    t1 = fr._ts({"timestamp": "2026-11-15T10:00:00Z"})
    t2 = fr._ts({"timestamp": "2026-11-15T11:00:00Z"})
    assert fr.window_end(t1, [t1, t2]) == t2 and fr.window_end(t2, [t1, t2]) is None
    assert fr.window_end(t1, [t1, t1 + 5]) is None             # `claim --also`: shared window
    one = fr.model_usage(t, since=t1, until=fr.window_end(t1, [t1, t2]))
    two = fr.model_usage(t, since=t2, until=None)
    whole = fr.model_usage(t, since=t1)
    assert (one["messages"], two["messages"], whole["messages"]) == (2, 1, 3)
    assert (one["turns"], two["turns"]) == (1, 1)
    assert one["tokens"]["cache_read"] + two["tokens"]["cache_read"] == whole["tokens"]["cache_read"] == 7000
    assert one["active_minutes"] == 60                          # clipped at the boundary


def test_new_in_is_context_growth_not_cache_traffic(tmp_path):
    """cache_read re-counts the whole context on every call, and a cache
    rewrite after an idle gap is a huge cache_write with nothing new in it."""
    t = tmp_path / "s.jsonl"
    t.write_text(
        _call("2026-11-15T09:00:00Z", "pre", 50_000, out=1000) +          # before the window
        _call("2026-11-15T10:00:00Z", "a", 51_000, write=4000, out=500) +  # +4k new
        _call("2026-11-15T10:01:00Z", "b", 55_500, out=500) +              # nothing new
        _call("2026-11-15T12:00:00Z", "c", 0, write=58_000, out=0))        # TTL rewrite, +2k new
    m = fr.model_usage(t, since=fr._ts({"timestamp": "2026-11-15T09:30:00Z"}))
    assert m["new_in"] == 4000 + 0 + 2000
    assert m["tokens"]["cache_read"] == 106_500 and m["tokens"]["cache_write_1h"] == 62_000
    assert m["context_peak"] == 58_000 and m["context_avg"] == round((55_000 + 55_500 + 58_000) / 3)
    first = fr.model_usage(t, since=0)                                     # no earlier call: baseline counts
    assert first["new_in"] == 50_000 + 4000 + 2000


def test_subagent_tokens_count_but_not_as_main_calls(tmp_path):
    main, sub = tmp_path / "s.jsonl", tmp_path / "agent-x.jsonl"
    main.write_text(_call("2026-11-15T10:00:00Z", "a", 1000, out=100))
    sub.write_text(_call("2026-11-15T10:00:30Z", "s1", 500, out=50, model="claude-haiku-5-5") +
                   _call("2026-11-15T12:00:00Z", "s2", 500, out=50, model="claude-haiku-5-5"))
    m = fr.model_usage(main, since=0, until=fr._ts({"timestamp": "2026-11-15T11:00:00Z"}), extra=[sub])
    assert (m["messages"], m["subagent_calls"]) == (1, 1)
    assert m["tokens"]["cache_read"] == 1500 and m["tokens"]["output"] == 150
    assert m["context_peak"] == 1000 and sorted(m["by_model"]) == ["claude-fable-5-2", "claude-haiku-5-5"]
