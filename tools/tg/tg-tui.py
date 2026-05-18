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
import gcal_client  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
TG_FAST = str(Path("~/i446-monorepo/tools/tg/tg-fast.py").expanduser())
WIDTH_HINT = 50  # informs collapse logic, not strict
DETAIL_BEFORE_MIN = 120
DETAIL_AFTER_MIN = 120
SLOT_MIN = 15

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
        self.events: list[dict] = []  # today's gcal events
        self.scroll_min = 0  # detail band scroll (minutes offset from now)
        self.flash = ""  # one-line status
        self.flash_until = 0.0
        self.command_mode = False
        self.last_toggl_fetch = 0.0
        self.last_gcal_fetch = 0.0
        self.last_current_fetch = 0.0


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
        STATE.events = gcal_client.list_events(day_start, day_end, force=force)
        STATE.last_gcal_fetch = time.monotonic()
    except Exception as e:
        flash(f"gcal err: {e}")


# ─── Helpers ───────────────────────────────────────────────────────────────

def flash(msg: str, secs: float = 4.0):
    STATE.flash = msg
    STATE.flash_until = time.monotonic() + secs


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
    """Return (start, end) datetimes for the detail band, snapped to SLOT_MIN."""
    now = dt.datetime.now(TZ) + dt.timedelta(minutes=STATE.scroll_min)
    base = now.replace(second=0, microsecond=0)
    base = base - dt.timedelta(minutes=base.minute % SLOT_MIN)
    start = base - dt.timedelta(minutes=DETAIL_BEFORE_MIN)
    end = base + dt.timedelta(minutes=DETAIL_AFTER_MIN)
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
    title = f" tg · {now:%a %H:%M:%S} "
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


def section_rule(label: str) -> list[tuple[str, str]]:
    s = f"─ {label} "
    return [("class:rule", s + "─" * max(0, WIDTH_HINT - len(s)) + "\n")]


def render_morning() -> list[tuple[str, str]]:
    """Toggl-only collapsed view from 00:00 -> detail-band start."""
    start, _ = detail_window()
    cutoff = start
    out: list[tuple[str, str]] = section_rule("morning · toggl")
    items = [e for e in STATE.entries if e["start_dt"] < cutoff]
    if not items:
        out.append(("class:dim", "  (nothing logged)\n"))
        return out
    # Merge adjacent same-desc runs
    merged = []
    for e in items:
        end = min(e["end_dt"], cutoff)
        if merged and merged[-1]["desc"] == e["desc"]:
            merged[-1]["end_dt"] = end
        else:
            merged.append({"start_dt": e["start_dt"], "end_dt": end, "desc": e["desc"], "project_id": e["project_id"]})
    for m in merged:
        mins = int((m["end_dt"] - m["start_dt"]).total_seconds() // 60)
        if mins < 1:
            continue
        code = proj_code(m["project_id"])
        label = m["desc"] or "(blank)"
        if code:
            label = f"{label} · {code}"
        right = fmt_dur(mins)
        space = max(1, WIDTH_HINT - 2 - 6 - len(right) - 1)
        style = project_style(m["project_id"])
        out.append((style, f"  {m['start_dt']:%H:%M} {pad(truncate(label, space), space)} {right}\n"))
    return out


def render_detail() -> list[tuple[str, str]]:
    start, end = detail_window()
    out: list[tuple[str, str]] = section_rule(
        "detail · ±2h" + (f" (+{STATE.scroll_min}m)" if STATE.scroll_min else "")
    )
    now = dt.datetime.now(TZ)
    slot = start
    now_drawn = False
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

        # Insert now line before this slot if applicable
        if not now_drawn and slot >= now:
            if STATE.current:
                cur_desc = STATE.current.get("description") or ""
                cur_code = proj_code(STATE.current.get("project_id"))
                try:
                    cst = dt.datetime.fromisoformat(STATE.current.get("start", "")).astimezone(TZ)
                    elapsed = fmt_dur_live(int((now - cst).total_seconds()))
                except Exception:
                    elapsed = "0m00s"
                task_info = f"▶ {cur_desc}"
                if cur_code:
                    task_info += f" · {cur_code}"
                task_info += f"  {elapsed}"
                now_text = f" ── {now:%H:%M:%S}  {task_info} "
            else:
                now_text = f" ── {now:%H:%M:%S}  (no timer) "
            out.append(("class:now", now_text + "─" * max(0, WIDTH_HINT - len(now_text)) + "\n"))
            now_drawn = True

        time_str = f"{slot:%H:%M}"
        is_running = STATE.current and slot <= now < slot_end
        if is_running:
            cur_desc = STATE.current.get("description") or ""
            label = f"▶ {cur_desc}"
            pid = STATE.current.get("project_id")
        space = max(1, WIDTH_HINT - len(time_str) - 4)
        line = f" {time_str} {marker} {truncate(label or '·', space)}\n"
        if is_running:
            cls = f"bold {project_style(pid)}".strip() or "class:running"
        elif slot_end <= now:
            cls = project_style(pid) or "class:past"
        else:
            cls = gcal_sty or "class:future"
        out.append((cls, line))
        slot = slot_end
    if not now_drawn:
        now_text = f" ── now {now:%H:%M:%S} "
        out.append(("class:now", now_text + "─" * max(0, WIDTH_HINT - len(now_text)) + "\n"))
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
    day_end = dt.datetime.now(TZ).replace(hour=23, minute=59, second=59, microsecond=0)
    out: list[tuple[str, str]] = section_rule("evening · gcal")
    items = [
        ev for ev in STATE.events
        if ev["end_dt"] > cutoff and ev["start_dt"] < day_end
        and ev.get("transparency") != "transparent"
    ]
    if not items:
        out.append(("class:dim", "  (nothing scheduled)\n"))
        return out
    for ev in items:
        s = max(ev["start_dt"], cutoff)
        e = min(ev["end_dt"], day_end)
        mins = int((e - s).total_seconds() // 60)
        if mins < 1:
            continue
        prefix = "all-day" if ev.get("all_day") else f"{ev['start_dt']:%H:%M}"
        right = fmt_dur(mins)
        space = max(1, WIDTH_HINT - 2 - len(prefix) - 1 - len(right) - 1)
        title = pad(truncate(ev["title"], space), space)
        ev_sty = project_style(gcal_project_code(ev)) or "class:future"
        out.append((ev_sty, f"  {prefix} {title} {right}\n"))
    return out


def render_outlook() -> list[tuple[str, str]]:
    out = section_rule("outlook")
    out.append(("class:dim", "  (placeholder — wire later)\n"))
    return out


def render_footer() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if STATE.flash and time.monotonic() < STATE.flash_until:
        out.append(("class:flash", f" ▸ {STATE.flash}\n"))
    out.append(("class:hint", " [c]hange [s]top [r]efresh [j/k]scroll [q]uit\n"))
    return out


def render_all() -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    parts += render_header()
    parts += render_morning()
    parts += render_detail()
    parts += render_evening()
    parts += render_outlook()
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


def render_input_prompt():
    if STATE.command_mode:
        return [("class:prompt", " tg> ")]
    return [("class:hint", " (press c to change task)\n")]


input_window = Window(
    content=BufferControl(buffer=input_buffer, focusable=True),
    height=1,
)
prompt_window = Window(content=FormattedTextControl(render_input_prompt), height=1, width=Dimension.exact(5))

from prompt_toolkit.layout import VSplit  # noqa: E402

bottom = VSplit([prompt_window, input_window])
root = HSplit([main_window, bottom])

style = Style.from_dict({
    "header": "bold cyan",
    "running": "bold green",
    "idle": "italic #888888",
    "rule": "#666666",
    "dim": "italic #888888",
    "past": "#aaaaaa",
    "future": "#dddddd",
    "now": "bold #ff1493",
    "flash": "bold yellow",
    "hint": "italic #666666",
    "prompt": "bold cyan",
})

app = Application(layout=Layout(root, focused_element=main_window),
                  key_bindings=kb, full_screen=True, style=style)


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


async def ticker_clock(app):
    while True:
        await asyncio.sleep(1)
        app.invalidate()


async def _sigusr1_refresh():
    """Triggered by SIGUSR1: immediate full refresh (e.g. after /did starts a timer)."""
    fetch_current()
    fetch_today()
    app.invalidate()


async def main():
    fetch_current()
    fetch_today()
    fetch_gcal()

    # SIGUSR1 → instant refresh (sent by /did, /tg, /done after timer changes)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(_sigusr1_refresh()))

    # Write PID so other tools can signal us
    pid_file = Path.home() / ".cache" / "tg-tui.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    app.create_background_task(ticker_clock(app))
    app.create_background_task(ticker_current(app))
    app.create_background_task(ticker_today(app))
    app.create_background_task(ticker_gcal(app))
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
