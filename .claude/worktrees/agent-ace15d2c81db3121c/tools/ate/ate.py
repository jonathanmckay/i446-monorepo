#!/usr/bin/env python3
"""ate.py — write one /ate food entry to Neon's hcbi sheet as ONE batch.

Why (2026-09-17): the /ate skill used to issue 3 + N separate appends
(name, kcal, protein, each Daily Dozen group) as individual round-trips to
Ix. A Ctrl-C two seconds in left the name and kcal written and everything
else missing, and nothing reported the partial state. Here every cell goes
in a single excel-http ``/batch`` request (the daemon applies the whole
list under its Excel lock), and SIGINT is ignored for the duration of that
one request, so the entry is all-or-nothing from the caller's side. The
tracking-tier bump (hcbi!T) is a separate idempotent read/compare/write
afterwards, so a kill there costs nothing but the bump.

Usage:
    ate.py --name "2 eggs and toast" --kcal 140 --protein 13 \\
           [--groups "grains 1, flax x 2"] [--branch 辰] [--date 9/17] \\
           [--skip-food] [--dry-run] [--json]

--kcal / --protein accept arithmetic ("60+40+250+200"). --groups accepts the
skill's loose syntax: full names or abbrevs, "x 2" / "x2" / trailing count,
comma-separated, optionally wrapped in () or {}. --skip-food writes only
the groups (for topping up an entry whose food/kcal already landed).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import signal
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

TZ = ZoneInfo("America/Los_Angeles")
BRANCHES = "卯辰巳午未申戌"

GROUP_ALIASES = {
    "beans": "bn", "bean": "bn", "bn": "bn",
    "berries": "br", "berry": "br", "br": "br",
    "fruit": "fr", "fruits": "fr", "fr": "fr",
    "cruciferous": "cr", "cr": "cr",
    "greens": "gr", "green": "gr", "gr": "gr",
    "vegetables": "vg", "vegetable": "vg", "veg": "vg", "vg": "vg",
    "flax": "fx", "flx": "fx", "fx": "fx",
    "grains": "g", "grain": "g", "g": "g",
    "nuts": "nt", "nut": "nt", "nt": "nt",
    "spices": "sp", "spice": "sp", "sp": "sp",
    "water": "wtr", "wtr": "wtr",
}


def eval_num(expr: str | None) -> float | None:
    """'60+40+250+200' → 550.0; None/blank → None. Digits and + - * / . only."""
    if expr is None or not str(expr).strip():
        return None
    s = str(expr).strip()
    if not re.fullmatch(r"[\d\s+\-*/.()]+", s):
        raise ValueError(f"not a number/expression: {expr!r}")
    return float(eval(s, {"__builtins__": {}}))  # noqa: S307 — charset-restricted


_NUM = r"\d*\.?\d+"


def parse_groups(spec: str | None) -> list[tuple[str, float]]:
    """'{wtr, flx x 2, vegetables x 2, grain, beans, spice}' →
    [('wtr',1), ('fx',2), ('vg',2), ('g',1), ('bn',1), ('sp',1)].
    Accepts '(…)', '{…}', 'br 3', 'br:3', 'flax x2', 'flax x 2', and the
    count-first form '3 bean' / '.5 wtr' (2026-09-25: as typed in /ate;
    fractional counts are real for water — half a glass is half a glass)."""
    if not spec:
        return []
    s = spec.strip().strip("(){}[]").strip()
    out: list[tuple[str, float]] = []
    for item in filter(None, (x.strip() for x in s.split(","))):
        m = (re.fullmatch(rf"([A-Za-z]+)\s*(?:[:x×]\s*|\s+)?({_NUM})?", item)
             or re.fullmatch(rf"({_NUM})\s*(?:[x×]\s*)?([A-Za-z]+)", item))
        if not m:
            raise ValueError(f"unrecognised group item: {item!r}")
        a, b = m.group(1), m.group(2)
        name, raw_cnt = (a, b) if a[0].isalpha() else (b, a)
        cnt = float(raw_cnt or 1)
        cnt = int(cnt) if cnt.is_integer() else cnt
        name = name.lower()
        if name not in GROUP_ALIASES:
            raise ValueError(f"unknown Daily Dozen group: {name!r}")
        out.append((GROUP_ALIASES[name], cnt))
    return out


def build_appends(name: str | None, kcal: float | None, protein: float | None,
                  groups: list[tuple[str, float]], band: dict, *, skip_food: bool) -> list[dict]:
    """The batch items for one entry: name/kcal/protein into the band's
    triad, then one append per Daily Dozen group."""
    from neon import cols
    name_col, kcal_col, protein_col = band["cols"]
    items: list[dict] = []
    if not skip_food:
        if name:
            items.append({"col": name_col, "value": ", " + name})
        if kcal is not None:
            items.append({"col": kcal_col, "value": f"+{kcal:g}"})
        if protein is not None:
            items.append({"col": protein_col, "value": f"+{protein:g}"})
    for ab, n in groups:
        items.append({"col": cols.daily_dozen_col(ab), "value": f"+{n:g}"})
    return items


def tier_for(cal: float) -> int:
    return 0 if cal <= 0 else 10 + (5 if cal > 800 else 0) + (5 if cal > 1200 else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log one /ate entry to hcbi as a single batch write.")
    ap.add_argument("--name", help="food description (omit with --skip-food)")
    ap.add_argument("--kcal", help="calories, may be arithmetic")
    ap.add_argument("--protein", help="protein grams, may be arithmetic")
    ap.add_argument("--groups", help="Daily Dozen groups, e.g. 'grains 1, flax x 2'")
    ap.add_argument("--branch", help="force a time band glyph (卯辰巳午未申戌)")
    ap.add_argument("--date", help="M/D row (default today)")
    ap.add_argument("--skip-food", action="store_true", help="write only the groups")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    now = _dt.datetime.now(TZ)
    today = args.date or f"{now.month}/{now.day}"
    try:
        kcal, protein = eval_num(args.kcal), eval_num(args.protein)
        groups = parse_groups(args.groups)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.branch and args.branch not in BRANCHES:
        print(f"ERROR: bad branch {args.branch!r}", file=sys.stderr)
        return 2
    if not args.skip_food and not args.name:
        print("ERROR: --name required unless --skip-food", file=sys.stderr)
        return 2

    from neon import cols, excel
    band = cols.hcbi_band_by_branch(args.branch) if args.branch else cols.hcbi_band(now.hour)
    items = build_appends(args.name, kcal, protein, groups, band, skip_food=args.skip_food)
    if not items:
        print("nothing to write", file=sys.stderr)
        return 2
    src = "ate " + (args.name or "groups")[:60]
    if args.dry_run:
        print(json.dumps({"date": today, "band": band["branch"], "appends": items, "src": src},
                         ensure_ascii=False))
        return 0

    # One request, uninterruptible from this side: the daemon applies the
    # whole list under its lock, so a Ctrl-C can no longer leave half an entry.
    prev = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        resp = excel.batch_append("hcbi", items, date=today, src=src)
    finally:
        signal.signal(signal.SIGINT, prev)
    if not resp.get("ok"):
        print(f"ERROR: batch write failed: {resp.get('error') or resp}", file=sys.stderr)
        return 1
    row = resp.get("row") or next((r.get("row") for r in resp.get("results") or []
                                   if isinstance(r, dict) and r.get("row")), "?")

    # Tracking tier: idempotent, so it can run (and be killed) separately.
    bump = ""
    try:
        cal = float(excel.read("hcbi", cols.col("hcbi", "cal"), date=today)["value"] or 0)
        cur = float(excel.read("hcbi", cols.col("hcbi", "0s"), date=today)["value"] or 0)
        tier = tier_for(cal)
        if tier > cur:
            excel.write("hcbi", cols.col("hcbi", "0s"), date=today, value=str(tier), src="ate-tier")
            bump = f" · +{tier - cur:g} tracking pts (now {tier}/20)"
    except Exception as e:  # noqa: BLE001
        bump = f" · tier check failed ({e})"
        cal = None

    if args.json:
        print(json.dumps({"ok": True, "row": row, "band": band["branch"], "date": today,
                          "appends": items, "cal": cal}, ensure_ascii=False))
        return 0
    food = "" if args.skip_food else f"{args.name} ({kcal:g} kcal, {protein:g}g protein) " \
        if kcal is not None and protein is not None else f"{args.name} "
    gtxt = " ".join(f"{ab}{n}" for ab, n in groups)
    cal_txt = f" · cal today {cal:g}" if cal is not None else ""
    print(f"ate {food}→ hcbi {band['branch']} band ({'/'.join(band['cols'])}), row {row}"
          + (f" · groups {gtxt}" if gtxt else "") + cal_txt + bump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
