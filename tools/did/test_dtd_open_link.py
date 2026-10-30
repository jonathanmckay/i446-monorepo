#!/usr/bin/env python3
"""Regression: task links show as (link) and ctrl-l opens them in the task's
Chrome profile (2026-10-13: "m5x2, i9, everything else goes to 个")."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from test_dtd_running_highlight_by_id import _listgen_payload, _row_for_id

HERE = Path(__file__).parent
MONO = HERE.parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("dtd_open_link", HERE / "dtd-open-link.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_profile_by_domain():
    mod = _load()
    assert mod.profile_for({"labels": ["m5x2"]})[1] == "m5c7.com"
    assert mod.profile_for({"labels": ["i9", "#-1g"]})[1] == "MSFT"
    assert mod.profile_for({"labels": ["xk87"]})[1] == "个"
    assert mod.profile_for({})[1] == "个"


def test_first_url_from_markdown_bare_or_description():
    mod = _load()
    assert mod.first_url({"content": "pay [(link)](https://a.com/x) (5) [3]"}) == "https://a.com/x"
    assert mod.first_url({"content": "read https://b.org/p?q=1 (5)"}) == "https://b.org/p?q=1"
    assert mod.first_url({"content": "x", "description": "see https://c.io"}) == "https://c.io"
    assert mod.first_url({"content": "no link"}) is None


def _main(tmp_path, monkeypatch, host, task):
    mod = _load()
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"today": [task]}))
    calls = []
    monkeypatch.setattr(mod.socket, "gethostname", lambda: host)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    hdr = tmp_path / "hdr"
    rc = mod.main([task["id"], str(cache), str(hdr)])
    return rc, calls, hdr.read_text() if hdr.exists() else ""


def test_on_straylight_opens_locally_in_profile(tmp_path, monkeypatch):
    rc, calls, hdr = _main(tmp_path, monkeypatch, "Straylight-Refit.local",
                           {"id": "t", "content": "lease [(link)](https://m.com) (5)", "labels": ["m5x2"]})
    assert rc == 0 and calls == [["open", "-na", "Google Chrome", "--args",
                                  "--profile-directory=Profile 11", "--new-window", "https://m.com"]]
    assert "m5c7.com" in hdr


def test_on_ix_opens_on_straylight_over_ssh(tmp_path, monkeypatch):
    rc, calls, _ = _main(tmp_path, monkeypatch, "ix.local",
                         {"id": "t", "content": "read https://n.com/a (5)", "labels": ["i9"]})
    assert rc == 0 and calls[0][:4] == ["ssh", "-o", "BatchMode=yes", "-o"]
    assert "straylight-refit" in calls[0] and "'--profile-directory=Profile 1' --new-window https://n.com/a" in calls[0][-1]


def test_no_link_says_so(tmp_path, monkeypatch):
    rc, calls, hdr = _main(tmp_path, monkeypatch, "ix.local", {"id": "t", "content": "plain (5)"})
    assert rc == 1 and calls == [] and "no link" in hdr


def test_dtd_binds_ctrl_l_to_open_link():
    dtd = (HERE / "dtd.sh").read_text()
    assert '--bind "ctrl-l:execute-silent(python3 $DTD_OPEN_LINK {2} $DTD_CACHE_FILE $DTD_HDR)' in dtd


# ── list display ──

def _rows(tasks):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        home = tmp / "home"
        (home / "vault/z_ibx/agent-claims").mkdir(parents=True)
        (home / "i446-monorepo").symlink_to(MONO)

        def w(name, text):
            (tmp / name).write_text(text)
            return str(tmp / name)
        args = [w("cache.json", json.dumps({"updated": "2026-10-11T10:00:00", "today": tasks})),
                w("done.json", json.dumps({"date": "2026-10-11", "names": [], "ids": {}})),
                w("removed", ""), "2026-10-11", "120", w("skipped", ""),
                w("timer", f"x\t{int(time.time())}\tnone"), w("view", "")]
        (tmp / "removed.ids").write_text("")
        (tmp / "lg.py").write_text(_listgen_payload())
        env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(home / ".local/state")}
        r = subprocess.run([sys.executable, str(tmp / "lg.py"), *args], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _t(tid, content, short=None):
    t = {"id": tid, "content": content, "labels": ["i9"], "priority": 1, "due": "2026-10-11", "recurring": False}
    if short:
        t["short"] = short
    return t


def test_every_link_shows_as_link():
    out = _rows([_t("B", "read https://example.com/very/long/path?x=1 (5) [3]"),
                 _t("S", "review the long design doc for the new storage tier [(link)](https://d.com) (30) [20]",
                    short="review storage tier design doc (30) [20]"),
                 _t("N", "call [Bob](https://bob.com) re lease (15) [10]")])
    b, s, n = (_row_for_id(out, i) for i in "BSN")
    assert "🔗" in b and "example.com" not in re.sub(r"\x1b\]8;;[^\x1b]*", "", b)
    # the click target opens the task's profile (i9 -> Profile 1) in a new window
    assert "\x1b]8;;hammerspoon://dtdlink?p=Profile%201&u=https%3A%2F%2Fexample.com%2Fvery%2Flong%2Fpath%3Fx%3D1\x1b" in b
    assert "🔗" in s and s.index("🔗") < s.index("(30)")      # short name dropped it; re-added before estimates
    assert "Bob" in n and "🔗" in n
