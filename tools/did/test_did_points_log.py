"""User request 2026-09-18: "/did can handle inputs that are points +
category + note (and the note only goes in the 分 log) i.e.
'+90 @m5x2 monthly sync'". A leading signed token makes the run a 分 log:
points to the domain column, note into the ledger/completed-today only —
no Todoist match, no posthoc card, no timer stop; negatives allowed."""
import importlib.util
import sys
import types
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("didfast_pl", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["didfast_pl"] = df  # dataclass field resolution needs the module registered
_spec.loader.exec_module(df)


def test_parse_leading_signed_points_with_domain_and_note():
    (it,) = df.parse_input("+90 @m5x2 monthly sync")
    assert it.points_log is True
    assert it.points_override == 90 and it.bonus_points is None
    assert it.project_override == "m5x2"
    assert it.name == "monthly sync"


def test_parse_negative_points_log():
    (it,) = df.parse_input("-15 @m5x2 late fee")
    assert it.points_log and it.points_override == -15 and it.name == "late fee"


def test_mid_string_bonus_is_still_a_bonus_not_a_points_log():
    (it,) = df.parse_input("bball 30 +10")
    assert it.points_log is False and it.bonus_points == 10 and it.time_value == 30


def test_habit_names_with_leading_dash_are_not_points_logs():
    (it,) = df.parse_input("-1t")
    assert it.points_log is False and it.name == "-1t"


def test_route_points_log_to_domain_column_without_todoist():
    items = df.parse_input("+90 @m5x2 monthly sync, -15 @i9 slipped deadline")
    routes = df.route_items(items, {"0n": {}, "1n": {}}, {}, skip_todoist=True)
    assert [(r.step, r.fen_col, r.fen_points) for r in routes] == \
        [("variable", "S", 90), ("variable", "R", -15)]
    assert all(r.todoist_task is None for r in routes)
    assert df.is_points_log_run(items) is True
    assert df._fen_pts_ok(routes[1]) is True          # negative 分 log passes
    routes[1].item.points_log = False
    assert df._fen_pts_ok(routes[1]) is False         # ordinary items must be > 0


def test_points_log_without_domain_needs_the_agent():
    (it,) = df.parse_input("+90 monthly sync")
    (r,) = df.route_items([it], {"0n": {}, "1n": {}}, {}, skip_todoist=True)
    assert r.step == "needs_agent"


def test_append_batch_formats_negative_points_without_plus(monkeypatch):
    seen = {}
    fake = types.SimpleNamespace(batch_append=lambda sheet, values, **kw: seen.update(values=values) or {"ok": True, "row": 5})
    monkeypatch.setattr(df, "neon_excel", fake)
    df.append_0fen_batch([("S", 90), ("R", -15)], "9/18", ["monthly sync", "slipped"], "0fen")
    assert seen["values"] == [("S", "+90"), ("R", "-15")]


def test_janus_routes_leading_signed_points_to_did_fast():
    spec = importlib.util.spec_from_file_location("janus_pl", _HERE.parent / "tg" / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_pl"] = mod
    spec.loader.exec_module(mod)
    assert mod._is_points_log_cmd("+90 @m5x2 monthly sync")
    assert mod._is_points_log_cmd("-15 @m5x2 late fee")
    assert not mod._is_points_log_cmd("-1t")
    assert not mod._is_points_log_cmd("1815-1843 work [30]")
    assert not mod._is_points_log_cmd("bball 30 +10")


def test_bare_domain_code_after_signed_points_targets_that_column():
    """User request 2026-09-19: "-10 0g" via janus subtracts from the 0g
    column. "0g" is also a 0n habit header, so the 分-log path must never
    reach the habit match (that would mark the habit done, not dock it)."""
    headers = {"0n": {"0g": 20, "0l": 19}, "1n": {}}
    (it,) = df.parse_input("-10 0g")
    assert it.points_log and it.points_override == -10 and it.name == "0g"
    (r,) = df.route_items([it], headers, {}, skip_todoist=True)
    assert (r.step, r.fen_col, r.fen_points) == ("variable", "Q", -10)
    (r2,) = df.route_items(df.parse_input("+5 i9"), headers, {}, skip_todoist=True)
    assert (r2.step, r2.fen_col, r2.fen_points) == ("variable", "R", 5)


def test_points_log_with_at_code_still_wins_over_a_habit_name(monkeypatch):
    headers = {"0n": {"push": 5}, "1n": {}}
    (r,) = df.route_items(df.parse_input("+20 @i9 push"), headers, {}, skip_todoist=True)
    assert (r.step, r.fen_col, r.fen_points, r.item.name) == ("variable", "R", 20, "push")


def test_plain_habit_completion_is_untouched_by_the_0g_column_alias():
    headers = {"0n": {"0g": 20}, "1n": {}}
    (r,) = df.route_items(df.parse_input("0g"), headers, {}, skip_todoist=True)
    assert r.step == "0n"
