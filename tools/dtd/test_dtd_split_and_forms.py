"""POST /api/split and GET/POST /api/form (2026-10-22, iOS parity with
terminal dtd's ctrl-p split and Enter-opens-a-survey-form). split-fast,
the form runner and every writer are faked: nothing here touches Todoist,
Neon/Excel or the vault."""
import datetime as dt
import importlib.util
import json
import subprocess
import types
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_splitforms", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)
forms = dtd.dtd_forms

TODAY = dt.date.today().isoformat()


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
    dtd._FORM_DONE.clear()
    return dtd.app.test_client()


# ----------------------------------------------------------------- split

def _fake_split(monkeypatch, out):
    seen = []

    class P:
        returncode = 0 if out.get("ok") else 1
        stdout = json.dumps(out)
        stderr = ""

    def run(args, **kw):
        seen.append(args)
        return P()
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_split_renames_remainder_in_cache(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("a1", "write memo (60) [40]")]})
    seen = _fake_split(monkeypatch, {"ok": True, "message": "✂ +[15] today / [25] kept for today",
                                     "new_content": "write memo (40) [25]", "rem_date": TODAY,
                                     "closed": False})
    r = c.post("/api/split", json={"id": "a1", "pts": 15, "minutes": 20, "name": "drafted  intro"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "message": "✂ +[15] today / [25] kept for today"}
    args = seen[0]
    assert args[args.index("--pts") + 1] == "15" and args[args.index("--minutes") + 1] == "20"
    assert args[args.index("--name") + 1] == "drafted intro" and "--no-keep" not in args
    [t] = dtd.build_tasks()
    assert t["raw"] == "write memo (40) [25]" and t["points"] == 25


def test_split_no_keep_drops_row(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("a1", "write memo (60) [40]")]})
    seen = _fake_split(monkeypatch, {"ok": True, "message": "✂ +[10] today · rest dropped, task closed",
                                     "new_content": "write memo (60) [30]", "rem_date": TODAY,
                                     "closed": True})
    assert c.post("/api/split", json={"id": "a1", "pts": 10, "keep": False}).get_json()["ok"]
    assert "--no-keep" in seen[0]
    assert dtd.build_tasks() == []


def test_split_bonus_only_task_sends_bonus(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("g1", "😈 ship it {10}", labels=["#-1g"])]})
    seen = _fake_split(monkeypatch, {"ok": True, "message": "m", "new_content": "😈 ship it {6}",
                                     "rem_date": TODAY, "closed": False})
    assert c.post("/api/split", json={"id": "g1", "pts": 4}).status_code == 200
    assert "--bonus" in seen[0] and "--pts" not in seen[0]


def test_split_validation(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("a1", "write memo (60) [40]")]})
    seen = _fake_split(monkeypatch, {"ok": True})
    assert c.post("/api/split", json={"id": "a1", "pts": None, "minutes": 5}).status_code == 400
    assert c.post("/api/split", json={"id": "a1", "pts": 41}).status_code == 400
    assert c.post("/api/split", json={"id": "a1", "pts": 5, "minutes": 61}).status_code == 400
    assert c.post("/api/split", json={"id": "a1", "pts": True}).status_code == 400
    assert c.post("/api/split", json={"pts": 5}).status_code == 400
    assert seen == []


def test_split_failure_is_400(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("a1", "write memo (60) [40]")]})
    _fake_split(monkeypatch, {"ok": False, "error": "split: task not found"})
    r = c.post("/api/split", json={"id": "a1", "pts": 5})
    assert r.status_code == 400 and r.get_json() == {"ok": False, "error": "split: task not found"}


# ----------------------------------------------------------------- forms

def test_card_mapping_matches_dtd_sh():
    assert forms.form_for("0l (8) [20]") == "0s"
    assert forms.form_for("1s (20) [45]") == "1s"
    assert forms.form_for("1 xk88 10.12") == "xk887"          # dated copy
    assert forms.form_for("1 xk87 wknd (20) [20]") == "xk887"
    assert forms.form_for("Moral audit") == "moralaudit"
    assert forms.form_for("😈 -1g") == "-1g"
    assert forms.form_for("0g (4) [8]") == "0g"
    for card, form in (("1 hcme", "1hcme"), ("1 i9", "1i9"), ("1 hcb", "1hcb"), ("1 f693", "1f693")):
        assert forms.form_for(card + " (10) [30]") == form
    assert forms.form_for("1 i9 prep doc") == "" and forms.form_for("0lx") == ""


def test_every_form_has_a_spec_from_its_script():
    for name in forms.SCRIPTS:
        sp = forms.spec(name)
        assert sp and sp["fields"], name
        for f in sp["fields"]:
            assert f["type"] in ("text", "multiline", "int", "choice", "scale")
            assert f["type"] != "choice" or f["options"]
    keys = [f["key"] for f in forms.spec("0s")["fields"]]
    assert keys[0] == "title" and keys[-1] == "points_checked"
    assert len(forms.spec("xk887")["fields"]) == 29


def test_build_tasks_carries_form(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"today": [_task("p", "plain (10) [5]")],
                                   "0neon": [_task("l", "0l (8) [20]", labels=["0neon"])]})
    got = {t["id"]: t["form"] for t in dtd.build_tasks()}
    assert got == {"l": "0s", "p": ""}


def test_form_get(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"today": [_task("p", "plain (10) [5]")],
                                       "1neon": [_task("i", "1 i9 (10) [40]")]})
    j = c.get("/api/form?id=i").get_json()
    assert j["ok"] and j["form"] == "1i9" and [f["key"] for f in j["fields"]] == ["rating", "done", "exceed"]
    assert j["fields"][0]["options"] == ["MM", "MA", "EE", "OL"]
    r = c.get("/api/form?id=p")
    assert r.status_code == 200 and r.get_json() == {"ok": False, "form": None}


def _fake_runner(monkeypatch, result, hang=False):
    seen = []

    class Proc:
        returncode = 0

        def __init__(self, args, **kw):
            seen.append(args)
            self.args = args
            if not hang:
                Path(args[-1]).write_text(json.dumps(result))

        def wait(self, timeout=None):
            if hang:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return 0
    monkeypatch.setattr(subprocess, "Popen", Proc)
    return seen


def test_form_post_runs_runner_once(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("h", "1 hcb (5) [15]")]})
    seen = _fake_runner(monkeypatch, {"ok": True, "message": "1 hcb 10/22 · done"})
    body = {"id": "h", "form": "1hcb", "values": {"weight": "191.4", "learned": "a\nb", "junk": "x"}}
    r = c.post("/api/form", json=body)
    assert r.get_json() == {"ok": True, "message": "1 hcb 10/22 · done"}
    args = seen[0]
    assert args[2:4] == ["run", "1hcb"]
    assert json.loads(Path(args[4]).read_text()) == {"weight": "191.4", "learned": "a\nb", "goals": ""}
    again = c.post("/api/form", json=body).get_json()      # retried POST: no second write
    assert again["ok"] and again["already"] and len(seen) == 1


def test_form_post_validation_and_mismatch(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"1neon": [_task("h", "1 hcb (5) [15]")],
                                       "today": [_task("p", "plain (10) [5]")]})
    seen = _fake_runner(monkeypatch, {"ok": True})
    assert c.post("/api/form", json={"id": "h", "form": "1hcb", "values": {"weight": "lots"}}).status_code == 400
    assert c.post("/api/form", json={"id": "h", "form": "0s", "values": {"title": "x"}}).status_code == 400
    assert c.post("/api/form", json={"id": "p", "form": "0s", "values": {"title": "x"}}).status_code == 400
    assert c.post("/api/form", json={"id": "h", "form": "1hcb"}).status_code == 400
    assert seen == []


def test_form_post_slow_write_answers_pending(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"0neon": [_task("l", "0l (8) [20]")]})
    _fake_runner(monkeypatch, {}, hang=True)
    j = c.post("/api/form", json={"id": "l", "form": "0s", "values": {"title": "good day"}}).get_json()
    assert j["ok"] and j["pending"]


def test_form_post_runner_failure(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, {"0neon": [_task("l", "0l (8) [20]")]})
    _fake_runner(monkeypatch, {"ok": False, "error": "0s897 write failed"})
    j = c.post("/api/form", json={"id": "l", "form": "0s", "values": {"title": "x"}}).get_json()
    assert j == {"ok": False, "error": "0s897 write failed"}


# ------------------------------------------- runner: argv / writer calls

def _capture_cli(monkeypatch):
    seen = []

    class R:
        returncode = 0
        stdout = "done ✓\n"
        stderr = ""

    def run(args, **kw):
        seen.append([str(a) for a in args])
        return R()
    monkeypatch.setattr(forms.subprocess, "run", run)
    return seen


def test_runner_cli_forms(tmp_path, monkeypatch):
    seen = _capture_cli(monkeypatch)
    assert forms.run_form("0s", {"title": "t", "points_checked": "1"}, tmp_path)["ok"]
    assert seen[-1][1].endswith("0s/0s.py") and seen[-1][2] == "--from-json"
    assert json.loads(Path(seen[-1][3]).read_text())["points_checked"] == "1"
    forms.run_form("1s", {"title": "t"}, tmp_path)
    assert seen[-1][-1] == "--no-mark"                           # as terminal dtd runs it
    forms.run_form("1hcme", {"answer": "calmer"}, tmp_path)
    assert seen[-1][2:] == ["--from-text", "calmer"]
    forms.run_form("1f693", {"who1": "AB", "what1": "unblock (20)", "who2": "", "what2": ""}, tmp_path)
    assert seen[-1][-2:] == ["--no-browser", "--no-launch"]
    assert json.loads(Path(seen[-1][3]).read_text())[0] == {"initials": "AB", "task": "unblock (20)"}
    forms.run_form("0g", {"goal1": "ship {20}"}, tmp_path)
    assert seen[-1][-1] == "--daily"
    assert json.loads(Path(seen[-1][3]).read_text()) == {"goals": ["ship {20}", "", ""]}
    forms.run_form("-1g", {"goal1": "x"}, tmp_path)
    assert "--daily" not in seen[-1]


def test_runner_module_forms(tmp_path, monkeypatch):
    calls = []
    hcb = types.SimpleNamespace(
        week_thursday=lambda d: dt.date(2026, 10, 22), md=lambda d: "10/22",
        parse_weight=lambda s: float(s), bullets=lambda s: s and "• " + s,
        write_week=lambda *a: calls.append(("hcb", a)), mark_done=lambda: "closed")
    i9 = types.SimpleNamespace(
        resolve_week=lambda a, t: (dt.date(2026, 10, 11), dt.date(2026, 10, 17), 44, "10.2"),
        i9_minutes=lambda s, e: 600, check_label=lambda r, l: "10.2",
        write_row=lambda *a: calls.append(("i9", a)), mark_done=lambda: "closed")
    xk = types.SimpleNamespace(
        SHEETS=[{"sheet": "xk88"}, {"sheet": "xk20"}], week_range=lambda a: (dt.date(2026, 10, 11), None),
        week_row_label=lambda s: "10.2", queue_write=lambda a, s, cfg: calls.append(("xk", cfg["sheet"])),
        queue_finish_marks=lambda: ["1 xk88"], drain_writes=lambda: True)
    ma = types.SimpleNamespace(write_note=lambda a, t: calls.append(("ma", a)) or tmp_path / "n.md")
    monkeypatch.setattr(forms, "_load", lambda f: {"1hcb": hcb, "1i9": i9, "xk887": xk, "moralaudit": ma}[f])
    assert forms.run_form("1hcb", {"weight": "191.4", "learned": "x", "goals": ""}, tmp_path)["ok"]
    assert calls[-1] == ("hcb", (dt.date(2026, 10, 22), 191.4, "• x", ""))
    assert forms.run_form("1i9", {"rating": "ma", "done": "a", "exceed": "b"}, tmp_path)["ok"]
    assert calls[-1] == ("i9", (44, "MA", "a", "b", 600))
    r = forms.run_form("xk887", {"xk88_good": "date night"}, tmp_path)
    assert r["ok"] and "closed 1 xk88" in r["message"]
    assert [c for c in calls if c[0] == "xk"] == [("xk", "xk88"), ("xk", "xk20")]
    assert forms.run_form("moralaudit", {"capital__i9__t": "ok"}, tmp_path)["ok"]
    i9.check_label = lambda r, l: "10.3"                     # wrong row: nothing written
    n = len(calls)
    assert not forms.run_form("1i9", {"rating": "MA", "done": "a"}, tmp_path)["ok"] and len(calls) == n
