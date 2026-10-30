"""Tiny Excel formula evaluator for the Neon rule engine.

Enough of Excel to recompute the hcbi composite behind 0分!W
(=hcbi!AA<r>+hcbi!Y<r>): + - * / ^ & and comparisons, parentheses, numbers,
"strings", cell refs with an optional sheet ('0n'!AE303, hcbi!AA304, AA304),
same-sheet ranges (E304:O304) and SUM AVERAGE MAX MIN COUNT COUNTA IF AND OR
NOT ABS ROUND. Values are float | str | bool | None (blank).

Excel semantics kept: a blank is 0 in arithmetic and FALSE in IF; SUM/
AVERAGE/MAX/MIN over a range skip text and blanks; COUNTA counts non-blank.
Anything else raises FormulaError, which callers treat as "can't derive".

Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

Value = object   # float | str | bool | None


class FormulaError(Exception):
    pass


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<func>[A-Za-z_][A-Za-z0-9_.]*(?=\())
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][\w.+]*)!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)
  | (?P<bool>TRUE|FALSE)
  | (?P<op><>|<=|>=|[-+*/^&=<>(),:])
""", re.X)

_REF = re.compile(r"^(?:(?P<sheet>'[^']+'|[^!]+)!)?\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)"
                  r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?$")


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


def tokenize(src: str) -> List[Tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise FormulaError(f"bad token at {src[pos:pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(kind)))
    return out


def parse_ref(text: str, default_sheet: str) -> Tuple[str, List[Tuple[str, int]]]:
    """'hcbi!E304:G304' → ("hcbi", [("E",304),("F",304),("G",304)])."""
    m = _REF.match(text)
    if not m:
        raise FormulaError(f"bad ref {text!r}")
    sheet = (m.group("sheet") or default_sheet).strip("'")
    c1, r1 = col_index(m.group("c1")), int(m.group("r1"))
    c2 = col_index(m.group("c2")) if m.group("c2") else c1
    r2 = int(m.group("r2")) if m.group("r2") else r1
    cells = [(col_letter(c), r) for r in range(min(r1, r2), max(r1, r2) + 1)
             for c in range(min(c1, c2), max(c1, c2) + 1)]
    return sheet, cells


def refs(formula: str, default_sheet: str) -> List[Tuple[str, str, int]]:
    """Every (sheet, col, row) a formula names."""
    f = (formula or "").lstrip("=")
    out = []
    try:
        for kind, text in tokenize(f):
            if kind == "ref":
                sheet, cells = parse_ref(text, default_sheet)
                out += [(sheet, c, r) for c, r in cells]
    except FormulaError:
        pass
    return out


def num(v: Value) -> float:
    """Arithmetic coercion: blank → 0, bool → 0/1, numeric text → float."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        raise FormulaError(f"#VALUE! {s[:20]!r}")


def truth(v: Value) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        if v.upper() in ("TRUE", "FALSE"):
            return v.upper() == "TRUE"
        if not v.strip():
            return False
        raise FormulaError(f"#VALUE! in condition {v[:20]!r}")
    return num(v) != 0


def _is_number(v: Value) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Range(list):
    """Values of a range argument (aggregates skip text/blank in these)."""


def evaluate(formula: str, sheet: str, get: Callable[[str, str, int], Value]) -> Value:
    """Value of `formula` (leading '=' optional) on `sheet`; `get(sheet, col,
    row)` supplies cell values."""
    toks = tokenize((formula or "").strip().lstrip("="))
    pos = [0]

    def peek(*want):
        if pos[0] < len(toks) and (not want or toks[pos[0]][1] in want or toks[pos[0]][0] in want):
            return toks[pos[0]]
        return None

    def take(*want):
        t = peek(*want)
        if t is None:
            raise FormulaError(f"expected {want} at token {pos[0]}")
        pos[0] += 1
        return t

    def comparison():
        a = concat()
        while peek("=", "<>", "<", ">", "<=", ">="):
            op = take()[1]
            b = concat()
            if isinstance(a, str) or isinstance(b, str):
                x, y = ("" if a is None else str(a)).lower(), ("" if b is None else str(b)).lower()
            else:
                x, y = num(a), num(b)
            a = {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
        return a

    def concat():
        a = additive()
        while peek("&"):
            take()
            b = additive()
            a = ("" if a is None else str(a)) + ("" if b is None else str(b))
        return a

    def additive():
        a = term()
        while peek("+", "-"):
            op = take()[1]
            b = term()
            a = num(a) + num(b) if op == "+" else num(a) - num(b)
        return a

    def term():
        a = power()
        while peek("*", "/"):
            op = take()[1]
            b = power()
            if op == "*":
                a = num(a) * num(b)
            else:
                d = num(b)
                if d == 0:
                    raise FormulaError("#DIV/0!")
                a = num(a) / d
        return a

    def power():
        a = unary()
        while peek("^"):
            take()
            a = num(a) ** num(unary())
        return a

    def unary():
        if peek("-"):
            take()
            return -num(unary())
        if peek("+"):
            take()
            return num(unary())
        return primary()

    def primary():
        t = take()
        kind, text = t
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1].replace('""', '"')
        if kind == "bool":
            return text == "TRUE"
        if kind == "ref":
            sh, cells = parse_ref(text, sheet)
            if len(cells) == 1:
                return get(sh, cells[0][0], cells[0][1])
            return _Range(get(sh, c, r) for c, r in cells)
        if kind == "func":
            return call(text.upper())
        if text == "(":
            v = comparison()
            take(")")
            return v
        raise FormulaError(f"unexpected {text!r}")

    def args_():
        take("(")
        out = []
        if peek(")"):
            take()
            return out
        while True:
            out.append(comparison())
            if peek(","):
                take()
                continue
            take(")")
            return out

    def nums(args):
        vals = []
        for a in args:
            if isinstance(a, _Range):
                vals += [float(v) for v in a if _is_number(v)]
            elif a is not None:
                vals.append(num(a))
        return vals

    def _guard(fn):
        # parse + evaluate a branch; an error only matters if it is taken.
        # A failed branch is re-parsed token-by-token to the next , or ) at depth 0.
        start = pos[0]
        try:
            return fn(), None
        except FormulaError as e:
            pos[0], depth = start, 0
            while pos[0] < len(toks):
                t = toks[pos[0]][1]
                if t == "(":
                    depth += 1
                elif t == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif t == "," and depth == 0:
                    break
                pos[0] += 1
            return None, e

    def call(name):
        if name == "IF":
            # evaluate lazily so the untaken branch can't raise
            take("(")
            c = truth(comparison())
            take(",")
            then, then_err = _guard(comparison)
            other, other_err = False, None
            if peek(","):
                take()
                other, other_err = _guard(comparison)
            take(")")
            if c and then_err:
                raise then_err
            if not c and other_err:
                raise other_err
            return then if c else other
        a = args_()
        if name == "SUM":
            return sum(nums(a))
        if name == "AVERAGE":
            v = nums(a)
            if not v:
                raise FormulaError("#DIV/0! AVERAGE")
            return sum(v) / len(v)
        if name == "MAX":
            v = nums(a)
            return max(v) if v else 0.0
        if name == "MIN":
            v = nums(a)
            return min(v) if v else 0.0
        if name == "COUNT":
            return float(len(nums(a)))
        if name == "COUNTA":
            n = 0
            for x in a:
                items = x if isinstance(x, _Range) else [x]
                n += sum(1 for v in items if v is not None and v != "")
            return float(n)
        if name == "AND":
            return all(truth(x) for x in a)
        if name == "OR":
            return any(truth(x) for x in a)
        if name == "NOT":
            return not truth(a[0])
        if name == "ABS":
            return abs(num(a[0]))
        if name == "ROUND":
            return round(num(a[0]), int(num(a[1])) if len(a) > 1 else 0)
        raise FormulaError(f"unsupported function {name}")

    v = comparison()
    if pos[0] != len(toks):
        raise FormulaError(f"trailing tokens in {formula!r}")
    return v


def as_number(v: Value) -> Optional[float]:
    """Final coercion of a cell result for point math (text → error)."""
    return num(v)
