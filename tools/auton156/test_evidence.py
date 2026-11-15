import datetime as dt
import json

import pytest

import evidence

DAY = dt.date(2026, 11, 5)


def test_parse_ranges_takes_both_clock_forms():
    assert evidence.parse_ranges(["0612-0631", "6:33-6:38"], DAY)[1][0] == dt.datetime(2026, 11, 5, 6, 33)


def test_parse_ranges_rejects_a_bare_time():
    with pytest.raises(SystemExit):
        evidence.parse_ranges(["0612"], DAY)


def test_range_end_is_exclusive():
    r = evidence.parse_ranges(["0612-0631"], DAY)
    assert evidence.inside(dt.datetime(2026, 11, 5, 6, 12), r)
    assert not evidence.inside(dt.datetime(2026, 11, 5, 6, 31), r)


def test_prompts_keep_jm_and_drop_subagents_and_skill_bodies(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    stamp = dt.datetime(2026, 11, 5, 6, 20).astimezone().astimezone(dt.timezone.utc)
    base = {"type": "user", "timestamp": stamp.isoformat().replace("+00:00", "Z")}
    rows = [
        {**base, "message": {"content": "do the fixes"}},
        {**base, "isSidechain": True, "message": {"content": "subagent brief"}},
        {**base, "message": {"content": "Base directory for this skill: x"}},
        {**base, "message": {"content": "<command-name>/0n</command-name>\n<command-args>0g 11.04</command-args>"}},
    ]
    (proj / "abcdef12.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = evidence.prompt_events(evidence.parse_ranges(["0600-0700"], DAY), DAY, tmp_path)
    assert [e[2] for e in got] == ["[abcdef] do the fixes", "[abcdef] /0n 0g 11.04"]


# ── find_holes (bare evidence.py, shared with /-1l) ───────────────────────────

class _Cov:
    """The two pieces of tools/2n/2n-coverage.py find_holes leans on, loaded for real."""
    @staticmethod
    def load():
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "cov", Path(__file__).resolve().parents[1] / "2n" / "2n-coverage.py")
        cov = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cov)
        return cov


def _e(cov, i, start, end, desc="work"):
    at = lambda hm: dt.datetime.combine(DAY, dt.time(*hm), cov.TZ)  # noqa: E731
    return {"id": i, "start": at(start), "end": at(end), "desc": desc, "code": "i9"}


def _spans(holes):
    return [(f"{h['lo']:%H:%M}", f"{h['hi']:%H:%M}") for h in holes]


def test_rest_of_today_is_not_a_hole():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(14, 25), cov.TZ)
    entries = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 2, (9, 30), (14, 0))]
    got = evidence.find_holes(DAY, now, entries, cov, 6)
    want = [((dt.datetime.combine(DAY, dt.time(*a), cov.TZ)).astimezone().strftime("%H:%M"),
             (dt.datetime.combine(DAY, dt.time(*b), cov.TZ)).astimezone().strftime("%H:%M"))
            for a, b in [((9, 0), (9, 30)), ((14, 0), (14, 25))]]
    assert _spans(got) == want
    assert got[1]["before"] == ""


def test_five_minute_seam_is_not_a_hole():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    entries = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 2, (9, 5), (10, 0))]
    assert evidence.find_holes(DAY, now, entries, cov, 6) == []


def test_empty_toggl_is_no_data_not_one_long_hole():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    assert evidence.find_holes(DAY, now, [], cov, 6) is None


def test_placeholder_is_a_hole_and_carries_its_id():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    entries = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 7, (9, 0), (9, 40), "generic placeholder"),
               _e(cov, 2, (9, 40), (10, 0))]
    got = evidence.find_holes(DAY, now, entries, cov, 6)
    assert len(got) == 1 and got[0]["placeholders"] == [7] and got[0]["minutes"] == 40
