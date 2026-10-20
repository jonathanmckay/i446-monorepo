"""User request 2026-07-28: tag the current entry with a media value tag
from janus and earn 媒分 for it ("-2 means I explicitly want the points ...
0.1/m for -1, 1/m for -3, 0.5/m for -2"), prefill the entry's current
HHMM-HHMM so retiming is editing digits, and right-justify calendar entries
so the two sources read as separate columns.

Credits fire ONLY for tags added through janus's edit flow — /tg shortcodes
auto-tag entries (睡觉 -3, hiit -2) and blanket-crediting every tagged entry
would hand sleep ~400分/day of 媒."""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_tags", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_tags"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


# ─── parsing ────────────────────────────────────────────────────────────────

def test_parse_edit_text_extracts_hash_tags():
    mod = _load_tui()
    desc, code, tr, tags = mod._parse_edit_text("eat @hcb 0745-0800 #-2")
    assert (desc, code, tr) == ("eat", "hcb", ("0745", "0800"))
    assert tags == ["-2"]


def test_parse_edit_text_bare_tag_leaves_rest_unchanged():
    mod = _load_tui()
    desc, code, tr, tags = mod._parse_edit_text("#-2")
    assert desc is None and code is None and tr is None
    assert tags == ["-2"]


# ─── minute credits (correction 2026-07-28: write MINUTES to the tag's own
# 0n column — AV/-1, AW/-2, AX/-3 — the sheet's formulas apply the ratio) ───

def test_value_tags_whitelist():
    mod = _load_tui()
    assert mod.VALUE_TAGS == ("-1", "-2", "-3")


def test_apply_tag_credit_writes_minutes_to_0n_tag_column(monkeypatch):
    mod = _load_tui()
    writes = []
    monkeypatch.setattr(mod, "_tag_col", lambda tag: {"-1": "AV", "-2": "AW", "-3": "AX"}[tag])
    monkeypatch.setattr(mod.neon_excel, "append",
                        lambda sheet, col, *, date, value: writes.append((sheet, col, date, value)))
    assert mod._apply_tag_credit("-2", 30, dtm.date(2026, 7, 28)) == 30
    assert writes == [("0n", "AW", "7/28", "+30")], \
        "minutes (not points) go to the tag's own 0n column"
    assert mod._apply_tag_credit("-2", 0, dtm.date(2026, 7, 28)) is None
    assert len(writes) == 1, "zero minutes must not write"


# ─── pending resolution ─────────────────────────────────────────────────────

def test_pending_credit_resolves_when_entry_stops(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.TAG_CREDITS = tmp_path / "janus-tag-credits.json"
    today = _midnight()
    mod.STATE.entries = [{
        "id": 42, "desc": "eat", "project_id": 1, "running": False,
        "start_dt": today.replace(hour=12), "end_dt": today.replace(hour=12, minute=30),
        "tags": ["-2"],
    }]
    mod.STATE.entries_yday = []
    mod.TAG_CREDITS.write_text(json.dumps({
        "date": dtm.date.today().isoformat(),
        "credited": [], "pending": [{"key": "42:-2", "id": 42, "tag": "-2"}],
    }))
    applied = []
    monkeypatch.setattr(mod, "_apply_tag_credit",
                        lambda tag, mins, day: applied.append((tag, mins)) or 15.0)
    mod._resolve_pending_tag_credits()
    assert applied == [("-2", 30)]
    st = json.loads(mod.TAG_CREDITS.read_text())
    assert st["pending"] == [] and "42:-2" in st["credited"]


def test_pending_credit_stays_queued_while_running(tmp_path, monkeypatch):
    mod = _load_tui()
    mod.TAG_CREDITS = tmp_path / "janus-tag-credits.json"
    today = _midnight()
    mod.STATE.entries = [{
        "id": 42, "desc": "eat", "project_id": 1, "running": True,
        "start_dt": today.replace(hour=12), "end_dt": today.replace(hour=12, minute=10),
        "tags": ["-2"],
    }]
    mod.STATE.entries_yday = []
    mod.TAG_CREDITS.write_text(json.dumps({
        "date": dtm.date.today().isoformat(),
        "credited": [], "pending": [{"key": "42:-2", "id": 42, "tag": "-2"}],
    }))
    monkeypatch.setattr(mod, "_apply_tag_credit",
                        lambda *a: (_ for _ in ()).throw(AssertionError("must not credit a running entry")))
    mod._resolve_pending_tag_credits()
    st = json.loads(mod.TAG_CREDITS.read_text())
    assert len(st["pending"]) == 1, "running entry keeps its queued credit"


def test_deleted_entry_drops_its_pending_credit(tmp_path):
    mod = _load_tui()
    mod.TAG_CREDITS = tmp_path / "janus-tag-credits.json"
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.TAG_CREDITS.write_text(json.dumps({
        "date": dtm.date.today().isoformat(),
        "credited": [], "pending": [{"key": "9:-2", "id": 9, "tag": "-2"}],
    }))
    mod._resolve_pending_tag_credits()
    assert json.loads(mod.TAG_CREDITS.read_text())["pending"] == []


def test_stale_credit_file_resets_by_date(tmp_path):
    mod = _load_tui()
    mod.TAG_CREDITS = tmp_path / "janus-tag-credits.json"
    mod.TAG_CREDITS.write_text(json.dumps({
        "date": "2020-01-01", "credited": ["old:-2"], "pending": [{"key": "x"}]}))
    st = mod._tag_credit_load()
    assert st["credited"] == [] and st["pending"] == []


# ─── retime prefill ─────────────────────────────────────────────────────────

def test_prefill_includes_current_range_for_single_completed_entry():
    mod = _load_tui()
    today = _midnight()
    item = {"kind": "entry", "start_dt": today.replace(hour=7, minute=45),
            "entry_ids": [1], "raw_desc": "eat", "project_id": None,
            "dur_min": 15, "running": False}
    assert mod._entry_edit_prefill(item) == "eat 0745-0800"


def test_prefill_omits_range_for_merged_rows():
    mod = _load_tui()
    today = _midnight()
    merged = {"kind": "entry", "start_dt": today.replace(hour=7),
              "entry_ids": [1, 2], "raw_desc": "eat", "project_id": None,
              "dur_min": 30, "running": False}
    assert mod._entry_edit_prefill(merged) == "eat", "merged rows can't retime"


def test_prefill_open_ended_for_running_row():
    """2026-08-11: a running row's prefill used to omit the range entirely
    ("eat"), which read exactly like typing a brand-new command — no
    signal that Enter would edit the entry that's live right now. It now
    carries the start time with a dangling dash ("eat 0700-"); blank end
    means "now" once resubmitted (see _parse_edit_text)."""
    mod = _load_tui()
    today = _midnight()
    running = {"kind": "entry", "start_dt": today.replace(hour=7),
               "entry_ids": [3], "raw_desc": "eat", "project_id": None,
               "dur_min": 5, "running": True}
    assert mod._entry_edit_prefill(running) == "eat 0700-"


# ─── calendar right-justification ───────────────────────────────────────────

def test_event_rows_right_justified_entry_rows_left():
    mod = _load_tui()
    today = _midnight()
    picks = [
        {"start_dt": today.replace(hour=8, minute=5), "time_str": "08:05",
         "label": "wash cloth", "style": "", "dur_min": 25,
         "entry_ids": [1], "raw_desc": "wash cloth", "project_id": None},
        {"start_dt": today.replace(hour=8, minute=30), "time_str": "08:30",
         "label": "XTECH LT", "style": "", "dur_min": 30, "is_event": True,
         "event": {"start_dt": today.replace(hour=8, minute=30),
                   "end_dt": today.replace(hour=9), "title": "XTECH LT"}},
    ]
    lines = "".join(t for _, t, *_ in mod._compact_block_lines("巳", 8, picks, 0, "")).split("\n")
    entry_line = next(l for l in lines if "wash cloth" in l)
    event_line = next(l for l in lines if "XTECH LT" in l)
    assert entry_line.index("wash cloth") < 10, "Toggl entries stay left-justified"
    assert event_line.rstrip().endswith("(30)")
    assert event_line.index("XTECH LT") > 20, \
        f"calendar entries must right-justify: {event_line!r}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
