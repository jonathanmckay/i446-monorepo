#!/usr/bin/env python3
"""edit-form.py — dtd's ctrl-o / ctrl-p screen: one curses form for a task.

2026-10-21 ("I don't like the new ctrl+o / ctrl+p page"): the one-line
`title @domain [N] // description ✓ pts` prompt is gone. The screen is three
sections, a blank line between them; inside a section the order is name
(two lines), haiku, minutes, points:

    done so far   name · haiku · (N) min · [N] pts — carve a done record
                  off the task; the name is prefilled from the task
    remainder     name · haiku · (N) min · [N] pts — what the task keeps:
                  min/pts are current − done, following the done section
                  as you type until you type here; then do-date and a
                  "keep remainder" checkbox
    common        notes · project — the task's own description and @domain

The haiku (short row name, shorten.py) is NOT typed: it shows the current
one and regenerates itself after the form exits when the name changed (dtd
backgrounds the save). With nothing in the done section the remainder is
simply the task (ctrl-o / ctrl-g: a plain edit). ctrl-p opens on the done
section, ctrl-o on the remainder; ^P / ^O jump between them. Enter / Tab
move on (Enter in notes = newline), Enter on the last field or ^S saves,
Esc / ^C / ^Q cancel.

Bonus and do-date (2026-10-22, "-1g goals"): a task with {N} gets a bonus
field beside pts done and a {N} bonus field in the remainder; with no {N}
neither appears. Bonus done reduces the remainder's bonus exactly like pts
done reduces [N]. The remainder's do-date shows the task's due date and
follows to tomorrow once anything is done (typed: ISO, M/D, M.D, today,
tmrw, mon..sun, +N). "keep remainder" (default checked) unchecked means
partial credit for a partial task: the done record is logged and the task is
closed, nothing deferred. Space / x toggles it.

Budget (user, 2026-10-21): the form must paint in well under 200 ms, ideally
80–100. So: curses (stdlib, ~10 ms), ONE interpreter (the cache is read here,
no dtd_resolve / edit-fast --current subprocesses), the description from
task-detail's cached bundle in-process, and a live fetch only on a cache miss,
in a daemon thread, with the notes field locked until it lands.

Output: `--out FILE` gets one JSON object on save (exit 0); cancel writes
nothing and exits 2. The caller (dtd.sh's DTD_EDIT) hands the file to
`edit-fast.py --fields --json FILE`, which applies what changed and prints the
SPLIT line for the split script.

Usage: edit-form.py --id <task id> <cache_file> --out <json> [--focus split]
                    [--timing <dtd timing log>]
"""
from __future__ import annotations

import curses
import json
import os
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
_NUM_ANNOT = re.compile(r"\s*(?:\(\d*\)|\[\d*\]|\[[\d.+]*/m\]|\{\d*\})")
_EST = re.compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}")
_INT = re.compile(r"^\d+$")
PROSE_CAP = 37          # shorten.PROSE_CAP (mirrored: importing shorten costs ~10 ms we don't need)
DETAIL_TTL = 600        # task-detail.CACHE_TTL
DOMAINS = ("i9", "i447", "f693", "f694", "m5x2", "m828", "g245", "infra", "cc", "hcmc",
           "hcm", "hci", "hcb", "hcbp", "xk87", "xk88", "xk20", "xk22", "s897")


def _stamp(timing: str | None, stage: str, arg: str = "") -> None:
    if not timing:
        return
    try:
        with open(timing, "a") as f:
            f.write(f"{time.time():.6f}\t{stage}\t{arg}\n")
    except OSError:
        pass


# ──────────────────────────────────────────────── prefill (pure, tested)

def find_task(cache: dict, task_id: str) -> dict | None:
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and str(t.get("id")) == str(task_id):
                    return t
    return None


def prose_of(content: str) -> str:
    return re.sub(r"\s+", " ", _EST.sub("", content)).strip(" -–—:")


def prefill(task: dict, description: str | None, domains=DOMAINS) -> dict:
    """The form's initial values from one snapshot-cache task."""
    content = task.get("content") or ""
    pts = re.search(r"\[(\d+)\]", content)
    curly = re.search(r"\{(\d+)\}", content)
    mins = re.search(r"\((\d+)\)", content)
    labels = [l for l in task.get("labels", []) if l in domains]
    prose = prose_of(content)
    short = task.get("short") or ""
    return {
        "name": re.sub(r"  +", " ", _NUM_ANNOT.sub("", content)).strip(),
        "points": pts.group(1) if pts else "",
        "bonus": curly.group(1) if curly else "",
        "has_bonus": bool(curly),
        "due": str(task.get("due") or "")[:10],
        "recurring": bool(task.get("recurring")),
        "time": mins.group(1) if mins else "",
        "short": prose_of(short) if short else "",
        "short_enabled": len(prose) > PROSE_CAP,
        "desc": description if description is not None else "",
        "desc_loaded": description is not None,
        "domain": labels[0] if labels else "",
    }


def remainder(total: str, time_: str, done_pts: str, done_min: str) -> tuple[str, str]:
    """What the remainder keeps, computed independently: (pts, min) as
    strings: current total − done. Nothing done yet = the whole total; ""
    only when the task has no such tag."""
    pts = mins = ""
    if _INT.match(total or ""):
        pts = str(max(0, int(total) - int(done_pts))) if _INT.match(done_pts or "") else total
    if _INT.match(time_ or ""):
        mins = str(max(0, int(time_) - int(done_min))) if _INT.match(done_min or "") else time_
    return pts, mins


def remainder_of(total: str, done: str) -> str:
    """One tag's remainder: total − done, the whole total when nothing is
    done, "" when the task has no such tag."""
    if not _INT.match(total or ""):
        return ""
    return str(max(0, int(total) - int(done))) if _INT.match(done or "") else total


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_date(text: str, today=None) -> str | None:
    """A typed do-date as ISO, or None when unreadable. Accepts ISO, M/D,
    M.D (this year, or next once that date has passed), today/tod,
    tomorrow/tmrw/tmr, a weekday (its next occurrence, never today) and
    +N (days from today)."""
    import datetime as _dt
    today = today or _dt.date.today()
    t = (text or "").strip().lower()
    if not t:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return _dt.date.fromisoformat(t).isoformat()
        except ValueError:
            return None
    if t in ("today", "tod"):
        return today.isoformat()
    if t in ("tomorrow", "tmrw", "tmr"):
        return (today + _dt.timedelta(days=1)).isoformat()
    m = re.fullmatch(r"\+(\d{1,3})d?", t)
    if m:
        return (today + _dt.timedelta(days=int(m.group(1)))).isoformat()
    for i, wd in enumerate(_WEEKDAYS):
        if t[:3] == wd and wd.startswith(t[:3]) and len(t) >= 3:
            ahead = (i - today.weekday()) % 7 or 7
            return (today + _dt.timedelta(days=ahead)).isoformat()
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})", t)
    if m:
        try:
            d = _dt.date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if d < today:
            d = d.replace(year=today.year + 1)
        return d.isoformat()
    return None


def splitting(vals: dict) -> bool:
    return bool((vals.get("done_pts") or "").strip() or (vals.get("done_bonus") or "").strip())


def validate(vals: dict) -> str | None:
    """None when the form may be saved, else the reason."""
    for k, label in (("points", "[N] pts"), ("time", "(N) min"), ("bonus", "{N} bonus"),
                     ("done_pts", "pts done"), ("done_min", "min done"), ("done_bonus", "bonus done")):
        v = (vals.get(k) or "").strip()
        if v and not _INT.match(v):
            return f"{label} must be a number"
    dp, dm = (vals.get("done_pts") or "").strip(), (vals.get("done_min") or "").strip()
    db = (vals.get("done_bonus") or "").strip()
    if dm and not (dp or db):
        return "min done needs pts or bonus done too"
    tp, tt = (vals.get("total_pts") or "").strip(), (vals.get("total_min") or "").strip()
    tb = (vals.get("total_bonus") or "").strip()
    if dp and _INT.match(tp) and int(dp) > int(tp):
        return f"pts done {dp} > current {tp}"
    if db and _INT.match(tb) and int(db) > int(tb):
        return f"bonus done {db} > current {tb}"
    if dm and _INT.match(tt) and int(dm) > int(tt):
        return f"min done {dm} > current {tt}"
    keep = vals.get("keep", True)
    if not keep and not (dp or db):
        return "unchecked keep remainder needs pts or bonus done"
    due = (vals.get("due") or "").strip()
    if keep and due and due != (vals.get("due_pre") or "") and parse_date(due) is None:
        return f"do-date {due!r}: try 2026-10-24, 10/24, tmrw, fri, +3"
    return None


def result(vals: dict) -> dict:
    """What edit-fast receives. Blank = keep (notes: blank = clear, as before)."""
    due = (vals.get("due") or "").strip()
    due_iso = "" if (not due or due == (vals.get("due_pre") or "")) else (parse_date(due) or "")
    out = {
        "name": " ".join((vals.get("name") or "").split()),
        "points": (vals.get("points") or "").strip(),
        "bonus": (vals.get("bonus") or "").strip(),
        "due": due_iso,
        "time": (vals.get("time") or "").strip(),
        "short": "",                # haiku names are generated, never typed (2026-10-21)
        "short_enabled": False,
        "domain": (vals.get("domain") or "").strip().lstrip("@"),
        "desc": (vals.get("desc") or "").rstrip() if vals.get("desc_loaded") else None,
        "split": None,
    }
    dp, db = (vals.get("done_pts") or "").strip(), (vals.get("done_bonus") or "").strip()
    if dp or db:
        dm = (vals.get("done_min") or "").strip()
        out["split"] = {"pts": int(dp) if dp else None, "bonus": int(db) if db else None,
                        "minutes": int(dm) if _INT.match(dm) else None,
                        "name": " ".join((vals.get("done_name") or "").split()), "short": "",
                        "keep": bool(vals.get("keep", True))}
    return out


# ──────────────────────────────────────────────── detail bundle

def _state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "jm" / "task-detail"


def load_bundle(task_id: str) -> dict | None:
    """task-detail's cached {task, comments} when fresh, else None."""
    p = _state_dir() / f"{task_id}.json"
    try:
        if time.time() - p.stat().st_mtime < DETAIL_TTL:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return None


def fetch_bundle(task_id: str) -> dict | None:
    """Live from Todoist (daemon thread on a cache miss); caches like task-detail."""
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import todoist  # noqa
        b = {"task": todoist.get_task(task_id), "comments": todoist.get_comments(task_id)}
        try:
            d = _state_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{task_id}.json").write_text(json.dumps(b))
        except OSError:
            pass
        return b
    except Exception:  # noqa: BLE001
        return None


_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s<>()\[\]]+")


def detail_lines(bundle: dict | None, project: str | None) -> list[tuple[str, str]]:
    """(style, text) rows for the read-only block: meta, comments, links."""
    if not bundle or not bundle.get("task"):
        return [("dim", "loading details…")]
    t = bundle["task"]
    rows: list[tuple[str, str]] = []
    meta = []
    if project:
        meta.append(f"#{project}")
    if t.get("labels"):
        meta.append(" ".join("@" + l for l in t["labels"]))
    due = t.get("due") or {}
    if due.get("string") or due.get("date"):
        meta.append(f"due {due.get('string') or due.get('date')}")
    if meta:
        rows.append(("dim", " · ".join(meta)))
    kept = [c for c in (bundle.get("comments") or []) if not (c.get("content") or "").startswith("dtd-short:")]
    for c in kept[:3]:
        body = _MD_LINK.sub(lambda m: m.group(1) or m.group(2), (c.get("content") or "").strip())
        rows.append(("dim", f"{(c.get('posted_at') or '')[:10]} {body.splitlines()[0] if body else ''}"))
    if len(kept) > 3:
        rows.append(("dim", f"… {len(kept) - 3} more comments"))
    urls: list[str] = []
    for text in [t.get("content", ""), t.get("description", "")] + [c.get("content", "") for c in kept]:
        for u in _URL.findall(text or ""):
            u = u.rstrip(".,;:!?'\"")
            if u not in urls:
                urls.append(u)
    for u in urls[:4]:
        rows.append(("link", u))
    return rows


def project_name(task: dict) -> str | None:
    pid = task.get("project_id")
    if not pid:
        return None
    try:
        return json.loads((_state_dir() / f"project-{pid}.json").read_text())
    except (OSError, ValueError):
        return None


# ──────────────────────────────────────────────── text editing

def wcw(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(s: str) -> int:
    return sum(wcw(c) for c in s)


class Field:
    def __init__(self, key: str, label: str, text: str = "", *, multiline=False,
                 width: int | None = None, enabled=True, hint="", rows: int = 1, check=False):
        self.key, self.label, self.text = key, label, text
        self.check = check          # a checkbox: text "x" = checked, "" = not
        self.multiline, self.width, self.enabled, self.hint = multiline, width, enabled, hint
        self.rows = rows            # > 1: a single value soft-wrapped over several rows
        self.cur = len(text)
        self.touched = False
        self.scroll = 0   # multiline: first visible line

    # cursor helpers
    def _line_bounds(self, pos: int) -> tuple[int, int]:
        start = self.text.rfind("\n", 0, pos) + 1
        end = self.text.find("\n", pos)
        return start, (len(self.text) if end < 0 else end)

    def insert(self, s: str) -> None:
        self.text = self.text[:self.cur] + s + self.text[self.cur:]
        self.cur += len(s)
        self.touched = True

    def backspace(self) -> None:
        if self.cur > 0:
            self.text = self.text[:self.cur - 1] + self.text[self.cur:]
            self.cur -= 1
            self.touched = True

    def delete(self) -> None:
        if self.cur < len(self.text):
            self.text = self.text[:self.cur] + self.text[self.cur + 1:]
            self.touched = True

    def kill_to_end(self) -> None:
        _, e = self._line_bounds(self.cur)
        if e == self.cur and e < len(self.text):
            e += 1
        self.text = self.text[:self.cur] + self.text[e:]
        self.touched = True

    def kill_to_start(self) -> None:
        s, _ = self._line_bounds(self.cur)
        self.text = self.text[:s] + self.text[self.cur:]
        self.cur = s
        self.touched = True

    def delete_word(self) -> None:
        i = self.cur
        while i > 0 and self.text[i - 1] in " \t":
            i -= 1
        while i > 0 and self.text[i - 1] not in " \t\n":
            i -= 1
        self.text = self.text[:i] + self.text[self.cur:]
        self.cur = i
        self.touched = True

    def home(self) -> None:
        self.cur = self._line_bounds(self.cur)[0]

    def end(self) -> None:
        self.cur = self._line_bounds(self.cur)[1]

    def up(self) -> bool:
        s, _ = self._line_bounds(self.cur)
        if s == 0:
            return False
        col = self.cur - s
        ps, pe = self._line_bounds(s - 1)
        self.cur = min(ps + col, pe)
        return True

    def down(self) -> bool:
        s, e = self._line_bounds(self.cur)
        if e >= len(self.text):
            return False
        col = self.cur - s
        ns, ne = self._line_bounds(e + 1)
        self.cur = min(ns + col, ne)
        return True


# ──────────────────────────────────────────────── the screen

class Form:
    LABEL_W = 9

    def __init__(self, task: dict, pre: dict, focus: str, out: str, timing: str | None, task_id: str):
        self.task, self.pre, self.out, self.timing, self.task_id = task, pre, out, timing, task_id
        self.bundle = load_bundle(task_id)
        self.project = project_name(task)
        self.msg = ""
        self.fields = [
            # done so far
            Field("done_name", "name", pre["name"], rows=2),
            Field("done_min", "min done", "", width=6),
            Field("done_pts", "pts done", "", width=6),
            *([Field("done_bonus", "bonus", "", width=6)] if pre["has_bonus"] else []),
            # remainder (the task itself)
            Field("name", "name", pre["name"], rows=2),
            Field("time", "(N) min", pre["time"], width=6),
            Field("points", "[N] pts", pre["points"], width=6),
            *([Field("bonus", "{N} bonus", pre["bonus"], width=6)] if pre["has_bonus"] else []),
            Field("due", "do-date", pre["due"], width=12),
            Field("keep", "keep remainder", "x", check=True),
            # common
            Field("desc", "notes", pre["desc"], multiline=True, enabled=pre["desc_loaded"],
                  hint="loading notes…"),
            Field("domain", "project", pre["domain"], width=10),
        ]
        self.by = {f.key: f for f in self.fields}
        # ctrl-p lands on the done section; ctrl-o / ctrl-g on the remainder
        self.focus = self.by["done_name"] if focus == "split" else self.by["name"]
        self._thread: threading.Thread | None = None
        self._fetched: dict | None = None
        if self.bundle is None:
            self._thread = threading.Thread(target=self._fetch, daemon=True)
            self._thread.start()

    def _fetch(self) -> None:
        self._fetched = fetch_bundle(self.task_id) or {"task": None}

    def _absorb_fetch(self) -> None:
        if self._fetched is None:
            return
        b, self._fetched = self._fetched, None
        self.bundle = b if b.get("task") else {"task": None, "comments": []}
        notes = self.by["desc"]
        if not notes.enabled:
            notes.enabled = True
            notes.hint = ""
            self.pre["desc_loaded"] = bool(b.get("task"))
            if b.get("task") and not notes.touched:
                notes.text = (b["task"].get("description") or "")
                notes.cur = len(notes.text)

    # navigation
    def _enabled(self) -> list[Field]:
        return [f for f in self.fields if f.enabled]

    def move(self, step: int) -> None:
        fs = self._enabled()
        i = fs.index(self.focus) if self.focus in fs else 0
        self.focus = fs[(i + step) % len(fs)]

    def is_last(self) -> bool:
        return self.focus is self._enabled()[-1]

    def autofill(self) -> None:
        """The remainder's [N] pts / (N) min follow current − done until the
        user types into them; an emptied field goes back to following."""
        pts, mins = remainder(self.pre["points"], self.pre["time"],
                              self.by["done_pts"].text, self.by["done_min"].text)
        follow = [("points", pts), ("time", mins)]
        if "bonus" in self.by:
            follow.append(("bonus", remainder_of(self.pre["bonus"], self.by["done_bonus"].text)))
        # the do-date: the task's own due date, tomorrow once anything is done
        import datetime as _dt
        split = splitting({k: f.text for k, f in self.by.items()})
        follow.append(("due", (_dt.date.today() + _dt.timedelta(days=1)).isoformat() if split
                       else self.pre["due"]))
        # unchecked keep remainder: there is no remainder to shape
        keep = bool(self.by["keep"].text)
        for key in ("time", "points", "bonus", "due"):
            if key in self.by:
                self.by[key].enabled = keep
        for key, val in follow:
            f = self.by[key]
            # an emptied field resumes following once the cursor has left it
            # (while it is focused, "" is the user mid-edit, not "auto")
            if f.touched and not f.text.strip() and f is not self.focus:
                f.touched = False
            if not f.touched and f.text != val:
                f.text = val
                f.cur = len(val)

    def values(self) -> dict:
        v = {f.key: f.text for f in self.fields}
        v["total_pts"], v["total_min"] = self.pre["points"], self.pre["time"]
        v["total_bonus"] = self.pre["bonus"]
        v["keep"] = bool(self.by["keep"].text)
        # an untouched do-date (still following) never reschedules on a plain
        # edit; on a split the split script defers to tomorrow by itself
        v["due_pre"] = self.pre["due"] if not self.by["due"].touched else ""
        if not self.by["due"].touched:
            v["due"] = ""
        v["short_enabled"] = self.pre["short_enabled"]
        v["desc_loaded"] = self.pre["desc_loaded"] and self.by["desc"].enabled
        return v

    # drawing
    def draw(self, scr) -> None:
        self._absorb_fetch()
        scr.erase()
        h, w = scr.getmaxyx()
        y = 0

        def put(yy, xx, s, attr=0):
            if yy >= h or xx >= w:
                return
            s = fit(s, w - xx - 1)
            try:
                scr.addstr(yy, xx, s, attr)
            except curses.error:
                pass

        # Budget the height from the bottom up (2026-10-21: in a short pane the
        # remainder fell off the screen): the field rows always fit; the
        # detail block and the notes box take what is left.
        FIXED = 1 + 5 + 1 + 7 + 1 + 1 + 1 + 1   # title, done (hdr+name2+haiku+nums), blank, remainder (+do-date, keep), blank, common hdr, project, legend
        spare = max(0, h - FIXED)
        notes_rows = max(1, min(6, spare - 1)) if spare > 1 else 1
        detail_cap = max(0, min(6, spare - notes_rows))
        put(y, 0, self.task.get("content", ""), curses.A_BOLD); y += 1
        for style, text in detail_lines(self.bundle, self.project)[:detail_cap]:
            put(y, 0, text, curses.A_DIM if style == "dim" else curses.A_UNDERLINE); y += 1
        cursor = (y, 0)
        self.autofill()

        def header(label):
            # a full rule with the CAPS label centred (2026-10-21: "more
            # visual distinction between the sections")
            nonlocal y
            lab = f" {label.upper()} "
            left = max(0, (w - 1 - dwidth(lab)) // 2)
            put(y, 0, "─" * left, curses.A_DIM)
            put(y, left, lab, curses.A_BOLD)
            put(y, left + dwidth(lab), "─" * max(0, w - 1 - left - dwidth(lab)), curses.A_DIM)
            y += 1

        def haiku(text):
            nonlocal y
            put(y, 0, f"{'haiku':<{self.LABEL_W}}", curses.A_DIM)
            put(y, self.LABEL_W, text, curses.A_DIM); y += 1

        header("done so far")
        c, used = self._draw_wrapped(scr, y, self.by["done_name"], w); cursor = c or cursor; y += used
        haiku("auto")
        c = self._draw_row(scr, y, [self.by[k] for k in ("done_min", "done_pts", "done_bonus") if k in self.by], w)
        cursor = c or cursor; y += 1
        y += 1
        header("remainder")
        c, used = self._draw_wrapped(scr, y, self.by["name"], w); cursor = c or cursor; y += used
        changed = " ".join(self.by["name"].text.split()) != self.pre["name"]
        haiku(("auto after save" if changed else (self.pre["short"] or "auto"))
              if self.pre["short_enabled"] or changed else "— title fits the row")
        c = self._draw_row(scr, y, [self.by[k] for k in ("time", "points", "bonus") if k in self.by], w)
        cursor = c or cursor; y += 1
        c = self._draw_row(scr, y, [self.by["due"]], w); cursor = c or cursor; y += 1
        c = self._draw_row(scr, y, [self.by["keep"]], w); cursor = c or cursor; y += 1
        y += 1
        header("common")
        c, used = self._draw_multiline(scr, y, self.by["desc"], w, notes_rows)
        cursor = c or cursor; y += used
        c = self._draw_row(scr, y, [self.by["domain"]], w); cursor = c or cursor; y += 1
        legend = self.msg or "⏎/Tab next · ⏎ on last or ^S save · Esc cancel · ^P done · ^O remainder"
        put(h - 1, 0, legend, curses.A_REVERSE if self.msg else curses.A_DIM)
        try:
            scr.move(*cursor)
        except curses.error:
            pass
        scr.refresh()

    def _draw_row(self, scr, y, fields: list[Field], w):
        """Single-line fields on one row; returns the cursor (y, x) if focused."""
        x = 0
        cursor = None
        h, _ = scr.getmaxyx()
        if y >= h - 1:
            return None
        for f in fields:
            if f.check:
                box = f"[{'x' if f.text else ' '}] {f.label}"
                try:
                    scr.addstr(y, x, box, curses.A_BOLD if f is self.focus else 0)
                except curses.error:
                    pass
                if f is self.focus:
                    cursor = (y, x + 1)
                x += dwidth(box) + 3
                continue
            label = f"{f.label:<{self.LABEL_W}}"
            try:
                scr.addstr(y, x, label, curses.A_DIM)
            except curses.error:
                pass
            x += self.LABEL_W
            avail = (f.width or (w - x - 2))
            if not f.enabled:
                try:
                    scr.addstr(y, x, fit(f.text or f.hint, avail), curses.A_DIM)
                except curses.error:
                    pass
                x += avail + 3
                continue
            # horizontal window so the cursor stays visible (focused field
            # only; an unfocused long value shows its beginning)
            start = 0
            if f is self.focus:
                while dwidth(f.text[start:f.cur]) >= avail:
                    start += 1
            shown = fit(f.text[start:], avail)
            attr = curses.A_UNDERLINE | (curses.A_BOLD if f is self.focus else 0)
            try:
                scr.addstr(y, x, shown + " " * max(0, avail - dwidth(shown)), attr)
            except curses.error:
                pass
            if f is self.focus:
                cursor = (y, x + dwidth(f.text[start:f.cur]))
            x += avail + 3
        return cursor

    def _draw_wrapped(self, scr, y, f: Field, w):
        """One value soft-wrapped over f.rows rows (the name, 2026-10-21:
        "two lines for the name"). Returns (cursor, rows used)."""
        rows = f.rows
        try:
            scr.addstr(y, 0, f"{f.label:<{self.LABEL_W}}", curses.A_DIM)
        except curses.error:
            pass
        avail = w - self.LABEL_W - 2
        chunks: list[tuple[int, str]] = []        # (start index, text)
        i = 0
        while True:
            seg = fit(f.text[i:], avail)
            chunks.append((i, seg))
            i += len(seg)
            if i >= len(f.text) or not seg:
                break
        # which chunk holds the cursor
        ci = 0
        for k, (st, seg) in enumerate(chunks):
            if st <= f.cur <= st + len(seg):
                ci = k
        first = max(0, ci - rows + 1)
        attr = curses.A_UNDERLINE | (curses.A_BOLD if f is self.focus else 0)
        cursor = None
        for r in range(rows):
            k = first + r
            seg = chunks[k][1] if k < len(chunks) else ""
            try:
                scr.addstr(y + r, self.LABEL_W, seg + " " * max(0, avail - dwidth(seg)), attr)
            except curses.error:
                pass
            if f is self.focus and k == ci:
                cursor = (y + r, self.LABEL_W + dwidth(f.text[chunks[k][0]:f.cur]))
        return cursor, rows

    def _draw_multiline(self, scr, y, f: Field, w, rows: int):
        label = f"{f.label:<{self.LABEL_W}}"
        try:
            scr.addstr(y, 0, label, curses.A_DIM)
        except curses.error:
            pass
        avail = w - self.LABEL_W - 2
        if not f.enabled:
            try:
                scr.addstr(y, self.LABEL_W, f.hint, curses.A_DIM)
            except curses.error:
                pass
            return None, 1
        lines = f.text.split("\n")
        cl = f.text.count("\n", 0, f.cur)           # cursor line
        cc = f.cur - (f.text.rfind("\n", 0, f.cur) + 1)
        if cl < f.scroll:
            f.scroll = cl
        if cl >= f.scroll + rows:
            f.scroll = cl - rows + 1
        cursor = None
        for i in range(rows):
            li = f.scroll + i
            text = lines[li] if li < len(lines) else ""
            attr = curses.A_UNDERLINE | (curses.A_BOLD if f is self.focus else 0)
            try:
                scr.addstr(y + i, self.LABEL_W, fit(text, avail) + " " * max(0, avail - dwidth(fit(text, avail))), attr)
            except curses.error:
                pass
            if f is self.focus and li == cl:
                cursor = (y + i, self.LABEL_W + dwidth(lines[cl][:cc]))
        return cursor, rows

    # keys
    def handle(self, key) -> str | None:
        """Returns 'save' / 'cancel' / None."""
        f = self.focus
        self.msg = ""
        if isinstance(key, int):
            if key == curses.KEY_LEFT:
                f.cur = max(0, f.cur - 1)
            elif key == curses.KEY_RIGHT:
                f.cur = min(len(f.text), f.cur + 1)
            elif key == curses.KEY_UP:
                if not (f.multiline and f.up()):
                    self.move(-1)
            elif key == curses.KEY_DOWN:
                if not (f.multiline and f.down()):
                    self.move(1)
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                f.backspace()
            elif key == curses.KEY_DC:
                f.delete()
            elif key == curses.KEY_HOME:
                f.home()
            elif key == curses.KEY_END:
                f.end()
            elif key == curses.KEY_BTAB:
                self.move(-1)
            elif key == curses.KEY_RESIZE:
                pass
            return None
        if key in ("\n", "\r", curses.KEY_ENTER):
            if f.multiline:
                f.insert("\n")
            elif self.is_last():
                return "save"
            else:
                self.move(1)
        elif key == "\t":
            if self.is_last():
                return "save"
            self.move(1)
        elif key in ("\x7f", "\x08"):
            f.backspace()
        elif key == "\x04":
            f.delete()
        elif key == "\x01":
            f.home()
        elif key == "\x05":
            f.end()
        elif key == "\x0b":
            f.kill_to_end()
        elif key == "\x15":
            f.kill_to_start()
        elif key == "\x17":
            f.delete_word()
        elif key == "\x13":
            return "save"
        elif key in ("\x03", "\x11"):
            return "cancel"
        elif key == "\x0f":
            self.focus = self.by["name"]
        elif key == "\x10":
            self.focus = self.by["done_name"]
        elif key == "\x1b":
            return "esc"
        elif f.check:
            if key in (" ", "x", "X"):
                f.text = "" if f.text else "x"
                f.touched = True
        elif key >= " ":
            f.insert(key)
        return None

    def run(self, scr) -> int:
        curses.raw()
        curses.noecho()
        scr.keypad(True)
        try:
            curses.set_escdelay(40)
        except Exception:  # noqa: BLE001
            pass
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        scr.timeout(60)
        self.draw(scr)
        _stamp(self.timing, "edit-form-paint", self.task_id)
        while True:
            try:
                key = scr.get_wch()
            except curses.error:
                if self._fetched is not None:
                    self.draw(scr)
                continue
            act = self.handle(key)
            if act == "esc":
                # a lone Esc cancels; Esc + key within the escdelay window is
                # an alt-combo (alt-backspace = delete word), never a cancel
                scr.nodelay(True)
                try:
                    nxt = scr.get_wch()
                except curses.error:
                    nxt = None
                scr.nodelay(False)
                scr.timeout(60)
                if nxt is None:
                    act = "cancel"
                elif nxt in ("\x7f", "\x08", curses.KEY_BACKSPACE):
                    self.focus.delete_word()
                    act = None
                else:
                    act = None
            if act == "cancel":
                return 2
            if act == "save":
                vals = self.values()
                why = validate(vals)
                if why:
                    self.msg = "✗ " + why
                    self.draw(scr)
                    continue
                Path(self.out).write_text(json.dumps(result(vals), ensure_ascii=False))
                return 0
            self.draw(scr)


def fit(s: str, width: int) -> str:
    """Clip to a display width, never splitting a wide char."""
    if width <= 0:
        return ""
    out, wsum = [], 0
    for ch in s.replace("\t", " "):
        cw = wcw(ch)
        if wsum + cw > width:
            break
        out.append(ch)
        wsum += cw
    return "".join(out)


def main() -> int:
    args = sys.argv[1:]
    task_id = cache_file = out = timing = None
    focus = "mother"
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--id":
            task_id = args[i + 1]; i += 2
        elif a == "--out":
            out = args[i + 1]; i += 2
        elif a == "--focus":
            focus = args[i + 1]; i += 2
        elif a == "--timing":
            timing = args[i + 1]; i += 2
        else:
            pos.append(a); i += 1
    cache_file = pos[0] if pos else None
    if not (task_id and cache_file and out):
        print("usage: edit-form.py --id <id> <cache_file> --out <json> [--focus split] [--timing log]", file=sys.stderr)
        return 2
    _stamp(timing, "edit-form-start", task_id)
    try:
        cache = json.loads(Path(cache_file).read_text())
    except (OSError, ValueError) as e:
        print(f"cache unreadable: {e}", file=sys.stderr)
        return 3
    task = find_task(cache, task_id)
    if not task:
        print(f"no task {task_id} in cache", file=sys.stderr)
        return 3
    bundle = load_bundle(task_id)
    desc = ((bundle.get("task") or {}).get("description") or "") if bundle else None
    pre = prefill(task, desc)
    os.environ.setdefault("ESCDELAY", "40")
    form = Form(task, pre, focus, out, timing, task_id)
    try:
        rc = curses.wrapper(form.run)
    finally:
        _stamp(timing, "edit-form-exit", task_id)
    return rc


if __name__ == "__main__":
    sys.exit(main())
