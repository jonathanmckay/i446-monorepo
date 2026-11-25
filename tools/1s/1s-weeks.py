#!/usr/bin/env python3
"""1s-weeks.py — week-over-week snapshot of the `1分+1s` sheet on Ix.

Pulls the review week's row plus the trailing N completed weeks from the
weekly summary sheet (∑分, 分/d, per-domain points, ritual rates, ratings,
title/win/miss text) and prints a comparison table, deltas vs the trailing
mean, and a deterministic "what moved" digest that /1s feeds into its
narrative. Read-only: never writes to the workbook.

Usage:
    1s-weeks.py [YYYY-MM-DD] [--weeks N] [--json]

No date → the most recent completed Sun–Sat week (same rule as 1s-survey.py).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import statistics
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client

HERE = Path(__file__).resolve().parent
SHEET = "1分+1s"

# One source of truth for week labels: reuse 1s-survey.py's quota logic.
_spec = importlib.util.spec_from_file_location("survey", HERE / "1s-survey.py")
_survey = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_survey)  # type: ignore[union-attr]
week_range = _survey.week_range
week_row_label = _survey.week_row_label

# Column → (key, kind). kind: "num" parsed as float (commas stripped), "txt" raw.
COLS: list[tuple[str, str, str]] = [
    ("A", "week", "txt"),
    ("C", "∑分", "num"),
    ("O", "分/d", "num"),
    ("G", "i9", "num"),
    ("H", "m7", "num"),
    ("I", "-2g", "num"),
    ("J", "hcmc", "num"),
    ("K", "hcmp", "num"),
    ("L", "hcb", "num"),
    ("M", "s89", "num"),
    ("N", "xk87", "num"),
    ("E", "-1₦/d", "num"),
    ("F", "0₲/d", "num"),
    ("AA", "-1n", "num"),
    ("Y", "avg", "num"),
    ("W", "high", "num"),
    ("X", "low", "num"),
    ("R", "title", "txt"),
    ("S", "win", "txt"),
    ("T", "missed", "txt"),
    ("U", "proud", "txt"),
    ("V", "regret", "txt"),
]
NUM_KEYS = [k for _, k, kind in COLS if kind == "num"]
TABLE_KEYS = ["∑分", "分/d", "i9", "m7", "-2g", "hcmc", "hcmp", "hcb", "s89",
              "xk87", "-1₦/d", "0₲/d", "-1n", "avg"]
FLAG_PCT = 15.0  # |Δ vs trailing mean| at or above this is called out


def _num(s: str) -> float | None:
    s = (s or "").strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# The sheet's number formats, which the old `string value` read applied for us:
# points, rates and ritual percentages show as whole numbers, the three ratings
# to one decimal. read_range returns the unrounded VALUE ("1590.857142857143",
# "1.079542857143E+4"), so the display rounding is applied here to keep every
# figure (and the means and deltas built on them) what the sheet shows.
# Verified against all 52 week rows 2026-11-25. _fmt below prints at the same
# precision. If a column's format changes in the sheet, change it here too.
DISPLAY_DP = {"avg": 1, "high": 1, "low": 1}     # every other num column: 0
FIRST_ROW, LAST_ROW = 2, 400
LAST_COL = "AA"                                   # rightmost column in COLS
_NUM_TEXT_RE = re.compile(r"^-?\d+\.\d+(?:[eE][-+]?\d+)?$")


def _col_index(letters: str) -> int:
    """0-based index of a column letter within an A.. range read."""
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _general(text: str) -> str:
    """A number value the way a General-format cell shows it: the week label
    "11.299999999999999" (=A(n-1)+0.1) → "11.3", "1.0" → "1". Text passes."""
    t = (text or "").strip()
    return "%.10g" % float(t) if _NUM_TEXT_RE.match(t) else (text or "")


def _shown(text: str, dp: int) -> float | None:
    """A numeric cell's value rounded the way its number format shows it
    (half away from zero, like Excel). Blank → None; text falls back to _num."""
    t = (text or "").strip()
    if not _NUM_TEXT_RE.match(t):
        return _num(t)
    q = Decimal(1).scaleb(-dp)
    return float(Decimal(t).quantize(q, rounding=ROUND_HALF_UP))


def parse_rows(grid: list) -> list[dict]:
    """read_range rows of A..LAST_COL (from FIRST_ROW) → one dict per week,
    stopping at the first blank col A like the old row loop did."""
    rows = []
    for cells in grid:
        cells = [str(c) for c in cells]
        if not cells or not cells[0].strip():
            break
        row: dict = {}
        for col, key, kind in COLS:
            i = _col_index(col)
            val = cells[i] if i < len(cells) else ""
            if key == "week":
                row[key] = _general(val).strip()
            elif kind == "num":
                row[key] = _shown(val, DISPLAY_DP.get(key, 0))
            else:
                row[key] = val.strip()
        if row.get("week"):
            rows.append(row)
    return rows


def fetch_rows() -> list[dict]:
    """One excel-http read (no raw AppleScript, 2026-11-25)."""
    r = neon_excel.read_range(SHEET, f"A{FIRST_ROW}:{LAST_COL}{LAST_ROW}")
    grid = r.get("rows") if r.get("ok") else None
    if not isinstance(grid, list):
        raise RuntimeError(f"{SHEET} read failed: {r.get('error') or r}")
    return parse_rows(grid)


def select_window(rows: list[dict], label: str, n: int) -> tuple[dict, list[dict]]:
    """Target row + up to n prior rows that have a recorded ∑分."""
    idx = next((i for i, r in enumerate(rows) if r["week"] == label), None)
    if idx is None:
        raise KeyError(f"week {label} not found in {SHEET} col A")
    target = rows[idx]
    prior = [r for r in rows[:idx] if r.get("∑分")][-n:]
    return target, prior


def _fmt(v: float | None, key: str) -> str:
    if v is None:
        return "—"
    if key in ("avg", "high", "low"):
        return f"{v:.1f}"
    return f"{v:,.0f}"


def deltas(target: dict, prior: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for k in TABLE_KEYS:
        vals = [r[k] for r in prior if r.get(k) is not None]
        t = target.get(k)
        if t is None or not vals:
            continue
        mean = statistics.fmean(vals)
        pct = (t - mean) / mean * 100 if mean else 0.0
        out[k] = {
            "value": t, "mean": mean, "pct": pct,
            "best": t >= max(vals), "worst": t <= min(vals),
        }
    return out


def digest(target: dict, prior: list[dict], d: dict[str, dict]) -> dict:
    up = sorted((k for k, v in d.items() if v["pct"] >= FLAG_PCT),
                key=lambda k: -d[k]["pct"])
    down = sorted((k for k, v in d.items() if v["pct"] <= -FLAG_PCT),
                  key=lambda k: d[k]["pct"])
    best = [k for k, v in d.items() if v["best"]]
    worst = [k for k, v in d.items() if v["worst"]]
    # Weeks in the window that look like the target on ∑分 (±10%) — what
    # did those weeks say worked?
    t_sum = target.get("∑分")
    peers = []
    if t_sum:
        for r in prior:
            if r.get("∑分") and abs(r["∑分"] - t_sum) / t_sum <= 0.10:
                peers.append(r["week"])
    return {"up": up, "down": down, "best_in_window": best,
            "worst_in_window": worst, "peer_weeks": peers}


def render_md(target: dict, prior: list[dict], d: dict, dg: dict) -> str:
    rows = prior + [target]
    lines = []
    n = len(prior)
    lines.append(f"### Week-over-week ({target['week']} vs trailing {n})")
    lines.append("")
    lines.append("| Week | " + " | ".join(TABLE_KEYS) + " | Title |")
    lines.append("|---|" + "---|" * len(TABLE_KEYS) + "---|")
    for r in rows:
        mark = "**" if r is target else ""
        cells = [mark + _fmt(r.get(k), k) + mark for k in TABLE_KEYS]
        title = (r.get("title") or "").replace("|", "/")
        lines.append(f"| {mark}{r['week']}{mark} | " + " | ".join(cells)
                     + f" | {title} |")
    if d:
        mean_cells = [_fmt(d[k]["mean"], k) if k in d else "—" for k in TABLE_KEYS]
        pct_cells = [(f"{d[k]['pct']:+.0f}%" if k in d else "—") for k in TABLE_KEYS]
        lines.append(f"| x̄ prior {n} | " + " | ".join(mean_cells) + " | |")
        lines.append("| Δ vs x̄ | " + " | ".join(pct_cells) + " | |")
    lines.append("")

    def _lst(keys: list[str]) -> str:
        return ", ".join(f"{k} ({d[k]['pct']:+.0f}%)" for k in keys) or "none"

    lines.append(f"**Up ≥{FLAG_PCT:.0f}% vs trailing mean:** {_lst(dg['up'])}")
    lines.append(f"**Down ≥{FLAG_PCT:.0f}% vs trailing mean:** {_lst(dg['down'])}")
    if dg["best_in_window"]:
        lines.append("**Best in window:** " + ", ".join(dg["best_in_window"]))
    if dg["worst_in_window"]:
        lines.append("**Worst in window:** " + ", ".join(dg["worst_in_window"]))
    if dg["peer_weeks"]:
        lines.append("**Similar-total weeks (±10% ∑分):** "
                     + ", ".join(dg["peer_weeks"]))
    lines.append("")
    lines.append("**What prior weeks said worked / missed:**")
    for r in prior:
        bits = []
        if r.get("win"):
            bits.append(f"win: {r['win']}")
        if r.get("missed"):
            bits.append(f"missed: {r['missed']}")
        if bits:
            lines.append(f"- {r['week']} ({r.get('title') or '—'}): "
                         + "; ".join(bits))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("date", nargs="?", help="any date in the review week (YYYY-MM-DD)")
    ap.add_argument("--weeks", type=int, default=8, help="trailing weeks to compare (default 8)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = ap.parse_args()

    sunday, _ = week_range(args.date)
    label = week_row_label(sunday)
    try:
        rows = fetch_rows()
        target, prior = select_window(rows, label, args.weeks)
    except (RuntimeError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    d = deltas(target, prior)
    dg = digest(target, prior, d)
    if args.json:
        print(json.dumps({"target": target, "prior": prior, "deltas": d,
                          "digest": dg}, ensure_ascii=False, indent=1))
    else:
        print(render_md(target, prior, d, dg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
