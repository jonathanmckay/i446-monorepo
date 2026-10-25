"""Phone dtd fixes, 2026-10-25 (three Todoist cards from new-notes):

* 6hqjQRh6rQfHwGMc "after form filled the task needs to disappear faster":
  a form that closes its card hides it from /api/tasks the moment the POST is
  accepted, not after the up-to-50 s Excel write; a failed writer unhides it;
  cards the form keeps open (1s, moralaudit, 0s without points checked) stay.
* 6hqjQRhQGH98vw36 "row for -1 tasks should still show how many points":
  -1neon ritual cards carry no [N]; their 分 come from block-rituals.json.
* 6hqjQRjcRW7W6Pqc "big tasks that need a number ... (like cpap)": every card
  terminal dtd prompts a value for, the server flags too (the phone-side bug
  was the iOS parser reading the label string as a Bool; see CoreCheck).

Writers, Todoist and Neon are all faked."""
import datetime as dt
import importlib.util
import json
import re
import subprocess
import threading
import time
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_phone1025", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

TODAY = dt.date.today().isoformat()
DTD_SH = Path(__file__).resolve().parent.parent / "did" / "dtd.sh"


def _task(tid, content, **kw):
    t = {"id": tid, "content": content, "labels": [], "priority": 1, "due": TODAY,
         "due_datetime": "", "recurring": False}
    t.update(kw)
    return t


def _setup(tmp_path, monkeypatch, cache):
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps({"updated": dt.datetime.now().isoformat(), **cache}))
    monkeypatch.setattr(dtd, "CACHE", cf)
    monkeypatch.setattr(dtd, "DONE_FILE", tmp_path / "completed-today.json")
    monkeypatch.setattr(dtd, "SNOOZE_FILE", tmp_path / "snooze.json")
    monkeypatch.setattr(dtd, "DEFERRED_DIR", tmp_path)
    (tmp_path / "z_ibx").mkdir()
    monkeypatch.setattr(dtd, "MIRROR_DIR", tmp_path / "z_ibx")
    monkeypatch.setattr(dtd, "FORMS_TMP", tmp_path / "forms")
    monkeypatch.setattr(dtd, "_refresh_cache_if_stale", lambda force=False: None)
    monkeypatch.setattr(dtd, "_run_refresh_subprocess", lambda: dtd._refresh_lock.release())
    monkeypatch.setattr(dtd, "_today", lambda: dt.date.today())
    dtd._FORM_DONE.clear()
    dtd._FORM_HIDDEN.clear()
    return dtd.app.test_client()


def _ids(c):
    return {t["id"] for t in c.get("/api/tasks").get_json()["tasks"]}


class _SlowProc:
    """Writer that outlasts FORM_WAIT, then finishes when `release` is set."""
    release = None
    result = {"ok": True, "message": "saved"}

    def __init__(self, args, **kw):
        self.args = args

    def wait(self, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(self.args, timeout)
        _SlowProc.release.wait(5)
        Path(self.args[-1]).write_text(json.dumps(_SlowProc.result))
        return 0


def _slow(monkeypatch, result):
    _SlowProc.release = threading.Event()
    _SlowProc.result = result
    monkeypatch.setattr(subprocess, "Popen", _SlowProc)
    return _SlowProc.release


def _wait_settled(tid):
    for _ in range(100):
        v = dtd._FORM_HIDDEN.get(tid)
        if v is None or v[1] is not None:
            return
        time.sleep(0.02)


# ------------------------------------------------------ 1. form hides at once

def test_pending_form_hides_card_immediately(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("h", "1 hcb (5) [15]")],
                                       "today": [_task("p", "plain (10) [5]")]})
    release = _slow(monkeypatch, {"ok": True, "message": "1 hcb · done"})
    assert _ids(c) == {"h", "p"}
    j = c.post("/api/form", json={"id": "h", "form": "1hcb", "values": {"weight": "191"}}).get_json()
    assert j["ok"] and j["pending"]
    assert _ids(c) == {"p"}            # gone while the writer still runs
    release.set(); _wait_settled("h")
    assert _ids(c) == {"p"}            # and stays gone until a fresher cache lands


def test_failed_background_form_brings_card_back(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("h", "1 hcb (5) [15]")]})
    release = _slow(monkeypatch, {"ok": False, "error": "excel down"})
    c.post("/api/form", json={"id": "h", "form": "1hcb", "values": {"weight": "191"}})
    assert _ids(c) == set()
    release.set()
    for _ in range(100):
        if "h" not in dtd._FORM_HIDDEN:
            break
        time.sleep(0.02)
    assert _ids(c) == {"h"}
    assert "h" not in dtd._FORM_DONE   # a retry runs the writer again, not "already"


def test_sync_failure_unhides(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("h", "1 hcb (5) [15]")]})

    class P:
        def __init__(self, args, **kw):
            Path(args[-1]).write_text(json.dumps({"ok": False, "error": "nope"}))

        def wait(self, timeout=None):
            return 1
    monkeypatch.setattr(subprocess, "Popen", P)
    assert not c.post("/api/form", json={"id": "h", "form": "1hcb", "values": {"weight": "191"}}).get_json()["ok"]
    assert _ids(c) == {"h"}


def test_fresher_cache_ends_the_hide(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {})
    dtd._form_hide("h"); dtd._form_settle("h", True)
    fin = dtd._FORM_HIDDEN["h"][1]
    before = dt.datetime.fromtimestamp(fin - 5).isoformat()
    after = dt.datetime.fromtimestamp(fin + 5).isoformat()
    assert dtd._form_hidden_ids(before) == {"h"}
    assert dtd._form_hidden_ids(after) == set()


def test_cards_the_form_keeps_open_stay(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("s", "1s (60) [50]")],
                                       "0neon": [_task("l", "0l (8) [20]")]})
    _slow(monkeypatch, {"ok": True})
    c.post("/api/form", json={"id": "s", "form": "1s", "values": {"wins": "x"}})
    c.post("/api/form", json={"id": "l", "form": "0s", "values": {"title": "day"}})
    assert _ids(c) == {"s", "l"}
    assert dtd.dtd_forms.closes_card("0s", {"points_checked": "1"})
    assert not dtd.dtd_forms.closes_card("moralaudit", {})
    g = c.get("/api/form?id=s").get_json()
    assert g["keepsCard"] is True
    assert c.get("/api/form?id=l").get_json()["closesIf"] == {"points_checked": "1"}


# --------------------------------------------- 2. -1 ritual cards' points

def test_ritual_cards_carry_their_points(tmp_path, monkeypatch):
    rit = lambda i, c: _task(i, c, labels=["-1neon"])
    c = _setup(tmp_path, monkeypatch, {"today": [
        rit("g", "😈 -1g"), rit("s", "😈 s سمش"), rit("x", "😈 -1ibx"),
        rit("o", "😈 -1g (15) [15]"),                       # explicit [N] wins
        _task("goal", "300分 {10}", labels=["#-1g", "g245"]),
        _task("plain", "-1g")]})                            # no 😈: not a ritual
    got = {t["id"]: (t["points"], t["est"]) for t in c.get("/api/tasks").get_json()["tasks"]}
    cfg = {r["tag"]: r["points"] for r in json.loads(
        (Path.home() / "i446-monorepo/config/block-rituals.json").read_text())["rituals"]}
    assert got["g"] == (cfg["-1g"], "[%d]" % cfg["-1g"])
    assert got["s"][0] == cfg["سمش"] and got["x"][0] == cfg["-1ibx"]
    assert got["o"] == (15, "(15) [15]")
    assert got["goal"] == (10, "{10}")
    assert got["plain"] == (0, "")


def test_api_done_reports_ritual_points(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(dtd, "complete", lambda content, tid=None: {"ok": True, "closed": True})
    assert c.post("/api/done", json={"content": "😈 -1ibx", "id": "z"}).get_json()["points"] > 0


# ---------------------------------- 3. value prompts: parity with dtd.sh

def _dtd_sh_prompt_names():
    """The literal names in dtd.sh's DONE-script `_ip=` case (cpap, xk20, …)."""
    src = DTD_SH.read_text()
    block = src[src.index('_ip=""\ncase'):]
    block = block[:block.index("esac")]
    names = []
    for m in re.finditer(r"^\s*([^$\n][^)\n]*)\)\s*_ip=", block, re.M):
        names += [n.strip().strip('"') for n in m.group(1).split("|")
                  if not n.strip().startswith("${")]  # VAR1N pats: test_weekly_* below
    return names


def test_every_cli_value_prompt_prompts_on_the_phone(tmp_path, monkeypatch):
    names = _dtd_sh_prompt_names()
    assert {"cpap", "xk20", "xk22", "xk26", "i444", "hiit", "新闻", "night hcmc"} <= set(names)
    for n in names:
        assert dtd.variable_prompt(f"{n} (15) [10]"), n
    c = _setup(tmp_path, monkeypatch, {"0neon": [_task("c", "cpap (1) [3]", labels=["0neon"])]})
    t = c.get("/api/tasks").get_json()["tasks"][0]
    assert t["variablePrompt"] == "CPAP quality (1-3)"


def test_weekly_variable_habits_prompt():
    for n in dtd.VARIABLE_1N_NAMES:
        assert dtd.variable_prompt(n), n
