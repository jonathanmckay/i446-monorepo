#!/usr/bin/env python3
"""neon_tabs: janus's Neon tabs, minus prompt_toolkit.

2026-11-21, "neon frontend -> CLI". Tab 0 is janus's own timeline; tabs 1..N
are the Neon sheets in the snapshot neon_grid.py writes. This module owns the
tab state (which tab, each sheet's scroll position, persisted so a janus
self-restart comes back where it was), decides when the snapshot needs a
rebuild, and turns a sheet into formatted-text fragments for a pane of a given
size: a tab strip, the pinned header rows, a window of body rows with the
frozen columns on the left, and a status line.

janus.py holds the glue (key bindings, the render_all branch, the ticker).
Everything here is plain data in / plain tuples out, so tests need no tty.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import neon_grid  # noqa: E402

STATE_FILE = Path.home() / ".cache" / "janus" / "neon-tab.json"
HOME_TAB = "janus"
REBUILD_MIN_S = 90          # never rebuild more often than this
BUILD_TIMEOUT_S = 120
WRITE_MARGIN_S = 30         # ledger writes this long before the save still overlay (a save in flight)
TODAY_FROM_BOTTOM = 3       # a sheet opens with today's row this far above the last line
STRIP_STYLE = "bg:#303030 #9a9a9a"
STATUS_STYLE = "#8a8a8a"


class Tabs:
    def __init__(self, state_file: Path | None = None, grid_file: Path | None = None,
                 src: Path | None = None):
        self.state_file = state_file or STATE_FILE
        self.grid_file = grid_file or neon_grid.OUT
        self.src = src or neon_grid.SRC
        self.tab = 0                    # 0 = janus timeline, i = sheets[i-1]
        self.scroll: dict = {}          # sheet name -> [top body row, first scrolling col]
        self.snap: dict | None = None
        self.snap_mtime = 0.0
        self.building = False
        self.last_build = 0.0           # monotonic, when a build last STARTED
        self.built_src_mtime = 0.0      # workbook mtime the last finished build saw
        self.error = ""
        self._memo: tuple | None = None
        # {sheet name: callable() -> ({col letter: value}, "HH:MM:SS" | None)}:
        # a fresher source for a sheet's today row than the saved workbook
        # (janus wires 0分 to the Neon ledger). Applied after every snapshot
        # load and on refresh_live(); the status line names it.
        self.live_sources: dict = {}
        self.live_through: dict = {}
        # callable(since_epoch) -> {(sheet, excel row, col): (value, "…THH:MM:SS")}:
        # the cell writes journaled since the workbook was saved (janus wires
        # the Neon ledger). Every sheet WITHOUT a live source gets its today
        # (or this-week) row repainted from it, so a write shows within a tick
        # instead of after Excel's next save plus a rebuild. Formula cells that
        # depend on the written cell still wait for the save.
        self.write_source = None
        self.write_through: dict = {}
        self._restore()

    # --- state ---------------------------------------------------------------
    def sheets(self) -> list:
        return (self.snap or {}).get("sheets") or []

    def names(self) -> list:
        return [HOME_TAB] + [s["name"] for s in self.sheets()]

    def sheet(self) -> dict | None:
        sh = self.sheets()
        return sh[self.tab - 1] if 1 <= self.tab <= len(sh) else None

    def _restore(self) -> None:
        try:
            d = json.loads(self.state_file.read_text())
            self.scroll = {k: [None if v[0] is None else int(v[0]), int(v[1])] for k, v in (d.get("scroll") or {}).items()}
            self._want = d.get("tab_name") or HOME_TAB
        except (OSError, ValueError, TypeError, IndexError):
            self._want = HOME_TAB
        self.load()
        names = self.names()
        self.tab = names.index(self._want) if self._want in names else 0

    def save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tab_name": self.names()[self.tab] if self.tab < len(self.names()) else HOME_TAB,
                                       "scroll": self.scroll}, ensure_ascii=False))
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def load(self) -> bool:
        """(Re)read the snapshot if the file changed. Keeps the current tab on
        the same SHEET when the sheet list changes under it."""
        try:
            m = self.grid_file.stat().st_mtime
        except OSError:
            return False
        if self.snap is not None and m == self.snap_mtime:
            return False
        snap = neon_grid.load_snapshot(self.grid_file)
        if snap is None:
            return False
        current = self.names()[self.tab] if self.snap is not None and self.tab < len(self.names()) else None
        self.snap, self.snap_mtime, self._memo = snap, m, None
        if current is not None:
            names = self.names()
            self.tab = names.index(current) if current in names else 0
        self.refresh_live()
        return True

    def refresh_live(self) -> bool:
        """Repaint each sheet's today row from its live source. Cheap (a local
        sqlite read for the ledger); a source that fails or has nothing leaves
        the workbook's own values in place. True when something changed."""
        if not self._current_day():
            return False                # yesterday's snapshot: its "today" row is the wrong row
        changed = False
        writes = {}
        if self.write_source is not None:
            try:
                writes = self.write_source((self.snap or {}).get("src_mtime", 0) - WRITE_MARGIN_S) or {}
            except Exception:
                writes = {}
        for sh in self.sheets():
            fn = self.live_sources.get(sh["name"])
            if fn is None:
                row = (sh.get("live") or {}).get("excel_row")
                mine = {k[2]: v for k, v in writes.items() if k[0] == sh["name"] and k[1] == row}
                through = max((str(v[1])[11:19] for v in mine.values()), default="") or None
                if mine and neon_grid.overlay(sh, {c: v[0] for c, v in mine.items()}, zeros=True):
                    changed = True
                if self.write_through.get(sh["name"]) != through:
                    changed = True
                    if through:
                        self.write_through[sh["name"]] = through
                    else:
                        self.write_through.pop(sh["name"], None)
                continue
            try:
                values, through = fn()
            except Exception:
                values, through = None, None
            if not values:
                if self.live_through.pop(sh["name"], None) is not None:
                    changed = True
                continue
            if neon_grid.overlay(sh, values) or self.live_through.get(sh["name"]) != through:
                changed = True
            self.live_through[sh["name"]] = through
        if changed:
            self._memo = None
        return changed

    def _current_day(self) -> bool:
        """The snapshot was built for today (older snapshots carry no day)."""
        day = (self.snap or {}).get("day")
        return day is None or day == dt.date.today().isoformat()

    def select(self, i: int) -> None:
        n = len(self.names())
        self.tab = i if 0 <= i < n else 0
        sh = self.sheet()
        if sh is not None and sh["name"] in self.scroll:
            # entering a tab lands on today's row again (a position scrolled to weeks
            # ago must not be what "today" looks like); the column scroll is kept
            self.scroll[sh["name"]][0] = None
        self.save()

    def next(self, step: int = 1) -> None:
        self.select((self.tab + step) % max(1, len(self.names())))

    # --- rebuilds ------------------------------------------------------------
    def needs_build(self, now: float | None = None) -> bool:
        """Only while a Neon tab is showing (or nothing has ever been built):
        the workbook moved on since the last build (or the snapshot is from
        another day), and the last build is not too recent. Excel's forced save bumps the mtime every ~2 minutes, so
        this is at most one ~2 s subprocess per REBUILD_MIN_S."""
        now = time.monotonic() if now is None else now
        if self.building or (self.last_build and now - self.last_build < REBUILD_MIN_S):
            return False
        try:
            src_m = self.src.stat().st_mtime
        except OSError:
            return False
        if self.snap is None:
            return True
        return self.tab != 0 and (src_m > self.built_src_mtime or not self._current_day())

    def build(self, run=subprocess.run) -> None:
        """Blocking: run neon_grid.py build in a subprocess, then reload.
        Call from a worker thread."""
        self.building, self.last_build = True, time.monotonic()
        try:
            src_m = self.src.stat().st_mtime
            r = run([sys.executable, str(HERE / "neon_grid.py"), "build", "--src", str(self.src),
                     "--out", str(self.grid_file)], capture_output=True, text=True,
                    timeout=BUILD_TIMEOUT_S, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                self.error, self.built_src_mtime = "", src_m
                self.load()
            else:
                self.error = (r.stderr or r.stdout or "build failed").strip().splitlines()[-1][:200]
        except Exception as e:                      # timeout, missing file, …
            self.error = "%s: %s" % (type(e).__name__, str(e)[:160])
        finally:
            self.building = False
            self._memo = None

    # --- scrolling -----------------------------------------------------------
    def _body(self, sh: dict) -> int:
        return max(0, len(sh["rows"]) - sh["header_rows"])

    def _pos(self, sh: dict, body_h: int) -> list:
        pos = self.scroll.get(sh["name"])
        if pos is None or pos[0] is None:
            top = 0
            if sh.get("today_row") is not None:
                top = sh["today_row"] - sh["header_rows"] - max(0, body_h - 1 - TODAY_FROM_BOTTOM)
            pos = self.scroll[sh["name"]] = [top, pos[1] if pos else 0]
        pos[0] = max(0, min(pos[0], max(0, self._body(sh) - body_h)))
        pos[1] = max(0, min(pos[1], max(0, len(sh["widths"]) - sh["frozen_cols"] - 1)))
        return pos

    def move(self, drow: int = 0, dcol: int = 0, body_h: int = 20) -> None:
        sh = self.sheet()
        if sh is None:
            return
        pos = self._pos(sh, body_h)
        pos[0] += drow
        pos[1] += dcol
        self._pos(sh, body_h)
        self.save()

    def home(self, body_h: int = 20) -> None:
        """Back to today's row (and the first column)."""
        sh = self.sheet()
        if sh is None:
            return
        self.scroll.pop(sh["name"], None)
        self._pos(sh, body_h)
        self.save()

    def end(self, body_h: int = 20) -> None:
        sh = self.sheet()
        if sh is not None:
            self._pos(sh, body_h)[0] = self._body(sh)
            self._pos(sh, body_h)
            self.save()

    # --- rendering -----------------------------------------------------------
    def body_height(self, lines: int) -> int:
        sh = self.sheet()
        return max(1, lines - 2 - (sh["header_rows"] if sh else 0))     # strip + status line

    def render(self, cols: int, lines: int, handler=None) -> list:
        """Fragments for a `cols` x `lines` pane. `handler` (optional) is
        attached to every fragment so the mouse wheel can scroll the grid."""
        cols, lines = max(20, cols), max(4, lines)
        sh = self.sheet()
        key = (self.tab, cols, lines, (self.snap or {}).get("hash"), self.building, self.error,
               tuple(self.scroll.get(sh["name"], ())) if sh else (), handler)
        if self._memo and self._memo[0] == key:
            return self._memo[1]
        out = self._strip(cols)
        if sh is None:
            msg = "building the Neon snapshot…" if self.building or not self.error else "Neon snapshot failed: " + self.error
            out.append(("", "\n " + msg + "\n"))
        else:
            body_h = self.body_height(lines)
            top, first = self._pos(sh, body_h)
            # recompute the key now that _pos may have initialised the scroll
            key = key[:6] + (tuple(self.scroll[sh["name"]]), handler)
            shown = _visible_cols(sh, first, cols)
            hdr = sh["header_rows"]
            for r in range(hdr):
                out += _row(sh, r, shown, cols, r)
            last = min(len(sh["rows"]), hdr + top + body_h)
            for r in range(hdr + top, last):
                out += _row(sh, r, shown, cols, hdr + top)
            out += [("", "\n")] * max(0, body_h - (last - hdr - top))
            out.append((STATUS_STYLE, self._status(sh, top, body_h, shown, cols)))
        if handler is not None:
            out = [(f[0], f[1], handler) for f in out]
        self._memo = (key, out)
        return out

    def _strip(self, cols: int) -> list:
        """The tab strip, scrolled so the current tab is always in view."""
        names = self.names()
        tabs = self.sheets()
        cells = []
        for i, name in enumerate(names):
            label = " %s " % name
            if i == self.tab:
                color = tabs[i - 1].get("tab") if i else None
                style = "bold bg:%s %s" % (color or "#d0d0d0", _ink(color or "#d0d0d0"))
            else:
                style = STRIP_STYLE
            cells.append((style, label, neon_grid.cwidth(label)))
        start = 0
        while start < self.tab and sum(c[2] for c in cells[start:self.tab + 1]) > cols:
            start += 1
        out, used = [], 0
        if start:
            out.append((STRIP_STYLE, "‹"))
            used = 1
        for style, label, w in cells[start:]:
            if used + w > cols:
                out.append((STRIP_STYLE, "›"))
                used += 1
                break
            out.append((style, label))
            used += w
        if used < cols:
            out.append((STRIP_STYLE, " " * (cols - used)))
        out.append(("", "\n"))
        return out

    def _status(self, sh: dict, top: int, body_h: int, shown: list, cols: int) -> str:
        body = self._body(sh)
        scrolling = [c for c in shown if c >= sh["frozen_cols"]]
        span = "%s–%s" % (sh["cols"][scrolling[0]], sh["cols"][scrolling[-1]]) if scrolling else "-"
        built = time.strftime("%H:%M", time.localtime((self.snap or {}).get("src_mtime", 0)))
        bits = ["%d–%d/%d" % (top + 1, min(body, top + body_h), body), span, "saved " + built]
        if self.live_through.get(sh["name"]):
            bits.append("today ledger " + str(self.live_through[sh["name"]])[:5])
        if self.write_through.get(sh["name"]):
            bits.append("ledger write " + self.write_through[sh["name"]][:5])
        if self.building:
            bits.append("refreshing…")
        if self.error:
            bits.append("! " + self.error)
        if sh.get("skipped_rules"):
            bits.append("%d colour rules skipped" % sh["skipped_rules"])
        line = " " + " · ".join(bits)
        hint = " · ^T tab · ⎋ janus"                 # the hint goes first when the pane is narrow
        if neon_grid.cwidth(line + hint) <= cols:
            line += hint
        return _clip(line, cols)


def _ink(bg: str) -> str:
    """Black or white text, whichever reads on `bg`."""
    return "#000000" if neon_grid._lum(bg.lstrip("#")) > 0.5 else "#ffffff"


def _clip(text: str, width: int) -> str:
    out, used = "", 0
    for ch in text:
        w = neon_grid.cwidth(ch)
        if used + w > width:
            break
        out += ch
        used += w
    return out


def _visible_cols(sh: dict, first: int, cols: int) -> list:
    """Column indexes that fit: the frozen ones, then as many scrolling ones
    as fit starting at `first` (a column is never drawn partly)."""
    widths, frozen = sh["widths"], sh["frozen_cols"]
    shown, used = [], 0
    for c in range(min(frozen, len(widths))):
        if used + widths[c] > cols:
            break
        shown.append(c)
        used += widths[c]
    for c in range(frozen + first, len(widths)):
        if used + widths[c] > cols:
            break
        shown.append(c)
        used += widths[c]
    if len(shown) <= frozen and frozen + first < len(widths):
        shown.append(frozen + first)            # pane narrower than one column: show it clipped
    return shown


def _cell_text(sh: dict, r: int, c: int, first_visible: int) -> str:
    cell = sh["rows"][r][c]
    if not cell[0] and len(cell) > 2 and r == first_visible and r - cell[2] >= 0:
        return sh["rows"][r - cell[2]][c][0]     # top of a merge is off-screen: show its label here
    return cell[0]


def _row(sh: dict, r: int, shown: list, cols: int, first_visible: int) -> list:
    out, used = [], 0
    styles, widths = sh["styles"], sh["widths"]
    for c in shown:
        cell = sh["rows"][r][c]
        bg, fg, bold, align = styles[cell[1]]
        w = min(widths[c], cols - used)
        if w <= 0:
            break
        text = _cell_text(sh, r, c, first_visible)
        if neon_grid.cwidth(text) > w and "," in text:
            text = text.replace(",", "")                      # 1,744 -> 1744 before clipping
        if neon_grid.cwidth(text) > w and text.lstrip("-").isdigit():
            text = "#" * w                                    # Excel's way: a cut-off integer reads as a wrong number
        text = _clip(text, w)
        pad = w - neon_grid.cwidth(text)
        if align == "r":
            text = " " * pad + text
        elif align == "c":
            text = " " * (pad // 2) + text + " " * (pad - pad // 2)
        elif pad:
            text = " " + text + " " * (pad - 1)               # Excel's cell padding: text never touches the cell to its left
        style = " ".join(x for x in (("bg:" + bg) if bg else "", fg or "", "bold" if bold else "") if x)
        out.append((style, text))
        used += w
    out.append(("", "\n"))
    return out
