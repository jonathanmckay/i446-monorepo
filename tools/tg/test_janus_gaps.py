"""Past-block gap rows: untracked stretches >= GAP_MIN render as their own
body line, labelled "empty → HH:MM (Nm)" (explicit end time + the word
"empty", not just a duration figure), pulsing solid-red-block↔plain-red-text;
the 卯 sleep total is dim like other duration figures, not bold white.

Redesigned 2026-07-11 (user report: the old thin ┄-fill red↔grey flash was too
subtle, never stated when the empty stretch began/ended, and never said the
word "empty" — you had to infer emptiness from dash texture alone). The
2026-07-11 redesign initially padded the label with blank spaces instead of
dashes; restored the ┄ fill on 2026-07-12 (user report: "add back the lines
for each block") since the label otherwise read as floating text in an
unfilled row rather than a highlighted bar."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_gaps", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_gaps"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_block_gaps_sees_stop_resume_on_same_task():
    """A break between two entries of the SAME description must surface —
    the merged display spans would join them, so the sweep uses raw entries."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = True
    mod.STATE.entries = [
        _entry("email", today.replace(hour=8), today.replace(hour=8, minute=40)),
        _entry("email", today.replace(hour=9, minute=10), today.replace(hour=10)),
    ]
    gaps = mod._block_gaps(8, 9, cutoff)  # 巳 block
    assert len(gaps) == 1
    assert gaps[0]["dur_min"] == 30
    assert gaps[0]["time_str"] == "08:40"
    assert gaps[0]["is_gap"] is True


def test_block_gaps_below_threshold_folded():
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = True
    mod.STATE.entries = [
        _entry("a", today.replace(hour=8), today.replace(hour=8, minute=58)),
        _entry("b", today.replace(hour=9, minute=2), today.replace(hour=10)),
    ]
    assert mod._block_gaps(8, 9, cutoff) == []  # 4m < GAP_MIN


def test_block_gaps_fully_empty_block_is_one_full_gap():
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    gaps = mod._block_gaps(8, 9, cutoff)
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 120


def test_block_gaps_spillover_coverage_clips_at_boundary():
    """An entry starting in the prior block still covers this one's start."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = True
    mod.STATE.entries = [
        _entry("deep work", today.replace(hour=7, minute=30), today.replace(hour=9, minute=15)),
        _entry("standup", today.replace(hour=9, minute=45), today.replace(hour=10)),
    ]
    gaps = mod._block_gaps(8, 9, cutoff)
    assert len(gaps) == 1
    assert gaps[0]["time_str"] == "09:15" and gaps[0]["dur_min"] == 30


def test_block_gaps_unconfirmed_entries_never_flash(monkeypatch):
    """Regression (2026-07-15, user report: "janus has time as empty but
    toggl shows it as filled"): a cold-start Toggl 402 (or any failed fetch)
    leaves STATE.entries at its empty default — indistinguishable from a
    genuinely tracked-nothing block. Without entries_known, _block_gaps
    rendered a confident "empty → HH:MM" flash over time Toggl had actually
    filled, just not yet successfully re-fetched. entries_known False must
    suppress ALL gaps, even an otherwise-legitimate one."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = False
    mod.STATE.entries = []  # never fetched, NOT "confirmed empty"
    assert mod._block_gaps(8, 9, cutoff) == []

    # Even with real (stale-looking) entries present, an unconfirmed state
    # must not compute/report gaps around them either — we simply don't know.
    mod.STATE.entries = [
        _entry("email", today.replace(hour=8), today.replace(hour=8, minute=40)),
    ]
    assert mod._block_gaps(8, 9, cutoff) == []


def test_block_gaps_resume_confirmed_after_entries_known_flips_true():
    """Once entries_known flips back to True (the next successful fetch),
    gaps resume normally — this isn't a permanent kill switch."""
    mod = _load_tui()
    today = _midnight()
    cutoff = today.replace(hour=10)
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    gaps = mod._block_gaps(8, 9, cutoff)
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 120


def test_fetch_today_marks_entries_known_on_success_and_unknown_on_failure():
    """fetch_today must set entries_known the same way fetch_current sets
    current_known — the AST shape, since a live network call isn't
    exercised here."""
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_today(")
    i_end = src.index("\n\n\n", i_def)
    body = src[i_def:i_end]
    assert "STATE.entries_known = True" in body
    assert "STATE.entries_known = False" in body
    # The success assignment must come before the failure one in source order
    # (try body, then except block) so a successful read isn't immediately
    # undone by leftover except-block logic.
    assert body.index("STATE.entries_known = True") < body.index("STATE.entries_known = False")


def test_gap_alarm_on_toggles_each_half_second():
    """The flash toggle flips every 0.5s and repeats every 1s, so past gaps
    visibly pulse under the 0.1s repaint."""
    mod = _load_tui()
    from datetime import datetime, timezone
    on0 = mod._gap_alarm_on(datetime.fromtimestamp(0.0, timezone.utc))
    off = mod._gap_alarm_on(datetime.fromtimestamp(0.5, timezone.utc))
    on1 = mod._gap_alarm_on(datetime.fromtimestamp(1.0, timezone.utc))
    assert on0 is True and off is False and on1 is True


def test_gap_row_states_empty_and_end_time_off_phase(monkeypatch):
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: False)  # off phase
    today = _midnight()
    gap = {"start_dt": today.replace(hour=8, minute=40), "time_str": "08:40",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    entry = {"start_dt": today.replace(hour=8), "time_str": "08:00",
             "label": "email", "style": "#888888", "dur_min": 40}
    frags = mod._compact_block_lines("巳", 8, [entry, gap], 0, "")
    text = "".join(t for _, t, *_ in frags)
    assert text.count("\n") == 4, "block must stay exactly 4 lines"
    assert "empty" in text, "gap row must say the word 'empty', not just imply it"
    assert "09:10" in text, "gap row must state its real end time (08:40 + 30m)"
    # Off phase: plain red text, not the solid-block alarm style.
    assert any("empty" in t for s, t, *_ in frags if s == "class:no_entry")
    assert not any("empty" in t for s, t, *_ in frags if "no_entry_bg" in s)


def test_gap_row_pulses_solid_block_when_alarm_on(monkeypatch):
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: True)  # on phase
    today = _midnight()
    gap = {"start_dt": today.replace(hour=8, minute=40), "time_str": "08:40",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    frags = mod._compact_block_lines("巳", 8, [gap], 0, "")
    # On phase: the whole label (padded to width) carries the solid-block
    # style, not plain "no_entry" text and not the old idle grey.
    assert any("empty" in t for s, t, *_ in frags if s == "class:no_entry_bg")
    assert not any(s == "class:no_entry" for s, t, *_ in frags if "empty" in t)
    assert not any("idle" in s for s, t, *_ in frags if "empty" in t)


def test_gap_row_fills_remaining_width_with_dashes(monkeypatch):
    """The gap row's label doesn't just sit in an otherwise-blank line — the
    remaining width fills with a ┄ dash line, same style as the label, so the
    row still reads as one continuous highlighted bar (restored 2026-07-12)."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: True)
    today = _midnight()
    gap = {"start_dt": today.replace(hour=8, minute=40), "time_str": "08:40",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    frags = mod._compact_block_lines("巳", 8, [gap], 0, "")
    body = "".join(t for s, t, *_ in frags if s == "class:no_entry_bg")
    assert "┄" in body, "gap row must fill its remaining width with dashes"
    assert not body.rstrip("\n").endswith(" "), (
        "the row must not end in blank padding once dashes are restored")


def test_gap_fill_helper_dashes_not_spaces():
    mod = _load_tui()
    today = _midnight()
    end = today.replace(hour=8, minute=50)
    label = mod._gap_label(end, 30)
    out = mod._gap_fill(label, 40)
    assert out.startswith(label)
    assert "┄" in out[len(label):]
    assert " " * 3 not in out[len(label):], "fill must be dashes, not runs of spaces"


def test_gap_style_never_uses_reverse_attribute():
    """The gap alarm must not use prompt_toolkit's 'reverse' attribute: the
    whole-screen _NoTimerFlash transformation also toggles 'reverse' (on the
    exact same wall-clock phase, since both derive from the sub-second clock),
    so a gap row styled with 'reverse' would cancel back to normal — or
    scramble — right when no timer is running, i.e. exactly when the user is
    most likely to be staring at gaps to backfill. Solid explicit fg/bg colors
    (no_entry / no_entry_bg) compose safely instead."""
    src = (HERE / "janus.py").read_text()
    i = src.index('"no_entry_bg":')
    line = src[i:src.index("\n", i)]
    assert "reverse" not in line


def test_detail_band_past_gap_states_empty_and_end_time(monkeypatch):
    """In the focus band, an untracked past stretch renders a real gap row
    that says 'empty' and states its true end time (window end here, since
    there are no entries to close it), pulsing solid-block when the alarm is
    on."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: True)  # on phase
    today = _midnight()
    # Window entirely before 'now' → fully elapsed, so the gap is a past row
    # (not the live idle now-row).
    monkeypatch.setattr(mod, "view_now", lambda: today.replace(hour=6, minute=30))
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=4), today.replace(hour=6)))
    mod.STATE.current = None
    mod.STATE.current_known = True
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.scroll_min = 0
    mod.STATE.entries = []  # no toggl entries → the whole window is one gap
    frags = mod.render_detail()
    text = "".join(t for _, t, *_ in frags)
    assert "empty" in text, "an untracked past stretch must say 'empty' outright"
    assert "06:00" in text, "gap must state its real end time (window end)"
    assert any("04:00 │" in t for s, t, *_ in frags), "gap is keyed by its start time"
    assert any(s == "class:no_entry_bg" and "empty" in t for s, t, *_ in frags), (
        "alarm-on phase must render the solid-block style, not plain text")


def test_detail_band_past_gap_muted_when_alarm_off(monkeypatch):
    """Off phase: the same gap row renders plain red text, not the solid-block
    alarm style — proving it pulses rather than sitting permanently loud."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: False)
    today = _midnight()
    monkeypatch.setattr(mod, "view_now", lambda: today.replace(hour=6, minute=30))
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=4), today.replace(hour=6)))
    mod.STATE.current = None
    mod.STATE.current_known = True
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.scroll_min = 0
    mod.STATE.entries = []
    frags = mod.render_detail()
    assert not any(s == "class:no_entry_bg" for s, t, *_ in frags)
    assert any(s == "class:no_entry" and "empty" in t for s, t, *_ in frags)


def test_detail_band_gap_end_time_survives_capped_closing_entry(monkeypatch):
    """A gap's stated end time must be the TRUE closing entry's start, computed
    against full coverage — not the next row actually shown. If the closing
    entry gets absorbed/capped out of `shown`, the next VISIBLE row could be a
    much later entry, which would make the gap look longer than it really was
    (regression guard for the gap_ends-by-full-coverage design)."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: True)
    today = _midnight()
    monkeypatch.setattr(mod, "view_now", lambda: today.replace(hour=6, minute=30))
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=4), today.replace(hour=6)))
    mod.STATE.current = None
    mod.STATE.current_known = True
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.scroll_min = 0
    # A tiny (sub-DETAIL_MIN) entry closes the gap at 04:20, then a real entry
    # runs 04:25-05:00. The tiny entry is absorbed (never shown), but the gap
    # must still report ending at 04:20, not 04:25.
    mod.STATE.entries = [
        _entry("blip", today.replace(hour=4, minute=20), today.replace(hour=4, minute=21)),
        _entry("work", today.replace(hour=4, minute=25), today.replace(hour=5)),
    ]
    frags = mod.render_detail()
    text = "".join(t for _, t, *_ in frags)
    assert "04:20" in text, "gap must end at the true closing entry's start"


def test_gap_never_rides_the_header_rule():
    """All-gap picks (spillover-covered block) render a bare rule + gap rows."""
    mod = _load_tui()
    today = _midnight()
    gap = {"start_dt": today.replace(hour=9, minute=15), "time_str": "09:15",
           "label": "", "style": "", "dur_min": 30, "is_gap": True}
    frags = mod._compact_block_lines("巳", 8, [gap], 0, "")
    header = "".join(t for _, t, *_ in frags).split("\n")[0]
    assert "09:15" not in header, "gap must be a body row, not inline in the rule"
    assert any("no_entry" in s for s, _, *_h in frags)


def test_mao_sleep_total_is_dim_not_bold_white():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("睡觉", today, today.replace(hour=5, minute=31))]
    mod.STATE.entries_yday = []
    frags = mod._mao_line(emojis="")
    sleep_frag = [(s, t) for s, t, *_ in frags if "331m" in t]
    assert sleep_frag, "sleep total missing"
    assert sleep_frag[0][0] == "class:dim"
