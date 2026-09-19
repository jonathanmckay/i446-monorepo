"""User report 2026-07-27 (follow-up to the near-now Tab fix): "I still
don't see any of the running in 亥 — the title is missing." The day's run
was two Toggl entries, 19:37-19:59 and 19:59-21:00 @hcbp. Two distinct
holes hid them:

1. Picks are assigned to an entry's START block only, so the 19:59 run's
   20:00-21:00 portion rendered in 亥 as anonymous ◇ │ continuation marks
   with no title. Only 睡觉 had spillover handling (_block_sleep_item).
   _block_spill_items generalizes it: any entry crossing the block start
   gets a clipped, titled row carrying the real entry id (selectable, and
   ⌥↵ can grant the spilled portion's points).

2. The over-full-block row cap kept the chronologically-FIRST max_rows
   items, silently dropping the merged 23m run from 戌's 3-row card in
   favor of two sub-10m entries. The cap now keeps the important rows
   (running first, then by duration), re-sorted chronologically."""
import datetime as dtm
import importlib.util
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_spill", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_spill"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup(mod):
    mod.STATE.day_offset = 0
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.current = None
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.STATE.entries = []


def _entry(desc, start, end, pid=1, eid=1, running=False):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": pid, "running": running, "id": eid}


def test_spill_item_clipped_titled_and_selectable():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.entries = [_entry("run", today.replace(hour=19, minute=59),
                                today.replace(hour=21, minute=0), eid=42)]
    cutoff = today.replace(hour=21, minute=20)
    items = mod._block_spill_items(20, 21, cutoff)  # 亥
    assert len(items) == 1
    it = items[0]
    assert it["start_dt"] == today.replace(hour=20)
    assert it["dur_min"] == 60
    assert "run" in it["label"]
    assert it["entry_ids"] == [42], "must carry the real id for selection/⌥↵"


def test_spill_excludes_sleep_and_non_crossing_entries():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today.replace(hour=19), today.replace(hour=23), eid=1),
        _entry("inside", today.replace(hour=20, minute=5),
               today.replace(hour=20, minute=30), eid=2),
    ]
    assert mod._block_spill_items(20, 21, today.replace(hour=21)) == []


def test_drop_redundant_spill_ignores_seconds_on_the_boundary():
    """User report 2026-09-06: "janus shows multiple entries when a time
    entry goes across a block. It should just show 1." A 14:41:03-16:32:07
    entry spanning 申→酉, immediately followed by a 16:32:07-18:27 entry —
    the exact same shape as the 2026-08-10 dedup case, just with realistic
    SECOND-level timestamps (real Toggl entries are almost never exactly on
    the minute).

    _block_spill_items' dur_min is minute-truncated
    (int(total_seconds // 60)), so the spill's reconstructed clipped end
    (start_dt + dur_min minutes) undershoots the entry's true end by up to
    59s. _drop_redundant_spill's OLD exact-datetime-equality check then
    never matched the next entry's full-precision start_dt, so the already-
    shown entry survived as a visible duplicate spill row right before the
    real one it was supposed to be deduped against."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.entries = [
        _entry("to mt davidson", today.replace(hour=14, minute=0),
               today.replace(hour=14, minute=40), eid=1),
        _entry("mt. davidson with Salem", today.replace(hour=14, minute=41, second=3),
               today.replace(hour=16, minute=32, second=7), eid=2),
        _entry("stern grove", today.replace(hour=16, minute=32, second=7),
               today.replace(hour=18, minute=27), eid=3),
    ]
    cutoff = today.replace(hour=19)
    real_picks = mod._past_block_picks("酉", [
        {**e, "start_dt": e["start_dt"], "end_dt": min(e["end_dt"], cutoff)}
        for e in mod.STATE.entries])
    spill = mod._drop_redundant_spill(mod._block_spill_items(16, 17, cutoff), real_picks)
    assert spill == [], (
        f"the already-shown 'mt. davidson with Salem' must not survive as a "
        f"duplicate spill row just because its true end carried seconds: {spill!r}")


def test_current_block_shows_spilled_run_with_title():
    """End-to-end repro of the report: the 19:59-21:00 run must render a
    titled row in 亥's focus card, not just ◇ │ marks."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    now = today.replace(hour=21, minute=20)
    mod.view_now = lambda: now
    mod.STATE.entries = [
        _entry("run", today.replace(hour=19, minute=37),
               today.replace(hour=19, minute=59), eid=41),
        _entry("run", today.replace(hour=19, minute=59),
               today.replace(hour=21, minute=0), eid=42),
        _entry("snack", today.replace(hour=21, minute=1),
               today.replace(hour=21, minute=15), eid=43),
    ]
    text = "".join(t for _, t, *_ in mod.render_focus_compact())
    hai = text.split("子:00")[0]
    # Updated 2026-09-19: a FINISHED spill whose end is followed within
    # GAP_MIN by the next row (snack at 21:01) is redundant — the run is
    # titled with its full duration in 戌, snack's own start says when it
    # stopped, and 亥 keeps only the ◇ │ continuation marks (user rule
    # 2026-08-10 / 2026-09-17: "there should be zero :00 rows"). A RUNNING
    # spill still gets its titled row (see the 亥:xx test below).
    assert "run" not in hai, f"finished spill must not repeat in 亥:\n{hai}"
    assert "◇ │" in hai, "the spilled hour still shows as continuation marks"
    spilled = [it for it in mod.STATE.visible_events
               if isinstance(it, dict) and it.get("kind") == "entry"
               and it.get("entry_ids") == [42]]
    assert not spilled, "no duplicate selectable row for the finished spill"


def test_spill_item_never_rides_the_header():
    """User report 2026-08-07: 戌's header showed "戌:00 bball 9m" — bball
    was really 酉:47's entry spilling 9 minutes into 戌, already shown with
    its full duration in 酉's own row. Repeating it as 戌's header is a
    double entry. A spill item must never itself become the header — the
    header only ever promotes to a genuinely NEW (non-spill) entry, and
    never past an earlier spill (see test_head0_never_promotes_past_an_
    earlier_spill_item, 2026-08-13, for what happens when the only
    candidate NEW entry starts after the spill)."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    spill = {"start_dt": today.replace(hour=18, minute=0), "time_str": "18:00",
             "label": "bball", "style": "", "dur_min": 9, "entry_ids": [1],
             "raw_desc": "bball", "project_id": None, "is_spill": True}
    new_entry = {"start_dt": today.replace(hour=18, minute=10), "time_str": "18:10",
                 "label": "冥想", "style": "", "dur_min": 15, "entry_ids": [2],
                 "raw_desc": "冥想", "project_id": None}
    frags = mod._compact_block_lines("戌", 18, [spill, new_entry], 0, "")
    text = "".join(t for _, t, *_ in frags)
    header_line = text.split("\n")[0]
    assert "bball" not in header_line, f"the spill item must never ride the header: {header_line!r}"
    assert "bball" in text, "the spill item must still render as a body row"


def test_head0_never_promotes_past_an_earlier_spill_item():
    """User report 2026-08-13: janus's 未 card showed "未:05 ▶ -1t" as its
    FIRST line, with "  :00 XTECH huddle" as its second — the header (a
    LATER time) drawn above an earlier spill row reads as running backward
    in time. head0 (-1t, the block's only genuinely new entry) started
    AFTER the spill (XTECH huddle, spilling in from the previous block) —
    promoting it to the header put a later time above an earlier one. The
    header must fall back to bare (":00", no promoted entry) whenever doing
    so would draw an earlier spill BELOW it; both items then render as
    ordinary chronological body rows, earliest first.

    (This supersedes the 2026-08-07 fix's original test scenario, which
    happened to have the same shape — a spill immediately followed by a
    promotable entry — and accepted the same backward-time header. See
    test_spill_item_never_rides_the_header, updated the same day, for the
    surviving half of that fix: a spill must still never itself BECOME the
    header.)"""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    spill = {"start_dt": today.replace(hour=12, minute=0), "time_str": "12:00",
             "label": "XTECH huddle", "style": "", "dur_min": 4, "entry_ids": [1],
             "raw_desc": "XTECH huddle", "project_id": None, "is_spill": True}
    head0_candidate = {"start_dt": today.replace(hour=12, minute=5), "time_str": "12:05",
                       "label": "-1t", "style": "", "dur_min": 28, "entry_ids": [2],
                       "raw_desc": "-1t", "project_id": None, "is_running": True}
    frags = mod._compact_block_lines("未", 12, [spill, head0_candidate], 28, "")
    text = "".join(t for _, t, *_ in frags)
    lines = text.split("\n")
    header_line = lines[0]
    # 2026-09-17: a header left bare for an earlier spill reads `未:xx`
    # ("waiting on a spill tail"), never `未:00` next to the spill's own :00 row.
    assert "未:xx" in header_line, f"header must stay bare (as :xx), not promote past the spill: {header_line!r}"
    assert "未:00" not in header_line
    assert "XTECH huddle" not in header_line and "-1t" not in header_line
    huddle_idx = next(i for i, l in enumerate(lines) if "XTECH huddle" in l)
    t_idx = next(i for i, l in enumerate(lines) if "-1t" in l)
    assert huddle_idx < t_idx, \
        f"the earlier spill must render above the later entry:\n{text}"
    assert "▶" in lines[t_idx], "the running marker must survive falling back to a body row"


def test_spill_row_shows_no_duration():
    """User report 2026-09-11: "we're still showing two lines for things
    that happen on the hour... since there is other info on the block:00
    line, we can get rid of the time duration for that individual time
    entry." _block_spill_items always clips a spill's start to exactly the
    block's own :00 (see its docstring), so its row sits directly under an
    otherwise-bare ":00" header that already carries the block's own info
    (emoji/分) -- production case: a real "未:00" header showed only
    "4 158分" while the very next line, "  :00 XTECH Products Leads Sync
    120m", repeated the same :00 slot with a second, redundant duration
    number for a meeting whose REAL duration already rendered in full in
    the block it actually started in. The row itself must still render (a
    spill must never itself become the header -- test_spill_item_never_
    rides_the_header) but now bare of a number."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    spill = {"start_dt": today.replace(hour=12, minute=0), "time_str": "12:00",
             "label": "XTECH Products Leads Sync", "style": "", "dur_min": 120,
             "entry_ids": [1], "raw_desc": "XTECH Products Leads Sync",
             "project_id": None, "is_spill": True}
    frags = mod._compact_block_lines("未", 12, [spill], 158, "")
    text = "".join(t for _, t, *_ in frags)
    spill_line = next(l for l in text.split("\n") if "XTECH" in l)
    assert not re.search(r"\d+m\b", spill_line), \
        f"a spill row must not show its own clipped duration: {spill_line!r}"


def test_non_spill_entry_keeps_its_duration():
    """Guard against the spill fix above swallowing ordinary durations too
    -- only is_spill rows go bare; a genuine tracked entry still shows its
    real Nm."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    entry = {"start_dt": today.replace(hour=12, minute=5), "time_str": "12:05",
             "label": "meeting", "style": "", "dur_min": 45,
             "entry_ids": [1], "raw_desc": "meeting", "project_id": None}
    frags = mod._compact_block_lines("未", 12, [entry], 0, "")
    text = "".join(t for _, t, *_ in frags)
    assert "45m" in text, "an ordinary entry must keep its own duration"


def test_row_cap_keeps_biggest_not_earliest():
    """戌's 3-row card: five entries where the LATEST is the second-biggest —
    the cap must drop the smallest of the BODY candidates, not the latest.

    2026-08-06: the block's chronologically-first entry (backboard) now
    always rides the header (see test_janus_compact_blocks.py's widened
    header-promotion rule), so it's no longer a candidate the row cap can
    drop at all — it's guaranteed visible regardless of size. This test
    adds a second small entry (tiny, 2m) that ISN'T first, so there's still
    a genuine cap decision among the remaining body candidates: 4 of them
    for 3 slots, and the cap must drop the smallest of those four (tiny),
    not the latest (run)."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = []
    for name, h, m, dur, eid in [("backboard", 18, 18, 7, 1), ("1 f694", 18, 25, 8, 2),
                                 ("tiny", 18, 30, 2, 5),
                                 ("figure out space", 18, 38, 51, 3), ("run", 19, 37, 23, 4)]:
        picks.append({"start_dt": today.replace(hour=h, minute=m),
                      "time_str": f"{h}:{m:02d}", "label": name, "style": "",
                      "dur_min": dur, "entry_ids": [eid], "raw_desc": name,
                      "project_id": None})
    frags = mod._compact_block_lines("戌", 18, picks, 0, "")
    text = "".join(t for _, t, *_ in frags)
    assert "backboard" in text.split("\n")[0], "the chronologically-first entry rides the header"
    assert "run" in text and "figure out space" in text and "1 f694" in text
    assert "tiny" not in text, "the smallest of the BODY candidates is the one to drop"


def test_row_cap_never_drops_the_running_entry():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = [{"start_dt": today.replace(hour=18, minute=5 * i),
              "time_str": "", "label": f"e{i}", "style": "",
              "dur_min": 30 + i, "entry_ids": [i], "raw_desc": f"e{i}",
              "project_id": None} for i in range(4)]
    picks.append({"start_dt": today.replace(hour=19, minute=50), "time_str": "",
                  "label": "live", "style": "", "dur_min": 1, "is_running": True,
                  "entry_ids": [9], "raw_desc": "live", "project_id": None})
    text = "".join(t for _, t, *_ in mod._compact_block_lines("戌", 18, picks, 0, ""))
    assert "live" in text, "the running row survives the cap regardless of duration"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def _pick(label, start, dur, eid, **kw):
    return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": label, "style": "",
            "dur_min": dur, "entry_ids": [eid], "raw_desc": label, "project_id": None, **kw}


def test_trimmed_spill_does_not_leave_the_header_bare():
    """User report 2026-09-17: 辰's card showed a bare "辰:00" header over
    lego / streamline bag / 一起饭, and the 14m "take photos…" entry was
    missing — "this block should have the time entry for taking photos… it's
    just blank… janus should use all 4 lines". The 0t tail spilling in at
    :00 (6m) sat before head0, so the header was vacated for chronology's
    sake, and then the 3-row body cap trimmed that very spill as the
    shortest row. A spill that cannot survive the cap must not cost the
    block its header line: drop it and let head0 ride the header."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    h = lambda hh, mm: today.replace(hour=hh, minute=mm)  # noqa: E731
    picks = [
        _pick("0t", h(6, 0), 6, 1, is_spill=True),
        _pick("take photos of and track tech stack", h(6, 25), 14, 2),
        _pick("lego", h(6, 40), 39, 3),
        _pick("streamline bag", h(7, 19), 15, 4),
        _pick("一起饭", h(7, 36), 24, 5),
    ]
    frags = mod._compact_block_lines("辰", 6, picks, 251, "")
    lines = [ln for ln in "".join(t for _, t, *_ in frags).split("\n") if ln]
    assert "take photos" in lines[0] and "辰:25" in lines[0], \
        f"head0 must ride the header once the spill can't make the cut:\n{lines}"
    assert "0t" not in "".join(lines), "the un-showable spill is dropped, not left as a phantom"
    body = "\n".join(lines[1:])
    for name in ("lego", "streamline bag", "一起饭"):
        assert name in body, f"{name} missing from body:\n{lines}"
    assert len(lines) == 4, f"all four lines describe the block:\n{lines}"


def test_surviving_earlier_spill_still_keeps_the_header_bare():
    """The 2026-08-13 rule is untouched when the spill DOES fit: with room
    in the body, the earlier spill renders as a row and head0 stays below."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    spill = _pick("XTECH huddle", today.replace(hour=12), 4, 1, is_spill=True)
    later = _pick("-1t", today.replace(hour=12, minute=5), 28, 2)
    frags = mod._compact_block_lines("未", 12, [spill, later], 28, "")
    lines = "".join(t for _, t, *_ in frags).split("\n")
    assert "未:xx" in lines[0] and "-1t" not in lines[0]
    assert any("XTECH huddle" in ln for ln in lines[1:])


# ── 2026-09-17: zero ":00" rows; the block's first row rides the header ──────

def _gap(start, dur):
    return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": "", "style": "",
            "dur_min": dur, "is_gap": True}


def test_untracked_gap_as_first_row_rides_the_header():
    """User report 2026-09-17: 未 showed a bare "未:00" header, then ":00 MBR
    debrief" (a spill), then ":10 empty → 12:24". "There should be zero
    [:00 rows] and the first time entry in this block should be 未:10." A
    gap that is the block's chronologically first row now rides the header
    like any entry would."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = [_gap(today.replace(hour=12, minute=10), 14),
             _pick("o314", today.replace(hour=12, minute=24), 18, 2, is_running=True)]
    frags = mod._compact_block_lines("未", 12, picks, 19, "", max_rows=8, track_selection=True)
    lines = "".join(t for _, t, *_ in frags).split("\n")
    assert lines[0].startswith("未:10") and "empty → 12:24" in lines[0], lines[0]
    assert "19分" in lines[0], "the block's points still ride the right edge of the header"
    assert not any(ln.lstrip().startswith(":10 ") for ln in lines[1:]), \
        f"the gap must not ALSO render as a body row:\n{lines}"
    assert any("o314" in ln for ln in lines[1:])
    assert any(v.get("kind") == "empty" for v in mod.STATE.visible_events), \
        "the header gap stays selectable (Enter → fill this stretch)"


def test_finished_spill_is_dropped_when_a_gap_starts_where_it_ends():
    """The spilled MBR-debrief tail (11:35 → 12:10, already shown with its
    full 25m in 午) is redundant once a gap row starts at 12:10 — the gap's
    own start time says when the debrief ended. Whole-day render: 未's
    header becomes the 12:10 gap and "MBR debrief" appears exactly once."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    h = lambda hh, mm: today.replace(hour=hh, minute=mm)  # noqa: E731
    mod.STATE.entries = [
        _entry("minecraft weekly standup", h(10, 46), h(11, 35), eid=1),
        _entry("MBR debrief", h(11, 35), h(12, 10), eid=2),
        _entry("o314", h(12, 24), h(12, 42), eid=3),
        _entry("work", h(12, 42), h(13, 59), eid=4),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.day_offset = 1  # whole-day past view
    mod.view_now = lambda: h(15, 0)
    from unittest.mock import patch
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        frags = mod.render_morning(bo_emojis={})
    text = "".join(t for _, t, *_ in frags)
    wei = text.split("未", 1)[1].split("申")[0] if "未" in text else ""
    wei_lines = ["未" + wei.split("\n")[0]] + wei.split("\n")[1:]
    assert text.count("MBR debrief") == 1, f"the spill must not repeat in 未:\n{text}"
    assert wei_lines[0].startswith("未:10") and "empty → 12:24" in wei_lines[0], wei_lines
    assert not any(ln.startswith("  :00") for ln in wei_lines) and not wei_lines[0].startswith("未:00"), \
        f"zero block-start :00 rows on 未's card:\n{wei_lines}"


def test_running_spill_survives_a_row_starting_at_its_clipped_end():
    mod = _load_tui()
    today = _midnight()
    running_spill = _pick("run", today.replace(hour=20), 30, 1, is_spill=True, is_running=True)
    done_spill = _pick("call", today.replace(hour=20), 10, 2, is_spill=True)
    others = [_gap(today.replace(hour=20, minute=30), 20), _gap(today.replace(hour=20, minute=10), 20)]
    out = mod._drop_spills_covered_by([running_spill, done_spill], others)
    assert out == [running_spill]


def test_running_spill_first_in_block_reads_x_xx_header():
    """User request 2026-09-17: when the block is waiting on a still-running
    entry from the previous block, the header says so — `亥:xx` — with the
    running spill as the first body row."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    spill = _pick("run", today.replace(hour=20), 30, 1, is_spill=True, is_running=True)
    later_gap = _gap(today.replace(hour=20, minute=30), 20)
    frags = mod._compact_block_lines("亥", 20, [spill, later_gap], 0, "", max_rows=8)
    lines = "".join(t for _, t, *_ in frags).split("\n")
    assert lines[0].startswith("亥:xx"), lines[0]
    assert "run" in lines[1] and "▶" in lines[1]


def test_finished_spill_followed_within_gap_min_is_dropped():
    """2026-09-19: "?" spilled into 戌 until 18:11; chinese started 18:12.
    The one-minute sliver is below GAP_MIN so no gap row starts exactly at
    18:11, and the spill survived as a "戌:xx / :00 ?" pair. A later row
    starting within GAP_MIN of the spill's end now covers it too."""
    mod = _load_tui()
    today = _midnight()
    spill = _pick("?", today.replace(hour=18), 11, 1, is_spill=True)
    chinese = _pick("chinese", today.replace(hour=18, minute=12), 153, 2)
    assert mod._drop_spills_covered_by([spill, chinese], []) == [chinese]
    far = _pick("chinese", today.replace(hour=18, minute=30), 30, 3)
    assert mod._drop_spills_covered_by([spill, far], []) == [spill, far]
