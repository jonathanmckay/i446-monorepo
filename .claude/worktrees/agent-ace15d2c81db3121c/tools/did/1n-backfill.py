#!/usr/bin/env python3
"""1n-backfill.py — /1n: mark a weekly 1₦+ habit done for a PAST week, credit TODAY.

Usage:
    1n-backfill.py <habit> <week> [minutes] [--points N] [--domain D]
                   [--credit-only] [--dry-run]

    <week>     9.5 | 10.1 (the 1n+ col-B fiscal label) | last week
               | any date in that Sun-Sat week: 9/28, 2026-09-28, yesterday.
               X.Y with Y in 1-5 is a week label; write dates as M/D or ISO.
               Must be a PAST week: this week's card is still open, use /did.
    [minutes]  what the week cell records (default 1). Variable habits
               (family, s897, 业写, ...) need it: their points are per-minute.
    --points N credit N instead of the habit's row-5 expected points.
    --domain D 0分 domain (i9, m5x2, hcb, ...) for headers with no mapped
               column (1 cal, groceries, nails, ...).
    --credit-only  the week cell is ALREADY marked (by hand, or by a run whose
               0分 half failed): skip the cell write and only credit today.
    --dry-run  read everything, write nothing, print the plan.

Why simpler than /0n: 1n+ week cells feed no 0分 formula. /did credits a
weekly habit by an explicit 0分 append on the completion day (row-5 expected
points for standard habits, base+rate×minutes for variable ones), so a past
week has nothing to back out. This marks that week's cell (same AppleScript
as did-fast's step 4b, pointed at the past week's row) and appends the points
to TODAY's 0分 row through the excel-http daemon (ledger src "1n backfill
<habit> <week>"). 2026-10-05.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"
_IX_SPEC = importlib.util.spec_from_file_location("ix_osa", _IX_PATH)
_ix_mod = importlib.util.module_from_spec(_IX_SPEC)
sys.modules["ix_osa"] = _ix_mod
_IX_SPEC.loader.exec_module(_ix_mod)
ix_run = _ix_mod.run

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel as neon_excel  # noqa: E402
from neon import weeks as neon_weeks  # noqa: E402
import daytime  # noqa: E402

_DID_PATH = Path(__file__).resolve().parent / "did-fast.py"
WORKBOOK = "Neon分v12.2.xlsx"
WEEK_LABEL_RE = re.compile(r"^(\d{1,2})\.([1-5])$")


def _did():
    spec = importlib.util.spec_from_file_location("did_fast_for_1n", _DID_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_for_1n"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_0n_backfill():
    spec = importlib.util.spec_from_file_location(
        "zero_n_backfill", Path(__file__).resolve().parent / "0n-backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- pure helpers (unit-tested) ---------------------------------------------
def parse_week(raw: str, today: date) -> tuple[str, date]:
    """→ (fiscal label, that week's Sunday). Labels are searched backwards
    from this week (a label names exactly one Sunday per year)."""
    s = raw.strip().lower()
    this_sun = neon_weeks.week_sunday(today)
    if s in ("last week", "last", "lastweek"):
        d = this_sun - timedelta(days=7)
        return neon_weeks.fiscal_week_label(d), d
    if s in ("this week", "this"):
        return neon_weeks.fiscal_week_label(this_sun), this_sun
    if WEEK_LABEL_RE.match(s):
        for k in range(0, 60):
            d = this_sun - timedelta(days=7 * k)
            if neon_weeks.fiscal_week_label(d) == s:
                return s, d
        raise ValueError(f"no week labelled {s!r} in the last 60 weeks")
    if s == "yesterday":
        d = today - timedelta(days=1)
    elif len(s) == 10 and s[4] == "-":
        d = date.fromisoformat(s)
    else:
        sep = "/" if "/" in s else "."
        m, dd = (int(p) for p in s.split(sep))
        y = today.year - (1 if (m, dd) > (today.month, today.day) else 0)
        d = date(y, m, dd)
    sun = neon_weeks.week_sunday(d)
    return neon_weeks.fiscal_week_label(sun), sun


def resolve_fen_col(df, key: str, domain: str | None) -> str | None:
    """0分 column for a normalized 1n+ header. The map is keyed by raw names
    ("1 -2g") while lookups are header_normalize()d ("1 2g"), so normalize
    both sides here."""
    if domain:
        return df.LABEL_TO_0FEN.get(domain.lower())
    norm_map = {df.header_normalize(k): v for k, v in df.ONENEON_TO_0FEN.items()}
    return norm_map.get(key) or df.LABEL_TO_0FEN.get(key.split()[-1])


def is_marked(formula: str) -> bool:
    txt = (formula or "").strip().lstrip("=")
    if txt in ("", "0", "0.0", "0+0"):
        return False
    return True


def read_script(col: str, week_mw: str) -> str:
    return f'''tell application "Microsoft Excel"
    set ws to sheet "1n+" of workbook "{WORKBOOK}"
    set weekRow to 0
    repeat with r from 4 to 100
        if (string value of range ("B" & r) of ws) = "{week_mw}" then
            set weekRow to r
            exit repeat
        end if
    end repeat
    if weekRow = 0 then return "ERROR: week {week_mw} not found in 1n+"
    set f to (formula of range ("{col}" & weekRow) of ws) as text
    set e to (string value of range ("{col}5") of ws) as text
    set t to (character id 9)
    return "ROW" & t & weekRow & linefeed & "CELL" & t & f & linefeed & "E" & t & e
end tell'''


def parse_read(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("habit")
    ap.add_argument("week")
    ap.add_argument("minutes", nargs="?", type=int)
    ap.add_argument("--points", type=int)
    ap.add_argument("--domain")
    ap.add_argument("--credit-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    today = daytime.today()
    try:
        week_mw, week_sun = parse_week(a.week, today)
    except ValueError as e:
        raise SystemExit(f"ERROR: week {a.week!r}: {e}")
    if week_sun >= neon_weeks.week_sunday(today):
        raise SystemExit(f"ERROR: {week_mw} is this week; its card is still open, use /did")

    df = _did()
    raw = df.ONENEON_ALIASES.get(a.habit.lower(), a.habit.lower())
    key = df.header_normalize(raw)
    headers = {df.header_normalize(k): v for k, v in df.load_headers()["1n"].items()}
    if key not in headers:
        headers = {df.header_normalize(k): v for k, v in df.refresh_headers()["1n"].items()}
    if key not in headers:
        close = [h for h in headers if key in h or h in key]
        raise SystemExit(f"ERROR: {a.habit!r} is not a 1n+ header"
                         + (f"; close: {close}" if close else f"; headers: {sorted(headers)}"))
    col = headers[key]
    fen_col = resolve_fen_col(df, key, a.domain)
    if not fen_col or ":" in fen_col:
        raise SystemExit(f"ERROR: no 0分 column for {key!r}; pass --domain "
                         "(i9, m5x2, g245, hcmc, hcm, hcb, xk87, s897)")

    is_var = key in df.VARIABLE_1N
    threshold = df.THRESHOLD_1N.get(key)
    minutes = a.minutes

    r = ix_run(read_script(col, week_mw), timeout=30.0)
    if r.returncode != 0 or r.stdout.startswith("ERROR"):
        raise SystemExit(f"ERROR: excel read failed: {r.stderr or r.stdout}")
    rd = parse_read(r.stdout)
    pre = rd.get("CELL", "")
    row5 = rd.get("E", "").strip()

    # Points to credit today, and what the week cell records.
    if a.points is not None:
        points: int | str = a.points
    elif is_var:
        if threshold:
            if minutes is None or minutes < threshold["min"]:
                raise SystemExit(f"ERROR: {key} needs minutes >= {threshold['min']}")
        elif minutes is None and not df.VARIABLE_1N_BASES.get(key):
            raise SystemExit(f"ERROR: {key} is per-minute ({row5}); give minutes")
        points = df.variable_1n_points(key, minutes or 0)
    else:
        try:
            float(row5)
        except ValueError:
            raise SystemExit(f"ERROR: {key} row 5 is {row5!r}, not a number; pass --points")
        points = f"+'1n+'!{col}5"
    cell_value = (points if threshold else (minutes or 1))
    pts_display = points if isinstance(points, int) else row5

    out = {"habit": key, "1n_col": col, "week": week_mw, "week_of": week_sun.isoformat(),
           "row": rd.get("ROW"), "pre_cell": pre, "0分_col": fen_col,
           "points": pts_display, "credit_date": today.isoformat(),
           "src": f"1n backfill {key} {week_mw}"}

    marked = is_marked(pre)
    if marked and not (is_var or a.credit_only):
        out.update({"already_credited": True, "points_awarded": 0,
                    "message": (f"{key} already marked for {week_mw} ({pre}); no points "
                                "awarded (--credit-only if its points never reached 0分)")})
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if a.credit_only and not marked:
        raise SystemExit(f"ERROR: --credit-only but {col}{rd.get('ROW')} is empty; run without it")

    if a.dry_run:
        out.update({"dry_run": True, "would_write_cell": None if a.credit_only else cell_value})
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 1. mark the week cell (same builder did-fast uses; appends for variable)
    if not a.credit_only:
        item = df.ParsedItem(raw=key, name=key)
        w = df.RouteResult(item=item, step="1n", col_letter=col, fen_col=fen_col,
                           write_value=cell_value, is_variable_1n=is_var)
        res = ix_run(df.build_1n_script([w], week_mw), timeout=30.0)
        if res.returncode != 0 or not res.stdout.startswith("OK"):
            raise SystemExit(f"ERROR: 1n+ write failed: {res.stderr or res.stdout}")
        out["cell_written"] = res.stdout.splitlines()[0]

    # 2. credit today
    today_md = f"{today.month}/{today.day}"
    val = points if isinstance(points, str) else (f"+{points}" if points >= 0 else str(points))
    resp = neon_excel.batch_append("0分", [(fen_col, val)], date=today_md, src=out["src"])
    out["credit"] = {"date": today_md, "ok": bool(resp.get("ok")), "row": resp.get("row"),
                     "error": resp.get("error")}
    if not resp.get("ok"):
        out["recovery"] = (f"week cell marked but today's credit failed. Rerun: 1n-backfill.py "
                           f"{key!r} {week_mw} --credit-only"
                           + (f" --points {points}" if isinstance(points, int) else ""))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    # 3. save + dashboard (same refresher /0n and /0t use)
    try:
        out["dashboard"] = _load_0n_backfill().save_and_refresh()
    except Exception as e:  # noqa: BLE001
        out["dashboard"] = f"ERROR: {e}"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
