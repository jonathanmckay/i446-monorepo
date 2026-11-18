"""2neon: row↔card matching, due strings, anchoring, audit, sync (no Excel, no network)."""
import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load("neon2", HERE / "2neon.py")
monthly = m.monthly
defer = _load("defer_fast_2neon", HERE.parent / "did" / "defer-fast.py")


def _row(name, day=7, minutes=10, points=20, **actuals):
    return {"row": 70, "day": day, "name": name, "minutes": minutes, "points": points,
            "actuals": {int(k[1:]): v for k, v in actuals.items()}}


def _card(content, due_string="every 7th", due="2026-12-07", labels=("5^2₦", "g245"), tid="t1", recurring=True):
    return {"id": tid, "content": content, "labels": list(labels),
            "due": {"string": due_string, "date": due, "is_recurring": recurring}}


def _done(content, due, when):
    return {"event_date": when + "T10:00:00Z",
            "extra_data": {"content": content, "completed_due_date_local": due + "T23:59:59"}}


# ── matching ──

def test_card_key_keeps_parentheses_that_belong_to_the_name():
    assert monthly.card_key("5^2 s (上月的) (15) [60]") == ("5^2 s (上月的)", None)
    assert monthly.card_key("Special Event (s) (240) [200] @s897") == ("special event (s)", None)


def test_card_key_reads_a_stamp_before_or_after_the_name_parenthesis():
    assert monthly.card_key("more hair 10.17 (5) [10]") == ("more hair", 10)
    assert monthly.card_key("5^2 s 10.3 (上月的) (15) [60]") == ("5^2 s (上月的)", 10)


def test_row_name_for_uses_aliases_and_refuses_posthoc_records():
    names = ["1 ₦ efficiency", "more hair"]
    assert monthly.row_name_for("5^2 1 n Efficiency Clearance (5) [25]", names)[0] == "1 ₦ efficiency"
    assert monthly.row_name_for("deferred: more hair (5) [10] → 2026-11-20 [0]", names)[0] is None


# ── due strings and anchoring ──

def test_due_string_ordinals_and_month_end():
    assert [m.due_string(d) for d in (1, 2, 3, 11, 12, 13, 21, 22, 28)] == [
        "every 1st", "every 2nd", "every 3rd", "every 11th", "every 12th", "every 13th",
        "every 21st", "every 22nd", "every 28th"]
    assert m.due_string(29) == m.due_string(31) == "every last day"


def test_first_due():
    today = date(2026, 11, 17)
    assert m.first_due(7, today) == date(2026, 12, 7)                         # new card, day passed
    assert m.first_due(24, today) == date(2026, 11, 24)                       # new card, day ahead
    assert m.first_due(7, today, date(2026, 12, 6)) == date(2026, 12, 7)      # done for November
    assert m.first_due(7, today, date(2026, 11, 20)) == date(2026, 11, 20)    # still pending: untouched
    assert m.first_due(7, today, date(2026, 11, 6)) == date(2026, 11, 7)      # overdue stays overdue
    assert m.first_due(1, today, date(2027, 5, 27)) == date(2026, 12, 1)      # parked a year out
    assert m.first_due(31, today) == date(2026, 11, 30)


def test_defer_next_instance_returns_to_the_day_of_month():
    assert defer.next_instance("every 7th", date(2026, 11, 17)) == date(2026, 12, 7)
    assert defer.next_instance("every last day", date(2026, 11, 30)) == date(2026, 12, 31)
    assert defer.next_instance("every 31st", date(2027, 1, 31)) == date(2027, 2, 28)
    assert defer.next_instance("every month", date(2026, 11, 17)) == date(2026, 12, 17)
    assert "5^2₦" in defer.DATED_COPY_LABELS and "5^2₦" not in defer.HABIT_LABELS


# ── audit ──

def test_audit_passes_a_correct_card_and_ignores_one_off_copies():
    rows = [_row("more hair")]
    tasks = [_card("more hair (10) [20]"),
             _card("more hair 10.7 (10) [20]", due_string="2026-11-18", tid="c", recurring=False)]
    f = m.audit(rows, tasks, date(2026, 11, 17))[0]
    assert f["problems"] == [] and f["copies"] == 1


def test_audit_plans_a_create_for_a_row_with_no_card():
    f = m.audit([_row("Snail mail Triage", day=8, minutes=None)], [], date(2026, 11, 17))[0]
    assert f["fix"] == {"op": "create", "content": "Snail mail Triage (20) [20]",
                        "labels": ["5^2₦", "i447"], "due_string": "every 8th", "due_date": "2026-12-08"}


def test_audit_repairs_points_label_and_due_but_never_renames_a_wired_card():
    rows = [_row("1 ₦ efficiency", day=15, minutes=5, points=25)]
    tasks = [_card("5^2 1 n Efficiency Clearance (5) [20]", due_string="every month",
                   due="2026-12-11", labels=("airplane", "g245"))]
    fix = m.audit(rows, tasks, date(2026, 11, 17))[0]["fix"]
    assert fix["content"] == "5^2 1 n Efficiency Clearance (5) [25]"
    assert fix["labels"] == ["airplane", "g245", "5^2₦"]
    assert (fix["due_string"], fix["due_date"]) == ("every 15th", "2026-12-15")


def test_audit_keeps_the_cards_minutes_when_col_c_is_blank():
    rows = [_row("dealflow + 现金", day=16, minutes=None, points=25)]
    tasks = [_card("dealflow + 现金 (15) [25]", due_string="every 16th", due="2026-12-16")]
    assert m.audit(rows, tasks, date(2026, 11, 17))[0]["problems"] == []


def test_audit_flags_two_recurring_cards_without_a_fix():
    f = m.audit([_row("more hair")], [_card("more hair (10) [20]"), _card("more hair (10) [20]", tid="t2")],
                date(2026, 11, 17))[0]
    assert f["fix"] is None and "more than one" in f["problems"][0]


# ── sync ──

def test_credits_use_the_due_month_not_the_completion_month():
    rows = [_row("Plan下月特日")]
    got = m.credits_from([_done("Plan下月特日 (7) [30]", "2026-08-27", "2026-09-04")], rows, 2026)
    assert [(c["name"], c["month"]) for c in got] == [("Plan下月特日", 8)]


def test_a_stamped_copy_credits_the_month_it_stood_for():
    rows = [_row("more hair")]
    got = m.credits_from([_done("more hair 10.17 (5) [10]", "2026-11-02", "2026-11-02")], rows, 2026)
    assert got[0]["month"] == 10


def test_credits_skip_other_years_unknown_cards_and_repeat_months():
    rows = [_row("more hair")]
    ev = [_done("more hair (5) [10]", "2025-12-17", "2025-12-17"),
          _done("something else (5) [10]", "2026-03-01", "2026-03-01"),
          _done("more hair (5) [10]", "2026-03-17", "2026-03-17"),
          _done("more hair 3.17 (5) [10]", "2026-03-20", "2026-03-20")]
    assert [c["month"] for c in m.credits_from(ev, rows, 2026)] == [3]


def test_sync_writes_only_blank_cells_and_honours_skip(monkeypatch):
    calls = []
    monkeypatch.setattr(monthly, "credit_monthly_cell",
                        lambda habit, month, **kw: calls.append((habit, month)) or
                        {"ok": True, "row": 76, "col": "N", "value": 10})
    rows = [_row("more hair", m9=10), _row("2 hci")]
    ev = [_done("more hair (5) [10]", "2026-09-17", "2026-09-17"),
          _done("more hair (5) [10]", "2026-10-17", "2026-10-17"),
          _done("2 hci (15) [15]", "2026-10-26", "2026-10-27")]
    out = m.sync(rows, ev, 2026, skip=("2 hci",))
    assert calls == [("more hair", 10)] and out[0]["cell"] == "N76"


def test_block_rows_reads_definitions_and_actuals(monkeypatch):
    fake = types.SimpleNamespace(read_range=lambda sheet, rng: {"ok": True, "row": 61, "rows": [
        ["", "", "()", "₹", "1.0", "2.0"],
        ["7.0", "more\xa0hair ", "5.0", "10.0", "10.0", ""],
        ["", "", "", "", "", ""]]})
    import neon
    monkeypatch.setattr(neon, "excel", fake, raising=False)
    monkeypatch.setitem(sys.modules, "neon.excel", fake)
    assert monthly.block_rows() == [{"row": 62, "day": 7, "name": "more hair", "minutes": 5,
                                     "points": 10, "actuals": {1: 10, 2: None}}]
