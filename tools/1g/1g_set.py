#!/usr/bin/env python3
"""1g_set.py: write a domain's weekly goals into the Neon `1g` tab.

    1g_set.py <domain> <goals.json> [--dry-run]

goals.json = [{"text": "...", "fen": 30, "focus": 60}, ...] in order. Each
goal lands on the next row under the domain's col-A header (D text, E 分,
F focus bonus); leftover rows of the section are blanked (D..G).

Replaces the /1g skill's raw ix-osa AppleScript (Neon-off-Excel step 2c
deferred item, 2026-10-30). Writes go through the excel-http daemon, so each
cell is journaled. The section scan is a read-only ix-osa bulk read. Two bugs
of the old script are fixed here:
  * the last section (s897) had no next header, so its clear loop ran to row
    50 and blanked the totals row (F46 =SUM(...), G46 =I46/F46). A section
    now also ends at the first row holding a formula in D..G, and the clear
    never touches a formula cell.
  * headers were matched after trimming trailing space only, so A34's
    " xk87" (leading space) never matched. Headers are now fully stripped.
More goals than the section has rows is an error instead of overwriting the
next section's header.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel as neon_excel  # noqa: E402

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "1g"
SCAN_ROWS = 60
COLS = ("A", "D", "E", "F", "G")          # what the scan returns per row
CLEAR_COLS = ("D", "E", "F", "G")
SECTION_ALIAS = {"m5x2": "m5c7"}          # col A spells m5x2 as m5c7


def scan_script() -> str:
    return "\n".join([
        'tell application "Microsoft Excel"',
        f'  set ws to sheet "{SHEET}" of workbook "{WORKBOOK}"',
        f'  set f to formula of range "A1:G{SCAN_ROWS}" of ws',
        '  set out to ""',
        '  repeat with i from 1 to count of f',
        '    set rw to item i of f',
        '    set ln to ""',
        '    repeat with j in {1, 4, 5, 6, 7}',
        '      set ln to ln & (item (contents of j) of rw) & (character id 31)',
        '    end repeat',
        '    set out to out & ln & (character id 30)',
        '  end repeat',
        '  return out',
        'end tell',
    ])


def parse_scan(raw: str) -> dict[int, dict[str, str]]:
    """{row: {"A": formula, "D": ..., "G": ...}} for rows 1..SCAN_ROWS."""
    layout = {}
    for i, line in enumerate(raw.split("\x1e"), start=1):
        if i > SCAN_ROWS:
            break
        vals = (line.split("\x1f") + [""] * len(COLS))[:len(COLS)]
        layout[i] = dict(zip(COLS, vals))
    return layout


def read_layout() -> dict[int, dict[str, str]]:
    p = subprocess.run([str(IX_OSA)], input=scan_script(), capture_output=True,
                       text=True, timeout=90)
    out = p.stdout or ""
    if p.returncode != 0 or out.startswith("ERROR"):
        raise RuntimeError(f"1g scan failed: {(out or p.stderr).strip()[:300]}")
    return parse_scan(out.rstrip("\n"))


def _is_formula(v: str) -> bool:
    return str(v).startswith("=")


def section_bounds(layout: dict, section: str) -> tuple[int, int]:
    """(header_row, stop_row): goal rows are header+1 .. stop-1. A section
    ends at the next non-blank col A, or at the first row holding a formula
    in D..G (the totals row under the last section), or past the scan."""
    rows = sorted(layout)
    header = next((r for r in rows if layout[r]["A"].strip() == section), 0)
    if not header:
        raise LookupError(f"section {section!r} not found in {SHEET} col A")
    for r in rows:
        if r <= header:
            continue
        cells = layout[r]
        if cells["A"].strip() or any(_is_formula(cells[c]) for c in CLEAR_COLS):
            return header, r
    return header, rows[-1] + 1


def plan(layout: dict, domain: str, goals: list[dict]) -> dict:
    """Pure: {section, header, rows: {row: [writes]}} for the goals."""
    section = SECTION_ALIAS.get(domain, domain)
    header, stop = section_bounds(layout, section)
    capacity = stop - header - 1
    if len(goals) > capacity:
        raise ValueError(f"{len(goals)} goals but section {section} has only "
                         f"{capacity} rows ({header + 1}-{stop - 1})")
    rows: dict[int, list[dict]] = {}
    for i, g in enumerate(goals):
        text = str(g.get("text", "")).strip()
        if not text:
            raise ValueError(f"goal {i + 1} has no text")
        rows[header + 1 + i] = [
            {"col": "D", "value": text, "mode": "text"},
            {"col": "E", "value": _num(g.get("fen", 0))},
            {"col": "F", "value": _num(g.get("focus", 0))},
        ]
    for r in range(header + 1 + len(goals), stop):
        clears = [{"col": c, "value": "", "mode": "text"} for c in CLEAR_COLS
                  if layout.get(r, {}).get(c, "") != "" and not _is_formula(layout[r][c])]
        if clears:
            rows[r] = clears
    return {"section": section, "header": header, "stop": stop, "rows": rows}


def _num(v):
    n = float(v or 0)
    return int(n) if n.is_integer() else n


def apply(p: dict, domain: str) -> list[str]:
    """One /batch_write per row (the daemon addresses one row per call)."""
    errors = []
    for row, items in sorted(p["rows"].items()):
        risky = any(str(it["value"]).startswith("=") for it in items)
        r = neon_excel.batch_write(SHEET, items, row=row, src=f"1g {domain}",
                                   fallback=not risky)
        if not r.get("ok"):
            errors.append(f"row {row}: {r.get('error') or r}")
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="write weekly goals to the Neon 1g tab")
    ap.add_argument("domain")
    ap.add_argument("goals", help='JSON file: [{"text","fen","focus"}, ...]')
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    goals = json.loads(Path(a.goals).read_text(encoding="utf-8"))
    try:
        p = plan(read_layout(), a.domain, goals)
    except (LookupError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return 0
    errors = apply(p, a.domain)
    if errors:
        print("ERROR: " + "; ".join(errors), file=sys.stderr)
        return 1
    first, n = p["header"] + 1, len(goals)
    print(f"OK: wrote {n} goals to {SHEET} rows {first}-{first + n - 1} ({p['section']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
