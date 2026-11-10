#!/usr/bin/env python3
"""The janus habit strip's data rules, shared by the phone clients.

tools/tg/janus.py renders the two-line strip under its header from an
AppleScript read of the 0n and hcbi sheets; tools/janus/mobile.py (the web
mirror and the iOS app's /api/timeline feed) builds the same strip from the
excel-http daemon (2026-11-10, JM: "for janus CLI and iOS ... only show the
-1n tasks that are not done, as well as ... the total number of calories
eaten today on the hcb line"). The terminal keeps its own, test-pinned parse
loop in fetch_habits_today; this module is the phone-side twin of those
rules, and lib/test_habit_strip.py holds a parity test that feeds both the
same rows.

Pure: no I/O, no Excel. Callers hand in the header row and today's row.
"""
from __future__ import annotations

# Mirrors janus.py's HABIT_COLOR_DOMAIN / _HABIT_STRIP_SKIP / HABIT_YTD_CELLS
# keys. A habit not listed here still renders, in a neutral chip.
HABIT_COLOR_DOMAIN = {
    "睡觉": "睡觉", "cpap": "hcb", "wake up": "hcb", "i444": "i444", "i447": "i447",
    "charge": "infra", "tmrw": "g245", "2nd hci": "hci", "1st hci": "hci",
    "ibx s897": "s897", "新闻": "hcmc", "词汇": "hcmc", "night hcmc": "hcmc",
    "0t": "n156", "₦156": "n156", "0l": "g245", "0g": "g245",
    "stats i9": "i9", "notes": "i9", "ibx i9": "i9", "push": "g245", "teams": "i9",
    "slack github": "i9", "slack m5x2": "m5x2", "ibx m5x2": "m5x2", "m5x2 stats": "m5x2",
    "早餐": "hcb", "hiit": "hcb", "问学": "家", "xk20": "xk88", "xk22": "xk88",
    "xk26": "xk88", "qft": "hcm", "xk88": "xk88", "nvc + e": "hcm", "ص": "hcm",
    "o314": "hcm", "冥想": "hcm", "其他人": "hcm",
}
HABIT_STRIP_SKIP = {
    "mee", "日", "n color", "⎣∀clr", "#",
    # User-requested exclusions (2026-07-20): tracked in Neon, not wanted here.
    "词汇", "slack github", "问学",
}
# Minimum-commitment habits: a ±N YTD standing (0n!AQ375:AS375), never a
# daily done/pending chip.
HABIT_YTD_NAMES = ("o314", "冥想", "其他人")
HABIT_YTD_COLORS = {"o314": "#7c4dff", "冥想": "#aa00ff", "其他人": "#673ab7"}
PRAYER = "ص"

# hcbi Daily Dozen categories, E:O header order; the annual block
# (hcbi!E384:P385) splits them across two rows, odd columns on the first.
DAILY_DOZEN_ORDER = ["bn", "br", "fr", "cr", "gr", "vg", "fx", "g", "nt", "sp", "wtr"]
DAILY_DOZEN_COLORS = {
    "bn": "#880e4f", "br": "#ff1744", "fr": "#ff4136", "cr": "#1b5e20", "gr": "#00e676",
    "vg": "#ff6d00", "fx": "#fec4a4", "g": "#ffd600", "nt": "#8b3a00", "sp": "#e65100",
    "wtr": "#2979ff",
}


def _num(s) -> float | None:
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def parse_habit_row(headers: list, values: list) -> dict:
    """Today's 0n row -> {"pending": [name], "done": [(name, value)],
    "prayer": float | None}. The terminal's rules (fetch_habits_today):
    blank or unparseable = pending; an EXPLICIT 0 = marked N/A today, off
    both lists; ص is a counter, not a habit; bookkeeping/skip-listed/YTD
    columns never appear. A blank ص cell is 0 prayers, not unknown."""
    pending: list[str] = []
    done: list[tuple[str, float]] = []
    prayer: float | None = 0.0
    for h, v in zip(headers, values):
        name = str(h or "").strip()
        val = str(v if v is not None else "").strip()
        if not name:
            continue
        if name == PRAYER:
            p = _num(val)
            if p is not None:
                prayer = p
            continue
        if name.lower() in HABIT_STRIP_SKIP or name.lower() in HABIT_YTD_NAMES:
            continue
        if not val:
            pending.append(name)
            continue
        f = _num(val)
        if f is None:
            pending.append(name)
            continue
        if f == 0:
            continue
        done.append((name, f))
    return {"pending": pending, "done": done, "prayer": prayer}


def kcal_int(raw) -> int | None:
    """hcbi!U as whole kcal; None when blank/unreadable, so the chip stays
    off rather than showing a 0 the sheet doesn't hold."""
    v = _num(raw)
    return int(round(v)) if v is not None else None


def dozen_behind(row1: list, row2: list) -> list[tuple[str, float]]:
    """[(category, value)] for every Daily Dozen category currently BEHIND
    (negative) in the annual block's two rows (E:O, the value sits on
    whichever row is non-blank for that column)."""
    out = []
    for i, key in enumerate(DAILY_DOZEN_ORDER):
        v1 = str(row1[i]).strip() if i < len(row1) and row1[i] is not None else ""
        v2 = str(row2[i]).strip() if i < len(row2) and row2[i] is not None else ""
        v = _num(v1 or v2)
        if v is not None and v < 0:
            out.append((key, v))
    return out


def strip_chips(parsed: dict, *, colors: dict, ytd: dict | None = None,
                dozen: list | None = None, hcb_total=None, kcal=None) -> dict:
    """The two lines as [{"text", "bg"}] chips, the terminal's order:
    line 1 = pending names (domain fill), YTD standings at or below zero,
    "ص N"; line 2 = Daily Dozen categories behind, "hcb ±N" (always, signed),
    "kcal N". `colors` is the client's PROJECT_COLORS."""
    def dom(name):
        return colors.get(HABIT_COLOR_DOMAIN.get(name.lower(), ""), "#444444")
    line1 = [{"text": n, "bg": dom(n)} for n in parsed.get("pending", [])]
    for name, v in (ytd or {}).items():
        if v is not None and v <= 0:
            line1.append({"text": f"{name} {v:+g}", "bg": HABIT_YTD_COLORS.get(name, "#7c4dff")})
    if parsed.get("prayer") is not None:
        line1.append({"text": f"{PRAYER} {parsed['prayer']:g}", "bg": dom(PRAYER)})
    line2 = [{"text": f"{v:g}", "bg": DAILY_DOZEN_COLORS.get(k, "#444444")} for k, v in (dozen or [])]
    if hcb_total is not None:
        line2.append({"text": f"hcb {int(round(hcb_total)):+d}", "bg": colors.get("hcb", "#f81d78")})
    if kcal is not None:
        line2.append({"text": f"kcal {kcal:d}", "bg": colors.get("hcb", "#f81d78")})
    return {"line1": line1, "line2": line2}
