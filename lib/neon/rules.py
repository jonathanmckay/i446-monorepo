"""Neon rule engine v0 — derive a day's 0分 row from the ledger, diff it
against what Excel showed (Neon off Excel, step 3: parallel run).

Rulebook (read from the live workbook 2026-10-19; config/neon-rules.json
documents it, this module implements it):

  P (-1₦)  last `set` formula's literal sum (+ appends journaled after it)
  Q (0g)   9 if 0n!T (the "0g" habit) is marked and ≠ 0, + literal credits
  R (i9)   Σ weights of marked 0n U:Z            + credits
  S (m5)   Σ weights of marked 0n AA:AC          + credits
  T (个)   Σ weights of marked 0n D:M, Q:T       + credits
  U (媒)   Σ weights of marked 0n N:P + (−AU + AV·0.01 + AW·0.5 + AX) + credits
  V (思)   0n AP·0.5 + Σ(AQ:AS)                  + credits
  X (xk)   Σ(0n AJ:AO)                           + credits
  W (hcb)  hcbi!AA<r> + hcbi!Y<r> (refs read from the cell's own formula)
           + credits. The hcbi row is recomputed with formula.py from its
           cells: AA = SUM(E:O)/21*75 + IF(AL,-MAX((U-1800)/10,-40)) + T + S
           + (P-100)/2 + AI + W, Y = '0n'!AE<r'>*2 + tail, U/P = band sums;
           each cell's formula is the one the ledger saw, not assumed.
  Y (社)   credits only
  D (Σ)    Σ P..Y
  G..O     per-block: Excel = lock snapshots; ledger = event timestamps. Both
           REPORTED, neither judged (locks have fired hours late; see plan).

"Marked" follows Excel's SUMPRODUCT(--(NOT(ISBLANK(...)))): any non-blank
cell counts, an explicit 0 included. The Q bonus is the exception
(`<>0 AND <>""`).

Credits are read from the latest after-formula of the 0分 cell, not by
summing credit events: 40% of October's reference appends have unresolvable
points (fallback entries), while the formula tail is exact and includes hand
edits reconcile caught. `'1n+'!X5` references in that tail are reported as
unknown inputs (they drift when the weekly tab is edited).

An input the ledger has never observed is UNKNOWN, not blank: a derived
value with unknown inputs is partial and is never called a mismatch. The
nightly 0n/hcbi snapshot reconcile (scripts/neon-reconcile.py) shrinks the
unknown set to pre-2026-10-19 rows.

Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import cols as neon_cols
from . import formula as fx
from . import ledger

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "neon-rules.json"
TOL = 0.5  # |derived − observed| above this = mismatch

# 0n column groups feeding each 0分 column (from the live BF/BG/BH/BI formulas)
FLOW_RANGES = {
    "T": ["D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "Q", "R", "S", "T"],
    "R": ["U", "V", "W", "X", "Y", "Z"],
    "S": ["AA", "AB", "AC"],
    "U": ["N", "O", "P"],
}
MEDIA_EXTRA = {"AU": -1.0, "AV": 0.01, "AW": 0.5, "AX": 1.0}   # 0n!BB
MIND_COLS = {"AP": 0.5, "AQ": 1.0, "AR": 1.0, "AS": 1.0}         # 0n!BA
KID_COLS = ["AJ", "AK", "AL", "AM", "AN", "AO"]                   # 0n!AZ
Q_BONUS_COL, Q_BONUS = "T", 9.0
POINT_COLS = ["P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y"]
DERIVED_COLS = ["P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y"]
OBSERVE_ONLY: list = []
REF_COLS = ["W"]          # 0分 columns whose formula refs are evaluated (hcbi composite)
BLOCK_COLS = {"G": "卯", "H": "辰", "I": "巳", "J": "午", "K": "未",
              "L": "申", "M": "酉", "N": "戌", "O": "亥"}


# ── formula tail parsing ─────────────────────────────────────────────────────

_NUM = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_ARITH = re.compile(r"^[+-]?[\d.]+(?:[*/][\d.]+)+$")      # 7*45, 3*0.5/2 — literal products only
_WEEK_REF = re.compile(r"^[+-]?'1n\+'!([A-Z]{1,3})(\d+)$")


def _eval_arith(term: str) -> Optional[float]:
    """Value of a literal product/quotient term ("7*45" → 315). Only digits,
    '.', '*', '/' are allowed; anything else is not a literal."""
    if not _ARITH.match(term):
        return None
    sign = -1.0 if term.startswith("-") else 1.0
    body = term.lstrip("+-")
    try:
        val = None
        for i, part in enumerate(re.split(r"([*/])", body)):
            if i == 0:
                val = float(part)
            elif part in "*/":
                op = part
            else:
                val = val * float(part) if op == "*" else val / float(part)
        return sign * val
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def split_terms(formula: str) -> list:
    """Top-level +/- terms of an Excel formula, signs attached:
    "='0n'!BF292 + 0-20+'1n+'!C5-45 + 20" → ["'0n'!BF292", "+0", "-20",
    "+'1n+'!C5", "-45", "+20"]. Parentheses and quoted sheet names are
    opaque. A leading unsigned term keeps no sign."""
    f = (formula or "").strip()
    if f.startswith("="):
        f = f[1:]
    terms, buf, depth, quote = [], "", 0, False
    for ch in f:
        if ch == "'":
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch in "+-" and depth == 0:
                if buf.strip():
                    terms.append(buf.strip())
                buf = ch
                continue
        buf += ch
    if buf.strip():
        terms.append(buf.strip())
    return [t.replace(" ", "") for t in terms]


def classify_terms(formula: str) -> dict:
    """{"literals": float, "flow_refs": [...'0n'!X refs], "week_refs": [...'1n+'!X],
    "other_refs": [...], "funcs": [...IF(...)...], "n_literals": int}"""
    out = {"literals": 0.0, "n_literals": 0, "flow_refs": [], "week_refs": [],
           "other_refs": [], "funcs": []}
    for t in split_terms(formula):
        body = t[1:] if t[:1] in "+-" else t
        sign = -1.0 if t[:1] == "-" else 1.0
        if _NUM.match(body):
            out["literals"] += sign * float(body)
            out["n_literals"] += 1
        elif _eval_arith(t) is not None:
            out["literals"] += _eval_arith(t)              # "7*45" hand-typed products
            out["n_literals"] += 1
        elif "(" in body:
            out["funcs"].append(t)          # IF(...) bonus etc. — before the ref tests
        elif "'0n'!" in body:
            out["flow_refs"].append(t)
        elif "'1n+'!" in body:
            out["week_refs"].append(t)
        else:
            out["other_refs"].append(t)
    return out


# ── inputs ───────────────────────────────────────────────────────────────────

def load_weights(conn=None) -> dict:
    """0n row-2 weights by column letter: newest ledger snapshot of row 2 when
    present (weight drift is then itself a ledger event), else the config
    snapshot. Returns {col: float}."""
    weights: dict = {}
    try:
        rules = json.loads(RULES_PATH.read_text())
        for header, w in rules.get("0n_weights", {}).items():
            col = neon_cols.maybe_col("0n", header)
            if col and w is not None:
                weights[col] = float(w)
    except Exception:
        pass
    try:
        own = conn is None
        c = conn or ledger.connect()
        try:
            rows = c.execute(
                """SELECT e.col, e.after_value FROM events e
                     JOIN (SELECT col, MAX(id) mid FROM events
                            WHERE sheet='0n' AND row=2 GROUP BY col) m ON m.mid=e.id
                    WHERE e.after_value IS NOT NULL""").fetchall()
        finally:
            if own:
                c.close()
        for col, v in rows:
            weights[col] = float(v)
    except Exception:
        pass
    return weights


def marked(cell: Optional[dict]) -> Optional[bool]:
    """Excel NOT(ISBLANK): True when the cell holds anything (0 included),
    False when blank, None when the ledger has never seen it."""
    if cell is None:
        return None
    if cell["value"] is not None:
        return True
    return bool((cell.get("text") or "").strip())


def _num_or_none(cell: Optional[dict]) -> Optional[float]:
    if cell is None or cell["value"] is None:
        return None
    return float(cell["value"])


# ── cross-sheet cell book (hcbi composite behind W) ──────────────────────────

def _content(raw) -> object:
    """Literal cell content → float | str | None (blank)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s_ = str(raw).strip()
    if not s_:
        return None
    try:
        return float(s_)
    except ValueError:
        return s_


class _Book:
    """Values of any (sheet, col, row) the W formula reaches, recomputed from
    the ledger. Rows map to dates via row_dates. Formula cells are evaluated
    from the formula the ledger saw for that cell.

    loose: a cell's newest event of any kind (reconcile/snapshot included);
      a cell with no event, or a row with no known date, is UNKNOWN.
    strict: per cell, the last daemon `set` (or the day's baseline when no
      write came first) seeds the content, each journaled credit adds its
      points, a text write/append makes it non-blank; a cell no writer ever
      touched is blank. Excel-side observations are ignored."""

    def __init__(self, c, strict: bool):
        self.c, self.strict = c, strict
        self.unknown: list = []
        self.touched: dict = {}          # (sheet, col, row) → {"formula", "credits", "value"}
        self._sheets: dict = {}
        self._memo: dict = {}
        self._stack: set = set()

    def _row_date(self, sheet: str, row: int) -> Optional[str]:
        r = self.c.execute("SELECT date FROM row_dates WHERE sheet=? AND row=?", (sheet, row)).fetchone()
        return r[0] if r else None

    def _cells(self, sheet: str, date_iso: str) -> dict:
        """{col: {"content": str|float|None, "credits": [..], "unknown": [..]}}"""
        key = (sheet, date_iso)
        if key in self._sheets:
            return self._sheets[key]
        out: dict = {}
        if not self.strict:
            for col, st in ledger.state(date_iso, sheet, self.c).items():
                f = st.get("formula")
                if isinstance(f, str) and f.strip().startswith("="):
                    content = f.strip()
                elif st["value"] is not None:
                    content = st["value"]
                else:
                    content = _content(st.get("text"))
                out[col] = {"content": content, "credits": [], "unknown": [], "observed": st["value"]}
        else:
            rows = self.c.execute(
                """SELECT e.col, e.kind, e.jsonl_kind, e.points, e.value, e.after_formula, e.src
                     FROM events e LEFT JOIN row_dates rd ON rd.sheet = e.sheet AND rd.row = e.row
                    WHERE e.sheet=? AND COALESCE(e.date, rd.date)=?
                      AND (e.kind IN ('set','credit','credit_ref','text')
                           OR (e.kind='observe' AND e.jsonl_kind='baseline'))
                    ORDER BY e.ts, e.id""", (sheet, date_iso)).fetchall()
            written: set = set()
            for col, kind, jkind, pts, value, after, src in rows:
                cell = out.setdefault(col, {"content": None, "credits": [], "unknown": [], "observed": None})
                if kind == "observe":
                    if col not in written:
                        cell.update(content=_content(after), credits=[], unknown=[])
                    continue
                written.add(col)
                if kind in ("set", "text"):
                    raw = after if (after or "").strip() else value
                    cell.update(content=_content(raw), credits=[], unknown=[])
                elif pts is None:
                    cell["unknown"].append(f"{sheet}!{col}: credit without points ({(src or '?')[:30]})")
                else:
                    cell["credits"].append(float(pts))
        self._sheets[key] = out
        return out

    def get(self, sheet: str, col: str, row: int):
        key = (sheet, col, row)
        if key in self._memo:
            return self._memo[key]
        if key in self._stack:
            raise fx.FormulaError(f"circular ref {sheet}!{col}{row}")
        date_iso = self._row_date(sheet, row)
        if date_iso is None:
            self.unknown.append(f"{sheet}!{col}{row} (row date unknown)")
            self._memo[key] = None
            return None
        cell = self._cells(sheet, date_iso).get(col)
        if cell is None:
            if not self.strict:
                self.unknown.append(f"{sheet}!{col}{row}")
            self.touched[key] = {"formula": None, "credits": [], "value": None}
            self._memo[key] = None
            return None
        content = cell["content"]
        self.unknown += cell["unknown"]
        if isinstance(content, str) and content.startswith("="):
            self._stack.add(key)
            try:
                val = fx.evaluate(content, sheet, self.get)
            except fx.FormulaError:
                if self.strict or cell["observed"] is None:
                    raise
                val = cell["observed"]          # loose: fall back to what Excel showed
            finally:
                self._stack.discard(key)
        else:
            val = content
        if cell["credits"]:
            val = fx.num(val) + sum(cell["credits"])
        self.touched[key] = {"formula": content if isinstance(content, str) and content.startswith("=") else None,
                             "credits": list(cell["credits"]), "value": val}
        self._memo[key] = val
        return val

    def eval_refs(self, refs: list, sheet: str = "0分") -> float:
        """Σ of signed ref terms ("hcbi!AA304", "+hcbi!Y304")."""
        total = 0.0
        for t in refs:
            sign = -1.0 if t.startswith("-") else 1.0
            total += sign * fx.num(fx.evaluate(t.lstrip("+-"), sheet, self.get))
        return total


def _seed_formula(c, date_iso: str, sheet: str, col: str) -> str:
    """The formula strict mode starts a cell from: the last daemon `set`, or
    the newest baseline when no write came first."""
    seed, seen = "", False
    for kind, after in c.execute(
            """SELECT kind, after_formula FROM events WHERE date=? AND sheet=? AND col=?
                 AND (kind IN ('set','credit','credit_ref') OR (kind='observe' AND jsonl_kind='baseline'))
               ORDER BY ts, id""", (date_iso, sheet, col)).fetchall():
        if kind == "observe":
            if not seen:
                seed = after or ""
            continue
        seen = True
        if kind == "set":
            seed = after or ""
    return seed


def _ref_flow(c, date_iso: str, col: str, formula: str, strict: bool) -> tuple:
    """(value of the formula's cell refs, unknown, book) for a REF_COLS column."""
    refs_ = classify_terms(formula)["other_refs"]
    book = _Book(c, strict)
    if not refs_:
        return 0.0, ([] if strict else [f"0分!{col}: no refs in formula"]), book
    try:
        v = book.eval_refs(refs_)
    except fx.FormulaError as e:
        return 0.0, book.unknown + [f"0分!{col}: {e}"], book
    return v, book.unknown, book


# ── derivation ───────────────────────────────────────────────────────────────

def week_ref_values(conn) -> dict:
    """Newest ledger value of each 1n+ row-5 cell (the expected-points row the
    0分 credits reference as +'1n+'!C5): {col: float}. Fed by the nightly
    snapshot of 1n+ row 5; absent cells stay unknown."""
    rows = conn.execute(
        """SELECT e.col, e.after_value FROM events e
             JOIN (SELECT col, MAX(id) mid FROM events WHERE sheet='1n+' AND row=5 GROUP BY col) m
               ON m.mid = e.id WHERE e.after_value IS NOT NULL""").fetchall()
    return {col: float(v) for col, v in rows}


_CELL_REF = re.compile(r"^[+-]?([A-Z]{1,3})\d+$")


def _eval_row_formulas(on: dict) -> dict:
    """Strict mode: a 0n cell holding a same-row sum (=N298+O298 + 0, the
    media AX column) is recomputed from the ledger's own values of the cells
    it names instead of trusting Excel's cached result. Anything fancier is
    left as observed."""
    out = dict(on)
    for col, cell in on.items():
        f = (cell.get("formula") or "").strip()
        if not f.startswith("=") or "!" in f or "(" in f:
            continue
        total, ok, has_ref = 0.0, True, False
        for t in split_terms(f):
            body = t[1:] if t[:1] in "+-" else t
            sign = -1.0 if t[:1] == "-" else 1.0
            m = _CELL_REF.match(t)
            if _NUM.match(body):
                total += sign * float(body)
            elif m:
                has_ref = True
                ref = on.get(m.group(1)) or {}
                total += sign * float(ref.get("value") or 0.0)
            else:
                ok = False
                break
        if ok and has_ref:
            out[col] = {**cell, "value": total, "text": None}
    return out


def _strict_credits(c, date_iso: str, col: str, week5: dict) -> tuple:
    """Ledger-as-source credits for one 0分 cell: literals (+ resolved 1n+
    refs) of the last daemon `set` — or the 03:50 day-start baseline when no
    set came first — plus Σ credit/credit_ref points journaled after it.
    Reconcile/snapshot observations are ignored, so an edit typed into Excel
    shows up as a mismatch instead of being absorbed. → (value, unknown, comp)"""
    rows = c.execute(
        """SELECT kind, jsonl_kind, points, after_formula, src FROM events
            WHERE date=? AND sheet='0分' AND col=?
              AND (kind IN ('set','credit','credit_ref')
                   OR (kind='observe' AND jsonl_kind='baseline'))
            ORDER BY ts, id""", (date_iso, col)).fetchall()
    base, credits, unknown, seen_write = 0.0, 0.0, [], False

    def literal_base(formula: str) -> float:
        t = classify_terms(formula or "")
        v = t["literals"]
        for ref in t["week_refs"]:
            m = _WEEK_REF.match(ref)
            if m and m.group(2) == "5" and m.group(1) in week5:
                v += (-1.0 if ref.startswith("-") else 1.0) * week5[m.group(1)]
            else:
                unknown.append(ref)
        return v

    for kind, jkind, pts, formula, src in rows:
        if kind == "observe":
            if not seen_write:                 # baseline only seeds the day
                unknown.clear()
                base, credits = literal_base(formula), 0.0
            continue
        seen_write = True
        if kind == "set":
            unknown.clear()
            base, credits = literal_base(formula), 0.0
        elif pts is None:
            unknown.append(f"credit without points ({(src or '?')[:40]})")
        else:
            credits += float(pts)
    return base + credits, unknown, {"base": base, "ledger_credits": credits}


def derive_day(date_iso: str, conn=None, strict: bool = False) -> dict:
    """{"date", "cols": {col: {derived, observed, delta, status, components, unknown}},
    "blocks": {...}, "weights_source": ...}. status ∈ ok | mismatch | partial
    (unknown inputs) | observe-only | no-observation.

    strict=True is the cutover test: credits come from journaled credit
    events (not Excel's formula), 0n marks only from daemon writes, and a
    0n cell the ledger never saw written counts as blank."""
    own = conn is None
    c = conn or ledger.connect()
    strict_cr: dict = {}
    try:
        fen = ledger.state(date_iso, "0分", c)
        on = ledger.state(date_iso, "0n", c, writes_only=strict)
        if strict:
            on = _eval_row_formulas(on)
        weights = load_weights(c)
        week5 = week_ref_values(c)
        p_events = c.execute(
            """SELECT kind, ts, after_formula, value FROM events
                WHERE date=? AND sheet='0分' AND col='P' AND kind IN ('set','credit')
                ORDER BY ts, id""", (date_iso,)).fetchall()
        if strict:
            strict_cr = {col: _strict_credits(c, date_iso, col, week5)
                         for col in POINT_COLS if col not in ("P",) + tuple(OBSERVE_ONLY)}
        ref_flow = {}
        for col in REF_COLS:
            f = _seed_formula(c, date_iso, "0分", col) if strict else (fen.get(col) or {}).get("formula") or ""
            fl_, unk_, _ = _ref_flow(c, date_iso, col, f, strict)
            ref_flow[col] = (fl_, unk_)
    finally:
        if own:
            c.close()

    def flow(cols_: list) -> tuple:
        total, unknown = 0.0, []
        for col in cols_:
            m = marked(on.get(col))
            if m is None and strict:
                m = False
            if m is None:
                unknown.append(f"0n!{col}")
            elif m:
                total += weights.get(col, 0.0)
        return total, unknown

    result: dict = {}
    for col in POINT_COLS:
        cell = fen.get(col)
        observed = _num_or_none(cell)
        formula = (cell or {}).get("formula") or ""
        terms = classify_terms(formula)
        comp: dict = {"literals": terms["literals"]}
        unknown: list = []
        week_pts = 0.0
        for ref in terms["week_refs"]:            # +'1n+'!C5 → row-5 expected points
            m = _WEEK_REF.match(ref)
            if m and m.group(2) == "5" and m.group(1) in week5:
                week_pts += (-1.0 if ref.startswith("-") else 1.0) * week5[m.group(1)]
            else:
                unknown.append(ref)
        if terms["week_refs"]:
            comp["week_refs"] = week_pts
        derived: Optional[float] = None
        if col in OBSERVE_ONLY:
            status = "observe-only"
        elif col == "P":
            last_set, extra = None, 0.0
            for kind, ts, after, value in p_events:
                if kind == "set":
                    last_set = classify_terms(after)["literals"]
                    extra = 0.0
                elif kind == "credit" and last_set is not None:
                    extra += float(value) if _NUM.match(str(value).replace(" ", "")) else 0.0
            comp = {"last_set": last_set, "appends_after": extra}
            derived = None if last_set is None else last_set + extra
            status = "pending" if derived is None else None
        else:
            fl, unk = 0.0, []
            if col in FLOW_RANGES:
                fl, unk = flow(FLOW_RANGES[col])
                comp["flow"] = fl
            if col in REF_COLS:
                fl, unk = ref_flow[col]
                fl, unk = round(fl, 6), list(unk)
                comp["refs"] = fl
            if col == "U":
                extra = 0.0
                for k, w in MEDIA_EXTRA.items():
                    v = _num_or_none(on.get(k))
                    if v is None:
                        if on.get(k) is None and not strict:
                            unk.append(f"0n!{k}")
                        continue
                    extra += w * v
                comp["media_extra"] = extra
                fl += extra
            if col == "V":
                for k, w in MIND_COLS.items():
                    v = _num_or_none(on.get(k))
                    if v is None:
                        if on.get(k) is None and not strict:
                            unk.append(f"0n!{k}")
                        continue
                    fl += w * v
                comp["flow"] = fl
            if col == "X":
                for k in KID_COLS:
                    v = _num_or_none(on.get(k))
                    if v is None:
                        if on.get(k) is None and not strict:
                            unk.append(f"0n!{k}")
                        continue
                    fl += v
                comp["flow"] = fl
            if col == "Q":
                t = on.get(Q_BONUS_COL)
                if t is None and strict:
                    bonus = 0.0
                elif t is None:
                    unk.append(f"0n!{Q_BONUS_COL}")
                    bonus = 0.0
                else:
                    v = _num_or_none(t)
                    bonus = Q_BONUS if (v not in (None, 0.0) or (v is None and marked(t))) else 0.0
                comp["bonus"] = bonus
                fl += bonus
            unknown += unk
            if strict:
                cr, cr_unk, cr_comp = strict_cr[col]
                comp.pop("literals", None)
                comp.pop("week_refs", None)
                comp.update(cr_comp)
                unknown = unk + cr_unk
                derived = fl + cr
            else:
                derived = fl + terms["literals"] + week_pts
            status = None
        if status is None:
            if observed is None:
                status = "no-observation"
            elif unknown:
                status = "partial"
            else:
                status = "ok" if abs(derived - observed) <= TOL else "mismatch"
        result[col] = {"derived": None if derived is None else round(derived, 3),
                       "observed": observed,
                       "delta": None if (derived is None or observed is None) else round(derived - observed, 3),
                       "status": status, "components": comp, "unknown": unknown,
                       "formula": formula}

    # Σ: derived from the derived point columns, vs observed D
    d_cell = fen.get("D")
    d_obs = _num_or_none(d_cell)
    parts, d_unknown = 0.0, []
    for col in POINT_COLS:
        r = result[col]
        v = r["derived"] if r["derived"] is not None else r["observed"]
        if v is None:
            d_unknown.append(col)
        else:
            parts += v
        d_unknown += r["unknown"]
    d_status = ("no-observation" if d_obs is None else "partial" if d_unknown
                else "ok" if abs(parts - d_obs) <= TOL else "mismatch")
    result["D"] = {"derived": round(parts, 3), "observed": d_obs,
                   "delta": None if d_obs is None else round(parts - d_obs, 3),
                   "status": d_status, "components": {}, "unknown": d_unknown, "formula": ""}

    blocks = {glyph: {"lock": _num_or_none(fen.get(col)) if (fen.get(col) or {}).get("kind") == "lock" else None,
                      "observed": _num_or_none(fen.get(col))}
              for col, glyph in BLOCK_COLS.items()}
    return {"date": date_iso, "cols": result, "blocks": blocks,
            "weights_known": len(weights), "strict": strict}


def event_blocks(date_iso: str, conn=None) -> dict:
    """Points credited per 地支 block by event timestamp (the ledger's own
    attribution): Σ credit points + P set deltas, by `block`."""
    own = conn is None
    c = conn or ledger.connect()
    try:
        rows = c.execute(
            """SELECT block, SUM(points) FROM events
                WHERE date=? AND sheet='0分' AND block IS NOT NULL
                  AND kind IN ('credit','credit_ref') AND points IS NOT NULL
                GROUP BY block""", (date_iso,)).fetchall()
        return {b: round(v, 3) for b, v in rows}
    finally:
        if own:
            c.close()


def summarize(day: dict) -> dict:
    cols = day["cols"]
    return {
        "date": day["date"],
        "mismatch": sorted(c for c, r in cols.items() if r["status"] == "mismatch"),
        "ok": sorted(c for c, r in cols.items() if r["status"] == "ok"),
        "partial": sorted(c for c, r in cols.items() if r["status"] == "partial"),
        "no_observation": sorted(c for c, r in cols.items() if r["status"] == "no-observation"),
        "unknown_inputs": sorted({u for r in cols.values() for u in r["unknown"]}),
    }


# ── strict (ledger-as-source) check ──────────────────────────────────────────

STRICT_COLS = [c for c in POINT_COLS if c not in OBSERVE_ONLY]
INPUT_CELLS = {col: list(FLOW_RANGES.get(col, [])) for col in POINT_COLS}
INPUT_CELLS["U"] += list(MEDIA_EXTRA)
INPUT_CELLS["V"] = list(MIND_COLS)
INPUT_CELLS["X"] = list(KID_COLS)
INPUT_CELLS["Q"] = [Q_BONUS_COL]


def _literal_terms(formula: str) -> list:
    out = []
    for t in split_terms(formula or ""):
        body = t[1:] if t[:1] in "+-" else t
        v = float(body) if _NUM.match(body) else _eval_arith(t) if _eval_arith(t) is not None else None
        if v is not None:
            v = -v if (t[:1] == "-" and _NUM.match(body)) else v
            if v != 0:
                out.append(round(v, 3))
    return out


def _ledger_terms(c, date_iso: str, col: str) -> list:
    """Literal terms the ledger can account for: the seeding set/baseline
    formula's literals + each journaled credit's points."""
    rows = c.execute(
        """SELECT kind, points, after_formula FROM events
            WHERE date=? AND sheet='0分' AND col=?
              AND (kind IN ('set','credit','credit_ref') OR (kind='observe' AND jsonl_kind='baseline'))
            ORDER BY ts, id""", (date_iso, col)).fetchall()
    terms, seen_write = [], False
    for kind, pts, formula in rows:
        if kind == "observe":
            if not seen_write:
                terms = _literal_terms(formula)
            continue
        seen_write = True
        if kind == "set":
            terms = _literal_terms(formula)
        elif pts is not None and pts != 0:
            terms.append(round(float(pts), 3))
    return terms


def _header(sheet: str, col: str) -> Optional[str]:
    try:
        return neon_cols.header_for(sheet, col)
    except Exception:
        return None


def _norm(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 3)
    return v


def _ref_diffs(c, date_iso: str, col: str, excel_formula: str) -> list:
    """Why a REF_COLS column (W) misses in strict: the cells its formula
    reaches whose Excel content no daemon write explains. Literal cells are
    compared by value; formula cells by their literal tail (a +5 typed after
    '0n'!AE296*2)."""
    from collections import Counter
    _, _, loose = _ref_flow(c, date_iso, col, excel_formula, strict=False)
    _, _, strict = _ref_flow(c, date_iso, col, _seed_formula(c, date_iso, "0分", col), strict=True)
    why = []
    for key in sorted(set(loose.touched) | set(strict.touched), key=lambda k: (k[0], fx.col_index(k[1]), k[2])):
        sheet, k, row = key
        lo, st = loose.touched.get(key), strict.touched.get(key)
        hdr = _header(sheet, k)
        name = f"{sheet}!{k}{row}{f'({hdr})' if hdr else ''}"
        lf, sf = (lo or {}).get("formula"), (st or {}).get("formula")
        if lf or sf:
            ex = Counter(_literal_terms(lf or ""))
            led = Counter(_literal_terms(sf or "")) + Counter(round(x, 3) for x in (st or {}).get("credits", []) if x)
            for v, n in (ex - led).items():
                why += [f"{v:+g} in {name} formula, never journaled (typed into {sheet}?)"] * n
            for v, n in (led - ex).items():
                why += [f"{v:+g} journaled to {name} but gone from Excel"] * n
            if not ex and not led and (lf or "").replace(" ", "") != (sf or "").replace(" ", ""):
                why.append(f"{name}: Excel `{lf or _fmt(lo and lo['value'])}`, ledger `{sf or _fmt(st and st['value'])}`")
            continue
        lv, sv = _norm((lo or {}).get("value")), _norm((st or {}).get("value"))
        if lv != sv and not (lv in (None, 0.0) and sv in (None, 0.0) and not isinstance(lv, str)):
            why.append(f"{name}: Excel {_fmt(lv)}, ledger {_fmt(sv)} (typed into {sheet}?)")
    return why


def _fmt(v) -> str:
    return "blank" if v in (None, "") else f"{v:g}" if isinstance(v, float) else str(v)


def strict_check(date_iso: str, conn=None) -> dict:
    """The cutover gate for one day: does the ledger alone reproduce Excel's
    0分 point columns? → {"date", "pass", "complete", "misses": [{col, field,
    derived, observed, delta, why: [...]}]}. `why` names the cause JM can act
    on: a +N in the Excel formula the ledger never saw (typed into Excel), a
    credit the ledger has that Excel lost, or a 0n habit cell whose Excel
    value no daemon write explains (typed into 0n)."""
    from collections import Counter
    own = conn is None
    c = conn or ledger.connect()
    try:
        strict = derive_day(date_iso, c, strict=True)
        week5 = week_ref_values(c)
        on_x = ledger.state(date_iso, "0n", c)
        on_l = ledger.state(date_iso, "0n", c, writes_only=True)
        misses = []
        complete = True
        for col in STRICT_COLS:
            r = strict["cols"][col]
            if r["status"] == "no-observation":
                complete = False
                continue
            if r["status"] == "ok":
                continue
            why = []
            if col != "P":
                excel = Counter(_literal_terms(r["formula"]))
                for ref in classify_terms(r["formula"])["week_refs"]:   # +'1n+'!U5 ≙ a credit_ref's points
                    m = _WEEK_REF.match(ref)
                    if m and m.group(2) == "5" and m.group(1) in week5 and week5[m.group(1)]:
                        excel[round((-1.0 if ref.startswith("-") else 1.0) * week5[m.group(1)], 3)] += 1
                led = Counter(_ledger_terms(c, date_iso, col))
                for v, n in (excel - led).items():
                    why += [f"{v:+g} in Excel formula, never journaled (typed into Excel?)"] * n
                for v, n in (led - excel).items():
                    why += [f"{v:+g} journaled but gone from Excel (removed by hand?)"] * n
            else:
                why.append("P (-1₦) edited after the last ritual write")
            if col in REF_COLS:
                why += _ref_diffs(c, date_iso, col, r["formula"])
            for k in INPUT_CELLS.get(col, []):
                xv = (on_x.get(k) or {}).get("value")
                lv = (on_l.get(k) or {}).get("value")
                xt = (on_x.get(k) or {}).get("text")
                lt = (on_l.get(k) or {}).get("text")
                flow_cell = k in FLOW_RANGES.get(col, []) or k == Q_BONUS_COL
                if not flow_cell:                # value-weighted: blank ≡ 0
                    if (xv or 0.0) == (lv or 0.0) and not (xt or lt):
                        continue
                if (xv, xt or None) != (lv, lt or None):
                    hdr = _header("0n", k)
                    why.append(f"0n!{k}{f'({hdr})' if hdr else ''}: Excel {_fmt(xv if xv is not None else xt)}, "
                               f"ledger {_fmt(lv if lv is not None else lt)} (typed into 0n?)")
            if r["unknown"]:
                why += [f"unknown: {u}" for u in r["unknown"]]
            misses.append({"col": col, "field": _header("0分", col),
                           "derived": r["derived"], "observed": r["observed"], "delta": r["delta"], "why": why})
        return {"date": date_iso, "pass": complete and not misses, "complete": complete,
                "misses": misses, "excluded": list(OBSERVE_ONLY)}
    finally:
        if own:
            c.close()


def streak(days: dict) -> int:
    """Consecutive passing days ending at the newest evaluated day."""
    n = 0
    for d in sorted(days, reverse=True):
        if not days[d].get("pass"):
            break
        n += 1
    return n
