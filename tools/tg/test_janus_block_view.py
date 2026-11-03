"""^O block view (JM 2026-11-02): "look at all time entries in a block for
Janus, same way I can look at the details of a task … ctrl+o opens the
block, all time entries are rendered, and esc goes back to the main janus
screen."

The main timeline caps each past block at 3 body rows and absorbs short
entries; the block view must list every raw entry (plus untracked gaps),
register each as selectable so the row actions work there, and Esc must back
out one level at a time (armed edit → block view → main).
"""
import datetime as dtm
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_janus_focus_compact import TZ, _load_tui, _setup_common  # noqa: E402


class _Event:
    app = None


def _binding(mod, key):
    hits = [b for b in mod.kb.bindings if b.keys == (key,)]
    assert hits, f"no binding for {key!r}"
    return hits[0].handler


def _yesterday_at(h, m):
    d = dtm.datetime.now(TZ) - dtm.timedelta(days=1)
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


DESCS = ["standup", "-1g", "coffee", "email triage", "1:1 lx", "prayer"]


def _setup(mod, monkeypatch):
    _setup_common(mod)
    mod.STATE.day_offset = -1
    mod.STATE.event_sel = None
    mod.STATE.block_view = None
    mod.STATE.edit_target = mod.STATE.split_target = mod.STATE.points_edit_target = None
    monkeypatch.setattr(mod, "_row_points", lambda *a, **k: (False, 0))
    spans = [(14, 0, 14, 25), (14, 25, 14, 27), (14, 27, 14, 29),
             (14, 40, 15, 10), (15, 10, 15, 40), (15, 40, 15, 42)]
    mod.STATE.entries = [
        {"id": 100 + i, "desc": d, "project_id": None, "tags": [], "running": False,
         "start_dt": _yesterday_at(a, b), "end_dt": _yesterday_at(c, e)}
        for i, (d, (a, b, c, e)) in enumerate(zip(DESCS, spans))]
    # A neighbouring block's entry must not leak in.
    mod.STATE.entries.append({"id": 999, "desc": "lunch", "project_id": None, "tags": [],
                              "running": False, "start_dt": _yesterday_at(12, 30),
                              "end_dt": _yesterday_at(13, 0)})


def _text(parts):
    return "".join(str(f[1]) for f in parts)


def _select(mod, entry_id):
    item = next(it for it in mod.STATE.visible_events
                if it.get("kind") == "entry" and entry_id in it["entry_ids"])
    mod.STATE.event_sel = mod._sel_key(item)


def test_ctrl_o_lists_every_entry_in_the_selected_rows_block(monkeypatch):
    mod = _load_tui()
    _setup(mod, monkeypatch)
    mod.render_all()
    _select(mod, 100)  # "standup", 申 block
    _binding(mod, "c-o")(_Event())
    assert mod.STATE.block_view == ("申", 14)

    out = _text(mod.render_all())
    for d in DESCS:
        assert d in out, f"{d!r} missing from the block view"
    assert "lunch" not in out
    assert "6 entries" in out
    # 14:29–14:40 and 15:42–16:00 are untracked.
    assert "14:29–14:40" in out and "15:42–16:00" in out

    entries = [it for it in mod.STATE.visible_events if it.get("kind") == "entry"]
    gaps = [it for it in mod.STATE.visible_events if it.get("kind") == "empty"]
    assert [it["entry_ids"] for it in entries] == [[100 + i] for i in range(6)]
    assert [g["dur_min"] for g in gaps] == [11, 18]


def test_esc_cancels_an_armed_edit_before_leaving_the_block_view(monkeypatch):
    mod = _load_tui()
    _setup(mod, monkeypatch)
    mod.STATE.block_view = ("申", 14)
    mod.render_all()
    mod.STATE.points_edit_target = {"desc": "x"}
    esc = _binding(mod, "escape")

    esc(_Event())
    assert mod.STATE.points_edit_target is None
    assert mod.STATE.block_view == ("申", 14)

    esc(_Event())
    assert mod.STATE.block_view is None
    assert mod.STATE.day_offset == -1  # Esc in the view must not also jump to today


def test_ctrl_o_on_a_past_day_needs_a_selection(monkeypatch):
    mod = _load_tui()
    _setup(mod, monkeypatch)
    _binding(mod, "c-o")(_Event())
    assert mod.STATE.block_view is None


def test_ctrl_g_still_fixes_points_not_block_view(monkeypatch):
    mod = _load_tui()
    _setup(mod, monkeypatch)
    mod.render_all()
    _select(mod, 100)
    _binding(mod, "c-g")(_Event())
    assert mod.STATE.block_view is None
