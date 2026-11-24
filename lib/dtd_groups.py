"""Task groups: a Todoist parent with ONE level of subtasks (JM 2026-11-23).

An outcome ("purchase l912 [1360]") is a parent task; its inputs are subtasks.
Rules, as settled with JM:

  visibility  A child shows only when it AND its parent are due. A deferred,
              snoozed or closed parent hides every child. Unknown parent state
              means SHOW: a child is hidden only on positive evidence, never
              because the parent happens to be missing from the cache.
  one block   A group is shown together: the outcome, then its due inputs
              beneath it, densest (分 per minute) first. The block sits where
              the OUTCOME ranks: move the parent and its inputs follow.
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
import os
import re
import sys
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
                   max_fetch: int = MAX_PARENT_FETCH, journal: Optional[Path] = None) -> int:
    """Stamp `parent_due`, `parent_content`, `parent_recurring` and
    `parent_closed` on every cached row that has a `parent_id`.

    The parent is read from the cache when it is there, else from `get_task`
    (a live GET, at most `max_fetch` per call). When neither answers, the row
    keeps whatever stamps it already had, or none: collapse() then shows it.
    Returns the number of rows stamped.

    Every cache writer ends here, so this is also where a parent's recent
    decrement is laid over rows fetched before it landed (overlay_recent)."""
    journal = journal or _default_journal()
    if journal is not None:
        try:
            overlay_recent(cache, journal)
        except Exception:                        # noqa: BLE001 — never fail a refresh
            pass
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
# its tier puts it, not pulled into its group's block. One exception, display
# only: a live habit card is ADOPTED into the block of its own dated copies
# (see adopted_rows).
PINNED_LABELS = frozenset({"-1neon", "#-1g", "#0g", "0neon", "夜neon", "1neon"})


def _pinned(t: dict) -> bool:
    return bool(PINNED_LABELS & set(t.get("labels") or []))


_ANN_RE = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_DATED_COPY_RE = re.compile(r"^(.+?)\s+\d{1,2}\.\d{1,2}$")


def _bare(content: str) -> str:
    """A card's name without (N)/[N]/{N}, a leading 😈, case or extra spaces."""
    s = _ANN_RE.sub("", content or "").replace("\U0001F608", "")
    return " ".join(s.lower().split())


def adopted_rows(rows: list, groups: dict) -> dict:
    """{parent id: [row, ...]}: live habit cards shown inside a group's block
    (JM 2026-11-24: the daily `ibx m5x2` under its backlog of `ibx m5x2 11.16`
    day cards). A row is adopted when it is top-level, RECURRING, not itself a
    group head, and its bare name is the habit that one of the block's inputs
    is a dated copy of. Display only: the copy gets no parent_id, so its
    completion, points and hide rules are those of the habit. Each row is
    adopted once (first group in display order wins)."""
    wanted: dict[str, str] = {}
    for pid, kids in groups.items():
        for k in kids:
            m = _DATED_COPY_RE.match(_bare(k.get("content") or ""))
            if m:
                wanted.setdefault(m.group(1), pid)
    out: dict[str, list] = {}
    if not wanted:
        return out
    for t in rows:
        if t.get("parent_id") or not t.get("recurring") or str(t.get("id")) in groups:
            continue
        pid = wanted.pop(_bare(t.get("content") or ""), None)
        if pid is not None:
            out.setdefault(pid, []).append(t)
    return out


def layout(rows: list, today_iso: str, hidden_parents: Iterable = ()) -> list:
    """`rows` = the tasks that survived every other hide, in display order.
    Returns the rows to show with each group as ONE contiguous block (JM
    2026-11-23: collocated): the outcome first, flagged `group_head`, then its
    due inputs beneath it, flagged `group_child`, densest first, an agent's
    inputs last. The block sits where the outcome itself ranked. Flagged rows
    are copies; the input list is not mutated.

    A child is dropped only on positive evidence that its parent is not due
    (see child_hidden). When the parent row itself is not among `rows` (its
    state is unknown, or it is undated) the inputs still form a block, with no
    head. Pinned-tier rows stay where they are, except a live habit card
    adopted by its dated copies (adopted_rows): it is listed first in their
    block, flagged `group_child` and `group_adopted`. A block with no head
    to follow sits at its adopted habit, else at its densest input. `hidden_parents` = ids of
    parents the caller knows to be snoozed, deferred, removed or completed."""
    hp = frozenset(str(i) for i in hidden_parents)
    by_id = {str(t.get("id")): t for t in rows}
    groups: dict[str, list] = {}
    for t in rows:
        if is_group_child(t) and not _pinned(t) and not child_hidden(t, today_iso, hp):
            groups.setdefault(str(t["parent_id"]), []).append(t)

    def rank(k):
        return (AGENT_LABEL in (k.get("labels") or []), -ppm(k.get("content") or ""))

    adopted = adopted_rows(rows, groups)
    adopted_pid = {str(a.get("id")): pid for pid, rs in adopted.items() for a in rs}
    anchor: dict[str, str] = {}
    for pid, kids in groups.items():
        kids.sort(key=rank)                       # stable: ties keep display order
        # The block sits where its OUTCOME ranks (JM 2026-11-24: "sorting is
        # based on the parent task"; move the parent and its inputs move with
        # it). With no head to follow (parent not in the list, or pinned to a
        # tier of its own) it sits at its adopted habit, else its densest input.
        head = by_id.get(pid)
        if head is not None and not _pinned(head):
            lead = head
        else:
            lead = adopted[pid][0] if adopted.get(pid) else kids[0]
        anchor[str(lead.get("id"))] = pid
    out = []
    for t in rows:
        tid = str(t.get("id"))
        if tid in adopted_pid or (is_group_child(t) and not _pinned(t)) \
                or (tid in groups and not _pinned(t)):
            pid = anchor.get(tid)
            if pid is None:
                continue                          # hidden, or emitted with its block
            head = by_id.get(pid)
            if head is not None and not _pinned(head):
                h = dict(head)
                h["group_head"] = True
                h["group_size"] = len(groups[pid])
                out.append(h)
            for a in adopted.get(pid, ()):
                c = dict(a)
                c["group_child"] = True
                c["group_adopted"] = True
                out.append(c)
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


# ---------------------------------------------------------------------------
# Cache overlay: the parent's rewritten [N] reaches dtd without a refresh
# ---------------------------------------------------------------------------
# JM 2026-11-23: closing an input rewrote the parent in Todoist ([120] to
# [110]) but dtd kept showing [120] until a manual refresh. The cache row is
# only rewritten by a full refresh, and several writers copy rows forward
# without re-reading Todoist. So the journal is the source: every cache
# writer lays its recent records over the rows (attach_parents), and the
# payment itself patches the cache file at once (patch_cache_file).

OVERLAY_MAX_AGE = 300        # seconds a journal record keeps correcting cached rows
CACHE_NAME = "task-queue.json"   # beside the journal in STATE_DIR


def _default_journal() -> Optional[Path]:
    """The real journal, except under pytest (tests pass their own)."""
    if "pytest" in sys.modules:
        return None
    try:
        import state_paths
        return state_paths.STATE_DIR / "group-decrements.jsonl"
    except Exception:                            # noqa: BLE001
        return None


def _carry_points(short: str, old: str, new: str) -> Optional[str]:
    """The display name `short` ends in the task's own estimates, and both dtd
    surfaces show it instead of `content`. Give it `new`'s [N] pairs; None
    when it does not carry `old`'s pairs (the caller then drops it)."""
    sm = list(fen_split.PAIR_RE.finditer(short or ""))
    nm = [m.group(0) for m in fen_split.PAIR_RE.finditer(new or "")]
    if fen_split.parse_points(short or "") != fen_split.parse_points(old or "") or len(sm) != len(nm):
        return None
    out, last = [], 0
    for m, txt in zip(sm, nm):
        out += [short[last:m.start()], txt]
        last = m.end()
    out.append(short[last:])
    return "".join(out)


def _advance(val: str, seq: list) -> str:
    """Walk `val` along one parent's rewrites [(from, to), ...] in time order.
    Already at the newest text: leave it. Otherwise start at the LAST step
    that begins where `val` is, so a value the chain passes through twice
    (decrement, sibling decrement, undo) is not sent round again. A value the
    chain never held (JM edited it) is left alone."""
    if not seq or val == seq[-1][1]:
        return val
    idx = next((i for i in range(len(seq) - 1, -1, -1) if seq[i][0] == val), None)
    if idx is None:
        return val
    for frm, to in seq[idx:]:
        if val == frm:
            val = to
    return val


def overlay_recent(cache: dict, journal: Path, now: Optional[float] = None,
                   max_age: float = OVERLAY_MAX_AGE) -> int:
    """Bring cached group rows up to the journal's recent decrements and
    restores: the parent row's `content` and `short`, and each input's
    `parent_content` stamp. Returns the number of fields changed."""
    now = time.time() if now is None else now
    steps: list = []
    for r in _journal_rows(journal):
        if r.get("status") != "ok":
            continue
        pid = str(r.get("parent") or "")
        events = [(r.get("ts"), r.get("before"), r.get("after"))]
        if r.get("reversed"):
            events.append((r.get("restored_ts"), r.get("restored_from"), r.get("restored")))
        for ts, frm, to in events:
            if pid and ts and frm is not None and to is not None and frm != to:
                steps.append((ts, pid, frm, to))
    if not steps or now - max(s[0] for s in steps) > max_age:
        return 0
    newest: dict[str, float] = {}
    chains: dict[str, list] = {}
    for ts, pid, frm, to in sorted(steps, key=lambda s: s[0]):
        chains.setdefault(pid, []).append((frm, to))
        newest[pid] = ts
    changed = 0
    for t in _list_rows(cache):
        tid, pid = str(t.get("id")), str(t.get("parent_id") or "")
        if tid in chains and now - newest[tid] <= max_age:
            old = t.get("content") or ""
            new = _advance(old, chains[tid])
            if new != old:
                t["content"] = new
                if t.get("short"):
                    s = _carry_points(t["short"], old, new)
                    if s:
                        t["short"] = s
                    else:
                        t.pop("short", None)
                changed += 1
        if pid in chains and now - newest[pid] <= max_age:
            old = t.get("parent_content") or ""
            new = _advance(old, chains[pid])
            if new != old:
                t["parent_content"] = new
                changed += 1
    return changed


def patch_cache_file(journal: Path, cache_path: Optional[Path] = None,
                     wait: float = 2.0) -> int:
    """overlay_recent on the cache file itself, under the refresh lock, so
    dtd shows the parent's new [N] as soon as its input is paid. When the
    lock is busy a refresh is in flight and will overlay on its own way out
    (attach_parents), so give up quietly. Returns the fields changed."""
    cache_path = cache_path or (journal.parent / CACHE_NAME)
    if not cache_path.exists():
        return 0
    with open(cache_path.with_suffix(".lock"), "w") as lk:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    return 0
                time.sleep(0.1)
        try:
            before_m = cache_path.stat().st_mtime
            cache = json.loads(cache_path.read_text())
            n = overlay_recent(cache, journal)
            if not n:
                return 0
            # Own tmp name: ".tmp" is shared by the refreshers.
            tmp = cache_path.with_name(f"{cache_path.name}.grp{os.getpid()}.tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, cache_path)
            # dtd.sh reloads on a whole-second mtime change; a patch landing in
            # the same second as the last write would never be seen.
            if int(cache_path.stat().st_mtime) <= int(before_m):
                os.utime(cache_path, (int(before_m) + 1, int(before_m) + 1))
            return n
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _patch_quiet(journal: Path) -> None:
    try:
        patch_cache_file(journal)
    except Exception as e:                       # noqa: BLE001 — never fail a payment
        print(f"WARN group cache patch: {e}", file=sys.stderr)


def decrement_parent(child_id: str, parent_id: str, paid: int, day: str, *,
                     journal: Path, get_task: Callable[[str], Optional[dict]],
                     update_task: Callable[[str, dict], object]) -> dict:
    """Take `paid` points off the parent's [N] after its child was paid.

    Idempotent per (child, day): a retry finds the journal line and does
    nothing. Serialised by a flock on the journal so two children closed
    seconds apart cannot overwrite each other's rewrite. Returns a record:
    {"status": "ok"|"dup"|"skip"|"error", "taken": [[n, code]], ...}.
    On "ok" the dtd cache beside the journal is patched to match."""
    rec = _decrement_parent(child_id, parent_id, paid, day, journal=journal,
                            get_task=get_task, update_task=update_task)
    if rec.get("status") == "ok":
        _patch_quiet(journal)
    return rec


def restore_parent(child_id: str, day: str, *, journal: Path,
                   get_task: Callable[[str], Optional[dict]],
                   update_task: Callable[[str, dict], object]) -> dict:
    """Undo decrement_parent for (child, day): add back exactly what was
    taken (never a pre-image, so a sibling's later decrement survives).
    On "ok" the dtd cache beside the journal is patched to match."""
    out = _restore_parent(child_id, day, journal=journal,
                          get_task=get_task, update_task=update_task)
    if out.get("status") == "ok":
        _patch_quiet(journal)
    return out


def _decrement_parent(child_id: str, parent_id: str, paid: int, day: str, *,
                      journal: Path, get_task: Callable[[str], Optional[dict]],
                      update_task: Callable[[str, dict], object]) -> dict:
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


def _restore_parent(child_id: str, day: str, *, journal: Path,
                    get_task: Callable[[str], Optional[dict]],
                    update_task: Callable[[str, dict], object]) -> dict:
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
                    was = p.get("content") or ""
                    now_text = add_points(was, hit["taken"])
                    update_task(str(hit["parent"]), {"content": now_text})
                    # For overlay_recent: what the cache should move from and to.
                    hit.update(restored_from=was, restored=now_text, restored_ts=time.time())
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
