import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import youtube_history as yh


def _entry(video_id, minute, title="Some video", channel="Some Channel",
           extra_details=None, url_suffix=""):
    return {
        "header": "YouTube",
        "title": f"Watched {title}",
        "titleUrl": f"https://www.youtube.com/watch?v={video_id}{url_suffix}",
        "subtitles": [{"name": channel}],
        "time": f"2026-08-{10:02d}T{minute // 60:02d}:{minute % 60:02d}:00.000Z",
        **({"details": extra_details} if extra_details else {}),
    }


# ── load_entries filtering ──────────────────────────────────────────────

def test_filters_ads(tmp_path):
    data = [_entry("a1", 0), _entry("ad1", 5, extra_details=[{"name": "From Google Ads"}])]
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    entries = yh.load_entries(str(p))
    assert [e["video_id"] for e in entries] == ["a1"]


def test_filters_non_video_and_music(tmp_path):
    data = [
        _entry("a1", 0),
        {"header": "YouTube", "title": "Used YouTube", "time": "2026-08-10T00:05:00.000Z"},
        {"header": "YouTube Music", "title": "Played a song",
         "titleUrl": "https://music.youtube.com/watch?v=song1",
         "time": "2026-08-10T00:10:00.000Z"},
    ]
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    entries = yh.load_entries(str(p))
    assert [e["video_id"] for e in entries] == ["a1"]


def test_since_filter(tmp_path):
    data = [_entry("old", 0), _entry("new", 0)]
    data[0]["time"] = "2026-08-01T00:00:00.000Z"
    data[1]["time"] = "2026-08-10T00:00:00.000Z"
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    entries = yh.load_entries(str(p), since="2026-08-05")
    assert [e["video_id"] for e in entries] == ["new"]


def test_sorted_chronologically(tmp_path):
    data = [_entry("later", 30), _entry("earlier", 0)]
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    entries = yh.load_entries(str(p))
    assert [e["video_id"] for e in entries] == ["earlier", "later"]


def test_strips_watched_prefix(tmp_path):
    data = [_entry("a1", 0, title="Cool Video")]
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    entries = yh.load_entries(str(p))
    assert entries[0]["title"] == "Cool Video"


# ── ISO 8601 duration parsing ───────────────────────────────────────────

@pytest.mark.parametrize("s,expected", [
    ("PT14M32S", 14 * 60 + 32),
    ("PT1H2M", 3600 + 120),
    ("PT45S", 45),
    ("PT1H", 3600),
    ("PT0S", 0),
])
def test_parse_iso8601_duration(s, expected):
    assert yh.parse_iso8601_duration(s) == expected


# ── watch-time estimation: the core heuristic ───────────────────────────

def _mk(video_id, minute, seconds=0):
    base = datetime(2026, 8, 10, tzinfo=timezone.utc)
    return {"video_id": video_id, "title": video_id, "channel": "c",
            "time": base + timedelta(minutes=minute, seconds=seconds)}


def test_gap_smaller_than_duration_uses_gap():
    # video is 10 min long per API, but next entry starts only 3 min later
    entries = [_mk("a", 0), _mk("b", 3)]
    out = yh.estimate_watch_seconds(entries, durations={"a": 600}, default_cap_sec=2400)
    assert out[0]["est_seconds"] == 180


def test_gap_larger_than_duration_caps_at_real_length():
    # left playing / paused in background for an hour, but video is only 5 min
    entries = [_mk("a", 0), _mk("b", 60)]
    out = yh.estimate_watch_seconds(entries, durations={"a": 300}, default_cap_sec=2400)
    assert out[0]["est_seconds"] == 300


def test_last_entry_uses_cap_with_no_next_gap():
    entries = [_mk("a", 0)]
    out = yh.estimate_watch_seconds(entries, durations={"a": 480}, default_cap_sec=2400)
    assert out[0]["est_seconds"] == 480


def test_missing_duration_falls_back_to_default_cap():
    entries = [_mk("a", 0), _mk("b", 90)]  # 90 min gap, no known duration for "a"
    out = yh.estimate_watch_seconds(entries, durations={}, default_cap_sec=2400)  # 40 min cap
    assert out[0]["est_seconds"] == 2400


def test_zero_or_negative_gap_falls_back_to_cap():
    # duplicate/out-of-order timestamps shouldn't produce negative watch time
    entries = [_mk("a", 5), _mk("b", 5)]
    out = yh.estimate_watch_seconds(entries, durations={"a": 300}, default_cap_sec=2400)
    assert out[0]["est_seconds"] == 300


# ── summarize ────────────────────────────────────────────────────────────

def test_summarize_totals_and_top_channels():
    entries = [
        {**_mk("a", 0), "channel": "Chan A", "est_seconds": 600},
        {**_mk("b", 20), "channel": "Chan B", "est_seconds": 300},
        {**_mk("c", 40), "channel": "Chan A", "est_seconds": 100},
    ]
    summary = yh.summarize(entries, top=5)
    assert summary["total_seconds"] == 1000
    assert summary["video_count"] == 3
    assert summary["top_channels"][0] == ("Chan A", 700)
    assert summary["top_channels"][1] == ("Chan B", 300)


def test_cli_runs_without_api_key(tmp_path):
    data = [_entry("a1", 0), _entry("a2", 20)]
    p = tmp_path / "h.json"
    p.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "youtube_history.py"), str(p)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert "Total:" in result.stdout or "Total" in result.stdout
