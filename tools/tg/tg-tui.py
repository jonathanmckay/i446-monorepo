#!/usr/bin/env python3
"""tg-tui — narrow Toggl + Calendar TUI.

Sits next to dtd in the right half of a terminal. Three jobs:
  1. Switch / stop the running Toggl entry (press `c`, type as if /tg)
  2. Show ±2h around now in 15-min detail (Toggl past + gcal future)
  3. Show rest-of-day overview (morning = Toggl, evening = gcal)

Keys: c=change  s=stop  r=refresh  j/k=scroll detail  q=quit
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Toggl API import
sys.path.insert(0, str(Path("~/i446-monorepo").expanduser()))
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

# Load Toggl API key from claude.json (mirrors toggl_cli behaviour)
if not os.environ.get("TOGGL_API_KEY"):
    try:
        import json
        cj = json.loads(Path("~/.claude.json").expanduser().read_text())
        os.environ["TOGGL_API_KEY"] = (
            cj.get("mcpServers", {})
              .get("toggl_server", {})
              .get("env", {})
              .get("TOGGL_API_KEY", "")
        )
    except Exception:
        pass

from mcp.toggl_server import toggl_api  # noqa: E402
from mcp.toggl_server.config import PROJECT_NAMES  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from prompt_toolkit import Application  # noqa: E402
from prompt_toolkit.buffer import Buffer  # noqa: E402
from prompt_toolkit.key_binding import KeyBindings  # noqa: E402
from prompt_toolkit.layout import Layout, Window, HSplit  # noqa: E402
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl  # noqa: E402
from prompt_toolkit.layout.dimension import Dimension  # noqa: E402
from prompt_toolkit.styles import Style  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path("~/i446-monorepo/lib").expanduser()))
import gcal_client  # noqa: E402
from neon import excel as neon_excel  # noqa: E402
import outlook_client  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
TG_FAST = str(Path("~/i446-monorepo/tools/tg/tg-fast.py").expanduser())
WIDTH_HINT = 50  # informs collapse logic, not strict
DESC_MAX = 24  # max display width for task/event descriptions
# Earthly branch blocks (name, start_hour, end_hour inclusive)
BLOCKS = [
    ("卯", 4, 5),
    ("辰", 6, 7),
    ("巳", 8, 9),
    ("午", 10, 11),
    ("未", 12, 13),
    ("申", 14, 15),
    ("酉", 16, 17),
    ("戌", 18, 19),
    ("亥", 20, 21),
    ("子", 22, 23),
]


def hour_to_block(h: int) -> tuple[str, int, int] | None:
    """Return (name, start_hour, end_hour) for the block containing hour h."""
    for name, sh, eh in BLOCKS:
        if sh <= h <= eh:
            return name, sh, eh
    return None


def prev_block(h: int) -> tuple[str, int, int] | None:
    """Return the block before the one containing hour h."""
    for i, (name, sh, eh) in enumerate(BLOCKS):
        if sh <= h <= eh:
            return BLOCKS[i - 1] if i > 0 else None
    return None


def next_block(h: int) -> tuple[str, int, int] | None:
    """Return the block after the one containing hour h."""
    for i, (name, sh, eh) in enumerate(BLOCKS):
        if sh <= h <= eh:
            if i + 1 < len(BLOCKS):
                return BLOCKS[i + 1]
            return None
    # Before first block or after last: return first/None
    if h < BLOCKS[0][1]:
        return BLOCKS[0]
    return None
SLOT_MIN = 15
BUILD_ORDER = Path.home() / "vault/g245/-1₦ , 0₦ - Neon {Build Order}.md"
BLOCK_EMOJIS = ["☀️", "📧", "🎯", "⏱️", "✅", "⏰"]

# Project code lookup (id -> code) using inverse of PROJECT_MAP if present
PROJECT_CODE = {}
try:
    from mcp.toggl_server.config import PROJECT_MAP  # type: ignore
    PROJECT_CODE = {v: k for k, v in PROJECT_MAP.items()}
except Exception:
    pass

# Neon palette → vault/i447/neon-color-pallette.md
PROJECT_COLORS = {
    "g245": "#00e676",   # Matrix
    "epcn": "#00bfa5",   # Miami Vice
    "s897": "#1b5e20",   # Emerald Shadow
    "hcmc2": "#ffd600",  # Lightning
    "xk87": "#fd6c1d",   # Tangerine Dream
    "xk88": "#e65100",   # Molten
    "hci":  "#63ede0",   # Vaporwave
    "i9":   "#2979ff",   # Electric Blue
    "n156": "#1249b4",   # Sapphire
    "hcmc": "#0d3b66",   # Deep Sea
    "m5x2": "#d50032",   # Crimson
    "hcb":  "#f81d78",   # Bubblegum Shock
    "hcbp": "#ff4081",   # Flamingo
    "infra":"#9e9e9e",   # Concrete
    "i444": "#616161",   # Graphite
    "i447": "#a89c8a",   # Shadow (lightened from #303030 for readability on dark)
    "睡觉": "#666666",    # Abyss (lightened from #0a0a0a)
    "hcm":  "#aa00ff",   # Purple Haze (no map entry; reasonable fit for hcm parent)
    "hcmp": "#7c4dff",   # Lavender Lightning
    "hcmr": "#bda6ff",   # Weak-sauce Purple
    "家":   "#ff4136",    # Ferrari (family)
}


CALENDAR_PROJECT_MAP = {
    "m5x2 Cal": "m5x2",
    "3494 House": "m5x2",
    "CAIS School": "xk87",
    "Habits": "hcm",
    "lx@m5c7.com": "xk88",
    "lxu888": "xk88",
    "Calendar": "infra",
    "jonathan.b.mckay@gmail.com": "infra",
    "Outlook": "i9",
}

EVENT_KEYWORDS = [
    (["1:1", "1|1", "standup", "sprint", "retro", "slt", "metrics"], "i9"),
    (["m5x2", "property", "tenant", "lease", "appfolio"], "m5x2"),
    (["school", "cais", "pta", "ptc"], "xk87"),
    (["bball", "basketball", "gym", "hiit"], "hcbp"),
]


def gcal_project_code(event: dict) -> str:
    """Resolve a gcal event to a Neon project code."""
    cal = event.get("calendar", "")
    code = CALENDAR_PROJECT_MAP.get(cal)
    if code:
        return code
    title_lower = event.get("title", "").lower()
    for keywords, kw_code in EVENT_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return kw_code
    return ""


def project_style(pid_or_code) -> str:
    """Return a prompt_toolkit style string for a project id or code."""
    code = pid_or_code if isinstance(pid_or_code, str) else proj_code(pid_or_code)
    hexv = PROJECT_COLORS.get(code)
    return f"fg:{hexv}" if hexv else ""


# ─── State ─────────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.current = None  # running entry
        self.entries: list[dict] = []  # today's entries
        self.events: list[dict] = []  # today's combined calendar events (gcal + outlook)
        self.scroll_min = 0  # detail band scroll (minutes offset from now)
        self.flash = ""  # one-line status
        self.flash_until = 0.0
        self.flash_style = ""  # optional override style for flash
        self.command_mode = False
        self.today_points = 0  # 分 earned today
        self.block_points: dict[str, int] = {}  # per-block 分
        self.last_toggl_fetch = 0.0
        self.last_gcal_fetch = 0.0
        self.last_current_fetch = 0.0
        self.last_points_fetch = 0.0


STATE = State()


# ─── Data fetchers ─────────────────────────────────────────────────────────

def fetch_current():
    try:
        STATE.current = toggl_api.get_current()
        STATE.last_current_fetch = time.monotonic()
    except Exception as e:
        if "402" in str(e):
            flash("toggl: rate limited (free tier)", 30.0)
        else:
            flash(f"toggl current err: {e}")


def fetch_today():
    try:
        today = dt.datetime.now(TZ).date()
        raw = toggl_api.get_entries(
            start_date=(today - dt.timedelta(days=1)).isoformat(),
            end_date=(today + dt.timedelta(days=2)).isoformat(),
        ) or []
        out = []
        for e in raw:
            try:
                st = dt.datetime.fromisoformat(e.get("start", "")).astimezone(TZ)
            except Exception:
                continue
            if st.date() != today:
                continue
            stop_raw = e.get("stop")
            if stop_raw:
                en = dt.datetime.fromisoformat(stop_raw).astimezone(TZ)
            else:
                en = dt.datetime.now(TZ)
            out.append({
                "start_dt": st,
                "end_dt": en,
                "desc": e.get("description") or "",
                "project_id": e.get("project_id"),
                "running": stop_raw is None,
                "id": e.get("id"),
            })
        out.sort(key=lambda x: x["start_dt"])
        STATE.entries = out
        STATE.last_toggl_fetch = time.monotonic()
    except Exception as e:
        if "402" in str(e):
            flash("toggl: rate limited (free tier)", 30.0)
        else:
            flash(f"toggl today err: {e}")


def fetch_gcal(force=False):
    try:
        now = dt.datetime.now(TZ)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + dt.timedelta(days=1)
        gcal_events = gcal_client.list_events(day_start, day_end, force=force)
        outlook_events = []
        try:
            outlook_events = outlook_client.list_events(day_start, day_end, force=force)
        except Exception as e:
            flash(f"outlook err: {e}", 10.0)
        # Merge and sort by start time
        combined = gcal_events + outlook_events
        combined.sort(key=lambda e: e["start_dt"])
        STATE.events = combined
        STATE.last_gcal_fetch = time.monotonic()
    except Exception as e:
        flash(f"gcal err: {e}")


def fetch_points():
    """Read today's 分 from Neon 0分 sheet, then distribute to blocks via Toggl entries.

    The topline total is the Σ column (D) — the authoritative grand total the
    personal dashboard also reads (see dashboard.py /api/points-today). Reading
    the same cell keeps the two toplines in lockstep. Summing per-domain columns
    (R:Y) here undercounts: it omits Q (g245/infra/0g), Z (n156), and the -1₦
    penalty in P, which is exactly why the numbers used to diverge.
    """
    try:
        now = dt.datetime.now(TZ)
        today_md = f"{now.month}/{now.day}"

        # Read the Σ total (column D) for today's row in one ix-osa call.
        try:
            import subprocess as _sp
            IX_OSA = str(Path.home() / ".claude/skills/_lib/ix-osa.sh")
            script = f'''tell application "Microsoft Excel"
    set ws to sheet "0分" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of range ("B" & i) of ws) = "{today_md}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERR"
    return value of range ("D" & todayRow) of ws
end tell'''
            r = _sp.run([IX_OSA], input=script,
                        capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip() not in ("", "ERR"):
                val = r.stdout.strip()
                # Handle formula strings like "70+12" defensively.
                try:
                    STATE.today_points = int(round(float(val)))
                except ValueError:
                    try:
                        STATE.today_points = int(round(float(eval(val))))  # safe: digits and +
                    except Exception:
                        pass
        except Exception:
            pass

        # Per-block points: use completed-today.json timestamps
        # (Neon doesn't have per-block columns; timestamps are best-effort)
        bp: dict[str, int] = {}
        try:
            ct_path = Path.home() / "vault/z_ibx/completed-today.json"
            ct_data = json.loads(ct_path.read_text())
            today_str = now.strftime("%Y-%m-%d")
            if ct_data.get("date") == today_str:
                ct_pts = ct_data.get("points", {})
                ct_ts = ct_data.get("timestamps", {})
                for name, val in ct_pts.items():
                    ts = ct_ts.get(name, "")
                    if ts and val:
                        try:
                            h = int(ts.split(":")[0])
                            blk = hour_to_block(h)
                            if blk:
                                bp[blk[0]] = bp.get(blk[0], 0) + val
                        except Exception:
                            pass
        except Exception:
            pass
        STATE.block_points = bp
        STATE.last_points_fetch = time.monotonic()
    except Exception:
        pass


# ─── Helpers ───────────────────────────────────────────────────────────────

def flash(msg: str, secs: float = 4.0, style: str = ""):
    STATE.flash = msg
    STATE.flash_until = time.monotonic() + secs
    STATE.flash_style = style or ""


def proj_code(pid):
    if not pid:
        return ""
    return PROJECT_CODE.get(pid) or PROJECT_NAMES.get(pid, "") or ""


def fmt_dur(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


def fmt_dur_live(total_seconds: int) -> str:
    """Live elapsed with seconds, for the running timer."""
    h, rem = divmod(max(0, total_seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def detail_window():
    """Return (start, end) for detail band: current+next block, or prev+current if no next."""
    now = dt.datetime.now(TZ) + dt.timedelta(minutes=STATE.scroll_min)
    cur = hour_to_block(now.hour)
    nxt = next_block(now.hour)
    prv = prev_block(now.hour)
    if nxt:
        # current + next
        start_h = cur[1] if cur else nxt[1]
        end_h = nxt[2] + 1
    elif prv:
        # prev + current (no next block available)
        start_h = prv[1]
        end_h = (cur[2] + 1) if cur else (prv[2] + 1)
    elif cur:
        start_h = cur[1]
        end_h = cur[2] + 1
    else:
        start_h = max(0, now.hour - 2)
        end_h = min(24, now.hour + 2)
    start = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(hours=end_h)
    return start, end


try:
    from wcwidth import wcswidth as _wcswidth, wcwidth as _wcwidth
except ImportError:
    def _wcswidth(s):
        return len(s)
    def _wcwidth(c):
        return 1


def dwidth(s: str) -> int:
    """Display width accounting for CJK double-width chars."""
    w = _wcswidth(s)
    return w if w >= 0 else len(s)


def truncate(s: str, n: int) -> str:
    """Truncate to display width n (not codepoints)."""
    if dwidth(s) <= n:
        return s
    out = ""
    used = 0
    for c in s:
        cw = _wcwidth(c) or 1
        if used + cw > n - 1:
            break
        out += c
        used += cw
    return out + "…"


def pad(s: str, n: int) -> str:
    """Left-pad to display width n."""
    return s + " " * max(0, n - dwidth(s))


# ─── Renderers ─────────────────────────────────────────────────────────────

def render_header() -> list[tuple[str, str]]:
    now = dt.datetime.now(TZ)
    pts = STATE.today_points
    pts_str = f" · {pts}分" if pts else ""
    title = f" tg · {now:%a %H:%M:%S}{pts_str} "
    line = title + "─" * max(0, WIDTH_HINT - len(title))
    return [("class:header", line + "\n")]


def render_current() -> list[tuple[str, str]]:
    cur = STATE.current
    if not cur:
        return [("class:idle", " (no timer running)\n")]
    desc = cur.get("description") or "(no description)"
    pid = cur.get("project_id")
    code = proj_code(pid)
    try:
        st = dt.datetime.fromisoformat(cur.get("start", "")).astimezone(TZ)
        elapsed_s = int((dt.datetime.now(TZ) - st).total_seconds())
    except Exception:
        elapsed_s = 0
    line = f" ▶ {desc}"
    if code:
        line += f"  · {code}"
    line += f"   {fmt_dur_live(elapsed_s)}\n"
    style = project_style(pid) or "class:running"
    return [(f"bold {style}".strip(), line)]


def section_rule(label: str, focus: bool = False) -> list[tuple[str, str]]:
    s = f"─ {label} "
    cls = "class:focus_rule" if focus else "class:rule"
    return [(cls, s + "─" * max(0, WIDTH_HINT - len(s)) + "\n")]


def _read_block_emojis() -> dict[str, str]:
    """Read build order file, return {branch_char: emoji_string} for today's blocks."""
    try:
        text = BUILD_ORDER.read_text()
    except Exception:
        return {}
    result = {}
    in_section = False
    for line in text.splitlines():
        if line.strip().startswith("## -1₲"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        if line.startswith("- ") and not line.startswith("    "):
            tail = line[2:].strip()
            if tail:
                branch = tail[0]
                emojis = "".join(ch for ch in BLOCK_EMOJIS if ch in tail)
                if emojis:
                    result[branch] = emojis
    return result


def render_morning() -> list[tuple[str, str]]:
    """Toggl-only collapsed view from 00:00 -> detail-band start.

    For each completed block: top 4 entries by duration (shown chronologically),
    plus build-order emojis and block total duration.
    """
    start, _ = detail_window()
    cutoff = start
    items = [e for e in STATE.entries if e["start_dt"] < cutoff]
    if not items:
        return []
    merged = []
    for e in items:
        end = min(e["end_dt"], cutoff)
        if merged and merged[-1]["desc"] == e["desc"]:
            merged[-1]["end_dt"] = end
        else:
            merged.append({"start_dt": e["start_dt"], "end_dt": end, "desc": e["desc"], "project_id": e["project_id"]})
    block_durations: dict[str, dict[int | None, int]] = {}
    for m in merged:
        mins = int((m["end_dt"] - m["start_dt"]).total_seconds() // 60)
        if mins < 1:
            continue
        blk = hour_to_block(m["start_dt"].hour)
        if blk:
            block_durations.setdefault(blk[0], {})
            block_durations[blk[0]][m["project_id"]] = block_durations[blk[0]].get(m["project_id"], 0) + mins
    block_entries: dict[str, list] = {}
    for m in merged:
        mins = int((m["end_dt"] - m["start_dt"]).total_seconds() // 60)
        if mins < 5:
            continue
        blk = hour_to_block(m["start_dt"].hour)
        blk_name = blk[0] if blk else "?"
        block_entries.setdefault(blk_name, [])
        block_entries[blk_name].append(m)

    bo_emojis = _read_block_emojis()

    out: list[tuple[str, str]] = []
    for blk_name, blk_sh, blk_eh in BLOCKS:
        if blk_eh + 1 > cutoff.hour:
            break  # rest handled by detail view
        if blk_eh < 4:
            continue  # skip before 卯
        entries = block_entries.get(blk_name, [])
        for ent in entries:
            ent["_dur_min"] = int((ent["end_dt"] - ent["start_dt"]).total_seconds() // 60)
        top4 = sorted(entries, key=lambda e: e["_dur_min"], reverse=True)[:4]
        top4.sort(key=lambda e: e["start_dt"])

        bd = block_durations.get(blk_name, {})
        dom_pid = max(bd, key=lambda p: bd[p], default=None) if bd else None
        blk_style = f"bold {project_style(dom_pid)}".strip() if dom_pid else "class:dim"
        emojis = bo_emojis.get(blk_name, "")
        total_min = sum(bd.values())
        blk_pts = STATE.block_points.get(blk_name, 0)
        blk_label = f" {blk_name}"
        if emojis:
            blk_label += f" {emojis}"
        if total_min:
            blk_label += f"  {fmt_dur(total_min)}"
        if blk_pts:
            blk_label += f" · {blk_pts}分"
        blk_label += " "
        trail = max(0, WIDTH_HINT - 1 - dwidth(blk_label))
        out.append((blk_style, "─"))
        out.append((blk_style, blk_label))
        out.append((blk_style, "─" * trail + "\n"))

        for m in top4:
            is_sleep = (m["desc"] or "").strip() == "睡觉"
            display_time = f"{m['end_dt']:%H:%M}" if is_sleep else f"{m['start_dt']:%H:%M}"
            code = proj_code(m["project_id"])
            dur = fmt_dur(m["_dur_min"])
            label = m["desc"] or "(blank)"
            if code:
                label = f"{label} · {code}"
            style = project_style(m["project_id"])
            space = max(1, WIDTH_HINT - 8 - len(dur) - 1)
            out.append(("class:time", f"  {display_time} "))
            out.append((style, f"{pad(truncate(label, space), space)}"))
            out.append(("class:dim", f" {dur}\n"))
    return out


def render_detail() -> list[tuple[str, str]]:
    start, end = detail_window()
    now = dt.datetime.now(TZ)
    effective_hour = now.hour + STATE.scroll_min // 60
    cur = hour_to_block(effective_hour)
    nxt = next_block(effective_hour)
    prv = prev_block(effective_hour)

    scroll_suffix = f" (+{STATE.scroll_min}m)" if STATE.scroll_min else ""
    bo_emojis = _read_block_emojis()

    if nxt:
        # Normal: current block header at top
        top_name = cur[0] if cur else "?"
        boundary_h = cur[2] + 1 if cur else end.hour
    else:
        # No next block: show prev block header at top, current at bottom
        top_name = prv[0] if prv else "?"
        boundary_h = cur[1] if cur else end.hour

    top_emojis = bo_emojis.get(top_name, "")
    top_pts = STATE.block_points.get(top_name, 0)
    top_label = f"{top_name}{' ' + top_emojis if top_emojis else ''}"
    if top_pts:
        top_label += f" · {top_pts}分"
    top_label += scroll_suffix
    out: list[tuple[str, str]] = section_rule(top_label, focus=True)

    boundary = now.replace(hour=boundary_h, minute=0, second=0, microsecond=0)
    slot = start
    now_drawn = False
    block_rule_drawn = False
    gcal_shown: set[str] = set()  # track gcal event titles already labelled
    toggl_shown: set[str] = set()  # track toggl descriptions already labelled
    while slot < end:
        slot_end = slot + dt.timedelta(minutes=SLOT_MIN)

        # Block boundary (no separator line; top/bottom rules are enough)
        if not block_rule_drawn and slot >= boundary:
            block_rule_drawn = True

        pid = None
        gcal_sty = ""
        if slot_end <= now:
            label, pid = _slot_label_toggl(slot, slot_end)
            marker = "│"
        elif slot >= now:
            label, gcal_sty = _slot_label_gcal(slot, slot_end)
            marker = "│"
        else:
            label, pid = _slot_label_toggl(slot, slot_end)
            if not label:
                label, gcal_sty = _slot_label_gcal(slot, slot_end)
            marker = "│"

        # Deduplicate gcal labels: show title only on first slot, then just color bar
        if label and label.startswith("◇ "):
            raw_title = label[2:].strip()
            if raw_title in gcal_shown:
                label = "◇ │"
            else:
                gcal_shown.add(raw_title)

        # Insert now line before this slot if applicable
        if not now_drawn and slot >= now:
            frac = int((now.microsecond / 1_000_000) * 10)
            time_now = f"{now:%H:%M:%S}.{frac}"
            if STATE.current:
                cur_desc = STATE.current.get("description") or ""
                cur_code = proj_code(STATE.current.get("project_id"))
                cur_pid = STATE.current.get("project_id")
                try:
                    cst = dt.datetime.fromisoformat(STATE.current.get("start", "")).astimezone(TZ)
                    elapsed = fmt_dur_live(int((now - cst).total_seconds()))
                except Exception:
                    elapsed = "0m00s"
                task_label = f"▶ {cur_desc}"
                if cur_code:
                    task_label += f" · {cur_code}"
                task_label += f"  {elapsed}"
                task_style = f"bold {project_style(cur_pid)}".strip() or "bold class:now"
                space = max(1, WIDTH_HINT - len(time_now) - 4)
                out.append(("bold class:now", f" {time_now}"))
                out.append((task_style, f" │ {truncate(task_label, space)}\n"))
            else:
                out.append(("bold class:now", f" {time_now}"))
                out.append(("bold class:idle", f" │ (no timer)\n"))
            now_drawn = True

        time_str = f"{slot:%H:%M}"
        is_running = STATE.current and slot <= now < slot_end
        if is_running:
            cur_desc = STATE.current.get("description") or ""
            label = f"▶ {cur_desc}"
            pid = STATE.current.get("project_id")

        # Deduplicate Toggl labels: show description only on first slot
        if label and not is_running and not label.startswith("◇ "):
            if label in toggl_shown:
                label = "″"
            else:
                toggl_shown.add(label)

        space = min(DESC_MAX, max(1, WIDTH_HINT - len(time_str) - 4))
        content = f" {marker} {truncate(label or '·', space)}\n"
        if is_running:
            cls = f"bold {project_style(pid)}".strip() or "class:running"
        elif slot_end <= now:
            cls = project_style(pid) or "class:past"
        else:
            cls = gcal_sty or "class:future"
        out.append(("class:time", f" {time_str}"))
        out.append((cls, content))
        slot = slot_end
    if not now_drawn:
        frac = int((now.microsecond / 1_000_000) * 10)
        time_now = f"{now:%H:%M:%S}.{frac}"
        out.append(("bold class:now", f" {time_now}"))
        out.append(("bold class:idle", f" │ (no timer)\n"))
    # Bottom block header
    if nxt:
        bot_name, bot_sh, bot_eh = nxt
    elif cur:
        bot_name, bot_sh, bot_eh = cur
    else:
        bot_name = None
    if bot_name:
        bot_emojis = bo_emojis.get(bot_name, "")
        bot_pts = STATE.block_points.get(bot_name, 0)
        bot_label = f"{bot_name}{' ' + bot_emojis if bot_emojis else ''}"
        if bot_pts:
            bot_label += f" · {bot_pts}分"
        out += section_rule(bot_label, focus=True)
    return out


def _slot_label_toggl(slot_s, slot_e):
    overlapping = [e for e in STATE.entries if e["start_dt"] < slot_e and e["end_dt"] > slot_s]
    if not overlapping:
        return "", None
    if len(overlapping) == 1:
        e = overlapping[0]
        code = proj_code(e["project_id"])
        return (f"{e['desc'] or '(blank)'}" + (f"  · {code}" if code else ""), e["project_id"])
    descs = ", ".join(dict.fromkeys(e["desc"] or "?" for e in overlapping))
    # Use the longest-overlap entry's project for color
    dominant = max(overlapping, key=lambda e: (min(e["end_dt"], slot_e) - max(e["start_dt"], slot_s)).total_seconds())
    return f"{len(overlapping)}× {descs}", dominant["project_id"]


def _slot_label_gcal(slot_s, slot_e):
    """Return (label, style_str) for a gcal slot. style_str may be empty."""
    overlapping = [
        ev for ev in STATE.events
        if ev["start_dt"] < slot_e and ev["end_dt"] > slot_s
        and ev.get("transparency") != "transparent"
        and not ev.get("all_day")
    ]
    if not overlapping:
        return "", ""
    if len(overlapping) == 1:
        sty = project_style(gcal_project_code(overlapping[0]))
        return f"◇ {overlapping[0]['title']}", sty
    dominant = max(overlapping, key=lambda ev: (min(ev["end_dt"], slot_e) - max(ev["start_dt"], slot_s)).total_seconds())
    sty = project_style(gcal_project_code(dominant))
    titles = ", ".join(ev["title"] for ev in overlapping)
    return f"◇ {len(overlapping)}× {titles}", sty


def render_evening() -> list[tuple[str, str]]:
    _, end = detail_window()
    cutoff = end
    now = dt.datetime.now(TZ)
    sleep_time = now.replace(hour=22, minute=0, second=0, microsecond=0)

    # Collect gcal events keyed by block
    gcal_by_block: dict[str, list] = {}
    for ev in STATE.events:
        if ev["end_dt"] <= cutoff or ev["start_dt"] >= sleep_time:
            continue
        if ev.get("transparency") == "transparent":
            continue
        blk = hour_to_block(ev["start_dt"].hour)
        blk_name = blk[0] if blk else "?"
        gcal_by_block.setdefault(blk_name, [])
        gcal_by_block[blk_name].append(ev)

    # Show every remaining block from cutoff through 22:00
    out: list[tuple[str, str]] = []
    for name, sh, eh in BLOCKS:
        block_end_h = eh + 1
        if block_end_h <= cutoff.hour:
            continue
        if sh >= 22:
            break
        evs = gcal_by_block.get(name, [])
        if evs:
            # Block rule with first event inline
            first = evs[0]
            ev_sty = project_style(gcal_project_code(first)) or "class:future"
            prefix = f"{first['start_dt']:%H:%M}" if not first.get("all_day") else "all-day"
            label = truncate(first["title"], min(DESC_MAX, max(1, WIDTH_HINT - 12)))
            inner = f" {name}  {prefix} {label} "
            trail = max(0, WIDTH_HINT - 1 - len(inner))
            out.append(("class:rule", "─"))
            out.append((ev_sty, inner))
            out.append(("class:rule", "─" * trail + "\n"))
            for ev in evs[1:4]:
                prefix = "all-day" if ev.get("all_day") else f"{ev['start_dt']:%H:%M}"
                space = min(DESC_MAX, max(1, WIDTH_HINT - 2 - len(prefix) - 1))
                title = truncate(ev["title"], space)
                ev_sty = project_style(gcal_project_code(ev)) or "class:future"
                out.append(("class:time", f"  {prefix}"))
                out.append((ev_sty, f" {title}\n"))
        else:
            # Empty block: just the rule with block name
            rule_text = f" {name} "
            trail = max(0, WIDTH_HINT - 1 - len(rule_text))
            out.append(("class:rule", "─"))
            out.append(("class:dim", rule_text))
            out.append(("class:rule", "─" * trail + "\n"))
    # Sleep marker
    rule_text = f" 睡觉 "
    trail = max(0, WIDTH_HINT - 1 - len(rule_text))
    out.append(("class:rule", "─"))
    out.append((f"fg:{PROJECT_COLORS.get('睡觉', '#666666')}", rule_text))
    out.append(("class:rule", "─" * trail + "\n"))
    return out


def render_current_bottom() -> list[tuple[str, str]]:
    """Mirror of the running timer, pinned above the footer so it's always visible.
    Clock on left, timer desc on right, sub-second decimals as a heartbeat."""
    now = dt.datetime.now(TZ)
    frac = int((now.microsecond / 1_000_000) * 10)
    clock = f" {now:%H:%M:%S}.{frac}"
    cur = STATE.current
    if not cur:
        return [("class:time", clock), ("class:idle", "  (no timer)\n")]
    desc = cur.get("description") or "(no description)"
    pid = cur.get("project_id")
    code = proj_code(pid)
    try:
        st = dt.datetime.fromisoformat(cur.get("start", "")).astimezone(TZ)
        elapsed = (now - st).total_seconds()
    except Exception:
        elapsed = 0.0
    h, rem = divmod(max(0, int(elapsed)), 3600)
    m, s = divmod(rem, 60)
    frac = int((elapsed % 1) * 10)  # tenths of a second
    if h:
        dur = f"{h}h{m:02d}m{s:02d}.{frac}s"
    else:
        dur = f"{m}m{s:02d}.{frac}s"
    right = f" ▶ {desc}"
    if code:
        right += f" · {code}"
    right += f"  {dur}"
    pad = max(0, WIDTH_HINT - len(clock) - len(right))
    style = project_style(pid) or "class:running"
    return [
        ("class:time", clock),
        (f"bold {style}".strip(), f"{'':>{pad}}{right}\n"),
    ]


def render_footer() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if STATE.flash and time.monotonic() < STATE.flash_until:
        sty = STATE.flash_style or "class:flash"
        out.append((sty, f" ▸ {STATE.flash}\n"))
    out.append(("class:hint", " [c]hange [s]top [r]efresh [j/k]scroll [q]uit\n"))
    return out


def render_all() -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    parts += render_header()
    parts += render_morning()
    parts += render_detail()
    parts += render_evening()
    return parts


def render_bottom_bar() -> list[tuple[str, str]]:
    """Pinned bar: current timer + flash + hints. Lives outside the scroll area."""
    parts = render_current_bottom()
    parts += render_footer()
    return parts


# ─── Command execution ─────────────────────────────────────────────────────

def run_tg_fast(text: str) -> str:
    try:
        proc = subprocess.run(
            ["python3", TG_FAST, text],
            capture_output=True, text=True, timeout=15,
        )
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return out[-1] if out else "(no output)"
    except Exception as e:
        return f"err: {e}"


# ─── App ───────────────────────────────────────────────────────────────────

kb = KeyBindings()
input_buffer = Buffer(multiline=False)


@kb.add("q", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    event.app.exit()


@kb.add("c-c")
def _(event):
    if STATE.command_mode:
        STATE.command_mode = False
        input_buffer.reset()
        event.app.layout.focus(main_window)
    else:
        event.app.exit()


@kb.add("c", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    STATE.command_mode = True
    input_buffer.reset()
    event.app.layout.focus(input_window)


@kb.add("s", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    flash("stopping…")

    async def _stop():
        res = await asyncio.to_thread(run_tg_fast, "stop")
        flash(res)
        event.app.invalidate()
        for delay in (0.4, 0.8, 1.5):
            await asyncio.sleep(delay)
            await asyncio.to_thread(fetch_current)
            await asyncio.to_thread(fetch_today)
            event.app.invalidate()

    event.app.create_background_task(_stop())


@kb.add("r", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    flash("refreshing…")

    async def _refresh():
        await asyncio.to_thread(fetch_current)
        await asyncio.to_thread(fetch_today)
        await asyncio.to_thread(fetch_gcal, True)
        flash("refreshed")
        event.app.invalidate()

    event.app.create_background_task(_refresh())


@kb.add("j", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    STATE.scroll_min += 30


@kb.add("k", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    STATE.scroll_min -= 30


@kb.add("0", filter=~__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    STATE.scroll_min = 0


@kb.add("escape", filter=__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    STATE.command_mode = False
    input_buffer.reset()
    event.app.layout.focus(main_window)


@kb.add("enter", filter=__import__("prompt_toolkit").filters.Condition(lambda: STATE.command_mode))
def _(event):
    text = input_buffer.text.strip()
    STATE.command_mode = False
    input_buffer.reset()
    event.app.layout.focus(main_window)
    if not text:
        return
    flash(f"$ tg {text}")

    async def _run_and_refresh():
        res = await asyncio.to_thread(run_tg_fast, text)
        flash(res, 6.0)
        event.app.invalidate()
        # Toggl /current has propagation lag; poll a few times
        for delay in (0.4, 0.8, 1.5):
            await asyncio.sleep(delay)
            await asyncio.to_thread(fetch_current)
            await asyncio.to_thread(fetch_today)
            event.app.invalidate()

    event.app.create_background_task(_run_and_refresh())


main_window = Window(
    content=FormattedTextControl(render_all, focusable=True),
    wrap_lines=False,
    width=Dimension(preferred=WIDTH_HINT),
)

# Pinned bottom bar: current timer + flash + hints (never scrolls)
bottom_bar = Window(
    content=FormattedTextControl(render_bottom_bar),
    height=3,  # timer line + flash/hint line + input line headroom
    wrap_lines=False,
)


def render_input_prompt():
    if STATE.command_mode:
        return [("class:prompt", " tg> ")]
    return [("class:hint", "")]


input_window = Window(
    content=BufferControl(buffer=input_buffer, focusable=True),
    height=1,
)
prompt_window = Window(content=FormattedTextControl(render_input_prompt), height=1, width=Dimension.exact(5))

from prompt_toolkit.layout import VSplit  # noqa: E402

input_row = VSplit([prompt_window, input_window])
root = HSplit([main_window, bottom_bar, input_row])

style = Style.from_dict({
    "header": "bold cyan",
    "running": "bold green",
    "idle": "italic #888888",
    "rule": "#666666",
    "focus_rule": "bold #ffffff",
    "dim": "italic #888888",
    "past": "#aaaaaa",
    "future": "#dddddd",
    "time": "#888888",
    "now": "bold #ffffff",
    "flash": "bold yellow",
    "hint": "italic #666666",
    "prompt": "bold cyan",
})

app = Application(layout=Layout(root, focused_element=main_window),
                  key_bindings=kb, full_screen=True, style=style,
                  refresh_interval=0.1)


async def ticker_current(app):
    while True:
        await asyncio.sleep(30)
        fetch_current()
        app.invalidate()


async def ticker_today(app):
    while True:
        await asyncio.sleep(300)
        fetch_today()
        app.invalidate()


async def ticker_gcal(app):
    while True:
        await asyncio.sleep(300)
        fetch_gcal()
        app.invalidate()


async def ticker_points(app):
    while True:
        await asyncio.sleep(120)
        await asyncio.to_thread(fetch_points)
        app.invalidate()


async def _sigusr1_refresh():
    """Triggered by SIGUSR1: immediate full refresh (e.g. after /did starts a timer)."""
    old_count = len(STATE.entries)
    fetch_current()
    fetch_today()
    fetch_points()
    # If entry count grew (task completed → new entry, or timer stopped),
    # flash purple as a prayer/mindfulness prompt
    if len(STATE.entries) != old_count or STATE.current is None:
        flash("☀️", 6.0, style="bold fg:#aa00ff")
    app.invalidate()


async def main():
    fetch_current()
    fetch_today()
    fetch_gcal()
    fetch_points()

    # SIGUSR1 → instant refresh (sent by /did, /tg, /done after timer changes)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(_sigusr1_refresh()))

    # Write PID so other tools can signal us
    pid_file = Path.home() / ".cache" / "tg-tui.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    app.create_background_task(ticker_current(app))
    app.create_background_task(ticker_today(app))
    app.create_background_task(ticker_gcal(app))
    app.create_background_task(ticker_points(app))
    try:
        await app.run_async()
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
