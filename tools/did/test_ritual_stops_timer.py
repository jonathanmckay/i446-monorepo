"""Regression (2026-11-12): "I entered the -1g goal and saved, but then I
returned to dtd, the task was active, not finished. Same thing happened
yesterday."

dtd.sh's form launcher ($DTD_FORM) starts the card's Toggl timer in the
background before opening the -1g/0g form, so Enter on `😈 -1g` leaves a
"-1g @g245" timer running. -1g-form.py's save() then closes the card with
`did-fast.py --ritual -1g`, and that branch never touched Toggl (only the ⌥⏎
done path's ritual-only branch calls stop_matching_toggl). The card closed,
the timer kept running, and dtd rendered -1g as the active (▶/🧮, top) row —
28 min and 13 min of "-1g" time on 2026-11-11, and a timer still running
hours later on 2026-11-12. The web client never starts the timer, so the
phone path was clean; only terminal saves showed it.

Fix: the --ritual handler stops a running timer whose description matches
the ritual tag, exactly as the done path does for a ritual-only completion.
"""
import importlib.util
import json
import sys
from pathlib import Path

DID_FAST = Path(__file__).resolve().parent / "did-fast.py"


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_ritual_timer", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_ritual_cli(monkeypatch, capsys, tag, ritual_result, running_desc):
    df = _load()
    calls = {"stop": [], "run": []}

    def fake_run_ritual(t):
        calls["run"].append(t)
        return dict(ritual_result)

    def fake_stop(names):
        calls["stop"].append(list(names))
        if running_desc is not None and any(df._timer_key(n) == df._timer_key(running_desc) for n in names):
            return {"description": running_desc, "minutes": 2}
        return None

    monkeypatch.setattr(df, "run_ritual", fake_run_ritual)
    monkeypatch.setattr(df, "stop_matching_toggl", fake_stop)
    monkeypatch.setattr(df, "refresh_task_queue", lambda block=True: {})
    monkeypatch.setattr(sys, "argv", ["did-fast.py", "--ritual", tag])
    df.main()
    out = json.loads(capsys.readouterr().out)
    return calls, out


def test_ritual_cli_stops_its_own_running_timer(monkeypatch, capsys):
    calls, out = _run_ritual_cli(
        monkeypatch, capsys, "-1g",
        {"ritual": "-1g", "todoist": {"id": "x", "content": "😈 -1g", "closed": True}},
        running_desc="-1g")
    assert calls["run"] == ["-1g"]
    assert calls["stop"] == [["-1g"]], "the --ritual handler must stop the ritual's own timer"
    assert out["toggl_stopped"]["description"] == "-1g"


def test_ritual_cli_leaves_an_unrelated_timer_alone(monkeypatch, capsys):
    calls, out = _run_ritual_cli(
        monkeypatch, capsys, "-1g",
        {"ritual": "-1g", "todoist": {"id": "x", "content": "😈 -1g", "closed": True}},
        running_desc="1st hci")
    # stop_matching_toggl is name-matched: a different running task is untouched.
    assert calls["stop"] == [["-1g"]]
    assert "toggl_stopped" not in out


def test_ritual_cli_does_not_touch_toggl_on_unknown_tag(monkeypatch, capsys):
    calls, out = _run_ritual_cli(
        monkeypatch, capsys, "bogus",
        {"error": "unknown ritual tag 'bogus'", "known": []},
        running_desc="bogus")
    assert calls["stop"] == [], "a failed ritual must not stop anything"
