#!/usr/bin/env python3
"""Regression (2026-08-06): "every time I finish a task the whole list
reorders for like 5 seconds."

A ritual/d359-met completion triggers did-fast --refresh-cache, which
re-fetches tasks from Todoist (a "today|overdue" filter query plus several
separate per-label queries, unioned) and rewrites the shared task-queue.json.
dtd's auto-reload watcher polls that file's mtime, and on a change copies it
wholesale into the session's own cache file and reloads fzf. Todoist's
return order for same-priority-tier tasks is NOT guaranteed identical
call-to-call, and the list generator applied no sort of its own within a
tier — it just rendered whatever order the cache array happened to be in —
so the exact same SET of tasks could render in a different relative order
after a refresh, which read as the whole list reordering.

Fix: sort every task bucket in the loaded cache by id immediately after
load, so render order only ever depends on WHICH tasks exist, never on the
order any given Todoist fetch happened to return them in.
"""
import json
import subprocess
import sys
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run_listgen(tmp, cache_obj):
    def _w(name, obj_or_text):
        p = tmp / name
        p.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
        return str(p)
    cache = _w("cache.json", cache_obj)
    done = _w("done.json", {"date": "2026-08-06", "names": [], "ids": {}})
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", "")
    blockpick = _w("blockpick", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), cache, done, removed,
         "2026-08-06", "120", skipped, timer, view, blockpick],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def _ids_in_order(output: str) -> list[str]:
    return [line.rsplit("\t", 1)[-1] for line in output.splitlines() if line.strip()]


def _task(task_id, content, priority=3, due="2026-08-06"):
    return {"id": task_id, "content": content, "labels": ["i9"],
            "priority": priority, "due": due, "recurring": False}


# Three same-priority (p3), same-section (critical-path) tasks -- exactly
# the case Todoist's fetch order isn't guaranteed to preserve call-to-call.
TASKS = [
    _task("AAA111", "first thing (10) [5]"),
    _task("BBB222", "second thing (10) [5]"),
    _task("CCC333", "third thing (10) [5]"),
]


def test_render_order_is_identical_regardless_of_cache_array_order():
    """The actual reported bug, reproduced: the SAME set of same-priority
    tasks, fed in two DIFFERENT array orders (simulating two different
    Todoist fetches), must render in the SAME order both times."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        order_a = {"updated": "t", "today": [], "关键路径": list(TASKS)}
        order_b = {"updated": "t", "today": [], "关键路径": list(reversed(TASKS))}

        out_a = _run_listgen(tmp, order_a)
        out_b = _run_listgen(tmp, order_b)

        ids_a = [i for i in _ids_in_order(out_a) if i in {"AAA111", "BBB222", "CCC333"}]
        ids_b = [i for i in _ids_in_order(out_b) if i in {"AAA111", "BBB222", "CCC333"}]

        assert ids_a == ids_b, (
            f"render order changed between two fetches of the same task set: "
            f"{ids_a!r} vs {ids_b!r} -- this is the 'whole list reorders' bug")


def test_render_order_is_by_id_within_a_priority_tier():
    """More specific than the round-trip check above: pin the actual
    resulting order to something deterministic (ascending id), not just
    'the same both times' -- catches a fix that's merely stable but keyed
    off something incidental (e.g. dict insertion order) instead of a real
    sort key."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = {"updated": "t", "today": [], "关键路径": list(reversed(TASKS))}
        out = _run_listgen(tmp, cache)
        ids = [i for i in _ids_in_order(out) if i in {"AAA111", "BBB222", "CCC333"}]
        assert ids == ["AAA111", "BBB222", "CCC333"]


def test_cache_load_sorts_every_list_bucket_by_id():
    """Structural: the fix must sort ALL task buckets right after load (not
    just 'today'), since 0neon/1neon/夜neon/关键路径 are independent
    Todoist fetches with the same call-to-call ordering instability."""
    body = _listgen_payload()
    i = body.index("with open(cache_file) as f:")
    j = body.index("try:", i)
    load_region = body[i:j]
    assert "for _k, _v in d.items():" in load_region
    assert "isinstance(_v, list)" in load_region
    assert "sorted(_v, key=" in load_region and "t.get('id'" in load_region


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
