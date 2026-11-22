"""done_session: which tasks /done closes, and when a run counts as closed."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"{name}_t", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ds = _mod("done_session")
outcome = _mod("dtd_outcome")


def test_done_claims_count_released_do_not():
    # agent_claims.py done empties held(), so the ✅ claim must still be found;
    # a released claim is a task this session handed back, never closed by /done.
    claims = {"1": {"session": "s", "state": "done"},
              "2": {"session": "s", "state": "released"},
              "3": {"session": "other", "state": "done"},
              "4": {"session": "s", "state": "working"}}
    assert ds.session_tasks("s", claims, ["4"]) == ["4", "1"]
    assert ds.session_tasks("s", claims, ["1"]) == ["1"]


def test_content_prefers_live_cache_over_claim_record():
    claims = {"9": {"task": "old name"}}
    assert ds.current_content("9", claims, [{"id": "9", "content": "new name [10]"}]) == "new name [10]"
    assert ds.current_content("9", claims, []) == "old name"


def _run(todoist):
    return json.dumps({"results": [{"name": "fix x", "step": "todoist", "todoist": todoist}]})


def test_closed_requires_todoist_close(monkeypatch):
    monkeypatch.setattr(outcome, "_completed_today", lambda: (set(), set()))
    assert ds.closed_ok(0, _run({"id": "7", "closed": True}), "7", "fix x", outcome)[0]
    ok, display = ds.closed_ok(0, _run({"id": "7", "closed": False}), "7", "fix x", outcome)
    assert not ok and "Todoist close failed" in display


def test_failed_run_is_not_closed():
    assert not ds.closed_ok(1, "", "7", "fix x", outcome)[0]
    assert not ds.closed_ok(0, "  ", "7", "fix x", outcome)[0]


def test_timer_line():
    raw = json.dumps({"toggl_stopped": {"description": "fix x", "stopped": True, "minutes": 12}})
    assert ds.timer_line(raw) == "stopped fix x (12m)"
    assert ds.timer_line(json.dumps({"toggl_stopped": None})) is None


# --- /fin (2026-11-14): "if I put an integer or time after it it means to delay
# that many days or to that time today" ---------------------------------------
import datetime as dt
import subprocess

NOW = dt.datetime(2026, 11, 14, 9, 30)


def test_parse_when_bare_integer_is_days_not_a_time():
    assert ds.parse_when(None) is None and ds.parse_when("") is None
    assert ds.parse_when("3", NOW) == ("days", 3)
    assert ds.parse_when("15", NOW) == ("days", 15)          # fifteen DAYS, never 15:00
    assert ds.parse_when("365", NOW) == ("days", 365)


def test_parse_when_time_forms_land_today():
    for a in ("1500", "15:00", "3pm", "3PM", "3:00pm"):
        assert ds.parse_when(a, NOW) == ("time", dt.datetime(2026, 11, 14, 15, 0)), a
    assert ds.parse_when("3:30pm", NOW) == ("time", dt.datetime(2026, 11, 14, 15, 30))
    assert ds.parse_when("12am", NOW) == ("time", dt.datetime(2026, 11, 14, 0, 0))
    assert ds.parse_when("12pm", NOW) == ("time", dt.datetime(2026, 11, 14, 12, 0))
    assert ds.parse_when("0930", NOW) == ("time", dt.datetime(2026, 11, 14, 9, 30))


def test_parse_when_rejects_nonsense():
    import pytest
    for bad in ("0", "366", "2460", "13pm", "25:00", "tomorrow", "1.5", "15h"):
        with pytest.raises(ValueError):
            ds.parse_when(bad, NOW)


def test_snooze_until_writes_dtd_ctrl_v_shape_and_mirror(tmp_path):
    """Same file dtd.sh's DTD_BLOCKAPPLY writes for ctrl-v +Nm: {date, snoozes:
    {id: epoch FLOAT}} (an int would read as an hour-of-day block delay), plus
    the per-host mirror in the vault so dtd web / the other host see it."""
    path = tmp_path / "state" / "dtd-block-snooze.json"
    mirror = tmp_path / "z_ibx"
    until = dt.datetime(2026, 11, 14, 15, 0)
    epoch = ds.snooze_until(["T1", "T2"], until, path=path, mirror_dir=mirror, now=NOW)
    data = json.loads(path.read_text())
    assert data["date"] == "2026-11-14"
    assert data["snoozes"] == {"T1": epoch, "T2": epoch}
    assert isinstance(data["snoozes"]["T1"], float) and epoch == until.timestamp()
    mirrors = list(mirror.glob("dtd-block-snooze-*.json"))
    assert len(mirrors) == 1 and json.loads(mirrors[0].read_text()) == data
    # existing same-day snoozes survive; a stale (older) date is wiped
    ds.snooze_until(["T3"], dt.datetime(2026, 11, 14, 16, 0), path=path, mirror_dir=mirror, now=NOW)
    assert set(json.loads(path.read_text())["snoozes"]) == {"T1", "T2", "T3"}
    path.write_text(json.dumps({"date": "2026-11-13", "snoozes": {"OLD": 18}}))
    ds.snooze_until(["T4"], until, path=path, mirror_dir=mirror, now=NOW)
    assert set(json.loads(path.read_text())["snoozes"]) == {"T4"}


def test_snooze_until_refuses_a_time_already_passed(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        ds.snooze_until(["T1"], dt.datetime(2026, 11, 14, 9, 0), path=tmp_path / "s.json",
                        mirror_dir=tmp_path, now=NOW)


def test_stop_timer_only_when_it_is_this_task():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        if cmd[-1] == "current":
            return subprocess.CompletedProcess(cmd, 0, "Running: 09:12-running 😈 fix dtd (30) [15] @i447 (running) [id:9]", "")
        return subprocess.CompletedProcess(cmd, 0, "stopped", "")
    assert ds.stop_timer_if("other task [10]", run) is None and calls == ["current"]
    calls.clear()
    assert ds.stop_timer_if("fix dtd (30) [15]", run) == "⏱ 😈 fix dtd (30) [15] stopped" and calls == ["current", "stop"]


def test_defer_days_uses_defer_fast_by_id_without_points():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"task": "x", "target_date": "2026-11-17"}), "")
    ok, display = ds.defer_days("ABC", 3, run)
    assert ok and display == "→ 2026-11-17"
    assert seen["cmd"][1:] == [str(ds.DEFER_FAST), "--id", "ABC", "3"], "dtd's ctrl-d form: id, days, NO points arg"
    bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, json.dumps({"error": "task id ABC not found"}), "")
    assert ds.defer_days("ABC", 3, bad) == (False, "task id ABC not found")


def test_skill_renamed_to_fin_and_nothing_says_done():
    skills = HERE.parent.parent / "skills" / "claude-skills"
    assert (skills / "fin" / "SKILL.md").exists() and not (skills / "done").exists()
    import re
    # fin's own description may name the old command ("Replaces /done") so a
    # typed /done still routes here; nothing else may. "archive/done" is prose.
    offenders = [p for p in skills.glob("*/SKILL.md")
                 if p.parent.name != "fin" and re.search(r"(?<![\w])/done\b", p.read_text())]
    assert offenders == [], offenders
    fin = (skills / "fin" / "SKILL.md").read_text()
    assert fin.startswith('---\nname: "fin"\ndescription: "Use when')
    assert "done_session.py" in fin and "→ /clear" in fin


def test_stop_timer_by_id_beats_name(monkeypatch):
    import subprocess
    calls = []
    def run(argv, **kw):
        calls.append(argv[-1])
        if argv[-1] == "current":
            return subprocess.CompletedProcess(argv, 0, "Running: 09:00-running 0l @g245 (running) [id:777]\n", "")
        return subprocess.CompletedProcess(argv, 0, "Stopped: 0l (5min)\n", "")
    links = {"777": "RECUR1"}
    # Linked to another task: same name, not stopped.
    assert ds.stop_timer_if("0l (8) [20]", run, tid="OTHER", task_for_entry=links.get) is None
    assert calls == ["current"]
    # Linked to this task: stopped even though the names differ.
    assert ds.stop_timer_if("renamed card [20]", run, tid="RECUR1", task_for_entry=links.get) == "⏱ 0l stopped"
    # Unlinked entry: name match as before.
    assert ds.stop_timer_if("0l", run, tid="X", task_for_entry=lambda eid: None) == "⏱ 0l stopped"


def test_mark_tab_close_only_for_a_remote_driven_tab(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(ds, "FIN_CLOSE_DIR", tmp_path)
    hosts = tmp_path / "hosts"; hosts.mkdir()
    monkeypatch.setattr(ds, "SESSION_HOSTS_DIR", hosts)
    exits = tmp_path / "claims" / "by-session"
    monkeypatch.setattr(ds, "CLAIMS_ROOT", tmp_path / "claims")
    monkeypatch.setattr(ds.socket, "gethostname", lambda: "ix.local")
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "cc-fix-bug\n", "")
    env = {"CC_ORIGIN": "straylight", "TMUX_PANE": "%1", "TMUX": "/tmp/x"}
    assert ds.mark_tab_close("S1", env=env, run=run) is True
    assert (tmp_path / "cc-fix-bug").read_text().strip() == "S1"
    # 2026-11-22 (/bug "didn't exit the tab"): the marker alone closed nothing, because the
    # chain only starts when claude exits and /fin left the /exit to JM. /fin now also files
    # by-session/<sid>.exit, which the Stop hook turns into a typed /exit once the turn is over.
    assert (exits / "S1.exit").exists()
    assert ds.mark_tab_close("S1", env={"TMUX_PANE": "%1", "TMUX": "/tmp/x"}, run=run) is False   # local session
    assert ds.mark_tab_close("S1", env={**env, "CC_ORIGIN": "ix"}, run=run) is False             # driven from this host
    assert ds.mark_tab_close("S1", env={"CC_ORIGIN": "straylight"}, run=run) is False            # not in tmux
    assert sorted(p.name for p in exits.iterdir()) == ["S1.exit"]                                # no tab, no exit request
    # 2026-11-22: claude-tracked pops CC_ORIGIN before claude starts, so the live signal is
    # CC_TAB_ORIGIN; a session launched before that change is found through session-hosts/<id>.
    tmux = {"TMUX_PANE": "%1", "TMUX": "/tmp/x"}
    (tmp_path / "cc-fix-bug").unlink()
    assert ds.mark_tab_close("S2", env={**tmux, "CC_TAB_ORIGIN": "Straylight-Refit"}, run=run) is True
    assert (tmp_path / "cc-fix-bug").read_text().strip() == "S2"
    assert ds.mark_tab_close("S2", env={**tmux, "CC_TAB_ORIGIN": "ix"}, run=run) is False          # nested / local cc
    (hosts / "S3").write_text("straylight-refit")
    assert ds.mark_tab_close("S3", env=tmux, run=run) is True                                       # host-file fallback
    (hosts / "S4").write_text("ix")
    assert ds.mark_tab_close("S4", env=tmux, run=run) is False
