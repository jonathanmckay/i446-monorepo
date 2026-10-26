"""Project inference for new tg-fast entries typed without a project
(task 6hqxfpPgRG33qM9c): history map from toggl.sqlite, then Haiku. No real
sqlite store, Toggl or Anthropic is touched."""
import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import project_infer as pi  # noqa: E402

VALID = {"hcb", "hcbp", "i9", "xk87", "g245", "m5x2", "睡觉", "hcmc"}


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "toggl.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE entries (day TEXT, description TEXT, project TEXT)")
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=400)).isoformat()
    rows = (
        [(today, "hiit (30)", "hcbp")] * 4
        + [(today, "HIIT", "hcb")]                      # 4/5 = 80% hcbp
        + [(today, "lego", "xk87")] * 2 + [(today, "lego", "i9")] * 2   # split
        + [(today, "nap", "睡觉")] * 5                  # never inferred
        + [(today, "ren math", "xk87")] * 2             # too few
        + [(today, f"m5x2 strat {i}", "m5x2") for i in range(6)]
        + [(today, f"m5x2 deck v{i}", "m5x2") for i in range(6)]
        + [(old, "chess", "hcmc")] * 9                  # outside the window
        + [(today, "", "i9")] * 5
        + [(today, "untagged", "no project")] * 5
    )
    conn.executemany("INSERT INTO entries VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return p


def test_normalize():
    assert pi.normalize("HIIT (30) [5] @hcb #-2") == "hiit"
    assert pi.normalize("o314, 0930 journaling!") == "o314 journaling"
    assert pi.normalize("-1g") == "1g"


def test_history_map(db):
    m = pi.build_map(db, VALID)
    assert pi.from_history("hiit", m, VALID) == "hcbp"
    assert pi.from_history("Hiit (20)", m, VALID) == "hcbp"
    assert pi.from_history("lego", m, VALID) == ""          # no clear majority
    assert pi.from_history("nap", m, VALID) == ""           # sleep never inferred
    assert pi.from_history("ren math", m, VALID) == ""      # < 3 occurrences
    assert pi.from_history("chess", m, VALID) == ""         # older than window
    assert pi.from_history("untagged", m, VALID) == ""
    # first-word prefix fallback (12 m5x2 entries starting "m5x2")
    assert pi.from_history("m5x2 budget review", m, VALID) == "m5x2"
    assert "hcbp" in m["examples"] and "睡觉" not in m["examples"]


def test_cache_rebuild_and_reuse(db, tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(pi, "DB_PATH", db)
    monkeypatch.setattr(pi, "CACHE_PATH", cache)
    m = pi.load_map(VALID)
    assert cache.exists() and m["exact"]["hiit"][0] == "hcbp"
    # fresh cache is reused without touching the db
    monkeypatch.setattr(pi, "build_map", lambda *a, **k: pytest.fail("rebuilt"))
    assert pi.load_map(VALID)["exact"]["hiit"][0] == "hcbp"
    # stale cache + failing rebuild -> stale cache, not a crash
    data = json.loads(cache.read_text()); data["built"] = 0
    cache.write_text(json.dumps(data))
    monkeypatch.setattr(pi, "build_map", lambda *a, **k: 1 / 0)
    assert pi.load_map(VALID)["exact"]["hiit"][0] == "hcbp"


def _reply(text):
    return lambda body, timeout: {"content": [{"type": "text", "text": text}]}


def test_haiku(monkeypatch):
    monkeypatch.setattr(pi, "_api_key", lambda: "sk-test")
    seen = {}

    def post(body, timeout):
        seen["body"] = json.loads(body); seen["timeout"] = timeout
        return {"content": [{"type": "text", "text": " hcbp\n"}]}

    assert pi.from_haiku("spin class", VALID, {"hcbp": ["hiit"]}, post=post) == "hcbp"
    assert seen["body"]["model"] == "claude-haiku-5-5"
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert seen["timeout"] <= 3.0
    prompt = seen["body"]["messages"][0]["content"]
    assert "hcbp: hiit" in prompt and "睡觉" not in prompt
    assert pi.from_haiku("x", VALID, {}, post=_reply("none")) == ""
    assert pi.from_haiku("x", VALID, {}, post=_reply("睡觉")) == ""
    assert pi.from_haiku("x", VALID, {}, post=_reply("bogus")) == ""

    def boom(body, timeout):
        raise TimeoutError
    assert pi.from_haiku("x", VALID, {}, post=boom) == ""
    monkeypatch.setattr(pi, "_api_key", lambda: None)
    assert pi.from_haiku("x", VALID, {}, post=pytest.fail) == ""


def test_infer_order_and_kill_switch(monkeypatch):
    monkeypatch.delenv("TG_NO_INFER", raising=False)
    monkeypatch.setattr(pi, "load_map", lambda valid: {"exact": {"hiit": ["hcbp", 5, 5]}})
    monkeypatch.setattr(pi, "from_haiku", lambda *a, **k: "i9")
    assert pi.infer("hiit", VALID) == ("hcbp", "history")
    assert pi.infer("standup", VALID) == ("i9", "haiku")
    monkeypatch.setenv("TG_NO_INFER", "1")
    assert pi.infer("hiit", VALID) == ("", "")


# ---- tg-fast integration ---------------------------------------------------

def _load_tg():
    spec = importlib.util.spec_from_file_location("tg_fast_infer", HERE / "tg-fast.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tg(monkeypatch):
    mod = _load_tg()
    monkeypatch.setattr(mod, "_project_from_task_cache", lambda raw: "")
    monkeypatch.setattr(mod, "_get_toggl_projects", lambda: VALID)
    calls = []
    monkeypatch.setattr(mod, "_infer_project", lambda d: calls.append(d) or "hcbp")
    cli = []

    def fake_cli(*a):
        cli.append(a)
        return "Started: spin [id:1]"
    monkeypatch.setattr(mod, "_run_cli", fake_cli)
    monkeypatch.setattr(mod, "_update_cache", lambda *a, **k: None)
    mod._calls, mod._cli = calls, cli
    return mod


def test_resolve_default_does_not_infer(tg):
    assert tg.resolve("spin class") == ("spin class", "", [])
    assert tg._calls == []


def test_resolve_infer_only_when_no_project(tg):
    assert tg.resolve("spin class", infer=True)[1] == "hcbp"
    assert tg.resolve("spin @i9", infer=True)[1] == "i9"       # explicit wins
    assert tg.resolve("睡觉", infer=True)[1] == "睡觉"          # shortcode, no infer
    assert tg.resolve("hcm", infer=True)[1] == "hcm"            # domain-only
    assert tg._calls == ["spin class"]


def test_process_entry_marks_inferred(tg):
    out = tg._process_entry("spin class")
    assert out.endswith("→ hcbp (inferred)")
    assert tg._cli[-1] == ("start", "spin class", "hcbp")
    out = tg._process_entry("spin @i9")
    assert "inferred" not in out


def test_process_entry_inferred_range(tg, monkeypatch):
    class Api:
        @staticmethod
        def trim_range(*a, **k):
            return []
    monkeypatch.setattr(tg, "_toggl_api", lambda: Api)
    out = tg._process_entry("spin class 0900-0930")
    assert tg._cli[-1][:5] == ("create", "spin class", "09:00", "09:30", "hcbp")
    assert out.endswith("→ hcbp (inferred)")


def test_tg_no_infer_env_disables_real_inference(monkeypatch):
    mod = _load_tg()
    monkeypatch.setenv("TG_NO_INFER", "1")
    monkeypatch.setattr(mod, "_project_from_task_cache", lambda raw: "")
    assert mod.resolve("spin class", infer=True)[1] == ""


def test_janus_optimistic_guess_uses_cached_history_only(monkeypatch):
    spec = importlib.util.spec_from_file_location("janus_infer_t", HERE / "janus.py")
    janus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(janus)
    monkeypatch.delenv("TG_NO_INFER", raising=False)
    monkeypatch.setattr(janus, "_known_project_codes", lambda: VALID)
    seen = {}

    def fake_load(valid=None, rebuild=True):
        seen["rebuild"] = rebuild
        return {"exact": {"spin class": ["hcbp", 5, 5]}}
    monkeypatch.setattr(pi, "load_map", fake_load)
    monkeypatch.setattr(pi, "from_haiku", lambda *a, **k: pytest.fail("haiku on paint path"))
    janus._guess_project_code("lego")      # loads the tg-fast tables
    monkeypatch.setattr(janus._TG_MOD, "_search_task_cache", lambda raw, valid: "")
    assert janus._guess_project_code("spin class") == "hcbp"
    assert seen["rebuild"] is False
    assert janus._guess_project_code("spin class @i9") == "i9"
