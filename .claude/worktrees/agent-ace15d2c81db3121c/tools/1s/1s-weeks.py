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
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "Neon分v12.2.xlsx"
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
SEP = "\x1f"


def build_script(first_row: int = 2, last_row: int = 400) -> str:
    cols = ", ".join('"%s"' % c for c, _, _ in COLS)
    return f'''
tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    set s to sheet "{SHEET}" of wb
    set out to ""
    repeat with i from {first_row} to {last_row}
        set a to string value of range ("A" & i) of s
        if a is "" then exit repeat
        set rowTxt to ""
        repeat with c in {{{cols}}}
            set rowTxt to rowTxt & (string value of range ((c as string) & i) of s) & "{SEP}"
        end repeat
        set out to out & rowTxt & linefeed
    end repeat
    return out
end tell
'''


def _num(s: str) -> float | None:
    s = (s or "").strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_rows(raw: str) -> list[dict]:
    rows = []
    for line in raw.split("\n"):
        if SEP not in line:
            continue
        parts = line.split(SEP)
        row: dict = {}
        for (_, key, kind), val in zip(COLS, parts):
            row[key] = _num(val) if kind == "num" else val.strip()
        if row.get("week"):
            rows.append(row)
    return rows


def fetch_rows() -> list[dict]:
    proc = subprocess.run([str(IX_OSA)], input=build_script(),
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout.strip() or proc.stderr.strip()
                           or "ix-osa failed")
    return parse_rows(proc.stdout)


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
