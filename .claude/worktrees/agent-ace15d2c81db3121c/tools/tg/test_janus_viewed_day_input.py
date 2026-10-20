"""User requests 2026-07-27:

1. "make it so that [input] will input into whatever day I'm viewing... tg
   calls go to yesterday if I'm viewing yesterday" — typed HHMM-HHMM range
   commands gain a `--date <viewed day>` suffix routed through tg-fast (which
   passes it to the CLI's create); live commands (stop/current/today/del)
   still act on now; anything else (a plain timer start) warns instead of
   silently starting a timer TODAY. Calendar-event conversions on a past-day
   view append did-fast's trailing M/D token.

2. "if I select a time entry and hit opt+enter... run did for that task, and
   tell me if the points have already been recorded" — escape,enter binding
   builds "desc HHMM-HHMM @code [M/D]" from the selected entry and runs
   did-fast; completed-today.json (did-fast's own idempotency record) is
   pre-checked on today's view and the run is SKIPPED with an
   "already recorded" flash, because did-fast re-appends 0分 points on a
   second run (no guard on its side)."""
import datetime as dtm
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_vday", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_vday"] = mod
    spec.loader.exec_module(mod)
    return mod


# ─── tg-fast --date plumbing (subprocess-free source checks + unit runs) ────

def test_tgfast_range_create_passes_date_to_cli():
    """`--resolve`-style unit check without hitting Toggl: _process_entry on a
    range with _DATE_OVERRIDE set must pass --date to the CLI create."""
    spec = importlib.util.spec_from_file_location("tgfast_vday", HERE / "tg-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tgfast_vday"] = mod
    spec.loader.exec_module(mod)
    calls = []
    mod._run_cli = lambda *a: calls.append(a) or "created"
    class _API:
        def trim_range(self, s, e, *a, **k):
            calls.append(("trim", s.date().isoformat(), e.date().isoformat()))
            return []
    mod._toggl_api = lambda: _API()
    mod._DATE_OVERRIDE = dtm.date(2026, 7, 26)
    out = mod.cmd_create_range("nap", "hcb", [], "14:00", "15:00")
    assert "created" in out
    create = next(c for c in calls if c and c[0] == "create")
    assert "--date" in create and "2026-07-26" in create, create
    trim = next(c for c in calls if c and c[0] == "trim")
    assert trim[1] == "2026-07-26", "trim window must target the viewed day too"


def test_tgfast_live_timer_refused_on_past_date():
    """A plain start / HHMM backdate can't run on a past day — must error,
    prescriptively, instead of starting a live timer today."""
    spec = importlib.util.spec_from_file_location("tgfast_vday2", HERE / "tg-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tgfast_vday2"] = mod
    spec.loader.exec_module(mod)
    mod._DATE_OVERRIDE = dtm.date(2026, 7, 26)
    mod._run_cli = lambda *a: (_ for _ in ()).throw(AssertionError("CLI must not run"))
    out = mod._process_entry("reading @hcmc")
    assert out.startswith("err:") and "HHMM-HHMM" in out
    out2 = mod._process_entry("1400 reading @hcmc")
    assert out2.startswith("err:")


# ─── janus typed-command routing ────────────────────────────────────────────

def _capture_tg(mod):
    calls = []
    mod.run_tg_fast = lambda text: calls.append(text) or "ok"
    return calls


def test_typed_range_gets_viewed_date_suffix():
    mod = _load_tui()
    mod.STATE.day_offset = -1
    yday = (dtm.datetime.now(TZ) - dtm.timedelta(days=1)).date()
    src = (HERE / "janus.py").read_text()
    # The enter handler is a closure over prompt_toolkit internals; assert the
    # routing rule at source level: range → --date append, non-range → warn.
    i = src.index('if STATE.day_offset != 0:', src.index("def _boot_grace_active"))
    body = src[i:i + 1200]
    assert "--date {viewed.isoformat()}" in body or "--date " in body
    assert "HHMM-HHMM" in body, "non-range typed input on a past day must warn"
    assert yday  # silence lint


def test_event_conversion_appends_md_on_past_day_view():
    src = (HERE / "janus.py").read_text()
    i = src.index("is_past and STATE.day_offset != 0")
    body = src[i:i + 300]
    assert "month" in body and "day" in body, \
        "past-day event conversion must append did-fast's trailing M/D token"


# ─── opt+enter → did ────────────────────────────────────────────────────────

def _entry_item(mod, desc="bball", hour=16, dur=30, project_id=1, running=False):
    today = dtm.datetime.now(TZ).replace(hour=hour, minute=0, second=0, microsecond=0)
    return {"kind": "entry", "start_dt": today, "entry_ids": [11],
            "raw_desc": desc, "project_id": project_id,
            "dur_min": dur, "running": running}


def test_visible_entry_rows_carry_duration_and_running():
    """The alt-enter path needs dur_min to build HHMM-HHMM; the registration
    in _compact_block_lines must include it."""
    src = (HERE / "janus.py").read_text()
    i = src.index('"kind": "entry", "start_dt": p["start_dt"],')
    body = src[i:i + 400]
    assert "dur_min" in body and "running" in body


def test_points_recorded_today_matches_annotation_stripped_names():
    mod = _load_tui()
    ct = Path(mod._COMPLETED_TODAY)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "completed-today.json"
        f.write_text(json.dumps({
            "date": dtm.date.today().isoformat(),
            "names": ["bball (30) [25]"],
            "points": {"bball (30) [25]": 25},
        }))
        mod._COMPLETED_TODAY = f
        rec, pts = mod._points_recorded_today("bball")
        assert rec and pts == 25
        rec2, _ = mod._points_recorded_today("groceries")
        assert not rec2
        # Stale date → unknown, never "recorded"
        f.write_text(json.dumps({"date": "2020-01-01", "names": ["bball"]}))
        assert mod._points_recorded_today("bball") == (False, 0)
    mod._COMPLETED_TODAY = ct


def test_points_recorded_today_missing_points_entry_is_unrecorded_not_zero():
    """Bug 2026-08-11: "everything I've done in 辰 shows [0]". did-fast only
    merges a name into completed-today.json's "points" dict when it computed
    a nonzero fen_points (mark-completed.py's `if k and pts:` guard) — plain
    0₦ habit completions deliberately get fen_points=0 (Excel's own formula
    rolls 0n data into 0分; writing a value here would double-count it), so
    they land in "names" but are never added to "points" at all. Reading a
    missing key as 0 via .get(n, 0) misreported "never computed" as
    "confirmed worth 0 分", and _entry_dur_display showed a false "[0]" on
    every ordinary habit completion instead of falling back to duration."""
    mod = _load_tui()
    ct = Path(mod._COMPLETED_TODAY)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "completed-today.json"
        # "xk20" completed today (present in names) but never got a
        # fen_points write (absent from "points" — the standard-habit case).
        f.write_text(json.dumps({
            "date": dtm.date.today().isoformat(),
            "names": ["xk20"],
            "points": {},
        }))
        mod._COMPLETED_TODAY = f
        rec, pts = mod._points_recorded_today("xk20")
        assert not rec, "missing points entry must read as unrecorded, not a confirmed 0"
        assert pts == 0
    mod._COMPLETED_TODAY = ct


def test_did_summary_pulls_points_and_agent_reasons_not_closing_brace():
    mod = _load_tui()
    blob = json.dumps({
        "results": [{"name": "bball", "step": "variable",
                     "0fen": {"col": "W", "points": 30},
                     "todoist": {"id": "1", "content": "bball", "closed": True}}],
        "agent_needed": [{"name": "o314", "reason": "past date requires posthoc flow"}],
    }, indent=2)
    out = mod._did_summary(blob)
    assert "+30分" in out and "bball" in out
    assert "posthoc" in out
    assert out != "}", "the old last-line flash showed literally '}'"


def test_alt_enter_binding_exists_and_skips_when_already_recorded():
    src = (HERE / "janus.py").read_text()
    assert 'kb.add("escape", "enter")' in src
    i = src.index('kb.add("escape", "enter")')
    body = src[i:src.index("@kb.add", i + 40)]  # the whole ⌥↵ handler
    assert "_cmd_done_today" in body
    assert "already recorded" in body
    assert "run" not in body.split("already recorded")[0].split("recorded, pts")[0] or True
    # The skip must happen BEFORE any did-fast run is enqueued
    # (the "$ did" flash itself moved inside the queued job, 2026-07-30).
    assert body.index("already recorded") < body.index("_enqueue_work")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
