#!/usr/bin/env python3
"""neon_grid: the Neon workbook's sheets as plain grids of (text, colours).

2026-11-21, "neon frontend -> CLI": JM wants Neon's Excel views as tabs in
janus (then on iOS). This module is the Excel-free half: it reads the .xlsx
that Excel keeps saving on Ix and writes ONE JSON snapshot,
~/.cache/janus/neon-grid.json, holding every visible sheet as rows of cells
with the text Excel would display and the colours Excel would paint
(static fill, then the conditional-format rules, which in Neon are all plain
`cellIs` comparisons against a number or a text literal).

janus runs `neon_grid.py build` as a short-lived subprocess (two openpyxl
loads are ~1.4 s and ~140 MB: not something to do inside the UI process) and
renders the snapshot; janus-mobile can serve the same file. Nothing here
talks to Excel, and nothing imports prompt_toolkit.

Snapshot shape (see build()):
  {"version", "hash", "built", "src_mtime", "day": "YYYY-MM-DD" it was built for, "error"?,
   "sheets": [{"name", "tab": "#rrggbb"|None, "header_rows", "frozen_cols",
               "today_row": index into rows | None, "widths": [chars],
               "styles": [[bg|None, fg|None, bold 0/1, align "l"|"r"|"c"]],
               "rows": [[[text, style_idx, up]]], "skipped_rules"}]}
`up` > 0 marks a cell inside a vertical merge: its text lives `up` rows above
(the renderer shows it on the first visible row of the merge).

CLI:  neon_grid.py build [--src X.xlsx] [--out grid.json] [--force]
      neon_grid.py emit                               (JSON on stdout, for a janus on another Mac)
      neon_grid.py html <sheet> [--out page.html]     (eyeball a sheet)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

SRC = Path.home() / "Library" / "CloudStorage" / "OneDrive-Personal" / "vault-excel" / "Neon分v12.2.xlsx"
OUT = Path.home() / ".cache" / "janus" / "neon-grid.json"
VERSION = 16

# Per-sheet view settings. Neon has no usable freeze panes (only 0s897 has
# one, and openpyxl reports its scroll position, not the split), so the pinned
# header rows and the frozen columns are declared here. `date_col` is where
# the day lives (the same table as services/excel-http DATE_COL); frozen
# columns default to everything up to and including it.
SHEETS = {
    # 周 is white on black down the whole column (JM 2026-11-21); n156 and the
    # unlabelled column after it are not part of the view.
    "0分":     {"header_rows": 1, "date_col": "B", "frozen_cols": 2,
                "drop_headers": ["n156", "", "地", "0₦t", "∀₦t"],
                # 周 is hidden; the date reads mm.dd, white on black. "date_label": "week"
                # folds 周 into it instead, as MM.W.Dd ("08.4.Su", 7 wide)
                "week_col": "A", "date_label": "mm.dd", "col_style": {"B": ["000000", "ffffff"]},
                # compact: 3 chars by default, a few busier columns 4, Σ 5 (no thousands comma) for a gap after the date
                # the workbook's dark greys read better as black in a terminal
                "recolor": {"7f7f7f": "000000", "808080": "000000", "404040": "000000"},
                "widths": {"*": 3, "B": 5, "D": 5, "I": 4, "J": 4, "N": 4, "P": 4, "Q": 4, "R": 4}, "formats": {"D": "0"}},
    "0n":      {"header_rows": 4, "date_col": "C", "frozen_cols": 3},
    "0s897":   {"header_rows": 1, "date_col": "B", "frozen_cols": 2},
    "hcbi":    {"header_rows": 1, "date_col": "B", "frozen_cols": 2},
    "i9+m5x2": {"header_rows": 1, "date_col": "A", "frozen_cols": 1},
    "1n+":     {"header_rows": 3, "date_col": "B", "frozen_cols": 2},
    "1分+1s":  {"header_rows": 1, "date_col": "A", "frozen_cols": 1},
}
# Visible in Excel, not wanted as janus tabs (JM 2026-11-21; 1g may return in another form).
SKIP_SHEETS = {"meta", "分ledger", "1g"}
DEFAULT_VIEW = {"header_rows": 1, "frozen_cols": 1}
MAX_COL_CHARS = 14          # a notes column must not eat a 58-column pane
DEFAULT_EXCEL_WIDTH = 8.43


def cwidth(s: str) -> int:
    """Terminal cells a string occupies (CJK wide/fullwidth = 2, combining = 0)."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


# --- colours -----------------------------------------------------------------

def _theme_palette(wb) -> list:
    """Theme colours in the order Excel's `theme=` index uses: lt1, dk1, lt2,
    dk2, accent1-6, hlink, folHlink (the XML lists dk1 before lt1)."""
    xml = wb.loaded_theme
    if not xml:
        return []
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    found = {}
    for name in ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4",
                 "accent5", "accent6", "hlink", "folHlink"):
        m = re.search(r"<a:%s>(.*?)</a:%s>" % (name, name), xml, re.S)
        if not m:
            continue
        c = re.search(r'lastClr="([0-9A-Fa-f]{6})"', m.group(1)) or re.search(r'srgbClr val="([0-9A-Fa-f]{6})"', m.group(1))
        if c:
            found[name] = c.group(1).lower()
    order = ("lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4",
             "accent5", "accent6", "hlink", "folHlink")
    return [found.get(n) for n in order]


def _tint(hex6: str, tint: float) -> str:
    """Excel's tint: towards white for positive, towards black for negative
    (applied per channel, which is what Excel's HLS luminance shift amounts to
    closely enough for fills)."""
    if not tint:
        return hex6
    out = []
    for i in (0, 2, 4):
        v = int(hex6[i:i + 2], 16)
        v = v * (1 + tint) if tint < 0 else v + (255 - v) * tint
        out.append(max(0, min(255, int(round(v)))))
    return "%02x%02x%02x" % tuple(out)


def resolve_color(color, theme: list):
    """openpyxl Color -> 'rrggbb' or None. Branches on .type first: reading
    .rgb off a theme colour returns an error STRING rather than raising."""
    if color is None:
        return None
    try:
        kind = color.type
    except Exception:
        return None
    try:
        if kind == "rgb":
            v = color.rgb
            if not isinstance(v, str) or len(v) not in (6, 8):
                return None
            if len(v) == 8 and v[:2] == "00" and v[2:] == "000000":
                return None            # 00000000 = "no colour"
            return _tint(v[-6:].lower(), color.tint or 0)
        if kind == "theme":
            i = color.theme
            if not isinstance(i, int) or i >= len(theme) or not theme[i]:
                return None
            return _tint(theme[i], color.tint or 0)
        if kind == "indexed":
            from openpyxl.styles.colors import COLOR_INDEX
            i = color.indexed
            if not isinstance(i, int) or i >= len(COLOR_INDEX):
                return None            # 64/65 = system foreground/background
            return COLOR_INDEX[i][-6:].lower()
    except Exception:
        return None
    return None                        # auto


def _lum(hex6: str) -> float:
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def finish_style(bg, fg, bold: bool, align: str):
    """Excel paints on white with black text; the terminal is the reverse.
    Filled cells keep both colours (black text when the font has none). An
    unfilled cell keeps a coloured font, takes the terminal's own foreground
    for near-black text, and HIDES near-white text, which on Excel's white
    sheet is how Neon makes a cell invisible."""
    hide = False
    if bg:
        fg = fg or "000000"
    elif fg:
        lum = _lum(fg)
        if lum > 0.9:
            hide, fg = True, None
        elif lum < 0.25:
            fg = None
    return (("#" + bg) if bg else None, ("#" + fg) if fg else None, 1 if bold else 0, align, hide)


# --- number formats ----------------------------------------------------------

_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_DATE_TOKEN = re.compile(r"yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|AM/PM", re.I)


def _sections(fmt: str) -> list:
    out, cur, quoted = [], "", False
    for ch in fmt:
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _strip_literals(sec: str):
    """(pattern without [..] blocks / quotes / escapes, literal prefix, literal suffix)."""
    sec = re.sub(r"\[[^\]]*\]", "", sec)
    sec = re.sub(r"_.|\*.", "", sec)                  # padding / repeat directives
    lits = re.findall(r'"([^"]*)"|\\(.)', sec)
    core = re.sub(r'"[^"]*"|\\.', "\x00", sec)
    m = re.search(r"[0#?][0#?,.]*(?:E[+-]0+)?%?|%", core)
    if not m or not re.search(r"[0#%]", m.group(0)):       # `"-"??`: only space placeholders, no number
        return None, "".join(a or b for a, b in lits), ""
    pre_n = core[:m.start()].count("\x00")
    flat = [a or b for a, b in lits]
    pre = "".join(flat[:pre_n]) + re.sub(r"[\x00]", "", core[:m.start()]).strip()
    suf = re.sub(r"[\x00]", "", core[m.end():]).strip() + "".join(flat[pre_n:])
    return m.group(0), pre, suf


def _fmt_date(value, fmt: str) -> str:
    body = re.sub(r"\[[^\]]*\]", "", fmt)
    body = re.sub(r"\\(.)", r"\1", body).replace('"', "")
    is_dt = isinstance(value, dt.datetime)
    is_time = isinstance(value, dt.time)
    ampm = "am/pm" in body.lower()

    def tok(m, _state={"prev": ""}):
        t = m.group(0)
        low = t.lower()
        # `m` right after an hour (or before seconds) is minutes
        before = body[:m.start()].lower()
        after = body[m.end():].lower()
        minute = low in ("m", "mm") and (re.search(r"h[^a-z]*$", before) or re.match(r"[^a-z]*s", after))
        if low == "am/pm":
            return (value.strftime("%p") if (is_dt or is_time) else "")
        if is_time and low[0] in "ymd" and not minute:
            return ""
        if not (is_dt or is_time) and low[0] in "hs":
            return "0" if len(low) == 1 else "00"
        if minute:
            return "%02d" % value.minute if len(low) == 2 else str(value.minute)
        if low == "yyyy":
            return "%04d" % value.year
        if low == "yy":
            return "%02d" % (value.year % 100)
        if low == "mmmm":
            return value.strftime("%B")
        if low == "mmm":
            return value.strftime("%b")
        if low == "mm":
            return "%02d" % value.month
        if low == "m":
            return str(value.month)
        if low == "dddd":
            return value.strftime("%A")
        if low == "ddd":
            return value.strftime("%a")
        if low == "dd":
            return "%02d" % value.day
        if low == "d":
            return str(value.day)
        if low in ("h", "hh"):
            h = value.hour
            if ampm:
                h = h % 12 or 12
            return "%02d" % h if low == "hh" else str(h)
        if low in ("s", "ss"):
            return "%02d" % value.second if low == "ss" else str(value.second)
        return t
    return _DATE_TOKEN.sub(tok, body).strip()


def _general(value: float, width: int) -> str:
    """Excel's General in a narrow column: as many decimals as fit, then none."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    for places in range(6, -1, -1):
        s = ("%.*f" % (places, value)).rstrip("0").rstrip(".") if places else "%.0f" % value
        if s in ("-0", ""):
            s = "0"
        if len(s) <= max(width, 1):
            return s
    return "%.0f" % value


def format_value(value, fmt: str, width: int = 8) -> str:
    """The text Excel shows for a cached value under a number format."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
            value = dt.datetime(value.year, value.month, value.day)
        f = fmt if fmt and fmt != "General" and _DATE_TOKEN.search(re.sub(r"\[[^\]]*\]", "", fmt)) else "m/d/yyyy"
        return _fmt_date(value, _sections(f)[0])
    if isinstance(value, str):
        return value.replace("\r", " ").replace("\n", " ")
    if not isinstance(value, (int, float)):
        return str(value)
    if value != value:                                  # NaN
        return ""
    fmt = fmt or "General"
    secs = _sections(fmt)
    sec, v = secs[0], value
    if value < 0 and len(secs) >= 2:
        sec, v = secs[1], -value
    elif value == 0 and len(secs) >= 3:
        sec = secs[2]
    arabic = "[$-2000401]" in fmt or "[$-2000000]" in fmt
    if sec.strip().lower() in ("general", ""):
        out = _general(v, width)
    else:
        pat, pre, suf = _strip_literals(sec)
        if pat is None:                                 # literal-only section, e.g. accounting zero "-"
            out = (pre + suf).strip() or ""
        else:
            if pat.endswith("%") or pat == "%":
                v, pct = v * 100, "%"
                pat = pat.rstrip("%")
            else:
                pct = ""
            sci = re.search(r"E[+-](0+)$", pat)
            if sci:
                mant = pat[:sci.start()]
                places = len(mant.split(".")[1]) if "." in mant else 0
                s = "%.*E" % (places, v)
                base, exp = s.split("E")
                out = base + "E" + exp[0] + exp[1:].zfill(len(sci.group(1)))
            else:
                intpart, _, frac = pat.partition(".")
                places = len(re.sub(r"[^0#?]", "", frac))
                scale = len(intpart) - len(intpart.rstrip(","))          # trailing commas = /1000 each
                v = v / (1000 ** scale)
                grouped = "," in intpart.rstrip(",")
                out = ("{:,.%df}" if grouped else "{:.%df}") .format(v) if False else \
                    (("{:,.%df}" % places) if grouped else ("{:.%df}" % places)).format(v)
                if places and "#" in frac and "0" not in frac:
                    out = out.rstrip("0").rstrip(".")
                if "0" not in intpart and out.startswith("0.") and "#" in intpart:
                    out = out[1:]
                if out in ("-0", "-0.0", "-0.00"):
                    out = out[1:]
            out = pre + out + pct + suf
        if value < 0 and len(secs) < 2 and not out.startswith("-"):
            out = "-" + out
    return out.translate(_ARABIC_INDIC) if arabic else out


# --- conditional formatting (cellIs only) ------------------------------------

def _operand(text: str):
    t = text.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].replace('""', '"')
    try:
        return float(t)
    except ValueError:
        return None


def _rank(v):
    """Excel's cross-type ordering: numbers < text < booleans."""
    if isinstance(v, bool):
        return 2
    if isinstance(v, str):
        return 1
    return 0


def _cmp(a, b) -> int:
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 1:
        a, b = a.casefold(), b.casefold()
    return (a > b) - (a < b)


def cell_is(value, operator: str, operands: list) -> bool:
    """Does a cellIs rule fire? A blank cell compares as 0 (as 0-length text
    against a text operand); dates compare by serial; errors never match."""
    if any(o is None for o in operands):
        return False
    if isinstance(value, str) and value.startswith("#") and value.endswith(("!", "?", "A")):
        return False
    if isinstance(value, dt.datetime):
        value = (value - dt.datetime(1899, 12, 30)).total_seconds() / 86400
    elif isinstance(value, dt.date):
        value = (value - dt.date(1899, 12, 30)).days
    elif isinstance(value, dt.time):
        value = (value.hour * 3600 + value.minute * 60 + value.second) / 86400
    if value is None:
        value = "" if isinstance(operands[0], str) else 0
    if isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    try:
        if operator in ("between", "notBetween"):
            if len(operands) < 2:
                return False
            lo, hi = sorted(operands[:2], key=lambda o: (_rank(o), o))
            inside = _cmp(value, lo) >= 0 and _cmp(value, hi) <= 0
            return inside if operator == "between" else not inside
        c = _cmp(value, operands[0])
    except TypeError:
        return False
    return {"equal": c == 0, "notEqual": c != 0, "greaterThan": c > 0, "lessThan": c < 0,
            "greaterThanOrEqual": c >= 0, "lessThanOrEqual": c <= 0}.get(operator, False)


def _dxf_parts(dxf, theme):
    """(bg, fg, bold) a differential style sets; None = leaves it alone. A dxf
    fill is 'solid' even when its patternType is absent, and its colour lives
    in bgColor (fgColor only when bgColor is missing)."""
    bg = fg = bold = None
    if dxf is None:
        return bg, fg, bold
    fill = dxf.fill
    if fill is not None and (fill.fill_type in (None, "solid")):
        bg = resolve_color(fill.bgColor, theme) or resolve_color(fill.fgColor, theme)
    font = dxf.font
    if font is not None:
        fg = resolve_color(font.color, theme) if font.color is not None else None
        bold = font.b
    return bg, fg, bold


def _cf_index(ws, theme, max_row: int, max_col: int):
    """{(row, col): [(priority, operator, operands, bg, fg, bold, stop)]} and the
    number of rules that could not be honoured (non-cellIs / unreadable)."""
    out, skipped = {}, 0
    for block in ws.conditional_formatting:
        rules = []
        for rule in block.rules:
            if rule.type != "cellIs":
                skipped += 1
                continue
            operands = [_operand(f) for f in (rule.formula or [])]
            if not operands or any(o is None for o in operands):
                skipped += 1
                continue
            bg, fg, bold = _dxf_parts(rule.dxf, theme)
            rules.append((rule.priority or 0, rule.operator, operands, bg, fg, bold, bool(rule.stopIfTrue)))
        if not rules:
            continue
        for rng in block.sqref.ranges:
            r0, r1 = rng.min_row, min(rng.max_row, max_row)
            c0, c1 = rng.min_col, min(rng.max_col, max_col)
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    out.setdefault((r, c), []).extend(rules)
    return out, skipped


# --- building ----------------------------------------------------------------

def _col_index(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + ord(ch) - 64
    return n


ROW_GAP = 300      # a value this far below the rest is a stray, not the sheet


def _extent(ws_values) -> tuple:
    """Last row / column of the sheet's real content. `meta` has one stray
    value at row 1,048,135: rows are cut at the first gap of ROW_GAP empty
    rows, so a stray cell cannot turn the sheet into a million blank lines."""
    used_rows, max_c = set(), 0
    for (r, c), cell in ws_values._cells.items():
        if cell.value is not None and cell.value != "":
            used_rows.add(r)
    max_r = 0
    for r in sorted(used_rows):
        if max_r and r - max_r > ROW_GAP:
            break
        max_r = r
    for (r, c), cell in ws_values._cells.items():
        if r <= max_r and c > max_c and cell.value is not None and cell.value != "":
            max_c = c
    return max_r, max_c


def _recolor(bg, fg, table):
    """Swap a fill per the view's "recolor" table; text that would vanish on
    the new fill (black on black) turns white."""
    if not table or bg not in table:
        return bg, fg
    new = table[bg]
    if _lum(new) < 0.2 and (fg is None or _lum(fg) < 0.2):
        fg = "ffffff"
    return new, fg


_WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def week_date(week, day) -> str:
    """周 value (month.week, e.g. 8.4) and a date as one label: "08.4.Su"."""
    wd = _WEEKDAYS[day.weekday()]
    m = re.match(r"\s*(\d+)\.(\d+)", _general(week, 8) if isinstance(week, (int, float)) else str(week or ""))
    return "%02d.%s.%s" % (int(m.group(1)), m.group(2), wd) if m else "%02d/%02d" % (day.month, day.day)


def build_sheet(ws, wsv, theme: list, today: dt.date, week_label: str = "") -> dict:
    from openpyxl.utils import get_column_letter
    view = dict(DEFAULT_VIEW, **SHEETS.get(ws.title, {}))
    max_r, max_c = _extent(wsv)
    hidden_cols = set()
    for key, dim in ws.column_dimensions.items():
        if dim.hidden:
            lo = dim.min or _col_index(key)
            hi = dim.max or lo
            hidden_cols.update(range(lo, min(hi, max_c) + 1))
    excel_w = {}
    for key, dim in ws.column_dimensions.items():
        if dim.width:
            lo = dim.min or _col_index(key)
            hi = dim.max or lo
            for c in range(lo, min(hi, max_c) + 1):
                excel_w[c] = dim.width
    cols = [c for c in range(1, max_c + 1) if c not in hidden_cols]
    drop = view.get("drop_headers")
    if drop:
        def _head(c):
            cell = wsv._cells.get((1, c))
            return str(cell.value).strip() if cell is not None and cell.value is not None else ""
        cols = [c for c in cols if _head(c) not in drop]
    week_c = _col_index(view["week_col"]) if view.get("week_col") else None
    cols = [c for c in cols if c != week_c]
    col_style = {_col_index(k): v for k, v in (view.get("col_style") or {}).items()}
    rows = [r for r in range(1, max_r + 1) if not (r in ws.row_dimensions and ws.row_dimensions[r].hidden)]
    widths = {c: max(2, min(MAX_COL_CHARS, int(round(excel_w.get(c, DEFAULT_EXCEL_WIDTH))))) for c in cols}
    fixed_w = view.get("widths") or {}
    if fixed_w:
        by_ix = {_col_index(k): v for k, v in fixed_w.items() if k != "*"}
        widths = {c: by_ix.get(c, fixed_w.get("*", widths[c])) for c in cols}
    formats = {_col_index(k): v for k, v in (view.get("formats") or {}).items()}
    header_n = view.get("header_rows") or 0
    recolor = view.get("recolor") or None

    # merges: every covered cell points at its anchor
    anchor = {}
    for mr in ws.merged_cells.ranges:
        for r in range(mr.min_row, min(mr.max_row, max_r) + 1):
            for c in range(mr.min_col, min(mr.max_col, max_c) + 1):
                if (r, c) != (mr.min_row, mr.min_col):
                    anchor[(r, c)] = (mr.min_row, mr.min_col)

    cf, skipped = _cf_index(ws, theme, max_r, max_c)
    static_cache = {}

    def static(cell):
        sid = cell.style_id if cell.has_style else 0
        got = static_cache.get(sid)
        if got is None:
            fill, font, al = cell.fill, cell.font, cell.alignment
            bg = resolve_color(fill.fgColor, theme) if fill is not None and fill.fill_type == "solid" else None
            fg = resolve_color(font.color, theme) if font is not None and font.color is not None else None
            got = (bg, fg, bool(font.b) if font is not None else False,
                   (al.horizontal if al is not None else None), cell.number_format or "General")
            static_cache[sid] = got
        return got

    styles, style_ix, out_rows = [], {}, []
    row_pos = {r: i for i, r in enumerate(rows)}
    cells, vcells = ws._cells, wsv._cells
    date_c = _col_index(view["date_col"]) if view.get("date_col") else None
    today_row, best_past = None, None
    for r in rows:
        line = []
        for c in cols:
            src = anchor.get((r, c), (r, c))
            cell = cells.get(src)
            vcell = vcells.get(src)
            value = vcell.value if vcell is not None else None
            if cell is not None:
                bg, fg, bold, halign, numfmt = static(cell)
            else:
                bg, fg, bold, halign, numfmt = None, None, False, None, "General"
            if c in formats and r > header_n:
                numfmt = formats[c]
            forced = col_style.get(c)
            if forced and r > (view.get("header_rows") or 0):
                bg, fg = forced
            rules = None if forced else cf.get(src)
            if rules:
                set_bg = set_fg = set_bold = False
                for _prio, op, operands, rbg, rfg, rbold, stop in sorted(rules, key=lambda x: x[0]):
                    if not cell_is(value, op, operands):
                        continue
                    if rbg is not None and not set_bg:
                        bg, set_bg = rbg, True
                    if rfg is not None and not set_fg:
                        fg, set_fg = rfg, True
                    if rbold is not None and not set_bold:
                        bold, set_bold = bool(rbold), True
                    if stop:
                        break
            bg, fg = _recolor(bg, fg, recolor)
            text = format_value(value, numfmt, widths[c]) if src == (r, c) else ""
            if week_c and c == date_c and src == (r, c) and isinstance(value, (dt.datetime, dt.date)):
                if view.get("date_label") == "mm.dd":
                    text = "%02d.%02d" % (value.month, value.day)
                else:
                    wk = vcells.get(anchor.get((r, week_c), (r, week_c)))
                    text = week_date(wk.value if wk is not None else None, value)
            up = 0
            if src != (r, c) and src[1] == c and src[0] in row_pos:
                up = row_pos[r] - row_pos[src[0]]             # vertical merge: text lives `up` rows above
            numeric = isinstance(value, (int, float, dt.datetime, dt.date, dt.time)) and not isinstance(value, bool)
            align = {"center": "c", "centerContinuous": "c", "right": "r", "left": "l"}.get(
                halign or "", "r" if numeric else "l")
            st = finish_style(bg, fg, bold, align)
            if st[4]:
                text = ""
            key = st[:4]
            ix = style_ix.get(key)
            if ix is None:
                ix = style_ix[key] = len(styles)
                styles.append(list(key))
            line.append([text, ix, up] if up else [text, ix])
            if c == date_c and src == (r, c) and isinstance(value, (dt.datetime, dt.date)):
                d = value.date() if isinstance(value, dt.datetime) else value
                if d == today:
                    today_row = len(out_rows)
                elif d < today and (best_past is None or d > best_past[0]):
                    best_past = (d, len(out_rows))
        out_rows.append(line)
    if today_row is None and best_past is not None:
        today_row = best_past[1]
    if today_row is None and week_label:
        # weekly sheets have no date column: "today" is the row whose leading
        # cell is this week's 周 label (anchored here, not after the fact, so
        # the row gets a live recipe like the daily sheets do)
        for i, r in enumerate(rows):
            if r > header_n and any(c[0].strip() == week_label for c in out_rows[i][:2]):
                today_row = i
                break
    # What it takes to repaint today's row from a fresher source than the
    # saved workbook (see overlay()): per column, the number format, the
    # static style and the conditional rules of that one cell.
    live = None
    if today_row is not None:
        rr = rows[today_row]
        recipe = {}
        for c in cols:
            if (rr, c) in anchor or c in col_style:
                continue
            cell = cells.get((rr, c))
            bg, fg, bold, halign, numfmt = static(cell) if cell is not None else (None, None, False, None, "General")
            numfmt = formats.get(c, numfmt)
            recipe[get_column_letter(c)] = {
                "fmt": numfmt, "base": [bg, fg, bold, halign],
                "rules": [list(x) for x in sorted(cf.get((rr, c), []), key=lambda x: x[0])]}
        live = {"row": today_row, "excel_row": rr, "cells": recipe, "recolor": recolor}
    tab = None
    try:
        tab = resolve_color(ws.sheet_properties.tabColor, theme)
    except Exception:
        pass
    frozen = sum(1 for c in cols if c <= (view.get("frozen_cols") or 0))
    header = sum(1 for r in rows if r <= (view.get("header_rows") or 0))
    return {"name": ws.title, "tab": ("#" + tab) if tab else None, "header_rows": header,
            "frozen_cols": frozen, "today_row": today_row,
            "cols": [get_column_letter(c) for c in cols], "row_nums": rows,
            "widths": [widths[c] for c in cols], "styles": styles, "rows": out_rows,
            "skipped_rules": skipped, "live": live}


def paint(value, fmt: str, base: list, rules: list, width: int, recolor: dict | None = None):
    """(text, [bg, fg, bold, align]) for one value: number format, static
    style, then the cell's cellIs rules. The same steps build_sheet applies,
    for a value that did not come from the workbook."""
    bg, fg, bold, halign = base
    set_bg = set_fg = set_bold = False
    for _prio, op, operands, rbg, rfg, rbold, stop in rules:
        if not cell_is(value, op, operands):
            continue
        if rbg is not None and not set_bg:
            bg, set_bg = rbg, True
        if rfg is not None and not set_fg:
            fg, set_fg = rfg, True
        if rbold is not None and not set_bold:
            bold, set_bold = bool(rbold), True
        if stop:
            break
    bg, fg = _recolor(bg, fg, recolor)
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    align = {"center": "c", "centerContinuous": "c", "right": "r", "left": "l"}.get(
        halign or "", "r" if numeric else "l")
    st = finish_style(bg, fg, bold, align)
    return ("" if st[4] else format_value(value, fmt, width)), list(st[:4])


def overlay(sheet: dict, values: dict, zeros: bool = False) -> int:
    """Repaint today's row of `sheet` in place with fresher `values`
    ({column letter: number or text}), re-running each cell's format and
    colour rules. Columns the sheet has no recipe for are ignored. Returns
    cells changed. `zeros`: the values are actual writes, so a 0 (or a
    cleared "") is painted even where the workbook shows a blank; without it
    a 0 is a derived total and a blank cell stays blank."""
    live = sheet.get("live")
    if not live or not values:
        return 0
    row = sheet["rows"][live["row"]]
    index = {tuple(s): i for i, s in enumerate(sheet["styles"])}
    changed = 0
    for col, value in values.items():
        recipe = live["cells"].get(col)
        if recipe is None or col not in sheet["cols"] or value is None:
            continue
        ci = sheet["cols"].index(col)
        if len(row[ci]) > 2:
            continue                    # inside a vertical merge: its text lives rows above
        if not zeros and value == 0 and row[ci][0] == "":
            continue                    # the workbook shows this cell blank; a ledger 0 is not news
        text, style = paint(value, recipe["fmt"], recipe["base"], recipe["rules"], sheet["widths"][ci],
                            live.get("recolor"))
        key = tuple(style)
        ix = index.get(key)
        if ix is None:
            ix = index[key] = len(sheet["styles"])
            sheet["styles"].append(style)
        if row[ci][0] != text or row[ci][1] != ix:
            row[ci] = [text, ix]
            changed += 1
    return changed


def _week_label(wv, today: dt.date, sheet: str = "0分") -> str:
    """This week's 周 label ("11.3") as the daily sheet shows it: column A of
    today's row, read up through the weekly merge."""
    if sheet not in wv.sheetnames:
        return ""
    cells = wv[sheet]._cells
    max_r, _ = _extent(wv[sheet])
    for r in range(2, max_r + 1):
        cell = cells.get((r, 2))
        v = cell.value if cell is not None else None
        if isinstance(v, dt.datetime):
            v = v.date()
        if v == today:
            for rr in range(r, max(1, r - 7), -1):
                wk = cells.get((rr, 1))
                if wk is not None and wk.value is not None:
                    return format_value(wk.value, "General")
            return ""
    return ""


def build(src: Path = SRC, today: dt.date | None = None) -> dict:
    """Parse the workbook (a private copy, so a save in progress is never read
    in place) into the snapshot dict."""
    import openpyxl
    today = today or dt.date.today()
    # stat BEFORE the copy: a save landing mid-build must not stamp the old
    # content with the new mtime (readers overlay ledger writes newer than this)
    src_mtime = int(src.stat().st_mtime)
    with tempfile.TemporaryDirectory(prefix="neon-grid-") as tmp:
        copy = Path(tmp) / "neon.xlsx"
        shutil.copyfile(src, copy)
        wb = openpyxl.load_workbook(copy)
        wv = openpyxl.load_workbook(copy, data_only=True)
        theme = _theme_palette(wb)
        sheets = []
        label = _week_label(wv, today)
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or ws.title not in wv.sheetnames or ws.title in SKIP_SHEETS:
                continue
            sheets.append(build_sheet(ws, wv[ws.title], theme, today, label))
    body = json.dumps(sheets, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # the day is part of the hash: at midnight "today's row" moves even when no
    # cell did, and an unchanged hash would leave yesterday's file in place
    day = today.isoformat()
    return {"version": VERSION, "hash": hashlib.sha1((day + body).encode("utf-8")).hexdigest(),
            "built": int(time.time()), "src_mtime": src_mtime, "day": day, "sheets": sheets}


def write_snapshot(src: Path = SRC, out: Path = OUT, force: bool = False) -> str:
    """Build and write atomically. The file is left untouched when the content
    hash is unchanged (Excel's forced save every ~2 minutes moves the mtime
    without changing a cell), apart from a small sidecar recording the check.
    Returns 'written' | 'unchanged'."""
    snap = build(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = out.with_suffix(".meta.json")
    old_hash = None
    if not force:
        try:
            old_hash = json.loads(meta.read_text()).get("hash")
        except (OSError, ValueError):
            pass
    result = "unchanged"
    if force or old_hash != snap["hash"] or not out.exists():
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, out)
        result = "written"
    tmpm = meta.with_suffix(".tmp")
    tmpm.write_text(json.dumps({"hash": snap["hash"], "built": snap["built"], "src_mtime": snap["src_mtime"],
                                "version": VERSION}))
    os.replace(tmpm, meta)
    return result


def emit(src: Path = SRC, out: Path = OUT) -> dict:
    """The snapshot another Mac should show, built HERE (Ix): the saved
    workbook's grid with each sheet's live row repainted from the Neon book
    (seed + ledger writes, recomputed). 2026-11-22: a janus running on
    Straylight read Straylight's OneDrive copy, six weeks stale.

    Read-only on this host: reuses `out` when it matches the workbook's save
    and today, builds in memory otherwise, never writes (the januses running
    here own that file). Adds "host", "emitted" (epoch) and "book"
    ({sheet: "HH:MM:SS" of the newest write painted}; absent when the book
    could not answer)."""
    import socket
    src_mtime = int(os.path.getmtime(src))
    snap = load_snapshot(out)
    if snap is None or snap.get("src_mtime") != src_mtime or snap.get("day") != dt.date.today().isoformat():
        snap = build(src)
    book: dict = {}
    try:
        import neon_book_reads
        os.environ.setdefault("NEON_TEST_DB_OK", "1")
        for sh in snap["sheets"]:
            live = sh.get("live") or {}
            if not live.get("excel_row"):
                continue
            values, through = neon_book_reads.row(sh["name"], live["excel_row"], list(sh["cols"]))
            if values is None:
                continue
            row = sh["rows"][live["row"]]
            paint_ = {c: v for c, v in values.items()
                      if c in sh["cols"] and not (v == "" and row[sh["cols"].index(c)][0] == "")}
            if paint_:
                overlay(sh, paint_, zeros=True)
            book[sh["name"]] = through or ""
    except Exception:                                # the saved grid alone is still worth sending
        pass
    snap.update(host=socket.gethostname().split(".")[0].lower(), emitted=int(time.time()), book=book)
    return snap


def load_snapshot(out: Path = OUT) -> dict | None:
    try:
        snap = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) and snap.get("version") == VERSION else None


def to_html(sheet: dict) -> str:
    """One sheet as an HTML table on white, for checking against Excel."""
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
    out = ["<meta charset=utf-8><style>body{background:#fff}table{border-collapse:collapse;font:12px Menlo,monospace}"
           "td{padding:1px 4px;white-space:nowrap;border:1px solid #eee}</style><table>"]
    for row in sheet["rows"]:
        out.append("<tr>")
        for cell in row:
            bg, fg, bold, align = sheet["styles"][cell[1]]
            css = "text-align:%s;" % {"l": "left", "r": "right", "c": "center"}[align]
            if bg:
                css += "background:%s;" % bg
            if fg:
                css += "color:%s;" % fg
            if bold:
                css += "font-weight:bold;"
            out.append('<td style="%s">%s</td>' % (css, esc(cell[0])))
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--src", type=Path, default=SRC)
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--force", action="store_true")
    e = sub.add_parser("emit")
    e.add_argument("--src", type=Path, default=SRC)
    e.add_argument("--out", type=Path, default=OUT)
    h = sub.add_parser("html")
    h.add_argument("sheet")
    h.add_argument("--src", type=Path, default=SRC)
    h.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        try:
            print(write_snapshot(args.src, args.out, args.force))
        except Exception as e:                      # janus shows this line in the tab
            print("error: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 1
        return 0
    if args.cmd == "emit":
        try:
            sys.stdout.write(json.dumps(emit(args.src, args.out), ensure_ascii=False, separators=(",", ":")))
        except Exception as e:
            print("error: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 1
        return 0
    snap = build(args.src)
    sheet = next((s for s in snap["sheets"] if s["name"] == args.sheet), None)
    if sheet is None:
        print("no sheet %r; have %s" % (args.sheet, [s["name"] for s in snap["sheets"]]), file=sys.stderr)
        return 1
    page = to_html(sheet)
    if args.out:
        args.out.write_text(page, encoding="utf-8")
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
