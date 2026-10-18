"""-1g form (2026-10-16): three optional lines -> build-order block goals (on ix)
+ Todoist #-1g tasks, no Claude subprocess. Save logic tested with the writers
mocked; the planning step is pure."""
import importlib.machinery
import importlib.util
import io
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def form():
    loader = importlib.machinery.SourceFileLoader("one_g_form", str(HERE / "-1g-form.py"))
    spec = importlib.util.spec_from_loader("one_g_form", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_plan_goals_get_points_domain_and_plain_todos_split(form):
    p = form.plan(["ship the dtd form @i447 {12}", "call dentist [5]", ""], "午")
    assert p["goals"] == [{"text": "ship the dtd form {12}", "domain": "i447"}]
    assert p["todos"] == [{"text": "call dentist [5]", "domain": "i9"}]


def test_plan_defaults_points_and_block_domain(form):
    p = form.plan(["write the memo", "", ""], "酉")
    assert p["goals"] == [{"text": "write the memo {10}", "domain": "m5x2"}]


class _RC:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def test_all_blank_writes_nothing(form, monkeypatch):
    monkeypatch.setattr(form, "_append_on_ix", lambda *a: pytest.fail("must not write"))
    out = io.StringIO()
    assert form.save(["", "", ""], block="午", out=out) == 0
    assert "nothing written" in out.getvalue()


def test_save_appends_on_ix_creates_tasks_and_closes_ritual_once(form, monkeypatch):
    appended, created, ran = [], [], []
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: (appended.append((b, g)) or g))

    class T:
        @staticmethod
        def create_task(content, **kw):
            created.append((content, kw))
            return {"id": "x"}
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: (ran.append(cmd), _RC())[1])
    out = io.StringIO()
    rc = form.save(["fix janus @i447", "", "call dentist [5]"], block="巳", out=out)
    assert rc == 0
    assert appended == [("巳", ["fix janus {10}"])]
    assert created[0] == ("fix janus {10}", {"labels": ["#-1g", "i447"], "due_string": "today",
                                            "priority": 4, "project_id": form.GOALS_PROJECT})
    assert created[1] == ("call dentist [5]", {"labels": ["i9"], "due_string": "today"})
    # --ritual refreshes the cache itself: exactly one did-fast call, no separate --refresh-cache
    assert [c[-2:] for c in ran] == [["--ritual", "-1g"]]
    assert "1 goals, 1 todos" in out.getvalue()


def test_goals_already_in_build_order_get_no_duplicate_tasks(form, monkeypatch):
    created = []
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: [])   # all already present

    class T:
        @staticmethod
        def create_task(content, **kw):
            created.append(content)
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: _RC())
    form.save(["fix janus", "", ""], block="巳", out=io.StringIO())
    assert created == []


def test_todos_only_refreshes_but_does_not_close_the_ritual(form, monkeypatch):
    ran = []

    class T:
        @staticmethod
        def create_task(content, **kw):
            return {"id": "x"}
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: (ran.append(cmd), _RC())[1])
    form.save(["buy milk [3]", "", ""], block="巳", out=io.StringIO())
    assert [c[-1:] for c in ran] == [["--refresh-cache"]]


def test_ix_unreachable_writes_nothing_locally_and_dumps_recovery(form, monkeypatch, tmp_path):
    monkeypatch.setattr(form, "_on_ix", lambda: False)
    monkeypatch.setattr(form, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(form.nb, "append_block_goals", lambda *a, **k: pytest.fail("no local write"))
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: _RC(255) if cmd[0] == "ssh" else _RC())
    monkeypatch.setitem(sys.modules, "todoist", type("T", (), {"create_task": staticmethod(lambda *a, **k: pytest.fail("no task"))}))
    out = io.StringIO()
    assert form.save(["a goal", "", ""], block="巳", out=out) == 1
    assert "FAILED" in out.getvalue() and list(tmp_path.glob("*.json"))


def test_todoist_failure_is_reported_nonzero(form, monkeypatch):
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: g)

    class T:
        @staticmethod
        def create_task(content, **kw):
            raise RuntimeError("boom")
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: _RC())
    out = io.StringIO()
    assert form.save(["a goal", "", ""], block="巳", out=out) == 1
    assert "FAILED" in out.getvalue()


def test_form_has_escape_save_and_ctrl_q_discard(form):
    src = (HERE / "-1g-form.py").read_text()
    assert '@kb.add("escape", eager=True)' in src and '@kb.add("c-q")' in src


def test_detach_spawns_child_and_reports_in_dtd_header(form, monkeypatch, tmp_path):
    """2026-10-17: returning to dtd waited ~5s on Todoist + Excel + refresh.
    With --detach the form hands the lines to a detached child and exits at
    once; the child reports into dtd's header file."""
    spawned = []
    monkeypatch.setattr(form.subprocess, "Popen", lambda cmd, **kw: spawned.append((cmd, kw)))
    hdr = tmp_path / "hdr"
    monkeypatch.setenv("DTD_HDR_FILE", str(hdr))
    monkeypatch.setattr(form, "LOG", tmp_path / "log")
    form.detach(["fix janus", "", ""], "巳")
    cmd, kw = spawned[0]
    assert cmd[1].endswith("-1g-form.py") and "--from-json" in cmd and cmd[cmd.index("--block") + 1] == "巳"
    assert kw["start_new_session"] is True
    import json as _j
    payload = _j.loads(Path(cmd[cmd.index("--from-json") + 1]).read_text())
    assert payload == {"goals": ["fix janus", "", ""]}
    assert hdr.read_text().startswith("⏳ -1g → 巳")


def test_save_reports_result_into_dtd_header(form, monkeypatch, tmp_path):
    hdr = tmp_path / "hdr"
    monkeypatch.setenv("DTD_HDR_FILE", str(hdr))
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: g)

    class T:
        @staticmethod
        def create_task(content, **kw):
            return {"id": "x"}
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: _RC())
    form.save(["a goal", "", ""], block="巳", out=io.StringIO())
    assert hdr.read_text().startswith("✓ -1g → 巳: 1 goals")


def test_main_detach_skips_blank_form(form, monkeypatch):
    monkeypatch.setattr(form, "run_form", lambda block, daily=False: ["", "", ""])
    monkeypatch.setattr(form, "detach", lambda *a: pytest.fail("blank form must not spawn"))
    monkeypatch.setattr(sys, "argv", ["-1g-form.py", "--detach", "--block", "巳"])
    assert form.main() == 0


# --- 0g daily mode (2026-10-18: "0g should also go to a form like -1g does") ---

def test_plan_daily_converts_square_to_brace_and_defaults_15(form):
    assert form.plan_daily(["ship memo @i9 {20}", "call dentist [5]", "walk"]) == \
        ["ship memo @i9 {20}", "call dentist {5}", "walk {15}"]
    assert form.plan_daily(["", "", ""]) == []
    # [N] kept when {N} coexists (square then stays domain points)
    assert form.plan_daily(["mixed [5] {10}", "", ""]) == ["mixed [5] {10}"]


def test_save_daily_appends_0g_logs_syncs_and_marks_habit(form, monkeypatch):
    appended, ran = [], []
    monkeypatch.setattr(form, "_append_on_ix",
                        lambda b, g, daily=False: (appended.append((b, g, daily)) or g))
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: (ran.append(cmd), _RC())[1])
    out = io.StringIO()
    assert form.save_daily(["fix janus [5]", "", "walk"], out=out) == 0
    assert appended == [("", ["fix janus {5}", "walk {15}"], True)]
    # 0g_log (set-time archive), 0g-sync (canonical Todoist path), did-fast 0g, refresh
    assert [Path(c[1]).name for c in ran] == ["0g_log.py", "0g-sync.py", "did-fast.py", "did-fast.py"]
    assert ran[0][2:] == ["fix janus {5}", "walk {15}"]
    assert ran[1][-1] == "sync" and ran[2][-1] == "0g" and ran[3][-1] == "--refresh-cache"
    assert "2 goals" in out.getvalue()


def test_save_daily_all_duplicates_skips_log_but_still_closes_card(form, monkeypatch):
    ran = []
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g, daily=False: [])
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: (ran.append(cmd), _RC())[1])
    assert form.save_daily(["already set", "", ""], out=io.StringIO()) == 0
    assert [Path(c[1]).name for c in ran] == ["0g-sync.py", "did-fast.py", "did-fast.py"]


def test_save_daily_ix_unreachable_dumps_recovery(form, monkeypatch, tmp_path):
    monkeypatch.setattr(form, "_on_ix", lambda: False)
    monkeypatch.setattr(form, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(form.nb, "append_daily_goals", lambda *a, **k: pytest.fail("no local write"))
    monkeypatch.setattr(form.subprocess, "run",
                        lambda cmd, **kw: _RC(255) if cmd[0] == "ssh" else _RC())
    out = io.StringIO()
    assert form.save_daily(["a goal", "", ""], out=out) == 1
    assert "FAILED" in out.getvalue() and list(tmp_path.glob("*.json"))


def test_daily_detach_passes_daily_flag_and_reports_header(form, monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(form.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd))
    hdr = tmp_path / "hdr"
    monkeypatch.setenv("DTD_HDR_FILE", str(hdr))
    monkeypatch.setattr(form, "LOG", tmp_path / "log")
    form.detach(["walk", "", ""], "巳", daily=True)
    assert "--daily" in spawned[0]
    assert hdr.read_text().startswith("⏳ 0g: saving 1 goal(s)")


def test_main_daily_routes_to_save_daily(form, monkeypatch):
    called = {}
    monkeypatch.setattr(form, "run_form", lambda block, daily=False: ["walk", "", ""])
    monkeypatch.setattr(form, "save_daily", lambda lines, **kw: called.setdefault("lines", lines) and 0 or 0)
    monkeypatch.setattr(form, "save", lambda *a, **k: pytest.fail("block save must not run"))
    monkeypatch.setattr(sys, "argv", ["-1g-form.py", "--daily"])
    assert form.main() == 0
    assert called["lines"] == ["walk", "", ""]
