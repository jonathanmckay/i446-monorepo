#!/usr/bin/env python3
"""edit-form.py — dtd's ctrl-o / ctrl-p screen: one curses form for a task.

2026-10-21 ("I don't like the new ctrl+o / ctrl+p page"): the one-line
`title @domain [N] // description ✓ pts` prompt is gone. The screen is a form,
top to bottom:

    task (the mother)      [N] points · (N) minutes
                           haiku  — the short row name (shorten.py; only when
                                    the title is longer than the row fits)
                           name   — the full title
                           notes  — the Todoist description, multi-line
                           project — the @domain label
    done so far            pts done · min done — carve a done record off the
                                    task: the remainder keeps total - done
                                    points and time - done minutes
                           done as — name of the done record (blank = title)

ctrl-o opens with the cursor on the task; ctrl-p opens on "pts done" (the
breakoff is the default edit for ctrl-p). Inside the form ^O / ^P jump
between the two sections. Enter / Tab move on (Enter in notes = newline),
Enter on the last field or ^S saves, Esc / ^C / ^Q cancel.

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
        "points": pts.group(1) if pts else (("{" + curly.group(1) + "}") if curly else ""),
        "points_curly": bool(curly and not pts),
        "time": mins.group(1) if mins else "",
        "short": prose_of(short) if short else "",
        "short_enabled": len(prose) > PROSE_CAP,
        "desc": description if description is not None else "",
        "desc_loaded": description is not None,
        "domain": labels[0] if labels else "",
    }


def remainder(total: str, time_: str, done_pts: str, done_min: str) -> str:
    """The live '→ remainder' line: tags computed independently."""
    parts = []
    if _INT.match(total or "") and _INT.match(done_pts or ""):
        parts.append(f"[{max(0, int(total) - int(done_pts))}]")
    elif done_pts:
        parts.append("[?]")
    if _INT.match(time_ or "") and _INT.match(done_min or ""):
        parts.append(f"({max(0, int(time_) - int(done_min))})")
    return " ".join(parts)


def validate(vals: dict) -> str | None:
    """None when the form may be saved, else the reason."""
    for k, label in (("points", "[N] points"), ("time", "(N) minutes"),
                     ("done_pts", "pts done"), ("done_min", "min done")):
        v = (vals.get(k) or "").strip()
        if v and not _INT.match(v) and not (k == "points" and vals.get("points_curly")):
            return f"{label} must be a number"
    dp, dm = (vals.get("done_pts") or "").strip(), (vals.get("done_min") or "").strip()
    if dm and not dp:
        return "min done needs pts done too"
    if dp and _INT.match(vals.get("points") or "") and int(dp) > int(vals["points"]):
        return f"pts done {dp} > total {vals['points']}"
    if dm and _INT.match(vals.get("time") or "") and int(dm) > int(vals["time"]):
        return f"min done {dm} > time {vals['time']}"
    return None


def result(vals: dict) -> dict:
    """What edit-fast receives. Blank = keep (notes: blank = clear, as before)."""
    out = {
        "name": (vals.get("name") or "").strip(),
        "points": "" if vals.get("points_curly") else (vals.get("points") or "").strip(),
        "time": (vals.get("time") or "").strip(),
        "short": (vals.get("short") or "").strip() if vals.get("short_enabled") else "",
        "short_enabled": bool(vals.get("short_enabled")),
        "domain": (vals.get("domain") or "").strip().lstrip("@"),
        "desc": (vals.get("desc") or "").rstrip() if vals.get("desc_loaded") else None,
        "split": None,
    }
    dp = (vals.get("done_pts") or "").strip()
    if dp:
        out["split"] = {"pts": int(dp),
                        "minutes": int(vals["done_min"].strip()) if (vals.get("done_min") or "").strip() else None,
                        "name": " ".join((vals.get("done_name") or "").split())}
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
                 width: int | None = None, enabled=True, hint=""):
        self.key, self.label, self.text = key, label, text
        self.multiline, self.width, self.enabled, self.hint = multiline, width, enabled, hint
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
            Field("points", "[N] pts", pre["points"], width=6, enabled=not pre["points_curly"]),
            Field("time", "(N) min", pre["time"], width=6),
            Field("short", "haiku", pre["short"], enabled=pre["short_enabled"],
                  hint="— title fits the row; no haiku name needed"),
            Field("name", "name", pre["name"]),
            Field("desc", "notes", pre["desc"], multiline=True, enabled=pre["desc_loaded"],
                  hint="loading notes…"),
            Field("domain", "project", pre["domain"], width=10),
            Field("done_pts", "pts done", "", width=6),
            Field("done_min", "min done", "", width=6),
            Field("done_name", "done as", ""),
        ]
        self.by = {f.key: f for f in self.fields}
        # ctrl-p lands on the breakoff; ctrl-o on the first task field
        self.focus = self.by["done_pts"] if focus == "split" else self._enabled()[0]
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

    def values(self) -> dict:
        v = {f.key: f.text for f in self.fields}
        v["points_curly"] = self.pre["points_curly"]
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

        put(y, 0, self.task.get("content", ""), curses.A_BOLD); y += 1
        for style, text in detail_lines(self.bundle, self.project):
            put(y, 0, text, curses.A_DIM if style == "dim" else curses.A_UNDERLINE); y += 1
            if y > 7:
                break
        put(y, 0, "─" * (w - 1), curses.A_DIM); y += 1
        cursor = (y, 0)
        # row 1: points + time side by side
        cursor = self._draw_row(scr, y, [self.by["points"], self.by["time"]], w) or cursor; y += 1
        for key in ("short", "name"):
            c = self._draw_row(scr, y, [self.by[key]], w); cursor = c or cursor; y += 1
        c, used = self._draw_multiline(scr, y, self.by["desc"], w, max(3, min(6, h - y - 8)))
        cursor = c or cursor; y += used
        c = self._draw_row(scr, y, [self.by["domain"]], w); cursor = c or cursor; y += 1
        put(y, 0, "── done so far " + "─" * max(0, w - 16), curses.A_DIM); y += 1
        c = self._draw_row(scr, y, [self.by["done_pts"], self.by["done_min"]], w)
        cursor = c or cursor
        rem = remainder(self.by["points"].text, self.by["time"].text, self.by["done_pts"].text, self.by["done_min"].text)
        if rem:
            put(y, 40, f"→ remainder {rem}", curses.A_DIM)
        y += 1
        c = self._draw_row(scr, y, [self.by["done_name"]], w); cursor = c or cursor; y += 1
        legend = self.msg or "⏎/Tab next · ⏎ on last or ^S save · Esc cancel · ^O task · ^P done so far"
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
            # horizontal window so the cursor stays visible
            start = 0
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
            self.focus = self.by["done_pts"]
        elif key == "\x1b":
            return "esc"
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
