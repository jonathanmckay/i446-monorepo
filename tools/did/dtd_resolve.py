#!/usr/bin/env python3
"""Resolve an fzf-selected task id back to its canonical Todoist content.

dtd displays short (Haiku) names but every action binding must operate on the
real task text (to name the Toggl timer, resolve the project, and match the
Todoist task for completion/defer/delete). Each fzf line carries the task id as
a hidden field; bindings pass that id here to recover the canonical content.

Defensive: if the arg is NOT a bare id (e.g. a legacy code path passed the
visible line), echo it back unchanged so behaviour degrades gracefully instead
of breaking.

Usage: dtd_resolve.py <cache_file> <id_or_text>
"""
import json
import re
import sys


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    cache_file, arg = sys.argv[1], sys.argv[2]
    arg = arg.strip()
    # Strip ANSI + dtd row prefixes in case a caller passes a visible line.
    plain = re.sub(r"\033\[[0-9;]*m", "", arg)
    plain = re.sub(r"^↻ ", "", plain)
    plain = re.sub(r"^▶ [^·]* · ", "", plain)

    # Todoist v1 ids are opaque alphanumeric strings (e.g. 6gqQ59G5HHVRQR3R),
    # never containing spaces. Try an exact id match first; if none, the arg was
    # task text from a legacy path, so echo it back cleaned.
    if " " not in plain:
        try:
            d = json.load(open(cache_file))
            for v in d.values():
                if isinstance(v, list):
                    for t in v:
                        if isinstance(t, dict) and str(t.get("id")) == plain:
                            print(t.get("content", ""))
                            return 0
        except Exception:
            pass

    print(plain)  # not a known id → fall back to the (cleaned) text
    return 0


if __name__ == "__main__":
    sys.exit(main())
