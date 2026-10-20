"""Late wake-up: the overnight 睡觉 entry starts at 00:00 (outside every
block), so blocks it spills into (辰 onward) must synthesize a 睡觉 pick
covering the slept portion instead of rendering it as missing time."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_sleep", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_sleep"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_partial_spillover_wake_after_six():
    """Wake 07:03 → 辰 (6-8) gets a 63m 睡觉 pick ending at the wake time."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=7, minute=3))]
    item = mod._block_sleep_item(6, 7, today.replace(hour=10))
    assert item is not None
    assert item["label"] == "睡觉"
    assert item["dur_min"] == 63
    assert item["time_str"] == "07:03"  # sleep end-time convention
    assert item["start_dt"] == today.replace(hour=6)


def test_fully_slept_block():
    """Wake 10:30 → 辰 is entirely sleep: full 120m, clipped at block end."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=10, minute=30))]
    item = mod._block_sleep_item(6, 7, today.replace(hour=12))
    assert item["dur_min"] == 120
    assert item["time_str"] == "08:00"


def test_no_spillover_on_early_wake():
    """Wake 05:40 (inside 卯) → 辰 gets no synthetic sleep pick."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=5, minute=40))]
    assert mod._block_sleep_item(6, 7, today.replace(hour=10)) is None


def test_non_sleep_spillover_ignored():
    """A long non-sleep entry crossing the block boundary is not relabelled."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=5), today.replace(hour=7)),
    ]
    assert mod._block_sleep_item(6, 7, today.replace(hour=10)) is None


def test_sleep_cont_marks_fully_slept_block():
    """Sleeping clean through 辰 (6-8) marks every half-hour as a continuation."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=10, minute=30))]
    cont = mod._block_sleep_cont(6, today.replace(hour=12))
    assert set(cont.keys()) == {(6, 0), (6, 30), (7, 0), (7, 30)}


def test_sleep_cont_stops_at_wake():
    """Wake 06:30 in 辰 → only the 06:00 mark is covered, not the rest."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=6, minute=30))]
    cont = mod._block_sleep_cont(6, today.replace(hour=12))
    assert set(cont.keys()) == {(6, 0)}


def test_sleep_item_dropped_when_it_lines_up_with_the_next_real_entry():
    """User report 2026-09-07: "I don't want Janus to have an extra line at
    the cutover between blocks if there are already time entries that
    straddle that border... It should be :51, then 巳:09, without a line in
    between." Wake exactly where the next entry ("generic placeholder")
    starts (08:09) must not ALSO get its own synthetic 巳:00 睡觉 row --
    that entry's own start time already proves sleep ran right up until
    then. _block_spill_items' results already get this treatment via
    _drop_redundant_spill (2026-08-10); _block_sleep_item's synthetic pick
    never did, so it must independently pass the same check."""
    mod = _load_tui()
    today = _midnight()
    sleep_end = today.replace(hour=8, minute=9, second=17)
    mod.STATE.entries = [
        _entry("睡觉", today, sleep_end),
        _entry("generic placeholder", sleep_end, today.replace(hour=9, minute=59)),
    ]
    picks = mod._past_block_picks("巳", [
        {"start_dt": e["start_dt"], "end_dt": e["end_dt"], "desc": e["desc"],
         "project_id": e["project_id"], "running": False, "ids": [e["id"]],
         "tags": []}
        for e in mod.STATE.entries if e["desc"] != "睡觉"])
    sleep = mod._block_sleep_item(8, 9, today.replace(hour=10))
    assert sleep is not None
    assert mod._drop_redundant_spill([sleep], picks) == [], (
        "a wake time landing exactly on the next real entry's start must "
        "not survive as a duplicate synthetic 睡觉 row")


def test_render_morning_no_duplicate_sleep_line_at_block_cutover():
    """Integration: the exact screenshot scenario -- must go straight from
    辰's last 睡觉 row to 巳's real header, no separate '巳:00 睡觉' row."""
    mod = _load_tui()
    today = _midnight()
    sleep_end = today.replace(hour=8, minute=9, second=17)
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = [
        _entry("睡觉", today, sleep_end),
        _entry("generic placeholder", sleep_end, today.replace(hour=9, minute=59)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.detail_window = lambda: (today.replace(hour=11), today.replace(hour=13))
    text = "".join(t for _, t, *_ in mod.render_morning())
    lines = text.split("\n")
    si_lines = [l for l in lines if l.startswith("巳")]
    assert si_lines and si_lines[0].startswith("巳:09"), (
        f"expected 巳's header to be the real :09 entry with no extra 睡觉 "
        f"row before it, got: {si_lines!r}")
    assert not any("睡觉" in l and l.startswith("巳") for l in lines)


def test_render_morning_draws_sleep_continuation():
    """Integration: sleeping through 辰 puts a 睡觉 body row plus ◇ │ continuation
    on the covered marks, under the bare 辰:00 header."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=10, minute=30))]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = []
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    frags = mod.render_morning()
    text = "".join(t for _, t, *_ in frags)
    chen_idx = text.index("辰:00")
    chen_block = text[chen_idx:text.index("巳:00", chen_idx)]
    assert "睡觉" in chen_block
    assert "◇ │" in chen_block, f"expected sleep continuation, got:\n{chen_block}"


def test_render_morning_sleep_and_entry_in_body():
    """Integration: wake 07:03, then a 2-minute UNTRACKED gap before 新闻
    starts at 07:05 → 辰 header stays at the bare :00 stamp, and both 睡觉
    and 新闻 sit in the body. (Updated 2026-09-07: the original version of
    this test had 新闻 start at 07:03 with NO gap after wake — exactly
    lining up with the wake time, which the 2026-09-07 dedup fix now
    correctly recognizes as fully redundant and drops; see
    test_sleep_item_dropped_when_it_lines_up_with_the_next_real_entry and
    test_render_morning_no_duplicate_sleep_line_at_block_cutover for that
    case. This test now covers the genuinely-distinct case: a real gap
    between wake and the next entry, where sleep is NOT redundant and must
    still show.)"""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=7, minute=3)),
        _entry("新闻", today.replace(hour=7, minute=5), today.replace(hour=7, minute=30)),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.detail_window = lambda: (today.replace(hour=8), today.replace(hour=12))
    frags = mod.render_morning()
    text = "".join(t for _, t, *_ in frags)
    chen = [ln for ln in text.split("\n") if ln.startswith("辰:00")]
    assert chen, f"expected the bare 辰:00 stamp, got lines: {[l for l in text.split(chr(10)) if '辰' in l]}"
    chen_idx = text.index("辰:00")
    next_hdr = min(i for i in (text.find("巳", chen_idx), len(text)) if i >= 0)
    chen_block = text[chen_idx:next_hdr]
    assert "睡觉" in chen_block and "新闻" in chen_block, (
        f"a genuine gap must keep both entries visible: {chen_block!r}")


def test_render_morning_keeps_sleep_when_gap_follows_wake():
    """Sanity companion to the dedup test above: the wake time itself must
    still line up with the FULL clipped sleep duration even when a gap
    follows it -- the dedup only drops the row when nothing else changed
    about it, not the sleep computation itself."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=7, minute=3)),
        _entry("新闻", today.replace(hour=7, minute=5), today.replace(hour=7, minute=30)),
    ]
    item = mod._block_sleep_item(6, 7, today.replace(hour=10))
    assert item is not None
    assert item["dur_min"] == 63
    assert item["time_str"] == "07:03"
