#!/usr/bin/env python3
"""
Register a new domain in tasks.json.

Usage:
    register-domain.py <code> --display "..." --fen-header <header> \\
        --toggl-project-id <int> [--cal-color <name>]

Example:
    register-domain.py qz12 --display "Personal finance" \\
        --fen-header 0g --toggl-project-id 152057340

This is the canonical way to add a new domain. Hand-editing tasks.json works
but skips validation; use this instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG = Path.home() / "i446-monorepo/config/tasks.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("code")
    p.add_argument("--display", required=True)
    p.add_argument("--fen-header", required=True,
                   help="row-1 header on 0分 to route this domain's points to")
    p.add_argument("--toggl-project-id", type=int, required=True)
    p.add_argument("--cal-color")
    args = p.parse_args()

    data = json.loads(CONFIG.read_text())
    if args.code in data["domains"]:
        print(f"domain {args.code!r} already registered", file=sys.stderr)
        return 1

    # Validate fen_header is a real column on 0分
    sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
    from neon import cols
    try:
        col = cols.col("0分", args.fen_header)
    except KeyError:
        print(f"  ✗ fen_header {args.fen_header!r} not on 0分 row 1", file=sys.stderr)
        print(f"    add the column to Excel first, then run regen-neon-cols.py", file=sys.stderr)
        return 1

    entry: dict = {
        "display": args.display,
        "fen_header": args.fen_header,
        "toggl_project_id": args.toggl_project_id,
    }
    if args.cal_color:
        entry["cal_color"] = args.cal_color
    data["domains"][args.code] = entry
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"  ✓ {args.code} → 0分 col {col} ({args.fen_header!r})")
    print(f"  ✓ Toggl project {args.toggl_project_id}")
    if args.cal_color:
        print(f"  ✓ cal_color {args.cal_color}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
