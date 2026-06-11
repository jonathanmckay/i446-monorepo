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
import re
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
from prompt_toolkit.styles import Style, StyleTransformation  # noqa: E402

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
BUILD_ORDER = Path.home() / "vault/g245/build-order.md"
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
        # Whether STATE.current reflects a CONFIRMED Toggl read. False until the
        # first successful fetch, and reset whenever a fetch fails (e.g. the
        # free-tier 402 rate limit). The idle nag (whole-screen flash + red NO
        # TIME ENTRY) only fires when we've confirmed no timer — never when we
        # simply couldn't reach Toggl, which used to flash over a live timer.
        self.current_known = False
        self.boot_time = time.monotonic()  # refreshed in main(); gates the
        # enter handler so tty text queued before startup (e.g. the command
        # line cmux types when respawning the pane) can't start a junk timer
        self.entries: list[dict] = []  # today's entries
        self.entries_yday: list[dict] = []  # yesterday's (for 卯 sleep total)
        self.events: list[dict] = []  # today's combined calendar events (gcal + outlook)
        self.scroll_min = 0  # detail band scroll (minutes offset from now)
        self.flash = ""  # one-line status
        self.flash_until = 0.0
        self.flash_style = ""  # optional override style for flash
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
        STATE.current_known = True
        STATE.last_current_fetch = time.monotonic()
    except Exception as e:
        # Fetch failed → we no longer know the timer state. Leave STATE.current
        # as-is (last known) but mark it unconfirmed so the idle nag stays off.
        STATE.current_known = False
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
        yday = today - dt.timedelta(days=1)
        out = []
        yout = []
        for e in raw:
            try:
                st = dt.datetime.fromisoformat(e.get("start", "")).astimezone(TZ)
            except Exception:
                continue
            if st.date() != today and st.date() != yday:
                continue
            stop_raw = e.get("stop")
            if stop_raw:
                en = dt.datetime.fromisoformat(stop_raw).astimezone(TZ)
            else:
                en = dt.datetime.now(TZ)
            (out if st.date() == today else yout).append({
                "start_dt": st,
                "end_dt": en,
                "desc": e.get("description") or "",
                "project_id": e.get("project_id"),
                "running": stop_raw is None,
                "id": e.get("id"),
            })
        out.sort(key=lambda x: x["start_dt"])
        STATE.entries = out
        STATE.entries_yday = yout
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
    """Read today's 分 from Neon 0分: Σ total (col D) + per-block points (G:O).

    The topline total is the Σ column (D) — the authoritative grand total the
    personal dashboard also reads (see dashboard.py /api/points-today). Reading
    the same cell keeps the two toplines in lockstep. Summing per-domain columns
    (R:Y) here undercounts: it omits Q (g245/infra/0g), Z (n156), and the -1₦
    penalty in P, which is exactly why the numbers used to diverge.

    Per-block points come from columns G:O (headed 卯辰巳午未申酉戌亥) in the
    same row — the authoritative distribution. The completed-today.json
    timestamp reconstruction is a fallback only: it attributes points to the
    block they were logged in, which piles batch-logged work into the current
    block (the "everything shows in 申" bug).
    """
    try:
        now = dt.datetime.now(TZ)
        today_md = f"{now.month}/{now.day}"

        # Read the Σ total (column D) AND the per-block columns (G:O, headed
        # 卯辰巳午未申酉戌亥) for today's row in one ix-osa call. G:O is the
        # authoritative per-block distribution — reconstructing blocks from
        # completed-today.json logging timestamps lumps batch-logged points
        # into whichever block they were *recorded* in, not earned in.
        bp_excel: dict[str, int] = {}
        read_ok = False
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
    set out to ""
    try
        set out to (value of range ("D" & todayRow) of ws) as text
    end try
    repeat with c from 7 to 15
        set v to ""
        try
            set v to (value of cell c of row todayRow of ws) as text
        end try
        set out to out & "|" & v
    end repeat
    return out
end tell'''
            r = _sp.run([IX_OSA], input=script,
                        capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip() not in ("", "ERR"):
                read_ok = True
                parts = r.stdout.strip().split("|")
                val = parts[0].strip()
                # Handle formula strings like "70+12" defensively.
                try:
                    STATE.today_points = int(round(float(val)))
                except ValueError:
                    try:
                        STATE.today_points = int(round(float(eval(val))))  # safe: digits and +
                    except Exception:
                        pass
                branches = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
                for bname, raw in zip(branches, parts[1:10]):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        v = int(round(float(raw)))
                    except ValueError:
                        continue
                    if v:
                        bp_excel[bname] = v
        except Exception:
            pass

        # Only overwrite block_points when the Neon read SUCCEEDED. G:O is the
        # sole source of truth; if Excel is unreachable we keep the last good
        # values rather than substituting completed-today.json timestamps,
        # which attribute batch-logged points to the block they were *logged*
        # in (the 313-in-酉 bug: 33 earned there, 280 logged there in a batch).
        if read_ok:
            STATE.block_points = bp_excel
            STATE.last_points_fetch = time.monotonic()
    except Exception:
        pass


# ─── Helpers ───────────────────────────────────────────────────────────────

def flash(msg: str, secs: float = 4.0, style: str = ""):
    STATE.flash = msg
    STATE.flash_until = time.monotonic() + secs
    STATE.flash_style = style or ""


_PROJECTS_FETCHED = False


def _extend_codes_from_api():
    """One-shot fallback: map unknown project ids to codes by project NAME.

    Catches duplicate/recreated Toggl projects (e.g. a second project named
    'xk87' created via the mobile picker) that aren't in the static
    PROJECT_MAP — without this they render uncolored (white)."""
    global _PROJECTS_FETCHED
    _PROJECTS_FETCHED = True
    try:
        known = set(PROJECT_COLORS) | set(PROJECT_CODE.values())
        for p in toggl_api.get_projects() or []:
            pid, name = p.get("id"), (p.get("name") or "").strip()
            if pid and name and pid not in PROJECT_CODE and name in known:
                PROJECT_CODE[pid] = name
    except Exception:
        pass  # offline / rate-limited: keep static mapping


def _idle_since(now):
    """Latest end-time among today's completed entries at/before now, or None.
    Used to show how long there's been NO running timer."""
    ends = [e["end_dt"] for e in STATE.entries
            if not e.get("running") and e["end_dt"] <= now]
    return max(ends) if ends else None


def proj_code(pid):
    if not pid:
        return ""
    code = PROJECT_CODE.get(pid) or PROJECT_NAMES.get(pid, "")
    if not code and not _PROJECTS_FETCHED:
        _extend_codes_from_api()
        code = PROJECT_CODE.get(pid, "")
    return code or ""


def fmt_dur(minutes: int) -> str:
    # All durations denominated in minutes (95m, not 1h35m)
    return f"{minutes}m"


def fmt_dur_live(total_seconds: int) -> str:
    """Live elapsed with seconds, for the running timer. Minutes-denominated."""
    m, s = divmod(max(0, total_seconds), 60)
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


# ─── Short (Haiku) task names, shared with dtd ──────────────────────────────
# dtd displays AI-abbreviated task names from the `short` field of the task
# cache; tg-tui shows the same labels so a timer reads identically in both. The
# Toggl description is the task content minus (N)/[N]/{N} annotations, so we map
# normalized-cleaned content → cleaned short and look entries up by description.

import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
TASK_QUEUE = _sp.TASK_QUEUE
SHORT_NAMES: dict[str, str] = {}  # normalized cleaned content → cleaned short


def _clean_annotations(s: str) -> str:
    """Strip dtd/Todoist annotations: (30) time, [40] points, {60} estimate,
    and any trailing @project tag — leaving the bare task name."""
    s = re.sub(r"\s*\(\d+\)", "", s)
    s = re.sub(r"\s*\[\d+\]", "", s)
    s = re.sub(r"\s*\{\d+\}", "", s)
    s = re.sub(r"\s*@\S+", "", s)
    return s.strip()


def _norm_key(s: str) -> str:
    """Normalization key tolerant of dash/whitespace/case drift between the
    Toggl timer name and the task content (mirrors did-fast's _norm)."""
    return re.sub(r"[\s\-—–]+", " ", _clean_annotations(s)).strip().lower()


def fetch_short_names():
    """(Re)load dtd's short names from the task cache. Cheap local file read;
    refreshed at startup and on SIGUSR1 (when /did rewrites the cache)."""
    try:
        data = json.loads(TASK_QUEUE.read_text())
    except Exception:
        return
    out: dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            c, sh = o.get("content"), o.get("short")
            if c and sh:
                out[_norm_key(c)] = _clean_annotations(sh)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    if out:
        SHORT_NAMES.clear()
        SHORT_NAMES.update(out)


def display_desc(desc: str) -> str:
    """Map a Toggl description to dtd's short name when one exists, else the
    description unchanged (habits and ad-hoc timers have no cached short)."""
    if not desc:
        return desc
    return SHORT_NAMES.get(_norm_key(desc), desc)


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


def section_rule(label: str, focus: bool = False, pts: int = 0) -> list[tuple[str, str]]:
    """Full-width rule line. If pts is given, render it right-justified in
    bold white so the day's 分 are scannable at a glance."""
    s = f"─ {label} "
    cls = "class:focus_rule" if focus else "class:rule"
    pts_str = f" {pts}分" if pts else ""
    trail = max(0, WIDTH_HINT - dwidth(s) - dwidth(pts_str))
    out: list[tuple[str, str]] = [(cls, s + "─" * trail)]
    if pts_str:
        out.append(("bold #ffffff", pts_str))
    out.append((cls, "\n"))
    return out


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


def _compact_block_lines(blk_name, blk_sh, picks, pts, emojis) -> list[tuple[str, str]]:
    """Render one non-focus block as exactly 4 lines (header + 3 body).

    The header rule carries the most important entry inline to conserve space:
    ``─午:04 task ─────  12分``. Body rows carry the next entries by duration in
    chronological order; an empty block shows dim half-hour gridlines instead.

    picks: up to 4 normalized items, sorted chronologically, each a dict with
    start_dt, time_str, label, style, dur_min. Past blocks fill these from
    Toggl, future blocks from gcal — both render identically.
    """
    out: list[tuple[str, str]] = []
    dom = max(picks, key=lambda p: p["dur_min"]) if picks else None
    blk_style = f"bold {dom['style']}".strip() if dom and dom["style"] else "class:dim"
    pts_str = f" {pts}分" if pts else ""
    emoji_str = f" {emojis}" if emojis else ""

    # ── header line (carries picks[0] inline when present) ──
    head = picks[0] if picks else None
    if head:
        # Block rule doubles as the first entry: ─午:04-task──────  pts.
        left = f"─{blk_name}{emoji_str}:{head['start_dt'].minute:02d}-"
        task = truncate(head["label"], max(1, WIDTH_HINT - dwidth(left) - dwidth(pts_str) - 1))
        trail = max(0, WIDTH_HINT - dwidth(left) - dwidth(task) - dwidth(pts_str))
        out.append((blk_style, left))
        out.append((head["style"] or blk_style, task))
        out.append((blk_style, "─" * trail))
    else:
        left = f"─{blk_name}{emoji_str} "
        trail = max(0, WIDTH_HINT - dwidth(left) - dwidth(pts_str))
        out.append((blk_style, left + "─" * trail))
    if pts_str:
        out.append(("bold #ffffff", pts_str))
    out.append((blk_style, "\n"))

    # ── body lines (always 3, to keep every block 4 lines tall) ──
    body = picks[1:4]
    for p in body:
        dur = fmt_dur(p["dur_min"])
        space = max(1, WIDTH_HINT - 8 - len(dur) - 1)
        out.append(("class:time", f"  {p['time_str']} "))
        out.append((p["style"], pad(truncate(p["label"], space), space)))
        out.append(("class:dim", f" {dur}\n"))
    if not picks:
        # Empty block → all four 30-min placeholders under the header rule, so
        # untracked time reads as an explicit (faint) grid to fill in.
        for hh, mm in ((blk_sh, 0), (blk_sh, 30), (blk_sh + 1, 0), (blk_sh + 1, 30)):
            out.append(("class:time", f"  {hh:02d}:{mm:02d} "))
            out.append(("class:idle", "┄" * max(0, WIDTH_HINT - 8) + "\n"))
    else:
        for _ in range(3 - len(body)):  # pad partially-filled blocks
            out.append(("class:idle", "\n"))
    return out


def _past_block_picks(blk_name, merged) -> list[dict]:
    """Top-4 Toggl entries (by duration) starting in this block, chronological."""
    items = []
    for m in merged:
        blk = hour_to_block(m["start_dt"].hour)
        if not blk or blk[0] != blk_name:
            continue
        mins = int((m["end_dt"] - m["start_dt"]).total_seconds() // 60)
        if mins < 1:
            continue
        is_sleep = (m["desc"] or "").strip() == "睡觉"
        code = proj_code(m["project_id"])
        label = (display_desc(m["desc"]) or "(blank)") + (f" · {code}" if code else "")
        items.append({
            "start_dt": m["start_dt"],
            "time_str": f"{m['end_dt']:%H:%M}" if is_sleep else f"{m['start_dt']:%H:%M}",
            "label": label,
            "style": project_style(m["project_id"]),
            "dur_min": mins,
        })
    items.sort(key=lambda x: x["dur_min"], reverse=True)
    items = items[:4]
    items.sort(key=lambda x: x["start_dt"])
    return items


def _future_block_picks(blk_name, events) -> list[dict]:
    """Top-4 gcal events (by duration) starting in this block, chronological."""
    items = []
    for ev in events:
        blk = hour_to_block(ev["start_dt"].hour)
        if not blk or blk[0] != blk_name:
            continue
        if ev.get("transparency") == "transparent" or ev.get("all_day"):
            continue
        mins = max(1, int((ev["end_dt"] - ev["start_dt"]).total_seconds() // 60))
        items.append({
            "start_dt": ev["start_dt"],
            "time_str": f"{ev['start_dt']:%H:%M}",
            "label": ev["title"],
            "style": project_style(gcal_project_code(ev)),
            "dur_min": mins,
        })
    items.sort(key=lambda x: x["dur_min"], reverse=True)
    items = items[:4]
    items.sort(key=lambda x: x["start_dt"])
    return items


def _mao_line(emojis) -> list[tuple[str, str]]:
    """卯 layout exception: one line instead of the standard four.

    The 4:00-6:00 block is sleep; the signals worth a row are the wake time,
    rendered with the sleep end-time convention (睡觉 →HH:MM), and the total
    minutes slept right-justified — including last night's pre-midnight
    portion (the day-barrier rule splits overnight sleep at 00:00, so the
    evening half lives in STATE.entries_yday). Wake = latest 睡觉 entry
    ending before noon; naps don't count toward either number."""
    wake = None
    style = ""
    sleep_min = 0
    for e in STATE.entries:
        if (e["desc"] or "").strip() == "睡觉" and e["end_dt"].hour < 12:
            sleep_min += max(0, int((e["end_dt"] - e["start_dt"]).total_seconds() // 60))
            if wake is None or e["end_dt"] > wake:
                wake = e["end_dt"]
                style = project_style(e["project_id"])
    for e in STATE.entries_yday:
        if (e["desc"] or "").strip() == "睡觉" and e["start_dt"].hour >= 18:
            sleep_min += max(0, int((e["end_dt"] - e["start_dt"]).total_seconds() // 60))
    emoji_str = f" {emojis}" if emojis else ""
    pts_str = f" {sleep_min}m" if sleep_min else ""
    blk_style = f"bold {style}".strip() if style else "class:dim"
    out: list[tuple[str, str]] = []
    left = f"─卯{emoji_str} "
    out.append((blk_style, left))
    label = ""
    if wake:
        label = f"睡觉 →{wake:%H:%M} "
        out.append((style or blk_style, label))
    trail = max(0, WIDTH_HINT - dwidth(left) - dwidth(label) - dwidth(pts_str))
    out.append((blk_style, "─" * trail))
    if pts_str:
        out.append(("bold #ffffff", pts_str))
    out.append((blk_style, "\n"))
    return out


def render_morning() -> list[tuple[str, str]]:
    """Past blocks (00:00 → detail-band start), Toggl-filled, one row per
    important allocation. Same compact format as the future (evening) view."""
    start, _ = detail_window()
    cutoff = start
    items = [e for e in STATE.entries if e["start_dt"] < cutoff]
    merged: list[dict] = []
    for e in items:
        end = min(e["end_dt"], cutoff)
        if merged and merged[-1]["desc"] == e["desc"]:
            merged[-1]["end_dt"] = end
        else:
            merged.append({"start_dt": e["start_dt"], "end_dt": end,
                           "desc": e["desc"], "project_id": e["project_id"]})

    bo_emojis = _read_block_emojis()
    out: list[tuple[str, str]] = []
    for blk_name, blk_sh, blk_eh in BLOCKS:
        if blk_eh + 1 > cutoff.hour:
            break  # rest handled by the detail band
        pts = STATE.block_points.get(blk_name, 0)
        if blk_name == "卯":
            # Layout exception: sleep block collapses to a single wake-time line
            out += _mao_line(bo_emojis.get(blk_name, ""))
            continue
        picks = _past_block_picks(blk_name, merged)
        out += _compact_block_lines(blk_name, blk_sh, picks, pts, bo_emojis.get(blk_name, ""))
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
    else:
        # No next block: show prev block header at top, current at bottom
        top_name = prv[0] if prv else "?"

    top_emojis = bo_emojis.get(top_name, "")
    top_pts = STATE.block_points.get(top_name, 0)
    top_label = f"{top_name}{' ' + top_emojis if top_emojis else ''}"
    top_label += scroll_suffix
    out: list[tuple[str, str]] = section_rule(top_label, focus=True, pts=top_pts)

    slot = start
    gcal_shown: set[str] = set()  # track gcal event titles already labelled
    toggl_shown: set[str] = set()  # track toggl descriptions already labelled
    while slot < end:
        slot_end = slot + dt.timedelta(minutes=SLOT_MIN)

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

        # The slot containing "now" is the live row: ▶ + task + ticking elapsed
        # (no separate inserted now-line, so each block stays exactly 8 slots;
        # the live wall clock lives on the pinned bottom bar).
        time_str = f"{slot:%H:%M}"
        is_now_slot = bool(slot <= now < slot_end)
        is_running = bool(STATE.current) and is_now_slot
        # Only show the red NO TIME ENTRY alarm when we've CONFIRMED no timer.
        # An unconfirmed state (rate-limited fetch) renders as a plain slot.
        is_idle_now = is_now_slot and STATE.current_known and not STATE.current
        if is_running:
            cur_desc = display_desc(STATE.current.get("description") or "")
            cur_code = proj_code(STATE.current.get("project_id"))
            pid = STATE.current.get("project_id")
            try:
                cst = dt.datetime.fromisoformat(STATE.current.get("start", "")).astimezone(TZ)
                _el = (now - cst).total_seconds()
                _m, _s = divmod(max(0, int(_el)), 60)
                _fr = int((_el % 1) * 10)  # tenths heartbeat on the task clock
                elapsed = f"{_m}m{_s:02d}.{_fr}s"
            except Exception:
                elapsed = ""
            label = f"▶ {cur_desc}" + (f" · {cur_code}" if cur_code else "")
            if elapsed:
                label += f"  {elapsed}"
        elif is_idle_now:
            # No timer running: flashing alarm in the spot the task would be.
            since = _idle_since(now)
            gap = ""
            if since is not None:
                _gt = max(0.0, (now - since).total_seconds())
                _gm, _gs = divmod(int(_gt), 60)
                _gfr = int((_gt % 1) * 10)  # tenths, matching the running clock
                gap = f"  {_gm}m{_gs:02d}.{_gfr}s"
            # Flash the cursor every 0.5s (toggle each half-second).
            cursor = "█" if int(now.timestamp() * 2) % 2 == 0 else " "
            label = f"{cursor} NO TIME ENTRY{gap}"
            pid = None

        # The now-row (running task or idle alarm) draws a rule across the
        # width so "you are here" stands out from the plain slots.
        if is_running or is_idle_now:
            cls = ("class:no_entry" if is_idle_now
                   else (f"bold {project_style(pid)}".strip() or "class:running"))
            prefix = f" {time_str} "
            trail = max(0, WIDTH_HINT - dwidth(prefix) - dwidth(label) - 1)
            out.append(("class:time", prefix))
            out.append((cls, f"{label} " + "─" * trail + "\n"))
            slot = slot_end
            continue

        # Deduplicate Toggl labels: show description only on first slot
        if label and not label.startswith("◇ "):
            if label in toggl_shown:
                label = "″"
            else:
                toggl_shown.add(label)

        space = min(DESC_MAX, max(1, WIDTH_HINT - len(time_str) - 4))
        content = f" {marker} {truncate(label or '·', space)}\n"
        if slot_end <= now:
            cls = project_style(pid) or "class:past"
        else:
            cls = gcal_sty or "class:future"
        out.append(("class:time", f" {time_str}"))
        out.append((cls, content))
        slot = slot_end
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
        out += section_rule(bot_label, focus=True, pts=bot_pts)
    return out


def _slot_label_toggl(slot_s, slot_e):
    overlapping = [e for e in STATE.entries if e["start_dt"] < slot_e and e["end_dt"] > slot_s]
    if not overlapping:
        return "", None
    if len(overlapping) == 1:
        e = overlapping[0]
        code = proj_code(e["project_id"])
        return (f"{display_desc(e['desc']) or '(blank)'}" + (f"  · {code}" if code else ""), e["project_id"])
    descs = ", ".join(dict.fromkeys(display_desc(e["desc"]) or "?" for e in overlapping))
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
    """Future blocks (detail-band end → 22:00), gcal-filled, in the same
    compact format as the past (morning) view."""
    _, end = detail_window()
    cutoff = end
    bo_emojis = _read_block_emojis()
    out: list[tuple[str, str]] = []
    for name, sh, eh in BLOCKS:
        if eh + 1 <= cutoff.hour:
            continue
        if sh >= 22:
            break
        picks = _future_block_picks(name, STATE.events)
        out += _compact_block_lines(name, sh, picks, 0, bo_emojis.get(name, ""))
    # Sleep marker
    rule_text = " 睡觉 "
    trail = max(0, WIDTH_HINT - 1 - len(rule_text))
    out.append(("class:rule", "─"))
    out.append((f"fg:{PROJECT_COLORS.get('睡觉', '#666666')}", rule_text))
    out.append(("class:rule", "─" * trail + "\n"))
    return out


def render_current_bottom() -> list[tuple[str, str]]:
    """Mirror of the running timer, pinned above the footer so it's always visible.
    Clock on left, timer desc on right, sub-second decimals as a heartbeat."""
    now = dt.datetime.now(TZ)
    clock = f" {now:%H:%M:%S}"  # wall clock: no sub-second; heartbeat lives on the task timer
    cur = STATE.current
    if not cur:
        return [("class:time", clock), ("class:idle", "  (no timer)\n")]
    desc = display_desc(cur.get("description") or "") or "(no description)"
    pid = cur.get("project_id")
    code = proj_code(pid)
    try:
        st = dt.datetime.fromisoformat(cur.get("start", "")).astimezone(TZ)
        elapsed = (now - st).total_seconds()
    except Exception:
        elapsed = 0.0
    m, s = divmod(max(0, int(elapsed)), 60)
    frac = int((elapsed % 1) * 10)  # tenths of a second
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
    # One line: the flash when active (auto-expires), otherwise the key hints.
    # Collapsing to a single line keeps the input box pinned tight to the bottom.
    if STATE.flash and time.monotonic() < STATE.flash_until:
        sty = STATE.flash_style or "class:flash"
        return [(sty, f" ▸ {STATE.flash}\n")]
    return [("class:hint", " type to run · ^S stop · ^R refresh · ^J/^K scroll · ^Q quit\n")]


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


# The input is always focused (Claude/dtd style): you just type a tg command —
# a timer shortcode, "stop", etc. — and press Enter. Controls live on Ctrl-keys
# so plain letters always type into the box. prompt_toolkit runs the terminal in
# raw mode, so Ctrl+S/Ctrl+Q are real key events here, not XOFF/XON flow control.


def _boot_grace_active(window: float = 2.0) -> bool:
    """True while tg-tui is still booting. The tty can hold queued text from
    the spawning terminal (cmux respawn-pane types the launch command into the
    pane); without this gate that text + newline reaches the enter handler and
    starts a Toggl timer named after the command line (regression 2026-06-11:
    timer 'python3 ~/i446-monorepo/tools/tg/tg-tui.py')."""
    return time.monotonic() - STATE.boot_time < window


@kb.add("enter")
def _(event):
    text = input_buffer.text.strip()
    input_buffer.reset()
    if not text:
        return
    if _boot_grace_active():
        flash(f"ignored startup input: {text[:30]}", 4.0)
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


@kb.add("c-q")
@kb.add("c-c")
def _(event):
    event.app.exit()


@kb.add("c-s")
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


@kb.add("c-r")
def _(event):
    flash("refreshing…")

    async def _refresh():
        await asyncio.to_thread(fetch_current)
        await asyncio.to_thread(fetch_today)
        await asyncio.to_thread(fetch_gcal, True)
        flash("refreshed")
        event.app.invalidate()

    event.app.create_background_task(_refresh())


@kb.add("c-j")  # scroll the detail band forward (toward later blocks)
def _(event):
    STATE.scroll_min += 30


@kb.add("c-k")  # scroll back (toward earlier blocks)
def _(event):
    STATE.scroll_min -= 30


@kb.add("escape")  # snap the detail band back to now
def _(event):
    STATE.scroll_min = 0


main_window = Window(
    content=FormattedTextControl(render_all, focusable=False),
    wrap_lines=False,
    width=Dimension(preferred=WIDTH_HINT),
)

# Pinned bottom bar: current timer + flash/hint (never scrolls). Two lines so
# the input box below sits flush at the very bottom of the screen.
bottom_bar = Window(
    content=FormattedTextControl(render_bottom_bar),
    height=2,  # timer line + single flash/hint line
    wrap_lines=False,
)


def render_input_rule():
    # Horizontal border above the input, mirroring dtd's --input-border and
    # Claude's boxed prompt — separates the always-on command line from content.
    return [("class:rule", "─" * WIDTH_HINT + "\n")]


def render_input_prompt():
    # Permanent "> " prompt; the input is always focused, so it reads as a
    # live command box (type a tg shortcode / "stop" and press Enter).
    return [("class:prompt", " > ")]


rule_window = Window(content=FormattedTextControl(render_input_rule), height=1, wrap_lines=False)
input_window = Window(
    content=BufferControl(buffer=input_buffer, focusable=True),
    height=1,
)
prompt_window = Window(content=FormattedTextControl(render_input_prompt), height=1, width=Dimension.exact(3))

from prompt_toolkit.layout import VSplit  # noqa: E402

input_row = VSplit([prompt_window, input_window])
root = HSplit([main_window, bottom_bar, rule_window, input_row])

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
    "no_entry": "bold #ff4444",
    "flash": "bold yellow",
    "hint": "italic #666666",
    "prompt": "bold cyan",
})

def _no_timer_flash_on() -> bool:
    """Whole-screen flash cue: true during the first half of each second
    while no Toggl timer is running — a nag to go start one. Only fires once
    we've CONFIRMED no timer (current_known); a rate-limited / failed fetch is
    'unknown', not 'idle', so it must not flash over a live timer."""
    if STATE.current or not STATE.current_known:
        return False
    return dt.datetime.now(TZ).microsecond < 500_000


class _NoTimerFlash(StyleTransformation):
    """Invert fg/bg across the entire screen during the flash 'on' phase.
    Toggled at 1 Hz by the wall clock; the app's 0.1s refresh animates it."""

    def transform_attrs(self, attrs):
        if _no_timer_flash_on():
            return attrs._replace(reverse=not attrs.reverse)
        return attrs

    def invalidation_hash(self):
        # Flip the style cache key when the phase changes so each 0.1s
        # refresh actually redraws the inverted/normal frame.
        return _no_timer_flash_on()


app = Application(layout=Layout(root, focused_element=input_window),
                  key_bindings=kb, full_screen=True, style=style,
                  style_transformation=_NoTimerFlash(),
                  refresh_interval=0.1)


PID_FILE = Path.home() / ".cache" / "tg-tui.pid"


def _owns_pid_file() -> bool:
    try:
        return PID_FILE.read_text().strip() == str(os.getpid())
    except OSError:
        return False


def _assert_pid_file():
    """(Re-)register this instance for SIGUSR1 notifications.

    toggl_cli/tg-fast/did push instant refreshes via the pid in this file; if
    it's missing or stale, every timer change degrades to the 30s poll and the
    idle alarm flashes over a freshly started task. A second instance's exit
    cleanup used to delete the live instance's registration — self-heal by
    re-asserting ownership on every current tick."""
    if _owns_pid_file():
        return
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
    except OSError:
        pass


def _release_pid_file():
    """Exit cleanup: unlink only if WE own the registration — never delete
    another live instance's pid file."""
    if _owns_pid_file():
        PID_FILE.unlink(missing_ok=True)


async def ticker_current(app):
    while True:
        await asyncio.sleep(30)
        fetch_current()
        _assert_pid_file()
        app.invalidate()


async def ticker_today(app):
    while True:
        await asyncio.sleep(300)
        fetch_today()
        app.invalidate()


async def ticker_gcal(app):
    while True:
        await asyncio.sleep(300)
        # gcal+outlook can take up to ~15s (subprocess timeouts) — keep it off
        # the event loop (UI freeze) and on a daemon thread (exit block).
        _bg_fetch(app, fetch_gcal)


async def ticker_points(app):
    while True:
        await asyncio.sleep(120)
        _bg_fetch(app, fetch_points)


async def _sigusr1_refresh():
    """Triggered by SIGUSR1: immediate full refresh (e.g. after /did starts a timer).

    Every fetch stays OFF the event loop: fetch_points is Excel-over-ssh
    (4-15s) and the Toggl reads are network calls. Running them inline froze
    repaints exactly when the idle nag was mid-flash — the screen stuck in
    the inverted frame until the refresh finished. fetch_current goes first
    with its own repaint so the nag clears the moment the timer is confirmed."""
    old_count = len(STATE.entries)
    await asyncio.to_thread(fetch_current)
    app.invalidate()
    await asyncio.to_thread(fetch_today)
    await asyncio.to_thread(fetch_short_names)  # /did may have rewritten the cache
    # If entry count grew (task completed → new entry, or timer stopped),
    # flash purple as a prayer/mindfulness prompt
    if len(STATE.entries) != old_count or STATE.current is None:
        flash("☀️", 6.0, style="bold fg:#aa00ff")
    app.invalidate()
    _bg_fetch(app, fetch_points)  # slowest, repaints itself when done


def _bg_fetch(app, fn):
    """Run a slow fetch on a daemon thread, repaint when done.

    Daemon (not asyncio.to_thread): executor threads are non-daemon, so an
    in-flight 15s gcal fetch would block process exit after 'q'. fetch_* are
    all internally try/except'd; invalidate() is thread-safe."""
    import threading

    def run():
        try:
            fn()
        finally:
            try:
                app.invalidate()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()


async def _initial_slow_fetches(app):
    """First gcal + points load, off the event loop. These take ~4-15s
    (Excel-over-ssh, gcal/outlook subprocess timeouts); running them before
    first paint left the terminal blank for ~20s — it looked like a hang."""
    _bg_fetch(app, fetch_gcal)
    _bg_fetch(app, fetch_points)


async def main():
    # Fast fetches only (sub-second) — enough content for an instant first paint.
    fetch_current()
    fetch_today()
    fetch_short_names()  # dtd's abbreviated labels (local file read)

    # SIGUSR1 → instant refresh (sent by /did, /tg, /done after timer changes)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(_sigusr1_refresh()))

    # Write PID so other tools can signal us
    _assert_pid_file()

    # Arm the boot grace from the moment the app actually takes the tty
    STATE.boot_time = time.monotonic()

    app.create_background_task(_initial_slow_fetches(app))
    app.create_background_task(ticker_current(app))
    app.create_background_task(ticker_today(app))
    app.create_background_task(ticker_gcal(app))
    app.create_background_task(ticker_points(app))
    try:
        await app.run_async()
    finally:
        _release_pid_file()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
