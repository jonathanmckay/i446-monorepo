#!/usr/bin/env python3
"""
Routing dispatcher for /did.

Takes the user's /did input (e.g. "stats i9", "1 s897", "PTC", "30m session with lx")
and emits a JSON routing decision the agent can act on directly. Replaces the
prose-based routing in /did SKILL.md with a deterministic call.

Usage:
    python3 route.py "stats i9"
    python3 route.py "stats i9" --target-date 4/29

Output (JSON):
    {
      "input": "stats i9",
      "target_date": "4/29",
      "step": "0n",                   # 0n | 1n+ | 6 (variable) | unknown
      "habit_id": "stats-i9",
      "habit_name": "stats i9",
      "neon_sheet": "0n",
      "neon_col": "U",
      "domain": "i9",
      "fen_col": "R",
      "todoist_label": "0neon",
      "toggl": {"desc": "stats", "project": "i9", "tags": []},
      "cumulative": false,
      "aliases": []
    }

For one-off Todoist tasks (no registry hit), emits step="unknown" with a hint
to fall through to /did Step 5 (Todoist content matching).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

import registry  # noqa: E402
from neon import cols  # noqa: E402


def _today_md() -> str:
    n = datetime.now()
    return f"{n.month}/{n.day}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", help="User's /did input (one item, no comma-split)")
    p.add_argument("--target-date", default=_today_md())
    args = p.parse_args()

    raw = args.input.strip()

    # Strip @project override if present
    project_override = None
    parts = raw.split()
    if parts and parts[-1].startswith("@"):
        project_override = parts[-1][1:]
        raw = " ".join(parts[:-1]).strip()

    # Strip [N]/(N) annotations from query (used for routing only;
    # actual point/time values are extracted by the agent)
    import re
    annot_re = re.compile(r"\s*[\[\(\{][^\]\)\}]+[\]\)\}]\s*$")
    while True:
        new = annot_re.sub("", raw).strip()
        if new == raw: break
        raw = new

    h = registry.get_habit(raw)
    out: dict = {"input": args.input, "query_after_strip": raw, "target_date": args.target_date}

    if h:
        # Resolve all the columns + projection
        d = registry.get_domain(h.domain)
        try:
            neon_col = registry.resolve_neon_col(h)
        except KeyError:
            neon_col = None
        fen_col = None
        try:
            if d:
                fen_col = cols.col("0分", d.fen_header)
        except KeyError:
            fen_col = None

        out.update({
            "step": h.category,
            "habit_id": h.id,
            "habit_name": h.name,
            "neon_sheet": "0n" if h.category == "0n" else "1n+",
            "neon_col": neon_col,
            "neon_header": h.neon_header,
            "domain": h.domain,
            "fen_col": fen_col,
            "todoist_label": h.todoist_label,
            "toggl": {
                "desc": h.toggl_desc or h.name,
                "project": project_override or h.toggl_project or h.domain,
                "tags": h.toggl_tags,
            },
            "cumulative": h.cumulative,
            "cumulative_increment": h.cumulative_increment,
            "aliases": h.aliases,
            "neon_fen_col": (cols.col("0分", h.neon_fen_header) if h.neon_fen_header else None),
        })
    else:
        out["step"] = "unknown"
        out["hint"] = (
            "Registry has no entry for this input. Fall through to /did Step 5 "
            "(Todoist word-overlap match) for one-off tasks, or Step 6 (variable). "
            "If this should be a recurring habit, register it with "
            "~/i446-monorepo/scripts/register-habit.py"
        )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
