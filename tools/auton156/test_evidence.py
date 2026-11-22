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
    got = evidence.find_holes(DAY, now, entries, cov, evidence.GAP_MIN)
    want = [((dt.datetime.combine(DAY, dt.time(*a), cov.TZ)).astimezone().strftime("%H:%M"),
             (dt.datetime.combine(DAY, dt.time(*b), cov.TZ)).astimezone().strftime("%H:%M"))
            for a, b in [((9, 0), (9, 30)), ((14, 0), (14, 25))]]
    assert _spans(got) == want
    assert got[1]["before"] == ""


def test_threshold_is_the_one_janus_flashes_at():
    """JM 2026-11-21: a 17:15-17:20 gap flashed in janus and /-1l skipped it."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "tg" / "janus.py").read_text()
    assert int(re.search(r"^GAP_MIN = (\d+)", src, re.M).group(1)) == evidence.GAP_MIN


def test_five_minute_seam_is_a_hole_and_a_shorter_one_is_not():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    five = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 2, (9, 5), (10, 0))]
    got = evidence.find_holes(DAY, now, five, cov, evidence.GAP_MIN)
    assert len(got) == 1 and got[0]["minutes"] == 5
    # 4m59s: janus floors to 4 and stays quiet; rounding would have called it 5.
    short = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 2, (9, 5), (10, 0))]
    short[0]["end"] += dt.timedelta(seconds=1)
    assert evidence.find_holes(DAY, now, short, cov, evidence.GAP_MIN) == []


def test_empty_toggl_is_no_data_not_one_long_hole():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    assert evidence.find_holes(DAY, now, [], cov, evidence.GAP_MIN) is None


def test_placeholder_is_a_hole_and_carries_its_id():
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    entries = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 7, (9, 0), (9, 40), "generic placeholder"),
               _e(cov, 2, (9, 40), (10, 0))]
    got = evidence.find_holes(DAY, now, entries, cov, evidence.GAP_MIN)
    assert len(got) == 1 and got[0]["placeholders"] == [7] and got[0]["minutes"] == 40


# /-1l fills every blank, not only the 5-minute ones janus flashes (JM
# 2026-11-22: "if I call -1l and there are blank spots on the cal, fill them
# in"). The 1-3 minute seams between cards were never reported as holes.
def test_min_gap_one_reports_the_short_seams_too(monkeypatch, capsys):
    cov = _Cov.load()
    now = dt.datetime.combine(DAY, dt.time(10, 0), cov.TZ)
    entries = [_e(cov, 1, (0, 0), (9, 0)), _e(cov, 2, (9, 3), (9, 30)), _e(cov, 3, (9, 30), (10, 0))]
    assert evidence.find_holes(DAY, now, entries, cov, evidence.GAP_MIN) == []
    got = evidence.find_holes(DAY, now, entries, cov, 1)
    assert [(h["lo"].strftime("%H:%M"), h["minutes"]) for h in got] == [("09:00", 3)]
    # the CLI passes --min-gap through, and defaults to janus's threshold
    seen = []
    monkeypatch.setattr(evidence, "toggl_holes", lambda day, min_gap=evidence.GAP_MIN: seen.append(min_gap) or [])
    monkeypatch.setattr(evidence.sys, "argv", ["evidence.py", "--min-gap", "1"])
    evidence.main()
    monkeypatch.setattr(evidence.sys, "argv", ["evidence.py"])
    evidence.main()
    assert seen == [1, evidence.GAP_MIN]
