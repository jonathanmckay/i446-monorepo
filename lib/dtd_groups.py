"""Task groups: a Todoist parent with ONE level of subtasks (JM 2026-11-23).

An outcome ("purchase l912 [1360]") is a parent task; its inputs are subtasks.
Rules, as settled with JM:

  visibility  A child shows only when it AND its parent are due. A deferred,
              snoozed or closed parent hides every child. Unknown parent state
              means SHOW: a child is hidden only on positive evidence, never
              because the parent happens to be missing from the cache.
  one block   A group is shown together: the outcome, then its due inputs
              beneath it, densest (分 per minute) first. The block sits where
              its densest input ranks.
  value       The parent's [N] is the REMAINING value. Completing a child pays
              the child and takes the same amount off the parent (floor 0).
              Closing the parent pays what is left; Todoist closes the children
              with it (verified live 2026-11-23). No deadlines, no reserve
              floor, no stored original total: JM edits [N] by hand.
  recurring   A recurring parent is not a group (its [N] would drain for good).

Everything here is pure or takes its I/O as arguments, so both renderers
(tools/did/dtd.sh list generator, tools/dtd/dtd.py build_tasks), both cache
writers (did-fast --refresh-cache, refresh-cache.py) and did-fast's completion
path share one implementation. Tests: lib/test_dtd_groups.py.
"""
from __future__ import annotations

import fcntl
import json
import re
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import fen_split

GROUP_LABEL = "grp"          # marks a group parent in Todoist (set on first decrement)
AGENT_LABEL = "agent"        # an agent is doing this input; dimmed, last in its block
WAITING_LABEL = "waiting"    # the next move is someone else's; shown on its follow-up day
MAX_PARENT_FETCH = 25        # live GETs per cache refresh for parents not in the cache
_MIN_RE = re.compile(r"\((\d+)\)")


# ---------------------------------------------------------------------------
# Density
# ---------------------------------------------------------------------------

def ppm(content: str) -> float:
    """分 per minute: every [N] summed over the (N) estimate; 30 minutes when
    the task carries none. Same rule as dtd.sh's `_fen_per_min`."""
    pts = fen_split.total_points(content or "")
    if not pts:
        return 0.0
    m = _MIN_RE.search(content or "")
    mins = int(m.group(1)) if m and int(m.group(1)) > 0 else 30
    return pts / mins


# ---------------------------------------------------------------------------
# Cache: carry the parent's state on each child row
# ---------------------------------------------------------------------------

def raw_parent_id(raw: dict) -> str:
    """The parent id of a raw Todoist task dict ("" for a top-level task)."""
    return str((raw or {}).get("parent_id") or "")


def _list_rows(cache: dict) -> Iterable[dict]:
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict):
                    yield t


def attach_parents(cache: dict, get_task: Optional[Callable[[str], Optional[dict]]] = None,
                   max_fetch: int = MAX_PARENT_FETCH) -> int:
    """Stamp `parent_due`, `parent_content`, `parent_recurring` and
    `parent_closed` on every cached row that has a `parent_id`.

    The parent is read from the cache when it is there, else from `get_task`
    (a live GET, at most `max_fetch` per call). When neither answers, the row
    keeps whatever stamps it already had, or none: collapse() then shows it.
    Returns the number of rows stamped."""
    by_id: dict[str, dict] = {}
    for t in _list_rows(cache):
        by_id.setdefault(str(t.get("id")), t)
    fetched: dict[str, Optional[dict]] = {}
    stamped = 0
    for t in _list_rows(cache):
        pid = str(t.get("parent_id") or "")
        if not pid:
            continue
        p = by_id.get(pid)
        if p is not None:
            info = {"parent_due": (p.get("due") or "")[:10],
                    "parent_content": p.get("content") or "",
                    "parent_recurring": bool(p.get("recurring")),
                    "parent_closed": False}
        else:
            if pid not in fetched:
                raw = None
                if get_task is not None and len(fetched) < max_fetch:
                    try:
                        raw = get_task(pid)
                        if raw is None:          # 404: deleted
                            raw = {"checked": True}
                    except Exception:            # noqa: BLE001 — unknown, keep old stamps
                        raw = None
                fetched[pid] = raw
            raw = fetched[pid]
            if raw is None:
                continue
            due = raw.get("due") if isinstance(raw.get("due"), dict) else {}
            info = {"parent_due": ((due or {}).get("date") or "")[:10],
                    "parent_content": raw.get("content") or "",
                    "parent_recurring": bool((due or {}).get("is_recurring")),
                    "parent_closed": bool(raw.get("checked"))}
        t.update(info)
        stamped += 1
    return stamped


# ---------------------------------------------------------------------------
# Rendering: collapse each group to one row
# ---------------------------------------------------------------------------

def parent_ids(cache: dict) -> set:
    """Ids of every task some cached group child points at, whether or not
    that child is currently shown."""
    return {str(t["parent_id"]) for t in _list_rows(cache) if is_group_child(t)}


def is_group_parent(t: dict, parents: Iterable = ()) -> bool:
    """A row that is an outcome: it carries the group label or has a cached
    subtask. Never a recurring task."""
    if t.get("recurring"):
        return False
    return GROUP_LABEL in (t.get("labels") or []) or str(t.get("id")) in parents


def is_group_child(t: dict) -> bool:
    return bool(t.get("parent_id")) and not t.get("parent_recurring")


def child_hidden(t: dict, today_iso: str, hidden_parents: frozenset = frozenset()) -> bool:
    """True only on positive evidence that the parent is not due."""
    if not is_group_child(t):
        return False
    if t.get("parent_closed"):
        return True
    if str(t.get("parent_id")) in hidden_parents:
        return True
    pdue = (t.get("parent_due") or "")[:10]
    return bool(pdue) and pdue > today_iso


# Rows in these tiers keep their own place in dtd (block rituals, block and
# daily goals, daily and weekly habits): a subtask carrying one is shown where
# its tier puts it, not pulled into its group's block.
PINNED_LABELS = frozenset({"-1neon", "#-1g", "#0g", "0neon", "夜neon", "1neon"})


def _pinned(t: dict) -> bool:
    return bool(PINNED_LABELS & set(t.get("labels") or []))


def layout(rows: list, today_iso: str, hidden_parents: Iterable = ()) -> list:
    """`rows` = the tasks that survived every other hide, in display order.
    Returns the rows to show with each group as ONE contiguous block (JM
    2026-11-23: collocated): the outcome first, flagged `group_head`, then its
    due inputs beneath it, flagged `group_child`, densest first, an agent's
    inputs last. The block sits where its densest input ranked. Flagged rows
    are copies; the input list is not mutated.

    A child is dropped only on positive evidence that its parent is not due
    (see child_hidden). When the parent row itself is not among `rows` (its
    state is unknown, or it is undated) the inputs still form a block, with no
    head. Pinned-tier rows stay where they are. `hidden_parents` = ids of
    parents the caller knows to be snoozed, deferred, removed or completed."""
    hp = frozenset(str(i) for i in hidden_parents)
    by_id = {str(t.get("id")): t for t in rows}
    groups: dict[str, list] = {}
    for t in rows:
        if is_group_child(t) and not _pinned(t) and not child_hidden(t, today_iso, hp):
            groups.setdefault(str(t["parent_id"]), []).append(t)

    def rank(k):
        return (AGENT_LABEL in (k.get("labels") or []), -ppm(k.get("content") or ""))

    anchor: dict[str, str] = {}
    for pid, kids in groups.items():
        kids.sort(key=rank)                       # stable: ties keep display order
        anchor[str(kids[0].get("id"))] = pid
    out = []
    for t in rows:
        tid = str(t.get("id"))
        if tid in groups and not _pinned(t):
            continue                              # the head is emitted with its block
        if is_group_child(t) and not _pinned(t):
            pid = anchor.get(tid)
            if pid is None:
                continue                          # hidden, or emitted with its block
            head = by_id.get(pid)
            if head is not None and not _pinned(head):
                h = dict(head)
                h["group_head"] = True
                h["group_size"] = len(groups[pid])
                out.append(h)
            for k in groups[pid]:
                c = dict(k)
                c["group_child"] = True
                out.append(c)
            continue
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Value: take a paid child's points off the parent, reversibly
# ---------------------------------------------------------------------------

def add_points(content: str, taken: list) -> str:
    """Undo of fen_split.take_points: add each (n, code) back onto the first
    pair with that code (the first pair at all when none matches)."""
    out = content or ""
    for n, code in taken:
        matches = list(fen_split.PAIR_RE.finditer(out))
        if not matches:
            out = f"{out.rstrip()} [{int(n)}]" + (f" @{code}" if code else "")
            continue
        m = next((x for x in matches if x.group(2) == code), matches[0])
        new = int(m.group(1)) + int(n)
        out = out[:m.start()] + f"[{new}]" + (f" @{m.group(2)}" if m.group(2) else "") + out[m.end():]
    return out


def _journal_rows(journal: Path) -> list:
    try:
        return [json.loads(l) for l in journal.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def decrement_parent(child_id: str, parent_id: str, paid: int, day: str, *,
                     journal: Path, get_task: Callable[[str], Optional[dict]],
                     update_task: Callable[[str, dict], object]) -> dict:
    """Take `paid` points off the parent's [N] after its child was paid.

    Idempotent per (child, day): a retry finds the journal line and does
    nothing. Serialised by a flock on the journal so two children closed
    seconds apart cannot overwrite each other's rewrite. Returns a record:
    {"status": "ok"|"dup"|"skip"|"error", "taken": [[n, code]], ...}."""
    key = f"{child_id}:{day}"
    rec: dict = {"key": key, "child": str(child_id), "parent": str(parent_id),
                 "paid": int(paid or 0), "taken": [], "status": "skip"}
    if not parent_id or int(paid or 0) <= 0:
        rec["why"] = "nothing paid"
        return rec
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if any(r.get("key") == key and not r.get("reversed")
                   and r.get("status") == "ok" for r in _journal_rows(journal)):
                rec.update(status="dup")
                return rec
            try:
                p = get_task(str(parent_id))
            except Exception as e:                    # noqa: BLE001
                rec.update(status="error", why=f"get parent: {e}")
                return rec
            if not p or p.get("checked"):
                rec["why"] = "parent closed or gone"
                return rec
            if isinstance(p.get("due"), dict) and p["due"].get("is_recurring"):
                rec["why"] = "recurring parent is not a group"
                return rec
            before = p.get("content") or ""
            after, taken = fen_split.take_points(before, int(paid))
            body: dict = {}
            if after != before:
                body["content"] = after
            labels = list(p.get("labels") or [])
            if GROUP_LABEL not in labels:
                body["labels"] = labels + [GROUP_LABEL]
            try:
                if body:
                    update_task(str(parent_id), body)
            except Exception as e:                    # noqa: BLE001
                rec.update(status="error", why=f"update parent: {e}")
                return rec
            rec.update(status="ok", taken=[[n, c] for n, c in taken],
                       before=before, after=after, ts=time.time())
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            return rec
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def restore_parent(child_id: str, day: str, *, journal: Path,
                   get_task: Callable[[str], Optional[dict]],
                   update_task: Callable[[str, dict], object]) -> dict:
    """Undo decrement_parent for (child, day): add back exactly what was
    taken (never a pre-image, so a sibling's later decrement survives)."""
    key = f"{child_id}:{day}"
    out: dict = {"key": key, "status": "none"}
    if not journal.exists():
        return out
    with open(journal, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            rows = _journal_rows(journal)
            hit = next((r for r in reversed(rows) if r.get("key") == key
                        and r.get("status") == "ok" and not r.get("reversed")), None)
            if hit is None:
                return out
            try:
                p = get_task(str(hit["parent"]))
                if p and not p.get("checked") and hit.get("taken"):
                    update_task(str(hit["parent"]),
                                {"content": add_points(p.get("content") or "", hit["taken"])})
            except Exception as e:                    # noqa: BLE001
                out.update(status="error", why=str(e))
                return out
            hit["reversed"] = True
            # Rewrite in place: replacing the file would swap the inode out from
            # under the lock another process is waiting on.
            fh.truncate(0)
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            fh.flush()
            out.update(status="ok", parent=hit["parent"], taken=hit.get("taken"))
            return out
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def pay_child(child_id: str, parent_id: str, paid: int, day: str, **kw) -> dict:
    """decrement_parent with the real journal and Todoist client: the one call
    for payment paths outside did-fast (run.py, split-fast.py). `day` is an
    ISO date, optionally suffixed (":split:<id>") when one child can be paid
    more than once in a day."""
    import state_paths
    import todoist
    kw.setdefault("journal", state_paths.STATE_DIR / "group-decrements.jsonl")
    kw.setdefault("get_task", todoist.get_task)
    kw.setdefault("update_task", lambda i, body: todoist._request("POST", f"/tasks/{i}", body))
    return decrement_parent(child_id, parent_id, paid, day, **kw)


def unpay_child(key: str, **kw) -> dict:
    """restore_parent by journal key ("<child>:<day>[:suffix]")."""
    import state_paths
    import todoist
    kw.setdefault("journal", state_paths.STATE_DIR / "group-decrements.jsonl")
    kw.setdefault("get_task", todoist.get_task)
    kw.setdefault("update_task", lambda i, body: todoist._request("POST", f"/tasks/{i}", body))
    child, _, day = key.partition(":")
    return restore_parent(child, day, **kw)
