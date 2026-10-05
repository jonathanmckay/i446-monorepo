#!/usr/bin/env python3
"""A task whose delay fired today floats to the top of its tier (2026-10-05).

User ask: "hide a task until 4pm, but then for it to show up in the top of
its category when it comes back." ctrl-v (delay to 酉) or a /todo block
label hid the task, but on reveal it returned to its old slot, often below
the fold. Both renderers now stable-sort "woken" tasks (snooze fired today,
or block label hour passed) to the top of their tier: terminal dtd's list
generator (_woke/_float in dtd.sh) and dtd web (build_tasks in dtd.py).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
TODAY = dt.date.today().isoformat()


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _tasks():
    return [
        {"id": "A", "content": "alpha task (10) [5]", "labels": ["i9"],
         "priority": 1, "due": TODAY, "recurring": False},
        {"id": "B", "content": "bravo task (10) [5]", "labels": ["i9"],
         "priority": 1, "due": TODAY, "recurring": False},
        {"id": "C", "content": "charlie task (10) [5]", "labels": ["i9"],
         "priority": 1, "due": TODAY, "recurring": False},
    ]


def _order(out: str, names=("alpha", "bravo", "charlie")) -> list[str]:
    pos = {n: out.find(n) for n in names if out.find(n) >= 0}
    return sorted(pos, key=pos.get)


def _run_listgen(tmp: Path, snoozes: dict) -> str:
    home = tmp / "home"
    (home / ".local/state/jm").mkdir(parents=True)
    (home / ".local/state/jm/dtd-block-snooze.json").write_text(
        json.dumps({"date": TODAY, "snoozes": snoozes}))
    (home / "i446-monorepo").symlink_to(Path.home() / "i446-monorepo")
    files = {}
    for name, text in (("cache.json", json.dumps({"updated": f"{TODAY}T10:00:00",
                                                  "today": _tasks()})),
                       ("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}})),
                       ("removed", ""), ("skipped", ""), ("timer", ""), ("view", "")):
        p = tmp / name
        p.write_text(text)
        files[name] = str(p)
    (tmp / "removed.ids").write_text("")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    env = dict(os.environ, HOME=str(home))
    r = subprocess.run([sys.executable, str(payload), files["cache.json"], files["done.json"],
                        files["removed"], TODAY, "120", files["skipped"], files["timer"],
                        files["view"]], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def test_terminal_dtd_fired_delay_floats_to_top(tmp_path):
    # hour 0 has always passed: C's delay fired today
    out = _run_listgen(tmp_path, {"C": 0})
    assert _order(out) == ["charlie", "alpha", "bravo"]


def test_terminal_dtd_still_snoozed_stays_hidden(tmp_path):
    out = _run_listgen(tmp_path, {"C": 99})
    assert _order(out) == ["alpha", "bravo"]


def test_terminal_dtd_no_snooze_keeps_order(tmp_path):
    out = _run_listgen(tmp_path, {})
    assert _order(out) == ["alpha", "bravo", "charlie"]


def _load_web():
    spec = importlib.util.spec_from_file_location("dtd_web_woken", HERE.parent / "dtd" / "dtd.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dtd_web_fired_delay_floats_to_top(tmp_path):
    web = _load_web()
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps({"updated": dt.datetime.now().isoformat(), "today": _tasks()}))
    web.CACHE = cf
    web.DONE_FILE = tmp_path / "completed-today.json"
    web.SNOOZE_FILE = tmp_path / "dtd-block-snooze.json"
    web.MIRROR_DIR = tmp_path / "mirror"
    web.DEFERRED_DIR = tmp_path
    web._refresh_cache_if_stale = lambda force=False: None
    web.SNOOZE_FILE.write_text(json.dumps({"date": TODAY, "snoozes": {"C": 0, "B": 99}}))
    assert [t["id"] for t in web.build_tasks()] == ["C", "A"]
