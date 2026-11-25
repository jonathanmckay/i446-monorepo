"""Workbook calculation engine for Neon (Neon off Excel, 2026-11-22).

`formula.py` recomputes one hcbi composite for the rule engine. This is the
whole workbook: every formula Neon uses, evaluated from the cells alone, so
Excel is no longer needed to know what a cell holds.

  Book(cells)            cells: {(sheet, col_index, row): content}; a content
                         starting with "=" is a formula, anything else a value
                         (float, str, bool, None, or an Err).
  book.value(s, c, r)    the cell's value, memoised; a write invalidates.
  book.set(s, c, r, v)   replace a cell's content.

Covered (what the workbook uses, measured 2026-11-22 over 18,742 formulas):
+ - * / ^ & % comparisons; cell, range, whole-column and whole-row refs with
an optional sheet; SUM AVERAGE MAX MIN COUNT COUNTA COUNTIF COUNTIFS SUMIF
SUMPRODUCT MMULT IF AND OR NOT ISBLANK ISNUMBER ISTEXT ROW COLUMN INDIRECT
TODAY ABS ROUND INT IFERROR; element-wise array arithmetic; array formulas.

Excel semantics kept: errors are values and propagate (#DIV/0!, #VALUE!,
#REF!, #N/A); a blank is 0 in arithmetic, "" in text and FALSE in logic;
aggregates over a range skip text, booleans and blanks but propagate errors;
dates are serial numbers (1900 system). A formula is parsed once per distinct
text and cached.

`tools/neon-ledger/calc_check.py` is the gate: it evaluates every formula in
the saved workbook and compares with the value Excel cached for it.

Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

MAX_ROW, MAX_COL = 1048576, 16384
_EPOCH = _dt.datetime(1899, 12, 30)


class Err:
    """An Excel error value (#DIV/0!, #VALUE!, #REF!, #N/A, #NAME?, #NUM!)."""
    __slots__ = ("code",)

    def __init__(self, code: str):
        self.code = code

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, Err) and other.code == self.code

    def __hash__(self):
        return hash(("Err", self.code))


DIV0, VALUE, REF, NA, NAME, NUM = (Err("#DIV/0!"), Err("#VALUE!"), Err("#REF!"),
                                   Err("#N/A"), Err("#NAME?"), Err("#NUM!"))


class CalcError(Exception):
    """The formula cannot be parsed or uses something this engine lacks."""


class Arr:
    """A 2-D block of values. `ref` = (sheet, c1, r1) when it came from a
    range (aggregates then skip text/blank, and ROW() can see where it is)."""
    __slots__ = ("rows", "ref")

    def __init__(self, rows: List[list], ref: Optional[tuple] = None):
        self.rows, self.ref = rows, ref

    @property
    def shape(self) -> Tuple[int, int]:
        return len(self.rows), (len(self.rows[0]) if self.rows else 0)

    def flat(self):
        for r in self.rows:
            for v in r:
                yield v


def col_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def serial(v) -> float:
    """A date/datetime/time as an Excel serial number."""
    if isinstance(v, _dt.datetime):
        d = v - _EPOCH
        return d.days + d.seconds / 86400.0
    if isinstance(v, _dt.date):
        return float((_dt.datetime(v.year, v.month, v.day) - _EPOCH).days)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second) / 86400.0
    if isinstance(v, _dt.timedelta):
        return v.total_seconds() / 86400.0
    return v


# ── parsing ──────────────────────────────────────────────────────────────────

_SHEET = r"(?:(?:'(?:[^']|'')+'|[^\s!'()+\-*/^&=<>,:;{}%\"]+)!)"
_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<err>\#(?:REF!|DIV/0!|VALUE!|N/A|NAME\?|NUM!|NULL!))
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>%(sheet)s?(?:%(cell)s(?::%(cell)s)?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|\$?\d+:\$?\d+|\#REF!))
  | (?P<func>[A-Za-z_][A-Za-z0-9_.]*(?=\s*\())
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<bool>TRUE|FALSE|true|false)
  | (?P<op><>|<=|>=|[-+*/^&=<>(),:%%{};])
""" % {"sheet": _SHEET, "cell": _CELL}, re.X)

_A1 = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_COLS = re.compile(r"^\$?([A-Za-z]{1,3}):\$?([A-Za-z]{1,3})$")
_ROWS = re.compile(r"^\$?(\d+):\$?(\d+)$")


def parse_ref(text: str) -> tuple:
    """'hcbi!E4:G9' → ("hcbi", 5, 4, 7, 9); sheet None when unqualified.
    Whole columns/rows span to the sheet limits. '#REF!' → CalcError-free
    marker ("#REF!",)."""
    sheet = None
    if "!" in text and not text.endswith("#REF!") or text.count("!") > 1:
        i = text.rindex("!") if not text.endswith("#REF!") else text[:-5].rindex("!")
        sheet, text = text[:i], text[i + 1:]
        if sheet.startswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
    if text == "#REF!":
        return ("#REF!",)
    m = _COLS.match(text)
    if m:
        a, b = sorted((col_index(m.group(1).upper()), col_index(m.group(2).upper())))
        return (sheet, a, 1, b, MAX_ROW)
    m = _ROWS.match(text)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return (sheet, 1, a, MAX_COL, b)
    parts = text.split(":")
    m1 = _A1.match(parts[0])
    m2 = _A1.match(parts[-1])
    if not m1 or not m2:
        raise CalcError(f"bad ref {text!r}")
    c1, r1 = col_index(m1.group(1).upper()), int(m1.group(2))
    c2, r2 = col_index(m2.group(1).upper()), int(m2.group(2))
    return (sheet, min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2))


def tokenize(src: str) -> List[Tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise CalcError(f"bad token at {src[pos:pos + 16]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(kind)))
    return out


_PARSED: Dict[str, tuple] = {}


def parse(formula: str) -> tuple:
    """AST for a formula text (leading '=' optional), cached by text."""
    node = _PARSED.get(formula)
    if node is None:
        node = _PARSED[formula] = _Parser(tokenize(formula.strip().lstrip("=")), formula).run()
    return node


class _Parser:
    def __init__(self, toks, src):
        self.t, self.i, self.src = toks, 0, src

    def peek(self, *want):
        if self.i < len(self.t) and (self.t[self.i][0] == "op") and self.t[self.i][1] in want:
            return self.t[self.i][1]
        return None

    def take(self, want):
        if self.peek(want) is None:
            raise CalcError(f"expected {want!r} in {self.src[:60]!r}")
        self.i += 1

    def run(self):
        node = self.compare()
        if self.i != len(self.t):
            raise CalcError(f"trailing {self.t[self.i][1]!r} in {self.src[:60]!r}")
        return node

    def compare(self):
        a = self.concat()
        while True:
            op = self.peek("=", "<>", "<", ">", "<=", ">=")
            if op is None:
                return a
            self.i += 1
            a = ("bin", op, a, self.concat())

    def concat(self):
        a = self.additive()
        while self.peek("&"):
            self.i += 1
            a = ("bin", "&", a, self.additive())
        return a

    def additive(self):
        a = self.term()
        while True:
            op = self.peek("+", "-")
            if op is None:
                return a
            self.i += 1
            a = ("bin", op, a, self.term())

    def term(self):
        a = self.power()
        while True:
            op = self.peek("*", "/")
            if op is None:
                return a
            self.i += 1
            a = ("bin", op, a, self.power())

    def power(self):
        a = self.unary()
        while self.peek("^"):
            self.i += 1
            a = ("bin", "^", a, self.unary())
        return a

    def unary(self):
        op = self.peek("-", "+")
        if op:
            self.i += 1
            return ("neg", self.unary()) if op == "-" else ("pos", self.unary())
        return self.postfix()

    def postfix(self):
        a = self.primary()
        while self.peek("%"):
            self.i += 1
            a = ("bin", "/", a, ("val", 100.0))
        return a

    def primary(self):
        if self.i >= len(self.t):
            raise CalcError(f"unexpected end in {self.src[:60]!r}")
        kind, text = self.t[self.i]
        self.i += 1
        if kind == "num":
            return ("val", float(text))
        if kind == "str":
            return ("val", text[1:-1].replace('""', '"'))
        if kind == "bool":
            return ("val", text.upper() == "TRUE")
        if kind == "err":
            return ("val", Err(text))
        if kind == "ref":
            r = parse_ref(text)
            return ("val", REF) if r[0] == "#REF!" else ("ref",) + r
        if kind == "func":
            name = text.upper()
            self.take("(")
            args = []
            if self.peek(")"):
                self.i += 1
                return ("call", name, args)
            while True:
                if self.peek(",") or self.peek(")"):
                    args.append(("val", None))          # an omitted argument
                else:
                    args.append(self.compare())
                if self.peek(","):
                    self.i += 1
                    if self.peek(")"):
                        args.append(("val", None))
                        self.i += 1
                        return ("call", name, args)
                    continue
                self.take(")")
                return ("call", name, args)
        if kind == "op" and text == "(":
            node = self.compare()
            self.take(")")
            return node
        if kind == "op" and text == "{":
            rows, row = [], []
            while True:
                k, t = self.t[self.i]
                self.i += 1
                sign = 1.0
                if k == "op" and t in "+-":
                    sign = -1.0 if t == "-" else 1.0
                    k, t = self.t[self.i]
                    self.i += 1
                row.append(sign * float(t) if k == "num" else t[1:-1] if k == "str"
                           else (t.upper() == "TRUE") if k == "bool" else Err(t))
                sep = self.t[self.i][1]
                self.i += 1
                if sep == ";":
                    rows.append(row)
                    row = []
                elif sep == "}":
                    rows.append(row)
                    return ("val", Arr(rows))
        raise CalcError(f"unexpected {text!r} in {self.src[:60]!r}")


# ── values ───────────────────────────────────────────────────────────────────

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_num(v):
    """Arithmetic coercion; returns a float or an Err."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Err):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            if s.endswith("%"):
                try:
                    return float(s[:-1]) / 100.0
                except ValueError:
                    pass
            return VALUE
    return VALUE


def to_bool(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, Err):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        u = v.strip().upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
    return VALUE


def to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
    return str(v)


def _rank(v) -> int:
    return 0 if _is_num(v) else 1 if isinstance(v, str) else 2


def _compare(op: str, a, b):
    for v in (a, b):
        if isinstance(v, Err):
            return v
    if a is None:
        a = "" if isinstance(b, str) else False if isinstance(b, bool) else 0.0
    if b is None:
        b = "" if isinstance(a, str) else False if isinstance(a, bool) else 0.0
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        c = -1 if ra < rb else 1               # number < text < boolean
    else:
        x, y = (a.lower(), b.lower()) if ra == 1 else (a, b)
        c = (x > y) - (x < y)
    return {"=": c == 0, "<>": c != 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]


def _arith(op: str, a, b):
    if op == "&":
        for v in (a, b):
            if isinstance(v, Err):
                return v
        return to_text(a) + to_text(b)
    if op in ("=", "<>", "<", ">", "<=", ">="):
        return _compare(op, a, b)
    x = to_num(a)
    if isinstance(x, Err):
        return x
    y = to_num(b)
    if isinstance(y, Err):
        return y
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if op == "/":
        return DIV0 if y == 0 else x / y
    if op == "^":
        try:
            r = x ** y
        except (ZeroDivisionError, OverflowError):
            return NUM
        return NUM if isinstance(r, complex) else r
    raise CalcError(f"operator {op}")


def _broadcast(fn, a, b):
    """fn over two operands, element-wise when either is an Arr."""
    if not isinstance(a, Arr) and not isinstance(b, Arr):
        return fn(a, b)
    ar, ac = a.shape if isinstance(a, Arr) else (1, 1)
    br, bc = b.shape if isinstance(b, Arr) else (1, 1)
    rows, cols = max(ar, br), max(ac, bc)

    def at(v, shape, i, j):
        if not isinstance(v, Arr):
            return v
        r, c = shape
        if (r != 1 and i >= r) or (c != 1 and j >= c):
            return NA
        return v.rows[i if r != 1 else 0][j if c != 1 else 0]
    return Arr([[fn(at(a, (ar, ac), i, j), at(b, (br, bc), i, j)) for j in range(cols)] for i in range(rows)])


def _map(fn, a):
    if isinstance(a, Arr):
        return Arr([[fn(v) for v in r] for r in a.rows])
    return fn(a)


def _criteria(crit) -> Callable[[object], bool]:
    """COUNTIF/SUMIF criterion → predicate (">5", "<>", "x*", 3, "=a")."""
    if isinstance(crit, Arr):
        crit = crit.rows[0][0]
    op, target = "=", crit
    if isinstance(crit, str):
        m = re.match(r"^(<>|<=|>=|=|<|>)(.*)$", crit, re.S)
        if m:
            op, target = m.group(1), m.group(2)
        t = to_num(target) if target != "" else None
        if isinstance(target, str) and target.upper() in ("TRUE", "FALSE"):
            target = target.upper() == "TRUE"
        elif _is_num(t):
            target = t
    if isinstance(target, str):
        if target == "":
            if op == "=":
                return lambda v: v is None or v == ""
            if op == "<>":
                return lambda v: not (v is None or v == "")
        pat = None
        if any(ch in target for ch in "*?"):
            pat = re.compile("^" + "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
                                           for ch in target) + "$", re.I | re.S)
        low = target.lower()

        def text_pred(v):
            if not isinstance(v, str):
                return op == "<>"
            hit = bool(pat.match(v)) if pat else v.lower() == low
            if op == "=":
                return hit
            if op == "<>":
                return not hit
            c = (v.lower() > low) - (v.lower() < low)
            return {"<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]
        return text_pred
    if isinstance(target, bool):
        return lambda v: (isinstance(v, bool) and v == target) == (op != "<>")
    tnum = float(target) if target is not None else 0.0

    def num_pred(v):
        if isinstance(v, str):
            x = to_num(v) if v.strip() else None
            if not _is_num(x):
                return op == "<>"
            v = x
        if not _is_num(v):
            return op == "<>"
        return {"=": v == tnum, "<>": v != tnum, "<": v < tnum, ">": v > tnum,
                "<=": v <= tnum, ">=": v >= tnum}[op]
    return num_pred


# ── the book ─────────────────────────────────────────────────────────────────

class Book:
    """Cells + lazy memoised evaluation. Not thread-safe."""

    def __init__(self, cells: Optional[dict] = None, today: Optional[_dt.date] = None,
                 arrays: Optional[dict] = None):
        self.cells: dict = cells if cells is not None else {}
        # {(sheet, col, row): (anchor_col, anchor_row)} for cells inside a
        # multi-cell array formula: their value is an element of the anchor's.
        self.arrays: dict = arrays or {}
        self.today = today
        # {(sheet, col, row)} formatted as a date in the workbook, or None when
        # this book was not loaded from one that says (an older seed).
        self.dates: Optional[set] = None
        self._memo: dict = {}
        self._arr_memo: dict = {}
        self._stack: set = set()
        self._extent: dict = {}
        self._index()
        if sys.getrecursionlimit() < 20000:
            sys.setrecursionlimit(20000)      # running totals chain a row per day

    def _index(self) -> None:
        self._extent = {}
        for (sheet, col, row) in self.cells:
            e = self._extent.get(sheet)
            if e is None:
                self._extent[sheet] = [col, row]
            else:
                if col > e[0]:
                    e[0] = col
                if row > e[1]:
                    e[1] = row

    def sheets(self) -> list:
        return list(self._extent)

    def set(self, sheet: str, col: int, row: int, content) -> None:
        if content is None or content == "":
            self.cells.pop((sheet, col, row), None)
        else:
            self.cells[(sheet, col, row)] = content
            e = self._extent.setdefault(sheet, [col, row])
            e[0], e[1] = max(e[0], col), max(e[1], row)
        self._memo.clear()
        self._arr_memo.clear()

    def content(self, sheet: str, col: int, row: int):
        return self.cells.get((sheet, col, row))

    def is_formula(self, sheet: str, col: int, row: int) -> bool:
        c = self.cells.get((sheet, col, row))
        return isinstance(c, str) and c.startswith("=") and len(c) > 1

    def value(self, sheet: str, col: int, row: int):
        key = (sheet, col, row)
        if key in self._memo:
            return self._memo[key]
        anchor = self.arrays.get(key)
        if anchor is not None and anchor != (col, row):
            whole = self._array_value(sheet, anchor[0], anchor[1])
            if isinstance(whole, Arr):
                i, j = row - anchor[1], col - anchor[0]
                nr, nc = whole.shape
                v = whole.rows[i if nr != 1 else 0][j if nc != 1 else 0] if (i < nr or nr == 1) and (j < nc or nc == 1) else NA
            else:
                v = whole
            self._memo[key] = v
            return v
        c = self.cells.get(key)
        if isinstance(c, str) and c.startswith("=") and len(c) > 1:
            if key in self._stack:
                return Err("#CIRC!")
            self._stack.add(key)
            try:
                if anchor is not None:
                    v = self._array_value(sheet, col, row)
                    if isinstance(v, Arr):
                        v = v.rows[0][0] if v.rows and v.rows[0] else None
                else:
                    v = self._scalar(self._eval(parse(c), sheet, col, row, False), col, row)
            finally:
                self._stack.discard(key)
            if v is None or (isinstance(v, float) and v == 0):
                v = 0.0                          # a formula over a blank is 0; no -0.0
        else:
            v = serial(c)
        self._memo[key] = v
        return v

    def _array_value(self, sheet, col, row):
        key = (sheet, col, row)
        if key not in self._arr_memo:
            self._arr_memo[key] = self._eval(parse(self.cells[key]), sheet, col, row, True)
        return self._arr_memo[key]

    def _scalar(self, v, col, row):
        """Implicit intersection for a non-array formula's final value."""
        if not isinstance(v, Arr):
            return v
        nr, nc = v.shape
        if nr == 1 and nc == 1:
            return v.rows[0][0]
        if v.ref is not None:
            _, c1, r1 = v.ref
            if nc == 1 and r1 <= row < r1 + nr:
                return v.rows[row - r1][0]
            if nr == 1 and c1 <= col < c1 + nc:
                return v.rows[0][col - c1]
            return VALUE
        return v.rows[0][0]

    def _range(self, sheet, c1, r1, c2, r2) -> Arr:
        e = self._extent.get(sheet)
        if e is None:
            return Arr([[None]], (sheet, c1, r1)) if sheet in self._known_sheets() else Arr([[REF]])
        c2, r2 = min(c2, max(e[0], c1)), min(r2, max(e[1], r1))
        val = self.value
        return Arr([[val(sheet, c, r) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)], (sheet, c1, r1))

    def _known_sheets(self):
        return self._extent

    # -- evaluation -----------------------------------------------------------
    def _eval(self, node, sheet, col, row, array):
        kind = node[0]
        if kind == "val":
            return node[1]
        if kind == "ref":
            _, sh, c1, r1, c2, r2 = node
            sh = sh or sheet
            if sh not in self._extent:
                return REF
            if c1 == c2 and r1 == r2:
                return self.value(sh, c1, r1)
            return self._range(sh, c1, r1, c2, r2)
        if kind == "bin":
            op = node[1]
            a = self._eval(node[2], sheet, col, row, array)
            b = self._eval(node[3], sheet, col, row, array)
            if not array:
                a, b = self._scalar(a, col, row), self._scalar(b, col, row)
            return _broadcast(lambda x, y: _arith(op, x, y), a, b)
        if kind == "neg" or kind == "pos":
            a = self._eval(node[1], sheet, col, row, array)
            if not array:
                a = self._scalar(a, col, row)
            sign = -1.0 if kind == "neg" else 1.0

            def f(x):
                n = to_num(x)
                return n if isinstance(n, Err) else sign * n
            return _map(f, a)
        if kind == "call":
            return self._call(node[1], node[2], sheet, col, row, array)
        raise CalcError(f"node {kind}")

    def _nums(self, vals, strict_args=True):
        """Numbers an aggregate sees: range cells that are numbers; direct
        scalar args coerced. Returns (list, Err|None)."""
        out = []
        for v in vals:
            if isinstance(v, Arr):
                for x in v.flat():
                    if isinstance(x, Err):
                        return out, x
                    if _is_num(x):
                        out.append(float(x))
            elif isinstance(v, Err):
                return out, v
            elif v is not None:
                n = to_num(v)
                if isinstance(n, Err):
                    return out, n
                out.append(n)
        return out, None

    def _call(self, name, args, sheet, col, row, array):
        ev = lambda n, arr=True: self._eval(n, sheet, col, row, arr)   # noqa: E731

        def evr(n):
            """An aggregate's argument: a single-cell ref is still a
            reference (its text/blank is skipped, not coerced)."""
            v = self._eval(n, sheet, col, row, True)
            if n[0] == "ref" and not isinstance(v, Arr) and not (isinstance(v, Err) and (n[1] or sheet) not in self._extent):
                return Arr([[v]], (n[1] or sheet, n[2], n[3]))
            return v
        if name == "IF":
            cond = ev(args[0], array)
            if not array:
                cond = self._scalar(cond, col, row)
            if isinstance(cond, Arr):
                then = ev(args[1], True) if len(args) > 1 else True
                other = ev(args[2], True) if len(args) > 2 else False

                def pick(c, t, o):
                    b = to_bool(c)
                    return b if isinstance(b, Err) else (t if b else o)
                return _broadcast(lambda c, to: pick(c, to[0], to[1]), cond,
                                  _broadcast(lambda t, o: (t, o), then, other))
            b = to_bool(cond)
            if isinstance(b, Err):
                return b
            if b:
                v = ev(args[1], array) if len(args) > 1 else True
            else:
                v = ev(args[2], array) if len(args) > 2 else False
            return 0.0 if v is None else v
        if name == "IFERROR":
            v = ev(args[0], array)
            if not array:
                v = self._scalar(v, col, row)
            return ev(args[1], array) if isinstance(v, Err) else v
        if name == "ROW" or name == "COLUMN":
            if not args:
                return float(row if name == "ROW" else col)
            n = args[0]
            if n[0] == "ref":
                _, _, c1, r1, c2, r2 = n
                if name == "ROW":
                    return float(r1) if r1 == r2 else Arr([[float(r)] for r in range(r1, r2 + 1)])
                return float(c1) if c1 == c2 else Arr([[float(c) for c in range(c1, c2 + 1)]])
            v = ev(n)
            if isinstance(v, Arr) and v.ref:
                return float(v.ref[2] if name == "ROW" else v.ref[1])
            return VALUE
        if name == "INDIRECT":
            t = self._scalar(ev(args[0], array), col, row)
            if isinstance(t, Err):
                return t
            try:
                r = parse_ref(to_text(t).strip())
            except CalcError:
                return REF
            if r[0] == "#REF!":
                return REF
            sh = r[0] or sheet
            if sh not in self._extent:
                return REF
            if r[1] == r[3] and r[2] == r[4]:
                return Arr([[self.value(sh, r[1], r[2])]], (sh, r[1], r[2]))
            return self._range(sh, r[1], r[2], r[3], r[4])
        if name == "TODAY":
            return serial(self.today or _dt.date.today())
        if name == "ISBLANK":
            return _map(lambda x: x is None, ev(args[0]) if array else self._scalar(ev(args[0]), col, row))
        if name == "ISNUMBER":
            return _map(_is_num, ev(args[0]) if array else self._scalar(ev(args[0]), col, row))
        if name == "ISTEXT":
            return _map(lambda x: isinstance(x, str), ev(args[0]) if array else self._scalar(ev(args[0]), col, row))
        if name == "NOT":
            def neg(x):
                b = to_bool(x)
                return b if isinstance(b, Err) else not b
            return _map(neg, ev(args[0]) if array else self._scalar(ev(args[0]), col, row))
        if name in ("AND", "OR"):
            seen, hit = False, name == "AND"
            for n in args:
                v = evr(n)
                for x in (v.flat() if isinstance(v, Arr) else [v]):
                    if isinstance(v, Arr) and (x is None or isinstance(x, str)):
                        continue                 # ranges ignore blanks and text
                    b = to_bool(x)
                    if isinstance(b, Err):
                        return b
                    seen = True
                    hit = (hit and b) if name == "AND" else (hit or b)
            return hit if seen else VALUE
        if name in ("SUM", "AVERAGE", "MAX", "MIN", "COUNT"):
            vals = [evr(n) for n in args]
            if name == "COUNT":
                n = 0
                for v in vals:
                    if isinstance(v, Arr):
                        n += sum(1 for x in v.flat() if _is_num(x))
                    elif _is_num(v) or (isinstance(v, str) and _is_num(to_num(v))) or isinstance(v, bool):
                        n += 1
                return float(n)
            nums, err = self._nums(vals)
            if err is not None:
                return err
            if name == "SUM":
                return float(sum(nums))
            if name == "AVERAGE":
                return sum(nums) / len(nums) if nums else DIV0
            if name == "MAX":
                return max(nums) if nums else 0.0
            return min(nums) if nums else 0.0
        if name == "COUNTA":
            n = 0
            for a in args:
                v = evr(a)
                n += sum(1 for x in (v.flat() if isinstance(v, Arr) else [v]) if x is not None)
            return float(n)
        if name in ("COUNTIF", "COUNTIFS", "SUMIF"):
            if name == "SUMIF":
                rng, crit = ev(args[0]), self._scalar(ev(args[1], array), col, row)
                if isinstance(rng, Err):
                    return rng
                if not isinstance(rng, Arr):
                    rng = Arr([[rng]])
                summed = rng
                if len(args) > 2:
                    n3 = args[2]
                    if n3[0] == "ref":
                        # Excel sizes the sum range from its top-left cell and
                        # the criteria range's shape, whatever was typed
                        nr, nc = rng.shape
                        sh3 = n3[1] or sheet
                        summed = REF if sh3 not in self._extent else Arr(
                            [[self.value(sh3, n3[2] + j, n3[3] + i) for j in range(nc)] for i in range(nr)])
                    else:
                        summed = ev(n3)
                if isinstance(summed, Err):
                    return summed
                if not isinstance(summed, Arr):
                    summed = Arr([[summed]])
                pred, total = _criteria(crit), 0.0
                sr, sc = summed.shape
                for i, r in enumerate(rng.rows):
                    for j, x in enumerate(r):
                        if not isinstance(x, Err) and pred(x) and i < sr and j < sc:
                            y = summed.rows[i][j]
                            if isinstance(y, Err):
                                return y
                            if _is_num(y):
                                total += y
                return total
            pairs = []
            for k in range(0, len(args) - 1, 2):
                rng = ev(args[k])
                if isinstance(rng, Err):
                    return rng
                if not isinstance(rng, Arr):
                    rng = Arr([[rng]])
                pairs.append((list(rng.flat()), _criteria(self._scalar(ev(args[k + 1], array), col, row))))
            if not pairs:
                return VALUE
            n = len(pairs[0][0])
            if any(len(p[0]) != n for p in pairs):
                return VALUE
            return float(sum(1 for i in range(n)
                             if all(not isinstance(vals[i], Err) and pred(vals[i]) for vals, pred in pairs)))
        if name == "SUMPRODUCT":
            arrs = []
            for n in args:
                v = ev(n, True)
                arrs.append(v if isinstance(v, Arr) else Arr([[v]]))
            shape = arrs[0].shape
            if any(a.shape != shape for a in arrs):
                return VALUE
            total = 0.0
            flats = [list(a.flat()) for a in arrs]
            for i in range(len(flats[0])):
                p = 1.0
                for f in flats:
                    x = f[i]
                    if isinstance(x, Err):
                        return x
                    p *= float(x) if _is_num(x) else 0.0      # text, blank and booleans count as 0
                total += p
            return total
        if name == "MMULT":
            a, b = ev(args[0], True), ev(args[1], True)
            a = a if isinstance(a, Arr) else Arr([[a]])
            b = b if isinstance(b, Arr) else Arr([[b]])
            (ar, ac), (br, bc) = a.shape, b.shape
            if ac != br:
                return VALUE
            out = []
            for i in range(ar):
                r = []
                for j in range(bc):
                    s = 0.0
                    for k in range(ac):
                        x, y = a.rows[i][k], b.rows[k][j]
                        if isinstance(x, Err):
                            return x
                        if isinstance(y, Err):
                            return y
                        if not _is_num(x) or not _is_num(y):
                            return VALUE
                        s += x * y
                    r.append(s)
                out.append(r)
            return Arr(out)
        if name in ("ABS", "INT", "ROUND"):
            v = self._scalar(ev(args[0], array), col, row)
            x = to_num(v)
            if isinstance(x, Err):
                return x
            if name == "ABS":
                return abs(x)
            if name == "INT":
                import math
                return float(math.floor(x))
            d = to_num(self._scalar(ev(args[1], array), col, row)) if len(args) > 1 else 0.0
            if isinstance(d, Err):
                return d
            q = 10 ** int(d)
            import math
            return math.copysign(math.floor(abs(x) * q + 0.5), x) / q      # half away from zero
        raise CalcError(f"unsupported function {name}")


# ── loading a workbook ───────────────────────────────────────────────────────

def load_xlsx(path, today: Optional[_dt.date] = None) -> Tuple["Book", dict]:
    """(Book, cached) from a saved workbook: every cell's content (formula
    text or value), and `cached` = {(sheet, col, row): the value Excel stored
    for a formula cell}. The file is opened read-only by openpyxl; callers
    that share it with Excel should pass a copy."""
    import openpyxl
    from openpyxl.worksheet.formula import ArrayFormula
    from openpyxl.styles.numbers import is_date_format
    wf = openpyxl.load_workbook(path)
    wv = openpyxl.load_workbook(path, data_only=True)
    cells, arrays, cached = {}, {}, {}
    # Cells whose number format is a date/time one. The book keeps a date as
    # its serial number; only the format says that Excel hands it to a range
    # read as "Wednesday, November 25, 2026 at 12:00:00 AM" (bookops.range_text).
    dates, fmt_memo = set(), {}
    for ws in wf.worksheets:
        name = ws.title
        vs = wv[name]._cells
        for (row, col), cell in ws._cells.items():
            v = cell.value
            if v is None:
                continue
            nf = cell.number_format
            if nf not in fmt_memo:
                try:
                    fmt_memo[nf] = bool(is_date_format(nf))
                except Exception:  # noqa: BLE001 — an odd format string is not a date
                    fmt_memo[nf] = False
            if fmt_memo[nf]:
                dates.add((name, col, row))
            if isinstance(v, ArrayFormula):
                text = v.text or ""
                text = text if text.startswith("=") else "=" + text
                r = parse_ref(v.ref) if v.ref else (None, col, row, col, row)
                for rr in range(r[2], r[4] + 1):
                    for cc in range(r[1], r[3] + 1):
                        arrays[(name, cc, rr)] = (col, row)
                v = text
            if cell.data_type == "e" and isinstance(v, str):
                v = Err(v)
            elif isinstance(v, str) and cell.data_type != "f" and not isinstance(cell.value, ArrayFormula):
                if v.startswith("="):
                    v = v                        # openpyxl reports formulas as "=..." strings
            elif isinstance(v, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
                v = serial(v)
            elif isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            cells[(name, col, row)] = v
            if isinstance(v, str) and v.startswith("=") and len(v) > 1:
                cv = vs.get((row, col))
                c = cv.value if cv is not None else None
                if cv is not None and cv.data_type == "e" and isinstance(c, str):
                    c = Err(c)
                cached[(name, col, row)] = serial(c)
    # a multi-cell array's non-anchor cells carry cached values too
    for (name, cc, rr), anchor in arrays.items():
        if (cc, rr) != anchor and (name, cc, rr) not in cells:
            cv = wv[name]._cells.get((rr, cc))
            if cv is not None and cv.value is not None:
                cached[(name, cc, rr)] = serial(cv.value)
    for ws in wf.worksheets:                     # an empty sheet still exists for refs
        cells.setdefault((ws.title, 1, 1), cells.get((ws.title, 1, 1)))
    cells = {k: v for k, v in cells.items() if v is not None or k[1:] == (1, 1)}
    book = Book(cells, today=today, arrays=arrays)
    book.dates = {k for k in dates if k in cells}
    return book, cached
