"""Canonical Earthly-Branch (地支) 2-hour block schedule and the shared
"has this block started yet?" gate.

Both the -1₦ ritual heatmap (skills/claude-skills/1-1n/make_heatmap.py) and
tg-tui render per-block ritual completion off the build order's `## -1₲`
section. That section PRE-STAMPS every block header for the whole day (☀️
prayer, 🎯 goal, ✅ done, …), so a naive reader credits rituals to blocks that
have not happened yet. A ritual cannot be completed in a block that has not
started, so every reader must drop future blocks.

This module is the single source of truth for that gate. The comparison lived
inline in two readers and shipped wrong in both before being fixed twice; a
third reader should import `is_future_block` rather than re-derive `>` vs `>=`.

The waking day starts at the 04:00 wake (卯). Blocks tile forward in 2-hour
steps. 子 (22:00) is the first sleep block; 丑/寅 (00:00, 02:00) are the
pre-wake small hours. Individual tools keep their own block tables when they
need end-hours or a sleep-block subset for layout; the START schedule and the
future gate are the parts that must agree, so they live here.
"""
from __future__ import annotations  # PEP 604 `X | None` annotations on py3.9

import datetime as dt

# (branch, start_hour) for the full 12-branch day. start_hour is the local
# clock hour the 2-hour block begins.
BLOCK_SCHEDULE = [
    ("卯", 4), ("辰", 6), ("巳", 8), ("午", 10), ("未", 12), ("申", 14),
    ("酉", 16), ("戌", 18), ("亥", 20), ("子", 22), ("丑", 0), ("寅", 2),
]

# branch char -> start hour
BLOCK_START = {branch: hour for branch, hour in BLOCK_SCHEDULE}


def is_future_block(start_hour: int, now: dt.datetime | None = None) -> bool:
    """True if a 2-hour block beginning at local hour ``start_hour`` has not
    started yet relative to ``now``.

    The in-progress block (``start_hour <= now.hour < start_hour + 2``) is NOT
    future — it has begun, so its already-earned rituals are legitimate. Only
    blocks whose start hour is strictly after the current hour are future.

    Hour-of-day based: callers apply it to today's daytime blocks, matching how
    the build order and the 0分 sheet bucket rituals. Pass a timezone-aware
    ``now`` from the caller's own clock; the bare-local default is a fallback.
    """
    if now is None:
        now = dt.datetime.now().astimezone()
    return start_hour > now.hour
