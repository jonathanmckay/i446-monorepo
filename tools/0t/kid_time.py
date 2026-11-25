#!/usr/bin/env python3
"""kid_time.py: fill the 0n kid-time columns from Toggl at day close.

JM 2026-11-24. xk20 / xk22 / xk26 (minutes with Theo / Ren / Rori) and xk88
(minutes with LX) used to be written only when a card was closed or a live
timer was finished through janus / did-fast. An entry backfilled afterwards
(/auton156, /-1l, /2n fill, the Toggl app) never reached its column: on
2026-11-18 a 41-minute "中文 #xk20" landed nowhere, and xk20 read 1.

Rule, per day and per column:
  * an entry counts for a kid when it carries that kid's tag (any project), or
    is an xk87 / project-less entry named with a kid nickname (熙熙);
  * weight 1.0 on an xk87 or project-less entry, janus_done.INDEPENDENT_RATE
    elsewhere (the kid worked alone while JM did something else);
  * xk88 = minutes of entries on the xk88 project;
  * credit = min(minutes of entries no client has logged yet,
                 max(0, the day's Toggl total - the cell)).
    The first term keeps an entry janus already logged from being paid twice;
    the second keeps a card closed on its "(N)" estimate from being paid again
    when its entry turns up. It never lowers a cell, so bonuses credited live
    stay, and a second run adds nothing.
  * every entry it counted is written to lib/entry_points.py, which is what
    janus's double-credit guard reads, so a later swipe on that row is refused.

Known limit: an estimate-closed card and a different, backfilled session on the
same day yield the larger of the two, not their sum.

plan() is pure. run() does the reads and writes and never raises.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "lib"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import janus_done  # noqa: E402

KIDS = ("xk20", "xk22", "xk26")
SPOUSE = "xk88"
SRC = "0t kid-time"
SWEEP_DAYS = 3          # yesterday and the two days before: late backfills


def _minutes(e: dict) -> float:
    return max(0, int(e.get("duration") or 0)) / 60.0


def _norm(desc) -> str:
    return " ".join(janus_done._ANNOT_RE.sub(" ", str(desc or "")).split()).lower()


def attribute(e: dict, code_of, kid_alias, session_names) -> tuple[dict, bool]:
    """({column: weighted minutes}, untagged teaching session?) for one entry."""
    mins = _minutes(e)
    if mins <= 0:
        return {}, False
    code = (code_of(e.get("project_id")) or "").lower()
    if code == SPOUSE:
        return {SPOUSE: mins}, False
    tags = [str(t).lower() for t in (e.get("tags") or [])]
    kids = [k for k in KIDS if k in tags]
    if not kids and code in ("", "xk87"):
        nick = kid_alias(_norm(e.get("description")))
        if nick in KIDS:
            kids = [nick]
    if kids:
        w = janus_done.kid_weight(code)
        # Both kids present = each kid's column gets the minutes, as the live
        # path does (primary kid + the other kid's tag as a secondary).
        return {k: mins * w for k in kids}, False
    return {}, code == "xk87" and _norm(e.get("description")) in session_names


def plan(entries: list[dict], logged: set, current: dict, code_of, kid_alias,
         session_names) -> dict:
    """{"credits": {col: minutes to add}, "totals": {col: day total},
    "counted": {entry id: minutes}, "untagged": [...]}. `current` maps each
    column to its cell's number; `logged` holds ids a client already logged."""
    totals = {c: 0.0 for c in KIDS + (SPOUSE,)}
    fresh = dict(totals)
    counted: dict = {}
    untagged: list = []
    for e in entries:
        eid = str(e.get("id"))
        got, teach = attribute(e, code_of, kid_alias, session_names)
        if teach and eid not in logged and round(_minutes(e)) >= 1:
            untagged.append({"id": e.get("id"), "desc": e.get("description"),
                             "minutes": round(_minutes(e))})
        for col, m in got.items():
            totals[col] += m
            if eid not in logged:
                fresh[col] += m
                counted[eid] = round(counted.get(eid, 0) + m)
    credits = {}
    for col in totals:
        room = round(totals[col]) - round(float(current.get(col) or 0))
        add = min(round(fresh[col]), max(0, room))
        if add > 0:
            credits[col] = add
    return {"credits": credits, "totals": {c: round(v) for c, v in totals.items() if v},
            "counted": counted, "untagged": untagged}


def _cell_number(resp) -> float:
    try:
        return float((resp or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def run(day: dt.date, entries: list[dict], dry: bool = False) -> dict:
    """Reconcile one closed day. `entries` are that local day's Toggl entries."""
    try:
        from neon import cols as neon_cols, excel as neon_excel
        from neon.rates import VARIABLE_SESSION_BONUS
        import entry_points
        from mcp.toggl_server.config import PROJECT_NAMES, kid_alias
    except Exception as e:   # noqa: BLE001
        return {"error": f"imports: {e}"}
    md = f"{day.month}/{day.day}"
    try:
        logged = {str(k) for k, v in entry_points.logged_for_day(day).items() if v is not None}
        letters = {c: neon_cols.col("0n", c) for c in KIDS + (SPOUSE,)}
        current = {c: _cell_number(neon_excel.read("0n", letters[c], date=md)) for c in letters}
    except Exception as e:   # noqa: BLE001
        return {"error": f"read: {e}"}
    p = plan(entries, logged, current, PROJECT_NAMES.get, kid_alias, set(VARIABLE_SESSION_BONUS))
    out = {"date": md, "totals": p["totals"], "cells": {c: round(v) for c, v in current.items() if v},
           "credits": p["credits"], "untagged": p["untagged"]}
    if dry:
        out["dry_run"] = True
        return out
    written = {}
    for col, add in p["credits"].items():
        res = neon_excel.append("0n", letters[col], date=md, value=f"+{add}", src=SRC)
        if not (res or {}).get("ok"):
            out["error"] = f"append {col} failed: {res}"
            break
        written[col] = add
    out["written"] = written
    # Mark what was counted even when the cell already covered it, so janus
    # refuses a second log of the same row. Not on a failed write: a rerun
    # must still see those entries as unlogged.
    if "error" not in out and p["counted"]:
        entry_points.record(day, p["counted"], SRC)
    return out
