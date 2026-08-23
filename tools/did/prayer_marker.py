#!/usr/bin/env python3
"""prayer_marker.py — stamp the صلاة ☀️ marker on the current 地支 block in the
build order.

The ☀️ glyph is read by janus, -2n/inbound, wakeup, and the 1-1n heatmap as
"prayer logged for this block". It is written by did-fast (when ص is logged via
/did) and by /inbound — but NOT by /ص, the standalone prayer counter, which used
to write only the Neon AP column. A prayer logged via /ص therefore never showed
up in janus (janus sources ☀️ exclusively from the build order). This module is
the shared stamp so every prayer-logging path can mark the block consistently.

CLI: `python3 prayer_marker.py` stamps the live build order for the current block.
"""
from __future__ import annotations

import sys as _sys
from datetime import datetime
from pathlib import Path

_sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import daytime  # noqa: E402  shared "now"/"today" resolution — see lib/daytime.py
# Single source of truth for the 地支 block schedule (was independently
# duplicated here, in dtd.sh x2, and in refresh-cache.py with a THIRD,
# inconsistent hour//2 formula — found consolidating for international-
# travel hardening, 2026-08-23). BRANCHES kept as a re-export: existing
# callers/tests reach for prayer_marker.BRANCHES.
from neon_blocks import BRANCHES, current_block_index  # noqa: E402

BUILD_ORDER = Path.home() / "vault/g245/5e-1/build-order.md"
PRAYER_MARKER = "☀️"


def current_block(now: datetime | None = None) -> str:
    """Return the 地支 branch char for the block containing `now` (local time).
    Defaults to the live-resolved active timezone (lib/daytime.py), not a
    naive datetime.now() that silently follows whatever the OS reports."""
    h = (now or daytime.local_now()).hour
    return BRANCHES[current_block_index(h)]


def stamp_prayer_marker(bo_path: Path | str = BUILD_ORDER,
                        now: datetime | None = None) -> bool:
    """Append ☀️ to the current block's header line inside the build order's
    `## -1₲` section, if not already present. Returns True iff the file changed.

    Idempotent and section-scoped (only touches a `- <branch>` header within the
    -1₲ section, never a stray match elsewhere), so it is safe to call from any
    prayer-logging path (/ص, /did ص, /inbound)."""
    bo = Path(bo_path)
    try:
        text = bo.read_text()
    except FileNotFoundError:
        return False
    if "## -1₲" not in text:
        return False
    bname = current_block(now)
    lines = text.split("\n")
    in_section = False
    for i, line in enumerate(lines):
        if line.startswith("## -1₲"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break  # left the -1₲ section without finding the block
        if in_section and line.startswith(f"- {bname}") and PRAYER_MARKER not in line:
            lines[i] = f"{line.rstrip()} {PRAYER_MARKER}"
            bo.write_text("\n".join(lines))
            return True
    return False


if __name__ == "__main__":
    block = current_block()
    if stamp_prayer_marker():
        print(f"prayer marker ☀️ stamped on block {block}")
    else:
        print(f"prayer marker: no change (block {block} already marked or no -1₲ section)")
