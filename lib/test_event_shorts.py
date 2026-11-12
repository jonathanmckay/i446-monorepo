"""lib/event_shorts.py — the shared Haiku event-title shortener (2026-11-12)."""
import json

import event_shorts as es


def _ev(title, **kw):
    return {"title": title, **kw}


LONG = "XBOX + Battle.net Workstream sync with partner teams (recurring)"
SHORT = "XBOX+Battle.net sync"


def test_key_folds_prompt_version_and_cols():
    assert es.key(LONG, 33) != es.key(LONG, 56)
    assert es.key(LONG, 33) == es.key(LONG, 33)
    assert len(es.key(LONG)) == 8


def test_dwidth_counts_cjk_double():
    assert es.dwidth("短名") == 4
    assert es.needs_short("季度战略评审会议与产品路线图深度讨论会", 33)   # 19 CJK = 38 cols
    assert not es.needs_short("standup", 33)


def test_apply_cached_hits_without_network(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({es.key(LONG, 33): {"raw": LONG, "short": SHORT}}))
    calls = []
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: calls.append(raw) or "x")
    evs = [_ev(LONG), _ev(LONG), _ev("standup")]
    misses = es.apply_cached(evs, 33, p)
    assert evs[0]["short"] == SHORT and evs[1]["short"] == SHORT
    assert "short" not in evs[2]
    assert misses == {} and calls == []


def test_apply_cached_returns_misses_grouped_by_title(tmp_path):
    a, b = _ev(LONG), _ev(LONG + " two")
    misses = es.apply_cached([a, b, _ev(LONG)], 33, tmp_path / "s.json")
    assert set(misses) == {es.key(LONG, 33), es.key(LONG + " two", 33)}
    assert len(misses[es.key(LONG, 33)]) == 2


def test_fill_calls_haiku_once_per_title_and_persists(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    calls = []
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: calls.append(raw) or SHORT)
    a, b = _ev(LONG), _ev(LONG)
    assert es.fill({es.key(LONG, 33): [a, b]}, 33, p, failed_titles=set())
    assert a["short"] == SHORT and b["short"] == SHORT
    assert calls == [LONG]
    assert json.loads(p.read_text()) == {es.key(LONG, 33): {"raw": LONG, "short": SHORT}}
    # second pass: cached, no call
    c = _ev(LONG)
    assert es.apply_cached([c], 33, p) == {} and c["short"] == SHORT


def test_fill_failure_is_negative_cached_for_the_process(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    failed = set()
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: None)
    ev = _ev(LONG)
    assert not es.fill({es.key(LONG, 33): [ev]}, 33, p, failed_titles=failed)
    assert "short" not in ev and LONG in failed
    calls = []
    monkeypatch.setattr(es, "haiku_shorten", lambda raw, cols: calls.append(raw) or SHORT)
    assert es.apply_cached([_ev(LONG)], 33, p, failed_titles=failed) == {}
    assert not es.fill({es.key(LONG, 33): [_ev(LONG)]}, 33, p, failed_titles=failed)
    assert calls == [], "a failed title is skipped by fill too, not only by apply_cached"


def test_fill_honours_title_key_for_mobile_rows(tmp_path, monkeypatch):
    """mobile.py's events carry the sanitized title in `title` and the real one
    in `raw_title`; the raw one is what gets hashed and sent to Haiku."""
    p = tmp_path / "s.json"
    raw = "CosmosDB Deprecation, Part 3 — partner teams readout (recurring)"
    ev = {"title": es.norm(raw), "raw_title": raw}
    monkeypatch.setattr(es, "haiku_shorten", lambda r, cols: "CosmosDB Deprecation 3")
    misses = es.apply_cached([ev], 33, p, title_key="raw_title")
    assert set(misses) == {es.key(raw, 33)}
    es.fill(misses, 33, p, title_key="raw_title")
    assert ev["short"] == "CosmosDB Deprecation 3"
    assert json.loads(p.read_text())[es.key(raw, 33)]["raw"] == raw


def test_raw_for_reverse_lookup_after_safe_title(tmp_path):
    p = tmp_path / "s.json"
    raw = "Tencent Partnership | CFO & CSO Kick-Off, Part 2"
    p.write_text(json.dumps({es.key(raw, 33): {"raw": raw, "short": "Tencent, CFO & CSO kickoff"}}))
    # the client echoes the short after mobile's _safe_title turned the comma into a space
    assert es.raw_for("Tencent  CFO & CSO kickoff", p) == raw
    assert es.raw_for("something else", p) is None
    assert es.raw_for("", p) is None


def test_fit_trims_overshoot_at_word_boundary():
    assert es.dwidth(es._fit("a very long short name that Haiku overshot badly", 20)) <= 20
    assert es._fit('"Quoted title"', 33) == "Quoted title"
    assert es._fit("Review with", 33) == "Review"


def test_legacy_string_entries_tolerated(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"abcd1234": "old short"}))
    assert es.load(p) == {"abcd1234": {"raw": None, "short": "old short"}}
    assert es.raw_for("old short", p) is None   # no raw known → no mapping


def test_haiku_shorten_retries_once_on_overshoot_then_fits(monkeypatch):
    asked = []
    answers = {33: "United Airlines flight 1334 to San Francisco", 29: "UA 1334 to SFO (CH31F1)"}
    monkeypatch.setattr(es, "_ask", lambda raw, cols: asked.append(cols) or answers[cols])
    assert es.haiku_shorten("United Airlines flight 1334 to San Francisco (CH31F1)", 33) == "UA 1334 to SFO (CH31F1)"
    assert asked == [33, 29]


def test_haiku_shorten_fits_when_the_retry_also_overshoots(monkeypatch):
    monkeypatch.setattr(es, "_ask", lambda raw, cols: "one two three four five six seven eight nine ten eleven")
    out = es.haiku_shorten("x" * 50, 33)
    assert out and es.dwidth(out) <= 33 and not out.endswith(" ")


def test_haiku_shorten_swallows_api_errors(monkeypatch, capsys):
    def boom(raw, cols):
        raise RuntimeError("TypeError: unexpected kwarg output_config")
    monkeypatch.setattr(es, "_ask", boom)
    assert es.haiku_shorten("x" * 50, 33) is None
    assert "WARN event_shorts" in capsys.readouterr().err
