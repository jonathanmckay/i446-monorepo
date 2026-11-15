"""dtd_codes — a two-letter handle for every dtd task.

JM 2026-11-15: "rather than a number, I can refer to each task by its initials
and rather than display by number, display by the 2 letter combo." dtd used to
lead each row with its display position and `/d 5` meant row 5. A position is
only true on the screen that drew it: JM's dtd runs on Straylight, Claude
sessions run on Ix, and Ix resolved the number against its own render.

A code is two lowercase ASCII letters/digits, or one CJK character: two
terminal cells either way. It is never all digits, so it cannot be mistaken
for an old row number.

Assignment, `codes(tasks, prev)`:
  1. A task whose whole name is already a code keeps it (`0t`, `1g`, `0l`), so
     `/d 0t` goes on meaning the 0t card. RESERVED codes (ritual names, domain
     codes) are handed out only this way: `/d i9` must stay a name search.
  2. A task that had a code in `prev` keeps it while it is still free. Without
     this, closing one task renamed another in a quarter of cases, and a code
     typed from memory landed on a different card.
  3. The rest take their first free candidate in ascending id order (Todoist
     ids sort by creation, so an older task is not renamed by a newer one):
     the initials of the first two words, the first two letters of the first
     word, the first two of the second word, any two letters of the name in
     reading order, the name's CJK characters, then anything free.

What a session resolves is what a dtd drew, not a recomputation. Each dtd
keeps its own map in ~/vault/z_ibx/dtd-codes-<host>.json (`render_codes`: its
`prev`, rewritten when the map changes and at least hourly as a heartbeat),
which Syncthing carries to the other hosts. `lookup` reads every fresh map:
one answer is the task; two different answers come back as two candidates, so
the caller asks instead of guessing. Only when no dtd has published a map does
it fall back to computing one from the local queue.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import unicodedata
from pathlib import Path

_EST = re.compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}|「\d+」")
_LABEL = re.compile(r"(?:^|\s)@\S+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"

# Never handed to a task by initials: the two-character ritual and review card
# names (each card takes its own in pass 1), and the leading two characters of
# the domain codes, which JM types to `/d` as a name search across that domain.
RESERVED = frozenset({
    "0t", "0g", "0l", "0n", "0s", "0r", "1s", "1g", "1n", "1t", "2n", "2s", "2g", "3s", "3n", "4g", "4s",
    "i9", "m5", "hc", "xk", "s8", "g2", "h3", "qz", "i4", "n1", "m8", "f8", "f6", "ep", "d3",
})

MIRROR_MAX_AGE = 3 * 3600    # a map older than this is from a dtd that is no longer running
HEARTBEAT = 3600             # rewrite an unchanged map this often, so it stays fresh


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


def _pairs(letters: str):
    for i in range(len(letters)):
        for j in range(i + 1, len(letters)):
            yield letters[i] + letters[j]


def _candidates(name: str):
    low = name.lower()
    words = [w for w in ("".join(c for c in w if c in _ALNUM) for w in low.split()) if w]
    cjk = [c for c in name if _is_cjk(c)]
    if words:
        f = words[0][0]
        if len(words) > 1:
            yield f + words[1][0]                 # ibx i9 -> ii
        if len(words[0]) > 1:
            yield f + words[0][1]                 # hiit -> hi
        if len(words) > 1 and len(words[1]) > 1:
            yield words[1][:2]                    # 1 hcmc -> hc
        if len(words) > 2:
            yield words[1][0] + words[2][0]       # 1 kids nature -> kn
            yield f + words[2][0]
        if len(words[0]) == 1 and len(words) > 1:
            # A one-character first word ("1 hcb", "2 hci") is a family
            # prefix: the rest of the name tells the cards apart.
            yield from _pairs("".join(words[1:]))
        yield from _pairs("".join(words))         # any two letters of the name, in reading order
    for c in cjk:
        yield c
    if words:
        for c in _ALNUM:
            yield words[0][0] + c
    for a in _ALNUM:
        for b in _ALNUM:
            yield a + b


def codes(tasks, prev: dict | None = None, reserved=RESERVED) -> dict[str, str]:
    """task_id -> code for every task with an id. Depends only on the set of
    (id, content) and on `prev`, never on the order `tasks` arrives in."""
    names: dict[str, str] = {}
    for t in tasks or []:
        if isinstance(t, dict) and t.get("id") and str(t["id"]) not in names:
            # The name dtd shows: its short name when the cache carries one
            # ("figure out allergy avoiding plan ..." is drawn as "Allergy
            # avoiding plan", so its code should start with a, not f).
            names[str(t["id"])] = prose(t.get("short") or t.get("content", ""))
    out: dict[str, str] = {}
    taken: set[str] = set()
    order = sorted(names)
    for tid in order:   # 1. a name that is already a code keeps it
        c = names[tid].lower()
        if is_code(c) and c not in taken:
            out[tid] = c
            taken.add(c)
    for tid in order:   # 2. the code it already had
        c = (prev or {}).get(tid)
        if tid not in out and c and is_code(c) and c not in taken and c not in reserved:
            out[tid] = c
            taken.add(c)
    for tid in order:   # 3. first free candidate, oldest task first
        if tid in out:
            continue
        for c in _candidates(names[tid]):
            if is_code(c) and c not in taken and c not in reserved:
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


def cell_pad(code: str) -> str:
    """The code padded to two terminal cells (a CJK code already fills them)."""
    return code if len(code) != 1 or _is_cjk(code) else code + " "


# --- the published maps -------------------------------------------------------
def mirror_dir() -> Path | None:
    """Where the per-host maps live. A test that has not pointed DTD_CODES_DIR
    somewhere gets None: it must never write into, or read from, the real vault."""
    d = os.environ.get("DTD_CODES_DIR")
    if d:
        return Path(d)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path.home() / "vault" / "z_ibx"


def host_slug() -> str:
    """Same slug dtd.sh puts in DTD_HOSTSLUG."""
    s = os.environ.get("DTD_HOSTSLUG")
    if s:
        return s
    return re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24] or "host"


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) and isinstance(d.get("codes"), dict) else {}
    except (OSError, ValueError):
        return {}


def render_codes(tasks, now: float | None = None) -> dict[str, str]:
    """The map a dtd draws: this host's previous codes kept where still free,
    then published for the sessions that will be asked to resolve them."""
    now = now or time.time()
    d = mirror_dir()
    path = d / f"dtd-codes-{host_slug()}.json" if d else None
    old = _read(path) if path else {}
    out = codes(tasks, prev=old.get("codes"))
    if path and (out != old.get("codes") or now - float(old.get("ts", 0)) > HEARTBEAT):
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"ts": now, "host": host_slug(), "codes": out}, ensure_ascii=False))
            tmp.replace(path)
        except OSError:
            pass
    return out


def published(now: float | None = None) -> list[dict[str, str]]:
    """Every fresh map, one per host whose dtd is running."""
    now = now or time.time()
    d = mirror_dir()
    if not d:
        return []
    maps = []
    for p in sorted(d.glob("dtd-codes-*.json")):
        rec = _read(p)
        if rec and now - float(rec.get("ts", 0)) <= MIRROR_MAX_AGE:
            maps.append({str(k): str(v) for k, v in rec["codes"].items()})
    return maps


def lookup(query: str, tasks, now: float | None = None) -> list[str]:
    """Ids the code `query` names: [] none, one id, or several when two dtds
    drew it on different tasks (the caller asks which). Ids outside `tasks`
    are dropped: the local queue has not caught up with that dtd."""
    q = (query or "").strip().lower()
    if not is_code(q):
        return []
    known = {str(t["id"]) for t in tasks or [] if isinstance(t, dict) and t.get("id")}
    maps = published(now)
    if not maps:
        maps = [codes(tasks)]
    out: list[str] = []
    for m in maps:
        for tid, c in m.items():
            if c == q and tid in known and tid not in out:
                out.append(tid)
    return out


def code_for(tid: str, tasks, now: float | None = None) -> str | None:
    """The code a dtd shows for task `tid`: from a published map when every
    one that knows the task agrees, else None; computed when none is published."""
    tid = str(tid)
    maps = published(now)
    if not maps:
        return codes(tasks).get(tid)
    seen = {m[tid] for m in maps if tid in m}
    return seen.pop() if len(seen) == 1 else None
