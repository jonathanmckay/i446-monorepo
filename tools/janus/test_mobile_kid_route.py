"""2026-11-05 (JM): xk20 / xk22 / xk26 are sub-projects of xk87. The Toggl
entry stays xk87 (+ a kid tag); logging it credits the kid's own 0n column
("@xk20" to did-fast), never the general xk87 0分!X, and never both.
Toggl and did-fast are mocked."""
import json

from test_mobile import jm  # noqa: F401


def _wire(jm, monkeypatch, entry_tags, seen):
    def fake_run(cmd, **kw):
        seen.append(cmd[-1])
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"name": "x", "step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    monkeypatch.setattr(jm, "_entry_range_and_tags", lambda eid, day: ("1500-1530", entry_tags))
    monkeypatch.setattr(jm, "_resolvable_points", lambda d: None)
    monkeypatch.setattr(jm, "_speedy_meeting_pts", lambda *a, **k: None)
    monkeypatch.setattr(jm, "_done_path", lambda *a, **k: jm.janus_done.ASK)
    monkeypatch.setattr(jm, "habit_tags", lambda tags: [t for t in tags if t in ("xk20", "其他人")])


def test_kid_tagged_xk87_entry_logs_to_kid_without_prompt_or_double(jm, monkeypatch):
    seen: list = []
    _wire(jm, monkeypatch, ["xk20"], seen)
    r = jm.log_entry("41", "playground", 30, "xk87", tags=["xk20", "其他人"])
    assert r["ok"] and not r.get("needs_points")
    assert seen == ["playground 1500-1530 @xk20 #xk20, 其他人 30"]


def test_plain_xk87_entry_unchanged(jm, monkeypatch):
    seen: list = []
    _wire(jm, monkeypatch, [], seen)
    r = jm.log_entry("42", "playground", 30, "xk87", tags=[])
    assert r.get("needs_points") and seen == []


def test_kid_route_rules(jm):
    janus_done = jm.janus_done
    assert janus_done.kid_route("xk87", ["xk26", "-1"]) == ("xk26", ["-1"])
    assert janus_done.kid_route("", ["xk22"]) == ("xk22", [])
    assert janus_done.kid_route("i9", ["xk20"]) == ("i9", ["xk20"])  # other parent: untouched
    assert janus_done.kid_route("xk87", []) == ("xk87", [])


def test_toggl_subproject_maps_to_xk87_plus_tag(jm):
    import mcp.toggl_server.config as tcfg
    assert tcfg.split_subproject("xk20") == ("xk87", "xk20")
    assert tcfg.with_subproject_tag("xk26", ["-1"]) == ["-1", "xk26"]
    assert tcfg.with_subproject_tag("xk22", ["xk22"]) == ["xk22"]
    assert tcfg.with_subproject_tag("i9", None) is None
    assert jm._resolve_project("@xk22") == (tcfg.PROJECT_MAP["xk87"], "xk87")


def test_untagged_math_asks_which_kid_when_client_can_answer(jm, monkeypatch):
    """JM 2026-11-07: math / english / 中文 route by kid tag, or ask."""
    seen: list = []
    _wire(jm, monkeypatch, [], seen)
    monkeypatch.setattr(jm, "_done_path", lambda *a, **k: jm.janus_done.TIME)
    r = jm.log_entry("43", "math", 26, "xk87", tags=[], kid_ask=True)
    assert r.get("needs_kid") and "Theo" in r["prompt"] and seen == []
    r = jm.log_entry("43", "math", 26, "xk87", tags=[], kid_ask=True, kid="20")
    assert r["ok"] and seen == ["math 1500-1530 @xk20 #xk20"]


def test_untagged_math_from_old_client_is_not_asked(jm, monkeypatch):
    """The iOS app predates needs_kid: without kid_ask it logs as before,
    instead of reading an unknown answer as success."""
    seen: list = []
    _wire(jm, monkeypatch, [], seen)
    monkeypatch.setattr(jm, "_done_path", lambda *a, **k: jm.janus_done.TIME)
    r = jm.log_entry("44", "math", 26, "xk87", tags=[])
    assert not r.get("needs_kid") and seen == ["math 1500-1530 @xk87"]


def test_bad_kid_answer_logs_nothing(jm, monkeypatch):
    seen: list = []
    _wire(jm, monkeypatch, [], seen)
    monkeypatch.setattr(jm, "_done_path", lambda *a, **k: jm.janus_done.TIME)
    r = jm.log_entry("45", "math", 26, "xk87", tags=[], kid_ask=True, kid="40")
    assert not r["ok"] and seen == []


def test_untagged_nickname_entry_logs_to_that_kid(jm, monkeypatch):
    """JM 2026-11-07: "熙熙" is Rori; her minutes go to xk26 untagged."""
    seen: list = []
    _wire(jm, monkeypatch, [], seen)
    r = jm.log_entry("46", "熙熙", 15, "", tags=[])
    assert r["ok"] and not r.get("needs_points")
    assert seen == ["熙熙 1500-1530 @xk26"]


def test_nickname_gap_fill_files_under_xk87_with_kid_tag(jm, monkeypatch):
    """A bare "熙熙" gap fill used to create a project-less entry."""
    calls = {}
    def fake_create(desc, start, stop, dur, project_id=None, tags=None, **kw):
        calls.update(desc=desc, pid=project_id, tags=tags)
        return {"id": 1}
    monkeypatch.setattr(jm.toggl_api, "create_entry", fake_create)
    r = jm.fill_gap("熙熙", "16:42", "16:57")
    assert r["ok"], r
    assert calls == {"desc": "熙熙", "pid": jm.PROJECT_MAP["xk87"], "tags": ["xk26"]}
