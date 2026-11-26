#!/usr/bin/env python3
"""neon_tabs: janus's Neon tabs, minus prompt_toolkit.

2026-11-21, "neon frontend -> CLI". Tab 0 is janus's own timeline; tabs 1..N
are the Neon sheets in the snapshot neon_grid.py writes. This module owns the
tab state (which tab, each sheet's scroll position, persisted so a janus
self-restart comes back where it was), decides when the snapshot needs a
rebuild, and turns a sheet into formatted-text fragments for a pane of a given
size: a tab strip, the pinned header rows, a window of body rows with the
frozen columns on the left, and a status line.

2026-11-25, "2-3 lines per day": a sheet wider than the pane is not scrolled
sideways any more. Each row (a day, a week) wraps onto up to WRAP_LINES lines
that use the whole pane width, the header wrapping the same way above it; what
still does not fit is a further PAGE of columns (left/right).

2026-11-26: hcbi opens with the current week (hcb / hcbp sums, a headerless
Daily Dozen block with its sums, each day's meals) above the full log, which
keeps one line a day there (ONE_LINE) and pages sideways.

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
REMOTE_EVERY_S = 45         # off Ix: refetch the snapshot from Ix this often while a Neon tab shows
REMOTE_HOST = os.environ.get("IX_HOST", "ix")
REMOTE_CMD = "/usr/bin/python3 ~/i446-monorepo/tools/tg/neon_grid.py emit"


def on_ix() -> bool:
    """This process runs on Ix, the one Mac whose workbook, ledger and book
    are current (same test as did-fast's _on_ix)."""
    import socket
    host = socket.gethostname().lower()
    return host in ("ix", "ix.local") or host.startswith("ix.") or "mac-mini" in host

BUILD_TIMEOUT_S = 120
WRITE_MARGIN_S = 30         # ledger writes this long before the save still overlay (a save in flight)
TODAY_FROM_BOTTOM = 3       # a sheet opens with today's row this far above the last line
WRAP_LINES = 3              # lines one row may wrap onto; columns past that are the next page (←/→)
TODAY_STYLE = "bg:#c3fc0d #000000 bold"     # today's date cell (janus's NEON_ACCENT)
DATE_STYLE = "#bcbcbc"      # every frozen (week / date) body cell: one colour down the gutter, no fills, no
                            # stripes (JM 2026-11-25: "keep the colors constant ... so I don't get a headache")
ZEBRA_BG = "#262626"        # every other wrapped row, so a 3-line day reads as one block
# A row's second and third lines hang under the frozen columns, leaving the
# date column a clear gutter to scan down (JM 2026-11-25, 0n: "everything is
# indented after the first line so I can navigate", then "on 0s897 and hcbi
# etc also"). Costs that width per line. Not on the lists whose frozen columns
# are a long task label rather than a date.
FLUSH = {"2n", "3n", "4n"}
# One line a day, the rest of the columns on further pages (JM 2026-11-26, hcbi:
# "each day can have a row and I'm willing to scroll for it").
ONE_LINE = {"hcbi"}
STRIP_STYLE = "bg:#303030 #9a9a9a"
STATUS_STYLE = "#8a8a8a"


class Tabs:
    def __init__(self, state_file: Path | None = None, grid_file: Path | None = None,
                 src: Path | None = None, remote: bool | None = None):
        # Off Ix the local workbook is somebody's OneDrive copy (Straylight's
        # stopped syncing on 2026-10-12 and the tabs showed zeros from then
        # on): the snapshot is fetched from Ix instead, never built here.
        # Explicit files (tests, tools) and pytest always stay local.
        if remote is None:
            env = os.environ.get("NEON_GRID_REMOTE", "")
            if env in ("0", "1"):
                remote = env == "1"
            else:
                remote = (grid_file is None and src is None and not on_ix()
                          and not os.environ.get("PYTEST_CURRENT_TEST"))
        self.remote = remote
        self.state_file = state_file or STATE_FILE
        self.grid_file = grid_file or neon_grid.OUT
        self.src = src or neon_grid.SRC
        self.tab = 0                    # 0 = janus timeline, i = sheets[i-1]
        self.scroll: dict = {}          # sheet name -> [top body row, page of columns]
        self.cols = 80                  # pane width last rendered / asked about (wrapping depends on it)
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
        # callable(sheet name, excel row, [column letters]) -> ({col: value}, "HH:MM:SS" | None):
        # the WHOLE live row as the Neon book computes it (seed + ledger writes,
        # formulas re-evaluated, so a total that depends on a written cell moves
        # too). When it answers for a sheet, it replaces both sources above for
        # that sheet; (None, None) falls back to them.
        self.row_source = None
        self.book_through: dict = {}
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
        if (self.snap or {}).get("host"):
            # fetched from Ix with the book's rows already painted there: this
            # host has no ledger worth overlaying (at best a stale copy)
            book = dict((self.snap or {}).get("book") or {})
            if book == self.book_through and not self.live_through and not self.write_through:
                return False
            self.book_through, self.live_through, self.write_through = book, {}, {}
            self._memo = None
            return True
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
            if self._book_row(sh):
                changed = True
            if sh["name"] in self.book_through:
                continue
            fn = self.live_sources.get(_source(sh))
            if fn is None:
                row = (sh.get("live") or {}).get("excel_row")
                mine = {k[2]: v for k, v in writes.items() if k[0] == _source(sh) and k[1] == row}
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

    def _book_row(self, sh: dict) -> bool:
        """Repaint `sh`'s live row from row_source. True when the grid or its
        status changed; the sheet is in book_through while the book answers."""
        live = sh.get("live") or {}
        values, through = None, None
        if self.row_source is not None and live.get("excel_row"):
            try:
                values, through = self.row_source(_source(sh), live["excel_row"], list(sh["cols"]))
            except Exception:
                values, through = None, None
        if values is None:
            return self.book_through.pop(sh["name"], None) is not None
        row = sh["rows"][live["row"]]
        # a blank stays out unless the grid shows something there (a cleared cell)
        paint = {c: v for c, v in values.items()
                 if c in sh["cols"] and not (v == "" and row[sh["cols"].index(c)][0] == "")}
        changed = bool(paint and neon_grid.overlay(sh, paint, zeros=True))
        through = through or ""
        if self.book_through.get(sh["name"]) != through:
            self.book_through[sh["name"]] = through
            changed = True
        return changed

    def _current_day(self) -> bool:
        """The snapshot was built for today (older snapshots carry no day)."""
        day = (self.snap or {}).get("day")
        if day is None or day == dt.date.today().isoformat():
            return True
        # a snapshot Ix emitted moments ago is current even if the two clocks
        # sit on either side of midnight
        return time.time() - ((self.snap or {}).get("emitted") or 0) < 2 * REMOTE_EVERY_S

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
        if self.remote:
            if self.building or (self.last_build and now - self.last_build < REMOTE_EVERY_S):
                return False
            return self.snap is None or not (self.snap or {}).get("host") or self.tab != 0
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
        if self.remote:
            return self._fetch(run)
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

    def _fetch(self, run=subprocess.run) -> None:
        """Remote mode's build: ask Ix for its snapshot (neon_grid.py emit)
        and keep it as this host's grid file. A failure leaves the last
        fetched snapshot on screen with the reason in the status line."""
        self.building, self.last_build = True, time.monotonic()
        try:
            r = run(["ssh", "-C", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=accept-new", REMOTE_HOST, REMOTE_CMD],
                    capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()
                self.error = "ix: " + (tail[-1][:160] if tail else "ssh exit %d" % r.returncode)
                return
            snap = json.loads(r.stdout)
            if not isinstance(snap, dict) or snap.get("version") != neon_grid.VERSION or not snap.get("host"):
                self.error = "ix sent snapshot v%s, this janus reads v%s" % (
                    snap.get("version") if isinstance(snap, dict) else "?", neon_grid.VERSION)
                return
            self.grid_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.grid_file.with_suffix(".fetch.%d.tmp" % os.getpid())
            tmp.write_text(r.stdout, encoding="utf-8")
            os.replace(tmp, self.grid_file)
            self.error = ""
            self.load()
        except Exception as e:                      # timeout, bad JSON, no ssh, …
            self.error = "ix: %s: %s" % (type(e).__name__, str(e)[:140])
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
        pos[1] = max(0, min(pos[1], len(_pages(sh, self.cols)) - 1))
        return pos

    def move(self, drow: int = 0, dcol: int = 0, body_h: int = 20) -> None:
        """`drow` rows down; `dcol` PAGES of columns right (a page is what
        WRAP_LINES lines hold at this pane width)."""
        sh = self.sheet()
        if sh is None:
            return
        pos = self._pos(sh, body_h)
        pos[0] += drow
        pos[1] += dcol
        self._pos(sh, body_h)
        self.save()

    def home(self, body_h: int = 20) -> None:
        """Back to today's row (and the first page of columns)."""
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
    def _layout(self, sh: dict, cols: int, lines: int) -> tuple:
        """(pages, lines per row, panel fragments-by-line, body rows that fit).
        The lines per row are the same on every page of columns (the widest
        page's), so paging sideways never changes how many rows fit."""
        pages = _pages(sh, cols)
        per = max(len(p) for p in pages)
        fixed = 2 + sh["header_rows"] * per                 # strip + status line + pinned header
        panel = hcbi_panel(sh, cols) if sh["name"] == "hcbi" else []
        if panel and lines - fixed - len(panel) < HCBI_GRID_MIN * per:
            panel = hcbi_panel(sh, cols, wrap=False)        # tight: one line of meals a day
        if panel and lines - fixed - len(panel) < 3 * per:
            panel = []                                      # short pane: the grid wins
        return pages, per, panel, max(1, (lines - fixed - len(panel)) // per)

    def body_height(self, lines: int, cols: int | None = None) -> int:
        """Body ROWS (days, weeks) that fit in a pane `lines` tall: each takes
        as many lines as the sheet wraps onto at `cols` wide."""
        if cols:
            self.cols = max(20, cols)
        sh = self.sheet()
        return self._layout(sh, self.cols, max(4, lines))[3] if sh else max(1, lines - 2)

    def render(self, cols: int, lines: int, handler=None) -> list:
        """Fragments for a `cols` x `lines` pane. `handler` (optional) is
        attached to every fragment so the mouse wheel can scroll the grid."""
        cols, lines = max(20, cols), max(4, lines)
        self.cols = cols
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
            pages, per, panel, body_h = self._layout(sh, cols, lines)
            top, page = self._pos(sh, body_h)
            # recompute the key now that _pos may have initialised the scroll
            key = key[:6] + (tuple(self.scroll[sh["name"]]), handler)
            segs = pages[page] + [[]] * (per - len(pages[page]))
            hdr = sh["header_rows"]
            today = sh.get("today_row")
            mark = (today, sh["frozen_cols"] - 1) if today is not None and sh["frozen_cols"] else None
            for line in panel:
                out += line
            hang = _hang(sh, cols)
            for i, seg in enumerate(segs):                  # each wrapped line's labels sit above its values
                for r in range(hdr):
                    out += _row(sh, r, seg, cols, r, indent=hang if i else 0)
            last = min(len(sh["rows"]), hdr + top + body_h)
            for r in range(hdr + top, last):
                zebra = per > 1 and r % 2 == 1
                for i, seg in enumerate(segs):
                    out += _row(sh, r, seg, cols, hdr + top, zebra, mark, hang if i else 0)
            body_lines = lines - 2 - len(panel) - hdr * per
            out += [("", "\n")] * max(0, body_lines - per * (last - hdr - top))
            out.append((STATUS_STYLE, self._status(sh, top, body_h, [c for seg in segs for c in seg], cols,
                                                   page, len(pages))))
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

    def _status(self, sh: dict, top: int, body_h: int, shown: list, cols: int,
                page: int = 0, npages: int = 1) -> str:
        body = self._body(sh)
        scrolling = [c for c in shown if c >= sh["frozen_cols"]]
        span = "%s–%s" % (sh["cols"][scrolling[0]], sh["cols"][scrolling[-1]]) if scrolling else "-"
        if npages > 1:
            span += " %s%d/%d%s" % ("←" if page else "", page + 1, npages, "→" if page < npages - 1 else "")
        saved_at = time.localtime((self.snap or {}).get("src_mtime", 0))
        built = time.strftime("%H:%M", saved_at)
        if ((self.snap or {}).get("src_mtime") or 0) > 1e9 and saved_at[:3] != time.localtime()[:3]:
            # a save from another day is not "current as of HH:MM" (2026-11-22:
            # a workbook copy six weeks stale read "saved 12:34")
            built = time.strftime("%m-%d %H:%M", saved_at) + " ⚠ stale"
        bits = ["%d–%d/%d" % (top + 1, min(body, top + body_h), body), span, "saved " + built]
        host = (self.snap or {}).get("host")
        if self.remote and not host:
            bits.append("⚠ this Mac's copy, not ix")
        elif host and self.remote:
            bits.insert(1, host)
        if self.live_through.get(sh["name"]):
            bits.append("today ledger " + str(self.live_through[sh["name"]])[:5])
        if sh["name"] in self.book_through:
            bits = [b for b in bits if not b.startswith(("saved ", "today ledger ", "ledger write "))]
            bits.insert(1 if bits else 0, "book " + (self.book_through[sh["name"]][:5] or "= save"))
        elif self.write_through.get(sh["name"]):
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


def _source(sh: dict) -> str:
    """The workbook sheet a tab reads from: its own name, or for a part of a
    split sheet (1n / 2n / 3n / 4n) the sheet it was cut out of."""
    return sh.get("source") or sh["name"]


def _pages(sh: dict, cols: int, max_lines: int | None = None) -> list:
    """How a sheet's columns lay out in a pane `cols` wide: a list of pages,
    each a list of lines, each a list of column indexes. A page's first line
    starts with the frozen columns; the scrolling columns then fill up to
    `max_lines` lines greedily (a column is never drawn partly unless it is
    wider than the pane), and whatever is left over starts the next page. A
    ONE_LINE sheet keeps each row on a single line."""
    if max_lines is None:
        max_lines = 1 if sh["name"] in ONE_LINE else WRAP_LINES
    widths = sh["widths"]
    n, frozen = len(widths), min(sh["frozen_cols"], len(widths))
    head, head_w = [], 0
    for c in range(frozen):
        if head_w + widths[c] > cols:
            break
        head.append(c)
        head_w += widths[c]
    hang = head_w if sh["name"] not in FLUSH and head_w < cols // 2 else 0
    pages, c = [], frozen
    while c < n or not pages:
        page, line, used, placed = [], list(head), head_w, 0
        while c < n:
            if used + widths[c] <= cols or not line:        # an empty line always takes one column
                line.append(c)
                used += widths[c]
                c += 1
                placed += 1
                continue
            if len(page) + 1 >= max_lines:
                if not placed:                              # frozen columns alone fill the page: show one anyway
                    line.append(c)
                    c += 1
                break
            page.append(line)
            line, used = [], hang
        page.append(line)
        pages.append(page)
    return pages


# --- hcbi: the current week above the full log --------------------------------
# JM 2026-11-26: "at the top should be my overall sum on hcbp, then a section
# for daily dozen, but I don't need headers. Just date and day of week and the
# color fill will tell me which color it is. I don't need padding between these
# columns either. Then one row for sums. Then a section for the same days the
# meals that I had, listed with a block prefix (卯 mocha 390 12, 申 …)."
HCBI_DOZEN = ("E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O")   # bn br fr cr gr vg fx g nt sp wtr
# Daily targets, tools/1hcb/1hcb.py's (FOOD_GROUPS, PROTEIN_TARGET, CAL_CEILING);
# test_neon_tabs holds the two in step. Grains and caffeine have no goal.
HCBI_DOZEN_TARGETS = (3, 1, 3, 1, 2, 2, 1, None, 1, 1, 3)
HCBI_EXTRA = (("P", 100, ">="), ("U", 1800, "<="), ("W", None, None))     # protein g, kcal (a ceiling), caf
HCBI_MET = "bg:#5fd75f #000000"     # a met target in a column the sheet left unfilled all week
HCBI_BLOCKS = "卯辰巳午未申戌"     # header glyphs of the meal triads: name, kcal, protein g
HCBI_HCBP = "Y"
HCBI_TOTAL = (388, "X")         # the sheet's combined hcbc+hcbp cell (janus.HCBI_HCB_TOTAL_CELL)
HCBI_WEEK = 7
HCBI_GRID_MIN = 8               # grid rows the panel must leave before its meals stop wrapping
MEAL_SEP = " · "                # not ", ": meal names carry commas
_DIM, _GLYPH = "#6c6c6c", "bold #d7af00"


def _num(text: str):
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def _rpad(text: str, width: int) -> str:
    return " " * max(0, width - neon_grid.cwidth(text)) + text


def _cell_style(sh: dict, cell: list) -> str:
    bg, fg, bold = sh["styles"][cell[1]][:3]
    return " ".join(x for x in (("bg:" + bg) if bg else "", fg or "", "bold" if bold else "") if x)


def _one(text: str) -> str:
    """A Daily Dozen count in one character: ½ for a half, # past nine."""
    v = _num(text)
    if v is None or neon_grid.cwidth(text) <= 1:
        return text
    return "½" if v == 0.5 else ("%d" % round(v) if 0 <= round(v) <= 9 else "#")


def _hcbi_week(sh: dict) -> list:
    """Row indexes of the current week, oldest first, ending on today: back to
    the row carrying a week label in column A (the sheet's weeks start Sunday)."""
    today, hdr = sh["today_row"], sh["header_rows"]
    first = today
    for r in range(today, max(hdr, today - HCBI_WEEK + 1) - 1, -1):
        first = r
        if sh["rows"][r][0][0].strip():
            break
    return list(range(first, today + 1))


def _meals(sh: dict, r: int) -> list:
    """[(block glyph, name, "kcal protein")] for the meals logged on row `r`."""
    out, n = [], len(sh["cols"])
    for c, head in enumerate(sh["rows"][0]):
        glyph = head[0].strip()
        if len(glyph) != 1 or glyph not in HCBI_BLOCKS:
            continue
        parts = [sh["rows"][r][i][0].strip() for i in range(c, min(n, c + 3))]
        if parts[0]:
            out.append((glyph, parts[0], " ".join(x for x in parts[1:] if x)))
    return out


def hcbi_panel(sh: dict, cols: int, wrap: bool = True) -> list:
    """The current week, as lines (each a list of fragments ending in a
    newline): the hcb / hcbp sums; a headerless Daily Dozen block, one line a
    day, the cells flush against each other in the sheet's own fills; their
    sums; then each day's meals behind their block glyph. `wrap` False keeps a
    day's meals to one clipped line. [] when the sheet lacks the columns or a
    today row."""
    today = sh.get("today_row")
    if today is None or any(c not in sh["cols"] for c in HCBI_DOZEN + (HCBI_HCBP, "B", "C")):
        return []
    rows, ci = sh["rows"], sh["cols"].index
    days = _hcbi_week(sh)
    b, c_, y = ci("B"), ci("C"), ci(HCBI_HCBP)
    labels = [_rpad(rows[r][b][0].strip(), 5) + " " + rows[r][c_][0].strip().ljust(2) + " " for r in days]
    gutter = 9

    def date(i, r):
        return (TODAY_STYLE if r == today else DATE_STYLE, labels[i])

    # 1. the sums
    bits = []
    if HCBI_TOTAL[0] in sh.get("row_nums", []) and HCBI_TOTAL[1] in sh["cols"]:
        v = _num(rows[sh["row_nums"].index(HCBI_TOTAL[0])][ci(HCBI_TOTAL[1])][0])
        if v is not None:
            bits.append("hcb %+d" % round(v))
    m = _num(rows[today][b][0].split("/")[0])
    quarter = "Q%d" % ((int(m) - 1) // 3 + 1) if m else ""
    for r in range(today + 1, len(rows)):
        if quarter and rows[r][b][0].strip() == quarter:
            v = _num(rows[r][y][0])
            if v is not None:
                bits.append("hcbp %s %+d" % (quarter, round(v)))
            break
    bits.append("week %+d" % round(sum(_num(rows[r][y][0]) or 0 for r in days)))
    out = [[("bold", _clip(" " + MEAL_SEP.join(bits), cols)), ("", "\n")]]

    # 2. the dozen, then protein / kcal / caf as the sheet fills them; 3. their
    # sums (filled like each column's best day: the legend) over the targets
    # for the days shown, a target filled only when the sum meets it
    plan = [(col, t, ">=") for col, t in zip(HCBI_DOZEN, HCBI_DOZEN_TARGETS)]
    plan += [m for m in HCBI_EXTRA if m[0] in sh["cols"]]
    sums, best, goals, texts = [], [], [], []
    for col, target, kind in plan:
        cells = [(_num(rows[r][ci(col)][0]), rows[r][ci(col)]) for r in days]
        cells = [x for x in cells if x[0] is not None]
        total = sum(x[0] for x in cells)
        style = _cell_style(sh, max(cells, key=lambda x: x[0])[1]) if cells else ""
        sums.append(("%g" % total, (style + " bold").strip()))
        if target is None:
            goals.append(("", ""))
        else:
            met = total >= target * len(days) if kind == ">=" else 0 < total <= target * len(days)
            fill = style if "bg:" in style else HCBI_MET
            goals.append((("≤" if kind == "<=" else "") + "%g" % (target * len(days)), fill if met else _DIM))
        texts.append([rows[r][ci(col)][0].strip().replace(",", "") for r in days])
    # A day's dozen is one character a column, flush; the sums and targets need
    # more, so they run wider under it and protein / kcal / caf start where
    # those rows end, on every line.
    nd = len(HCBI_DOZEN)
    for k in range(nd):
        texts[k] = [_one(t) for t in texts[k]]
    w = max([1] + [neon_grid.cwidth(x[0]) for x in sums[:nd] + goals[:nd]])
    w += w > 1 and any(neon_grid.cwidth(g[0]) == w for g in goals[:nd])    # unfilled targets must not run together
    widths = [w] * nd + [1 + max(neon_grid.cwidth(t) for t in texts[k] + [sums[k][0], goals[k][0]])
                         for k in range(nd, len(plan))]
    fit, used = 0, gutter                                   # a pane too narrow drops the last columns
    while fit < len(plan) and used + widths[fit] <= cols:
        used += widths[fit]
        fit += 1

    def cells_line(lead, items, day=False):
        line = [lead]
        for k, (t, st) in enumerate(items[:fit]):
            line.append((st, _rpad(t, 1 if day and k < nd else widths[k])))
            if day and k == nd - 1 and fit > nd:
                line.append(("", " " * (nd * (w - 1))))
        return line + [("", "\n")]

    out.append([("", "\n")])
    for i, r in enumerate(days):
        out.append(cells_line(date(i, r), [(texts[k][i], _cell_style(sh, rows[r][ci(plan[k][0])]))
                                           for k in range(len(plan))], day=True))
    out.append(cells_line((_DIM, "∑".ljust(gutter)), sums))
    out.append(cells_line((_DIM, "target".ljust(gutter)), goals))
    out.append([("", "\n")])

    # 4. the meals
    room = max(8, cols - gutter)
    for i, r in enumerate(days):
        line, used, first = [date(i, r)], 0, True
        for glyph, name, tail in _meals(sh, r) or [("", "·", "")]:
            # a meal wider than the pane loses the end of its name, never its numbers
            keep = room - 3 - (neon_grid.cwidth(tail) + 1 if tail else 0)
            text = _clip(name, max(4, keep)) + (" " + tail if tail else "")
            need = neon_grid.cwidth(glyph) + 1 + neon_grid.cwidth(text) if glyph else 1
            sep = 0 if first else neon_grid.cwidth(MEAL_SEP)
            if not first and used + sep + need > room:
                if not wrap:
                    break
                out.append(line + [("", "\n")])
                line, used, sep = [("", " " * gutter)], 0, 0
            if sep:
                line.append((_DIM, MEAL_SEP))
            if glyph:
                line += [(_GLYPH, glyph + " "), ("", text)]
            else:
                line.append((_DIM, text))
            used += sep + need
            first = False
        out.append(line + [("", "\n")])
    out.append([("", "\n")])
    return out


def _hang(sh: dict, cols: int) -> int:
    """Blank cells in front of a row's continuation lines (0 on a FLUSH sheet)."""
    if sh["name"] in FLUSH:
        return 0
    w = 0
    for c in range(min(sh["frozen_cols"], len(sh["widths"]))):
        if w + sh["widths"][c] > cols:
            break
        w += sh["widths"][c]
    return w if w < cols // 2 else 0


def _cell_text(sh: dict, r: int, c: int, first_visible: int) -> str:
    cell = sh["rows"][r][c]
    if not cell[0] and len(cell) > 2 and r == first_visible and r - cell[2] >= 0:
        return sh["rows"][r - cell[2]][c][0]     # top of a merge is off-screen: show its label here
    return cell[0]


def _row(sh: dict, r: int, shown: list, cols: int, first_visible: int,
         zebra: bool = False, mark: tuple | None = None, indent: int = 0) -> list:
    """One line of row `r`: the columns in `shown`. `zebra` tints the unfilled
    cells (and the rest of the line); `mark` = (row, column) of today's date
    cell, drawn in TODAY_STYLE. `indent` blank cells lead a continuation line."""
    out, used = ([("", " " * indent)], indent) if indent else ([], 0)
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
        if zebra and not bg:
            bg = ZEBRA_BG
        style = " ".join(x for x in (("bg:" + bg) if bg else "", fg or "", "bold" if bold else "") if x)
        if c < sh["frozen_cols"] and r >= sh["header_rows"]:
            style = DATE_STYLE
        out.append((TODAY_STYLE if mark == (r, c) else style, text))
        used += w
    if zebra and used < cols:
        out.append(("bg:" + ZEBRA_BG, " " * (cols - used)))
    out.append(("", "\n"))
    return out
