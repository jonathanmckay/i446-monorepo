#!/usr/bin/env python3
"""
Register a new habit (recurring task) in tasks.json.

Usage:
    register-habit.py <id> --name "..." --category {0n|1n+} \\
        --neon-header <header> --domain <code> \\
        [--toggl-desc <s>] [--toggl-project <code>] [--toggl-tag TAG]... \\
        [--todoist-label <s>] [--points N] [--minutes N] \\
        [--cumulative] [--cumulative-increment N] [--alias S]...

The script:
  1. Validates --domain is in tasks.json
  2. Validates --neon-header exists on the right sheet (0n or 1n+)
  3. Refuses to overwrite an existing id
  4. Writes the entry to tasks.json

It does NOT yet create the Todoist recurring task or add the Excel column —
those are still manual. This is a follow-up; the script will print the exact
Excel header and Todoist content to add.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG = Path.home() / "i446-monorepo/config/tasks.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("id")
    p.add_argument("--name", required=True)
    p.add_argument("--category", choices=["0n", "1n+", "夜neon"], required=True)
    p.add_argument("--neon-header", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--toggl-desc")
    p.add_argument("--toggl-project")
    p.add_argument("--toggl-tag", action="append", default=[])
    p.add_argument("--todoist-label")
    p.add_argument("--points", type=int)
    p.add_argument("--minutes", type=int)
    p.add_argument("--cumulative", action="store_true")
    p.add_argument("--cumulative-increment", type=int)
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--neon-fen-header",
                   help="for 1n+ habits: 0分 column header to append +'1n+'!ref into")
    p.add_argument("--create-excel-col", action="store_true",
                   help="auto-add the Excel column on the relevant sheet if --neon-header doesn't exist yet")
    p.add_argument("--create-todoist", action="store_true",
                   help="auto-create the recurring Todoist task with the right labels/content")
    p.add_argument("--recurrence", default="every day",
                   help="Todoist recurrence string (default: 'every day' for 0n, override for 1n+ etc)")
    args = p.parse_args()

    data = json.loads(CONFIG.read_text())

    if args.id in data["habits"]:
        print(f"  ✗ habit id {args.id!r} already registered", file=sys.stderr)
        return 1

    if args.domain not in data["domains"]:
        print(f"  ✗ domain {args.domain!r} not registered — run register-domain.py first", file=sys.stderr)
        return 1

    sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
    from neon import cols
    sheet = "0n" if args.category == "0n" else "1n+"
    try:
        col = cols.col(sheet, args.neon_header)
    except KeyError:
        # Column doesn't exist yet — auto-create it on Excel + regen neon-cols.json
        col = _next_col(cols, sheet)
        if not args.create_excel_col:
            print(f"  ✗ neon_header {args.neon_header!r} not on {sheet} row 1", file=sys.stderr)
            print(f"    pass --create-excel-col to add column {col} with header {args.neon_header!r}", file=sys.stderr)
            print(f"    or add it manually then re-run this script.", file=sys.stderr)
            return 1
        from neon import excel
        result = excel.write(sheet, col, row=1, value=args.neon_header)
        if not result.get("ok"):
            print(f"  ✗ failed to write Excel header: {result}", file=sys.stderr)
            return 1
        print(f"  ✓ added Excel column {col} with header {args.neon_header!r}")
        # Refresh neon-cols.json so this run + future runs see the new column
        import subprocess
        subprocess.run(["python3", str(Path.home() / "i446-monorepo/scripts/regen-neon-cols.py")],
                       check=True, capture_output=True)
        cols.reload()

    entry: dict = {
        "name": args.name,
        "category": args.category,
        "neon_header": args.neon_header,
        "domain": args.domain,
    }
    if args.toggl_desc:           entry["toggl_desc"] = args.toggl_desc
    if args.toggl_project:        entry["toggl_project"] = args.toggl_project
    if args.toggl_tag:            entry["toggl_tags"] = args.toggl_tag
    if args.todoist_label:        entry["todoist_label"] = args.todoist_label
    elif args.category == "0n":   entry["todoist_label"] = "0neon"
    elif args.category == "1n+":  entry["todoist_label"] = "1neon"
    if args.points is not None:   entry["points_default"] = args.points
    if args.minutes is not None:  entry["minutes_default"] = args.minutes
    if args.cumulative:           entry["cumulative"] = True
    if args.cumulative_increment: entry["cumulative_increment"] = args.cumulative_increment
    if args.alias:                entry["aliases"] = args.alias
    if args.neon_fen_header:      entry["neon_fen_header"] = args.neon_fen_header

    data["habits"][args.id] = entry
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    print(f"  ✓ {args.id} ({args.name}) → {sheet} col {col}")
    print(f"  ✓ domain {args.domain} → 0分 col {cols.col('0分', data['domains'][args.domain]['fen_header'])}")
    if args.toggl_desc:
        print(f"  ✓ toggl: '{args.toggl_desc}' → {args.toggl_project or args.domain}")

    # Optional: auto-create Todoist recurring task
    todoist_content = args.name
    if args.minutes:  todoist_content += f" ({args.minutes})"
    if args.points:   todoist_content += f" [{args.points}]"
    todoist_labels = [entry.get("todoist_label"), args.domain]
    todoist_labels = [l for l in todoist_labels if l]

    if args.create_todoist:
        from todoist import create_task
        recur = args.recurrence
        if args.category == "1n+" and recur == "every day":
            recur = "every week"
        try:
            t = create_task(todoist_content, labels=todoist_labels, due_string=recur)
            print(f"  ✓ Todoist task created: id={t.get('id')} content={t.get('content')!r}")
        except Exception as e:
            print(f"  ✗ Todoist create failed: {e}", file=sys.stderr)
            print(f"    Add manually: '{todoist_content}' labels={todoist_labels} due='{recur}'")
    else:
        print()
        print("Manual follow-ups:")
        print(f"  - In Todoist, create recurring task: '{todoist_content}' labels={todoist_labels}"
              + f" (or re-run with --create-todoist)")
    return 0


def _next_col(cols_mod, sheet: str) -> str:
    """Suggest the next available column letter on `sheet`."""
    headers = cols_mod._cfg()["sheets"][sheet]["headers"]
    used = set(headers.values())
    for i in range(1, 80):
        s = ""
        n = i
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        if s not in used:
            return s
    return "??"


if __name__ == "__main__":
    sys.exit(main())
