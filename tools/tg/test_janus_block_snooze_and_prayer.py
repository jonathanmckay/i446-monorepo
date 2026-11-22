"""User request 2026-07-27: (1) habits block-delayed in dtd (ctrl-v →
dtd-block-snooze.json) must not show in janus's pending habit row until their
chosen block starts — "tasks that are delayed for a different block don't
show up in the 1st few rows" (that day: 1st hci, xk20, xk22, hiit);
(2) ص (prayer count) gets its own always-visible labeled counter chip
("ص 3") instead of drowning as a bare number / pending name."""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_snooze", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_snooze"] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup(mod, tmp_path):
    mod.STATE.day_offset = 0
    mod.STATE.habits_today = []
    mod.STATE.habits_ytd = {}
    mod.STATE.prayer_count = None
    mod.STATE.events = []
    mod.STATE.domain_points = {}
    mod.BLOCK_SNOOZE = tmp_path / "dtd-block-snooze.json"
    mod.SNOOZE_MIRROR_DIR = tmp_path / "mirrors"      # never the real vault
    mod.SNOOZE_MIRROR_DIR.mkdir(exist_ok=True)
    mod._snooze_cache.update(sig=None, at=0.0, docs=[])
    mod.HABIT_CARDS.clear()
    mod.HABIT_DUES.clear()


def _write_snooze(mod, snoozes, date=None):
    mod.BLOCK_SNOOZE.write_text(json.dumps(
        {"date": date or dtm.date.today().isoformat(), "snoozes": snoozes}))
    mod._snooze_cache.update(sig=None, at=0.0)  # bust the TTL cache


def _strip_text(mod):
    return "".join(t for _, t, *_ in mod.render_habits_today())


def test_snoozed_habit_hidden_until_its_block(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None), ("0l", None)]
    future_h = min(23, dtm.datetime.now(TZ).hour + 2)
    _write_snooze(mod, {"id-hiit": future_h})
    text = _strip_text(mod)
    assert "hiit" not in text, "block-snoozed habit must leave the pending row"
    assert "0l" in text, "un-snoozed habits stay"


def test_snoozed_habit_reappears_once_block_starts(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None)]
    _write_snooze(mod, {"id-hiit": dtm.datetime.now(TZ).hour})  # block has started
    assert "hiit" in _strip_text(mod)


def test_stale_snooze_file_ignored(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None)]
    _write_snooze(mod, {"id-hiit": 23}, date="2020-01-01")
    assert "hiit" in _strip_text(mod), "yesterday's snoozes must not hide today"


def test_done_habit_is_off_the_strip_snoozed_or_not(tmp_path):
    """A habit with a value is done, so it leaves the strip (2026-11-10);
    a snooze on its card changes nothing either way."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", 10.0)]
    _write_snooze(mod, {"id-hiit": 23})
    assert "10" not in _strip_text(mod) and "hiit" not in _strip_text(mod)


def test_prayer_counter_closes_the_domain_points_row(tmp_path):
    """JM 2026-11-22: "for the prayers, we can put that down in the third
    row". ص leaves the pending line and closes the 分-by-domain row."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 3.0
    mod.STATE.habits_today = [("0l", 2.0), ("hiit", None)]
    mod.STATE.habits_ytd = {"其他人": -2.0}
    mod.STATE.domain_points = {label: 5 for label, _c in mod.DOMAIN_PTS_ROW}
    lines = _strip_text(mod).rstrip("\n").split("\n")
    assert "hiit" in lines[0] and "ص" not in lines[0], "pending line is only what is open"
    assert lines[-1].rstrip().endswith("ص 3"), "counter closes the last (domain points) row"
    assert " 5 " in lines[-1]


def test_prayer_counter_shows_before_domain_points_load(tmp_path):
    """Row 3 has no cells until the first trusted points read of the day; the
    counter must not wait for it."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 2.0
    mod.STATE.habits_today = [("hiit", None)]
    assert _strip_text(mod).rstrip("\n").split("\n")[-1].strip() == "ص 2"


def test_prayer_counter_survives_a_full_domain_row(tmp_path, monkeypatch):
    """Width is reserved: a full row drops a domain cell, never the counter."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 4.0
    mod.STATE.habits_today = [("hiit", None)]
    mod.STATE.domain_points = {label: 12345 for label, _c in mod.DOMAIN_PTS_ROW}
    monkeypatch.setattr(mod, "WIDTH_HINT", 30)
    last = _strip_text(mod).rstrip("\n").split("\n")[-1]
    assert last.rstrip().endswith("ص 4") and mod.dwidth(last) <= 30


def test_delay_made_on_another_host_hides_the_habit(tmp_path):
    """2026-11-22: 1st hci was delayed to 20:00 in dtd on Straylight; Ix's
    janus read only its own snooze file and kept showing it. Every host's
    mirror counts, as in dtd's own list."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["1st hci"] = [("id-hci", today)]
    mod.STATE.habits_today = [("1st hci", None), ("0l", None)]
    _write_snooze(mod, {"other": 23}, date="2020-01-01")          # local file is stale
    (mod.SNOOZE_MIRROR_DIR / "dtd-block-snooze-straylight.json").write_text(json.dumps(
        {"date": today, "snoozes": {"id-hci": min(24, dtm.datetime.now(TZ).hour + 2)}}))
    mod._snooze_cache.update(sig=None, at=0.0)
    text = _strip_text(mod)
    assert "1st hci" not in text and "0l" in text


def test_minute_delay_is_an_epoch_not_an_hour(tmp_path):
    """dtd stores minute and free-form delays as an absolute epoch. Read as
    an hour it is always in the future, so the habit never came back."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None)]
    now = dtm.datetime.now(TZ).timestamp()
    _write_snooze(mod, {"id-hiit": now + 600})
    assert "hiit" not in _strip_text(mod), "still delayed for ten more minutes"
    _write_snooze(mod, {"id-hiit": now - 600})
    assert "hiit" in _strip_text(mod), "delay passed ten minutes ago"


def test_yesterdays_delays_stop_hiding_after_midnight(tmp_path):
    """The date is judged on every read, not once when the file is cached, so
    a janus left running overnight does not carry yesterday's delays."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today.isoformat())]
    mod.STATE.habits_today = [("hiit", None)]
    _write_snooze(mod, {"id-hiit": 24})
    assert mod._snoozed_card_ids() == {"id-hiit"}                  # cached now
    tomorrow = dtm.datetime.combine(today + dtm.timedelta(days=1), dtm.time(0, 5), TZ)
    assert mod._snoozed_card_ids(tomorrow) == set()


def test_habit_fed_by_a_differently_named_card_hides_with_it(tmp_path):
    """xk20 / xk22 are the 0n columns; their cards are 轩轩中文 / 琪琪中文.
    Looked up by column name there was no card, so no delay or deferral could
    ever hide them (2026-11-22)."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today()
    mod.HABIT_CARDS[mod._norm_key("轩轩中文")] = [("id-theo", today.isoformat())]
    mod.HABIT_CARDS[mod._norm_key("琪琪中文")] = [("id-ren", today.isoformat())]
    mod.STATE.habits_today = [("xk20", None), ("xk22", None)]
    _write_snooze(mod, {"id-theo": 24})
    text = _strip_text(mod)
    assert "xk20" not in text and "xk22" in text, "only the delayed kid's chip hides"
    mod.HABIT_DUES[mod._norm_key("琪琪中文")] = [(today + dtm.timedelta(days=1)).isoformat()]
    assert "xk22" not in _strip_text(mod), "a card deferred past today hides its column too"


def test_card_aliases_match_did_fast_split_habits():
    """HABIT_CARD_ALIASES restates the one-column entries of did-fast.py's
    SPLIT_HABITS; this fails when a card is added or renamed there only."""
    import ast
    mod = _load_tui()
    src = (HERE.parent / "did" / "did-fast.py").read_text()
    line = next(l for l in src.splitlines() if l.startswith("SPLIT_HABITS ="))
    split = ast.literal_eval(line.split("=", 1)[1].strip())
    expected: dict = {}
    for card, cols in split.items():
        if len(cols) == 1:
            expected.setdefault(cols[0], set()).add(card)
    assert {k: set(v) for k, v in mod.HABIT_CARD_ALIASES.items()} == expected


def test_prayer_zero_still_shown(tmp_path):
    """0 prayers is information ("none yet"), not an empty state."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 0.0
    mod.STATE.habits_today = [("0l", None)]
    assert "ص 0" in _strip_text(mod)


def test_fetch_loop_diverts_prayer_from_habit_rows():
    """Source-level: fetch_habits_today must capture ص for the counter and
    never emit it as an ordinary done/pending habit."""
    src = (HERE / "janus.py").read_text()
    i = src.index("def fetch_habits_today")
    body = src[i:src.index("\ndef ", i + 10)]
    assert 'name == "ص"' in body
    assert "prayer_count" in body


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
