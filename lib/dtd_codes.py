"""dtd_codes — a two-letter handle for every dtd task.

JM 2026-11-15: "rather than a number, I can refer to each task by its initials
and rather than display by number, display by the 2 letter combo." dtd used to
lead each row with its display position and `/d 5` meant row 5. A position is
only true on the screen that drew it: JM's dtd runs on Straylight, Claude
sessions run on Ix, and Ix resolved the number against its own render. A code
is computed from the task queue alone, by this one function, so the dtd that
draws it and the session that reads it agree without sharing any state.

A code is two lowercase ASCII letters/digits, or one CJK character: two
terminal cells either way. It is never all digits, so it cannot be mistaken
for an old row number.

    codes(tasks) -> {task_id: code}

  1. A task whose whole name is already a code keeps it (`0t`, `1g`, `0l`), so
     `/d 0t` goes on meaning the 0t card.
  2. The rest take their first free candidate in ascending id order (Todoist
     ids sort by creation, so an older task is not renamed by a newer one):
     the initials of the first two words, the first two letters of the first
     word, the first two of the second word, any two letters of the name in
     reading order, the name's CJK characters, then the first letter with
     anything, then any free pair.

No stored state: when a task leaves the queue, a younger one that had lost a
collision to it may move up to the freed code.
"""
from __future__ import annotations

import re
import unicodedata

_EST = re.compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}|「\d+」")
_LABEL = re.compile(r"(?:^|\s)@\S+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"


def prose(content: str) -> str:
    """Task content without estimates, @labels, links' targets and 😈."""
    s = _LINK.sub(r"\1", content or "")
    s = _URL.sub(" ", s)
    s = _LABEL.sub(" ", _EST.sub(" ", s)).replace("😈", " ")
    return re.sub(r"\s+", " ", s).strip(" -–—:")


def _is_cjk(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F") and unicodedata.category(ch) == "Lo"


def is_code(s: str) -> bool:
    """Two ASCII letters/digits with at least one letter, or one CJK character."""
    if len(s) == 1:
        return _is_cjk(s)
    return len(s) == 2 and all(c in _ALNUM for c in s) and not s.isdigit()


def _candidates(name: str):
    low = name.lower()
    words = [w for w in ("".join(c for c in w if c in _ALNUM) for w in low.split()) if w]
    letters = "".join(words)
    cjk = [c for c in name if _is_cjk(c)]
    if words:
        f = words[0][0]
        if len(words) > 1:
            yield f + words[1][0]                 # ibx i9 -> ii
        if len(words[0]) > 1:
            yield f + words[0][1]                 # hiit -> hi
        if len(words) > 1 and len(words[1]) > 1:
            yield words[1][:2]                    # 1 hcmc -> hc (the "1 <domain>" family shares its first word)
        if len(words) > 2:
            yield words[1][0] + words[2][0]       # 1 kids nature -> kn
            yield f + words[2][0]
        for i in range(len(letters)):             # then any two letters of the name, in reading order
            for j in range(i + 1, len(letters)):
                yield letters[i] + letters[j]
    for c in cjk:
        yield c
    if words:
        for c in _ALNUM:
            yield words[0][0] + c
    for a in _ALNUM:
        for b in _ALNUM:
            yield a + b


def codes(tasks) -> dict[str, str]:
    """task_id -> code for every task with an id. The result depends only on
    the set of (id, content), never on the order `tasks` arrives in."""
    names: dict[str, str] = {}
    for t in tasks or []:
        if isinstance(t, dict) and t.get("id") and str(t["id"]) not in names:
            names[str(t["id"])] = prose(t.get("content", ""))
    out: dict[str, str] = {}
    taken: set[str] = set()
    order = sorted(names)
    for tid in order:   # 1. a name that is already a code keeps it
        c = names[tid].lower()
        if is_code(c) and c not in taken:
            out[tid] = c
            taken.add(c)
    for tid in order:   # 2. first free candidate, oldest task first
        if tid in out:
            continue
        for c in _candidates(names[tid]):
            if is_code(c) and c not in taken:
                out[tid] = c
                taken.add(c)
                break
    return out


def queue_tasks(queue: dict) -> list[dict]:
    """Every task in a task-queue.json, across its sections, first copy wins."""
    seen: set[str] = set()
    out: list[dict] = []
    for sec in (queue or {}).values():
        if not isinstance(sec, list):
            continue
        for t in sec:
            if isinstance(t, dict) and t.get("id") and str(t["id"]) not in seen:
                seen.add(str(t["id"]))
                out.append(t)
    return out


def resolve(query: str, tasks) -> str | None:
    """The id of the task whose code is `query`, or None."""
    q = (query or "").strip().lower()
    if not is_code(q):
        return None
    for tid, c in codes(tasks).items():
        if c == q:
            return tid
    return None


def cell_pad(code: str) -> str:
    """The code padded to two terminal cells (a CJK code already fills them)."""
    return code if len(code) != 1 or _is_cjk(code) else code + " "
