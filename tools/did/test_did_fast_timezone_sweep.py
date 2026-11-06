"""Regression test for did-fast.py's date/time computation routing.

Bug (found live 2026-08-26): the user, physically traveling (system clock
on CEST, not America/Los_Angeles), reported Neon writes landing on the
Pacific-time date instead of the current day. Root cause: `parse_input` —
the entry point every single /did invocation runs through to resolve
"today" for a task's target_date — called bare `date.today()` instead of
the shared `lib/daytime.py` resolution already wired into most of this
file during the international-travel hardening pass a few days earlier.
That earlier pass fixed the module's hardcoded PT `TZ` constant and one
specific day-wrap bug, but missed ~24 other naive date.today()/
datetime.now() call sites still scattered through the file — including
this one, the busiest of them all.

A bare date.today()/datetime.now() call is correct BY COINCIDENCE when
executed on the traveler's own laptop with an OS timezone that auto-follows
physical location (the common case) — which is why this went unnoticed for
days. It is wrong whenever the call happens to execute on a machine that
does NOT travel (ix, the always-on home server, invoked e.g. via
janus-mobile → did-fast.py locally on ix) or under an explicit /travel
override that diverges from the OS's own auto-detected zone.

Fix: swept every remaining date.today()/datetime.now() in did-fast.py to
route through the already-imported `_daytime` module (today()/local_now()).
"""
import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, frozen_now):
    spec = importlib.util.spec_from_file_location("df_tzsweep", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_tzsweep"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._daytime, "local_now", lambda: frozen_now)
    monkeypatch.setattr(mod._daytime, "today", lambda: frozen_now.date())
    return mod


def test_parse_input_target_date_follows_daytime_not_a_different_machine_clock(monkeypatch):
    """Freeze _daytime to a date that would differ from whatever the raw
    OS clock happens to read right now (e.g. if this test runs on a
    machine set to Pacific time) — proves parse_input's default
    target_date comes from the shared _daytime module, not a bare
    date.today() that would ignore an active /travel override or a
    divergent host clock (e.g. did-fast.py invoked locally on ix, which
    never travels and stays on Pacific time)."""
    frozen = datetime(2026, 3, 7, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    mod = _load(monkeypatch, frozen)

    items = mod.parse_input("hiit")
    assert len(items) == 1
    assert items[0].target_date == "3/7", (
        f"expected target_date to come from the frozen _daytime clock "
        f"(3/7), got {items[0].target_date!r} — parse_input is reading "
        f"some other clock, reintroducing the wrong-day bug"
    )


def test_defer_date_resolution_follows_daytime(monkeypatch):
    """The --tmrw defer-date shortcut must resolve relative to _daytime's
    'today', not a different machine's OS clock."""
    frozen = datetime(2026, 3, 7, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    mod = _load(monkeypatch, frozen)

    items = mod.parse_input("some task --tmrw")
    assert len(items) == 1
    assert items[0].defer_date == "2026-03-08"


def test_no_bare_date_today_or_datetime_now_remains():
    """Guard against a future edit reintroducing a naive call anywhere in
    this file — every date/time computation must route through the
    shared _daytime module (lib/daytime.py) so it correctly reflects an
    active /travel override or the OS's own local time, regardless of
    which machine (traveler's laptop vs. ix, which never travels)
    actually executes the code.

    AST-based (not text search): a text/regex search over the raw source
    would also match this file's own docstrings and comments describing
    the bug/fix (which legitimately mention "date.today()" in prose) —
    walking actual Call nodes only inspects real code, not narrative text.
    """
    import ast

    source = (HERE / "did-fast.py").read_text()
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # date.today() (never takes args) and bare datetime.now() (no args) —
        # not _daytime.today()/local_now(). datetime.now(some_tzinfo) is a
        # DIFFERENT, safe pattern (an absolute-instant "now", used for
        # elapsed-duration math against another aware datetime) — it reads
        # the same real instant regardless of the tz argument, so it's not
        # part of the "which calendar day is it" bug class this guards
        # against, and must not be flagged.
        if isinstance(func.value, ast.Name) and func.value.id in ("date", "datetime"):
            has_args = bool(node.args or node.keywords)
            if func.attr == "today" and not has_args:
                offenders.append(f"line {node.lineno}: {func.value.id}.{func.attr}()")
            elif func.attr == "now" and not has_args:
                offenders.append(f"line {node.lineno}: {func.value.id}.{func.attr}()")

    assert not offenders, (
        f"found bare date.today()/datetime.now() call(s) not routed through "
        f"_daytime — this is exactly the bug class this file was swept for "
        f"on 2026-08-26:\n" + "\n".join(offenders)
    )
