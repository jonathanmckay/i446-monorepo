#!/usr/bin/env python3
"""Regression: ctrl-t project view must group ritual cards with their domain.

Bug (2026-08-10, user report: "when I do sorting by project -1ibx doesn't get
sorted next to i9"). The project view sorts by domain_of(t), which resolved a
task's domain from its labels alone. Ritual cards carry only the '-1neon'
label (no domain label), so domain_of returned the 'zzz' unlabelled sentinel
and every ritual sank to the bottom of the list — even though the COLOR pass
right below already resolves rituals to a domain via RITUAL_DOMAIN
('-1ibx' → i9, '-1g' → g245, ...).

Fix: domain_of applies the same RITUAL_DOMAIN resolution before falling back
to labels, so 😈 -1ibx groups with the i9 tasks in project view.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()

TODAY = "2026-08-10"


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run_listgen(tmp, cache_obj, view_mode):
    def _w(name, text):
        p = tmp / name
        p.write_text(text)
        return str(p)
    cache = _w("cache.json", json.dumps(cache_obj))
    done = _w("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}}))
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", view_mode)
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), cache, done, removed,
         TODAY, "120", skipped, timer, view],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def _cache():
    return {
        "updated": f"{TODAY}T10:00:00",
        "today": [
            {"id": "RIT_IBX", "content": "😈 -1ibx", "labels": ["-1neon"],
             "due": TODAY, "recurring": True},
            {"id": "I9_1", "content": "review PRs (30) [20]",
             "labels": ["i9"], "due": TODAY, "recurring": False},
            {"id": "XK88_1", "content": "date night plan (10) [15]",
             "labels": ["xk88"], "due": TODAY, "recurring": False},
        ],
    }


def _line_index(out, needle):
    for i, line in enumerate(out.splitlines()):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not rendered:\n{out}")


def test_project_view_groups_neg1ibx_with_i9(tmp_path):
    """The core bug: in project view, -1ibx must sit in the i9 group, not at
    the end with unlabelled tasks."""
    out = _run_listgen(tmp_path, _cache(), "project")
    ibx = _line_index(out, "-1ibx")
    i9 = _line_index(out, "review PRs")
    xk88 = _line_index(out, "date night")
    assert abs(ibx - i9) == 1, (
        f"-1ibx (line {ibx}) must be adjacent to the i9 task (line {i9}) "
        f"in project view; got:\n{out}"
    )
    assert ibx < xk88, (
        "-1ibx must sort in the i9 group (before xk88), not sink to the "
        "unlabelled 'zzz' tail"
    )


def test_default_view_keeps_ritual_first(tmp_path):
    """Sanity: without a view mode, rituals keep their fixed top position."""
    out = _run_listgen(tmp_path, _cache(), "")
    ibx = _line_index(out, "-1ibx")
    assert ibx < _line_index(out, "review PRs")
    assert ibx < _line_index(out, "date night")
