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
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
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
from blocks import is_future_block  # noqa: E402  shared future-block gate

# dtd's Haiku title shortener, reused for long calendar event titles. Optional:
# tg-tui must still boot if the did tooling (or lib/state_paths) is broken.
try:
    sys.path.insert(0, str(Path("~/i446-monorepo/tools/did").expanduser()))
    import shorten as _dtd_shorten  # noqa: E402
except Exception:
    _dtd_shorten = None

TZ = ZoneInfo("America/Los_Angeles")
TG_FAST = str(Path("~/i446-monorepo/tools/tg/tg-fast.py").expanduser())
# Layout widths track the actual pane. tg-tui is the narrow companion to dtd:
# on a tty we measure the real column count so a wider 1/8-XDR pane shows fuller
# descriptions (and a narrower one never overflows the pane — the old fixed 50
# could), capped so it stays narrow. Off-tty (pytest, pipes) we keep the legacy
# fixed values so the layout-snapshot tests stay deterministic.
def _pane_cols() -> int:
    try:
        if sys.stdout.isatty():
            return shutil.get_terminal_size().columns
    except Exception:
        pass
    return 0

_cols = _pane_cols()
if _cols >= 40:
    WIDTH_HINT = min(_cols - 1, 64)      # fill the pane; cap keeps it narrow
    DESC_MAX = max(24, WIDTH_HINT - 16)  # descriptions scale with available width
    EVENT_SHORT_COLS = max(33, WIDTH_HINT - 8)  # Haiku-shorten only titles wider than this
else:
    WIDTH_HINT = 50  # informs collapse logic, not strict
    DESC_MAX = 24    # max display width for task/event descriptions
    EVENT_SHORT_COLS = 33  # event titles wider than this get a Haiku short name
GAP_MIN = 5  # untracked minutes in a past block before the gap earns a row
EVENT_SHORTS = Path("~/.cache/tg-tui/event-shortnames.json").expanduser()
# Earthly branch blocks (name, start_hour, end_hour inclusive). Local table:
# carries end-hours and the 子 sleep block for layout. Canonical start schedule
# + the future-block gate live in lib/blocks.py (is_future_block).
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
        self.day_offset = 0  # 0=today, -1=yesterday, … (≤0; for filling gaps)
        self.flash = ""  # one-line status
        self.flash_until = 0.0
        self.flash_style = ""  # optional override style for flash
        self.today_points = 0  # 分 earned today
        self.block_points: dict[str, int] = {}  # per-block 分
        # Current (in-progress) block's running 分, computed in fetch_points from
        # the UNROUNDED Σ and locked-block values and rounded once — so it matches
        # the sheet's own residual cell instead of compounding per-term rounding
        # (the 287-vs-288 bug from a 217.5分 locked block).
        self.block_running_pts = 0
        self.last_toggl_fetch = 0.0
        self.last_gcal_fetch = 0.0
        self.last_current_fetch = 0.0
        self.last_points_fetch = 0.0


STATE = State()


# ─── Data fetchers ─────────────────────────────────────────────────────────

def fetch_current(cached=False):
    """Refresh the running timer. cached=True rides the shared current cache
    (used by the steady 30s ticker, so tg-tui and every open dtd picker share
    ~one fetch per window); the post-command bursts pass cached=False to force a
    live read that beats Toggl's /current propagation lag."""
    try:
        STATE.current = (toggl_api.get_current_cached() if cached
                         else toggl_api.get_current())
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
        today = view_now().date()  # the viewed day (today, or a past day)
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
        now = view_now()  # viewed day's calendar
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
        _shorten_events(combined)
    except Exception as e:
        flash(f"gcal err: {e}")


# ── Event title shortening (dtd's Haiku shortener, sidecar-cached) ──────────

_event_shorts_lock = threading.Lock()
_event_shorts_failed: set[str] = set()  # titles Haiku failed on; skip until restart


def event_title(ev: dict) -> str:
    """Display title: the Haiku short name when one exists, else the raw title."""
    return ev.get("short") or ev.get("title") or ""


def _load_event_shorts() -> dict:
    try:
        return json.loads(EVENT_SHORTS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _shorten_events(events: list[dict]) -> None:
    """Attach 'short' to events whose title is too wide for a detail row.

    Cached names (sidecar JSON, keyed by title hash so recurring events hit
    forever) apply synchronously; misses go to a daemon thread because each is
    a Haiku round-trip — both fetch_gcal entry points (refresh ticker thread,
    ctrl-r to_thread) must return without waiting on the network."""
    if _dtd_shorten is None:
        return
    by_hash: dict[str, list[dict]] = {}
    for ev in events:
        title = ev.get("title") or ""
        if dwidth(title) > EVENT_SHORT_COLS and title not in _event_shorts_failed:
            h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            by_hash.setdefault(h, []).append(ev)
    if not by_hash:
        return
    cache = _load_event_shorts()
    misses = {}
    for h, evs in by_hash.items():
        if h in cache:
            for ev in evs:
                ev["short"] = cache[h]
        else:
            misses[h] = evs
    if misses:
        threading.Thread(
            target=_fill_event_shorts, args=(misses,), daemon=True
        ).start()


def _fill_event_shorts(by_hash: dict[str, list[dict]], max_new: int = 6) -> None:
    """Haiku-shorten up to max_new uncached titles, persist, repaint. The lock
    serializes the sidecar read-modify-write across concurrent refreshes."""
    with _event_shorts_lock:
        cache = _load_event_shorts()
        dirty = False
        for h, evs in list(by_hash.items())[:max_new]:
            title = evs[0].get("title") or ""
            short = cache.get(h)
            if short is None:
                short = _dtd_shorten._haiku_shorten(title)
                if short:
                    cache[h] = short
                    dirty = True
                else:
                    _event_shorts_failed.add(title)
                    continue
            for ev in evs:
                ev["short"] = short
        if dirty:
            try:
                EVENT_SHORTS.parent.mkdir(parents=True, exist_ok=True)
                tmp = EVENT_SHORTS.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
                tmp.replace(EVENT_SHORTS)
            except OSError:
                pass
    try:
        app.invalidate()
    except Exception:
        pass


def fetch_points():
    """Read today's 分 from Neon 0分: Σ total (col D) + per-block points (G:O).

    The topline total is the Σ column (D) — the authoritative grand total the
    personal dashboard also reads (see dashboard.py /api/points-today). Reading
    the same cell keeps the two toplines in lockstep. Summing per-domain columns
    (R:Y) here undercounts: it omits Q (g245/infra/0g), Z (n156), and the -1₦
    penalty in P, which is exactly why the numbers used to diverge.

    Per-block points come from columns G:O (headed 卯辰巳午未申酉戌亥) in the
    same row, read as FORMULAS. Blocks lock sequentially to literals; an
    unlocked block is the residual `=D-SUM(locked)`, which dumps the whole
    unallocated day into the first unlocked block. Those `=…` cells are skipped
    so only locked literal earnings show (the "everything piles into 巳" bug).
    The completed-today.json timestamp reconstruction is a fallback only: it
    attributes points to the block they were logged in, which piles batch-logged
    work into the current block (the "everything shows in 申" bug).
    """
    try:
        now = view_now()  # viewed day's 0分 row
        today_md = f"{now.month}/{now.day}"

        # Read the Σ total (column D) AND the per-block columns (G:O, headed
        # 卯辰巳午未申酉戌亥) for today's row in one ix-osa call. G:O is the
        # authoritative per-block distribution — reconstructing blocks from
        # completed-today.json logging timestamps lumps batch-logged points
        # into whichever block they were *recorded* in, not earned in.
        bp_excel: dict[str, int] = {}
        read_ok = False
        total_ok = False
        cand_f = None        # unrounded Σ total, for round-once residual
        locked_raw = 0.0     # unrounded sum of locked literal blocks
        raw_out = ""
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
            set v to (get formula of cell c of row todayRow of ws) as text
        end try
        set out to out & "|" & v
    end repeat
    repeat with c from 16 to 25
        set pv to ""
        try
            set pv to (value of cell c of row todayRow of ws) as text
        end try
        set out to out & "|" & pv
    end repeat
    repeat with c from 7 to 15
        set gv to ""
        try
            set gv to (value of cell c of row todayRow of ws) as text
        end try
        set out to out & "|" & gv
    end repeat
    return out
end tell'''
            r = _sp.run([IX_OSA], input=script,
                        capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip() not in ("", "ERR"):
                read_ok = True
                raw_out = r.stdout.strip()
                parts = raw_out.split("|")
                val = parts[0].strip()
                # Handle formula strings like "70+12" defensively. Keep the
                # unrounded float (cand_f) so the current-block residual rounds
                # exactly once (see the adoption gate below).
                candidate = None
                try:
                    cand_f = float(val)
                except ValueError:
                    try:
                        cand_f = float(eval(val))  # safe: digits and +
                    except Exception:
                        cand_f = None
                if cand_f is not None:
                    candidate = int(round(cand_f))
                # Commit the Σ total only when the read is trustworthy. Col D
                # (=SUM(P:Y)) is read mid-recalc during did/daemon writes and
                # transiently returns garbage — the rejection log has shown D=-46
                # and high spikes (4351, 1523) on a settled ~750分 day. A fixed cap
                # can't catch a spike that lands under it (1523 < cap), so cross-
                # check D against its own input range P:Y (read as values in the
                # same pass, parts[10:20]): a torn snapshot has the formula cache
                # disagreeing with the freshly-read cells. This poisoned the
                # current-block 分 reconstruction (Σ − locked); keep the last good
                # total on an untrustworthy read.
                sum_py = None
                py_parts = parts[10:20]
                if len(py_parts) == 10:
                    try:
                        sum_py = int(round(sum(float(p) for p in py_parts if p.strip())))
                    except ValueError:
                        sum_py = None  # non-numeric cell → torn; fall back to cap
                total_ok = _total_trustworthy(candidate, sum_py)
                if total_ok:
                    STATE.today_points = candidate
                branches = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
                # G:O are read as FORMULAS so a locked literal can be told apart
                # from the live residual `=D-SUM(locked)`; the cells' computed
                # VALUES are read in the same pass (parts[20:29]) so the residual
                # block can be mirrored straight from Neon.
                gio_vals = parts[20:29]
                for idx, (bname, raw) in enumerate(zip(branches, parts[1:10])):
                    raw = raw.strip()
                    if not raw:
                        continue
                    if raw.startswith("="):
                        # Residual cell: not a locked literal, but its VALUE is
                        # exactly what Neon shows for this block. Blocks lock
                        # sequentially, so the first unlocked block carries the
                        # running unallocated total and later residuals resolve to
                        # 0. Read it straight from the sheet instead of pinning
                        # Σ−locked to the *clock* block — that left 申 at 0 while
                        # Neon showed 90 because 未 locked ahead of the clock.
                        try:
                            rvv = float(gio_vals[idx].strip())
                        except (ValueError, IndexError, AttributeError):
                            continue
                        vv = int(round(rvv))
                        if vv > 0:
                            bp_excel[bname] = vv
                        continue
                    try:
                        rv = float(raw)
                    except ValueError:
                        continue
                    locked_raw += rv  # unrounded, for the cold-start residual
                    v = int(round(rv))
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
            STATE.last_points_fetch = time.monotonic()
            # Only adopt fresh blocks alongside a freshly-accepted total — never
            # pair new blocks with a stale total kept from a torn read.
            if total_ok and _blocks_consistent(STATE.today_points, bp_excel):
                STATE.block_points = bp_excel
                # Current-block running 分 = Σ − locked, rounded ONCE in full
                # precision so it equals the sheet's own residual cell (a 217.5分
                # locked block made the round-each-then-subtract path read 287
                # where the sheet shows 288).
                if cand_f is not None:
                    STATE.block_running_pts = max(0, int(round(cand_f - locked_raw)))
            else:
                # Torn read (implausible total, or daemon lock / did-fast append
                # in flight): keep last good values and leave evidence for diagnosis.
                try:
                    with open("/tmp/tg-tui-points-rejected.log", "a") as fh:
                        fh.write(f"{dt.datetime.now(TZ):%F %T} total_ok={total_ok} "
                                 f"cand={candidate} D={STATE.today_points} "
                                 f"bp={bp_excel} raw={raw_out!r}\n")
                except OSError:
                    pass
    except Exception:
        pass


# Backstop only — used when P:Y can't be read for the cross-check below. Heaviest
# realistic 分 day is well under 1000 (test fixtures top out ~900). Negative is torn.
_MAX_PLAUSIBLE_TOTAL = 2000


def _total_trustworthy(candidate: int | None, sum_py: int | None) -> bool:
    """Whether a col-D read may be committed as today's Σ total.

    D is defined as =SUM(P:Y). A trustworthy read is non-negative AND equals its
    own input range (sum_py, the P:Y cells read in the same pass). A torn mid-
    recalc snapshot has the formula cache disagreeing with the freshly-read cells
    — that catches spikes a fixed cap can't (1523 on a 758分 day). When P:Y is
    unreadable (sum_py is None) fall back to the loose cap so obvious garbage
    (D=-46, D=4351) is still rejected."""
    if candidate is None or candidate < 0:
        return False
    if sum_py is not None:
        return abs(candidate - sum_py) <= 1  # ±1 for float rounding
    return candidate <= _MAX_PLAUSIBLE_TOTAL


def _blocks_consistent(total: int, bp: dict[str, int]) -> bool:
    """Reject 0分 block reads that claim more points than the day's Σ total.

    While any residual formula (=D-SUM(locked)) is live in G:O, sum(blocks)
    == Σ exactly, so sum > Σ means the row was sampled mid-write (2026-06-12:
    未 read 975 of a 728分 day and stuck on screen). After the 22:00 lock all
    blocks are literals and Σ may legitimately exceed their sum, so only the
    sum > Σ direction is rejected."""
    return sum(bp.values()) <= total + 2


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


def view_now() -> dt.datetime:
    """Reference 'now' for the day currently being viewed. day_offset 0 = today
    (live now); a negative offset views a past day, anchored to its end (23:59)
    so the whole day reads as elapsed — every block past, gaps visible — which is
    the layout for filling in missed time entries. Only the day-view fetch +
    render use this; the live clock and running-timer mirror keep real now."""
    if STATE.day_offset == 0:
        return dt.datetime.now(TZ)
    base = dt.datetime.now(TZ) + dt.timedelta(days=STATE.day_offset)
    return base.replace(hour=23, minute=59, second=59, microsecond=0)


def detail_window():
    """Return (start, end) for detail band: current+next block, or prev+current if no next."""
    now = view_now() + dt.timedelta(minutes=STATE.scroll_min)
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
    if STATE.day_offset == 0:
        title = f" tg · {now:%a %H:%M:%S}{pts_str} "
        line = title + "─" * max(0, WIDTH_HINT - len(title))
        return [("class:header", line + "\n")]
    # Viewing a past day: badge the date so it's never mistaken for today.
    viewed = view_now()
    title = f" tg · ◀ {viewed:%a %-m/%-d}{pts_str} · ⎋ today "
    line = title + "─" * max(0, WIDTH_HINT - len(title))
    return [("class:no_entry", line + "\n")]


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


def _read_block_emojis(now: dt.datetime | None = None) -> dict[str, str]:
    """Read build order file, return {branch_char: emoji_string} for today's blocks.

    The build order pre-stamps future block headers (☀️ prayer, 🎯 goal, ✅ done,
    etc.) for the whole day, so a ritual cannot legitimately be "done" in a block
    that hasn't started yet. Drop any block whose start hour is still in the
    future, keeping the in-progress block (start_hour <= now). Without this,
    render_evening() shows prayer/tasks completed for obviously-future blocks."""
    try:
        text = BUILD_ORDER.read_text()
    except Exception:
        return {}
    now = now or view_now()
    block_start = {name: sh for name, sh, _eh in BLOCKS}
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
                sh = block_start.get(branch)
                if sh is not None and is_future_block(sh, now):
                    continue  # block hasn't started yet
                emojis = "".join(ch for ch in BLOCK_EMOJIS if ch in tail)
                if emojis:
                    result[branch] = emojis
    return result


def _gap_alarm_on(now: dt.datetime | None = None) -> bool:
    """Half-second on/off toggle for the past-untracked-time alarm. Animated by
    the app's 0.1s refresh_interval — same cadence as the NO TIME ENTRY cursor.
    Past empty stretches pulse red↔grey on this so untracked time nags."""
    t = (now or dt.datetime.now(TZ)).timestamp()
    return int(t * 2) % 2 == 0


# Placeholder timer labels — tracked time the user hasn't actually categorized.
# These nag (pulse red↔grey, exactly like empty/gap time) until relabelled.
_PLACEHOLDER_LABELS = {"generic placeholder"}


def _is_placeholder(label: str) -> bool:
    """Whether a timer label is an uncategorized placeholder. Tolerates the
    trailing ' · code' suffix and (N)/[N] annotations so it matches however the
    label was formatted for display."""
    if not label:
        return False
    base = label.split(" · ")[0].strip()
    return _clean_annotations(base).lower() in _PLACEHOLDER_LABELS


def _placeholder_style(now: dt.datetime | None = None) -> str:
    """The same red↔grey pulse empty time uses, for placeholder rows."""
    return "class:no_entry" if _gap_alarm_on(now) else "class:idle"


def _abbrev_tcol(hh: int, mm: int, prev_hour: int | None) -> tuple[str, int]:
    """Time column for a body row, abbreviated. Full ``HH:MM`` when the hour
    differs from the row above; otherwise minutes-only ``  :MM`` indented two so
    the colon aligns under the hour column. No leading pad (col 0). Returns
    (text, hour-to-carry-forward)."""
    if prev_hour is not None and hh == prev_hour:
        return f"  :{mm:02d}", prev_hour
    return f"{hh:02d}:{mm:02d}", hh


def _compact_block_lines(blk_name, blk_sh, picks, pts, emojis, cont=None,
                         is_future=False) -> list[tuple[str, str]]:
    """Render one non-focus block as 4 lines: a ``午:00 ☀️📧`` header + 3 body.

    Header is the block's :00 slot. A FUTURE block carries its dominant upcoming
    event inline with the duration as ``(N)`` minutes (``午:00 ☀️ standup (60)``);
    a PAST block shows points right-aligned and keeps the header bare. Body rows
    are the block's three later half-hour marks: each shows an entry (label +
    duration) when one starts in that half-hour, else just the faint time. The
    hour prints only when it rolls over (``13:00`` then ``  :30``). Future
    durations read ``(N)``, past ones ``Nm``. Labels arrive Haiku-shortened
    (event_title / dtd short names); truncate is only the width fallback.

    picks: normalized items {start_dt, time_str, label, style, dur_min[, is_gap]},
    chronological. cont is accepted for signature compatibility but no longer
    drawn — empty marks render as just the time (per the block redesign).
    """
    out: list[tuple[str, str]] = []
    emoji_str = f" {emojis}" if emojis else ""

    # ── header: the block's :00 slot ──
    if is_future and picks:
        head = picks[0]
        body_picks = picks[1:]
        left = f"{blk_name}:00{emoji_str} "
        dur = f"({head['dur_min']})"
        avail = max(1, WIDTH_HINT - dwidth(left) - dwidth(dur) - 1)
        label = truncate(head["label"], avail)
        head_sty = _placeholder_style() if _is_placeholder(head["label"]) else (head["style"] or "class:future")
        # Duration sits right after the label: `午:00 ☀️ GamePass sync (60)`.
        out.append(("class:dim", left))
        out.append((head_sty, label))
        out.append(("class:dim", f" {dur}\n"))
    else:
        body_picks = picks
        left = f"{blk_name}:00{emoji_str}"
        pts_str = f"{pts}分" if pts else ""
        trail = max(1, WIDTH_HINT - dwidth(left) - dwidth(pts_str))
        out.append(("class:dim", left + " " * trail))
        if pts_str:
            out.append(("bold #ffffff", pts_str))
        out.append(("class:dim", "\n"))

    # ── body: 3 half-hour marks after :00, entries on their slots ──
    # Empty marks always render (this is what fixes a future block like 午
    # dropping its 10:30 / 11:00 rows: the old code blank-padded partially-filled
    # blocks instead of showing the remaining grid).
    def _slot(p):
        return (p["start_dt"].hour, 0 if p["start_dt"].minute < 30 else 30)
    # Entries (and gaps) win the 3 rows; empty half-hour marks only fill what's
    # left, so a real entry is never crowded out by a gridline.
    entry_rows = sorted(
        ((p["start_dt"].hour * 60 + p["start_dt"].minute,
          p["start_dt"].hour, p["start_dt"].minute, p) for p in body_picks),
        key=lambda r: r[0])
    rows = entry_rows[:3]
    if len(rows) < 3:
        occupied = {_slot(p) for p in body_picks}
        marks = [(hh * 60 + mm, hh, mm, None)
                 for hh, mm in ((blk_sh, 30), (blk_sh + 1, 0), (blk_sh + 1, 30))
                 if (hh, mm) not in occupied]
        rows = rows + marks[:3 - len(rows)]
        rows.sort(key=lambda r: r[0])

    prev_hour = blk_sh  # the header established this hour at its :00 slot
    for _, hh, mm, p in rows:
        tcol, prev_hour = _abbrev_tcol(hh, mm, prev_hour)
        if p is None:
            if cont and (hh, mm) in cont:
                # A meeting started earlier flows through this slot: keep the
                # ◇ │ continuation rather than a bare time.
                out.append(("class:time", tcol + " "))
                out.append((cont[(hh, mm)] or "class:future", "◇ │\n"))
            else:
                # Genuinely empty: just the time, no fill.
                out.append(("class:time", tcol))
                out.append(("class:idle", "\n"))
            continue
        if p.get("is_gap"):
            # Untracked past stretch (≥ GAP_MIN): ┄ fill pulses red↔grey to nag.
            dur = fmt_dur(p["dur_min"])
            space = max(1, WIDTH_HINT - dwidth(tcol) - 1 - len(dur) - 1)
            fill_cls = "class:no_entry" if _gap_alarm_on() else "class:idle"
            out.append(("class:time", tcol + " "))
            out.append((fill_cls, "┄" * space))
            out.append(("class:no_entry", f" {dur}\n"))
            continue
        dur = f"({p['dur_min']})" if is_future else fmt_dur(p["dur_min"])
        space = max(1, WIDTH_HINT - dwidth(tcol) - 1 - dwidth(dur) - 1)
        sty = _placeholder_style() if _is_placeholder(p["label"]) else p["style"]
        out.append(("class:time", tcol + " "))
        out.append((sty, pad(truncate(p["label"], space), space)))
        out.append(("class:dim", f" {dur}\n"))

    # Pad to exactly 3 body rows so every block stays 4 lines tall.
    for _ in range(3 - len(rows)):
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


def _block_sleep_item(blk_sh, blk_eh, cutoff) -> dict | None:
    """Sleep spillover into a past block, as a synthetic pick.

    The overnight 睡觉 entry starts at 00:00 (day-barrier split), which is
    outside every block, so on a late wake-up the 6:00→wake stretch of 辰 (and
    any fully-slept later block) would silently vanish. Clip the entry to the
    block window so those blocks read 睡觉 instead. Wake time rides the time
    column via the sleep end-time convention."""
    blk_start = cutoff.replace(hour=blk_sh, minute=0, second=0, microsecond=0)
    blk_end = blk_start + dt.timedelta(hours=blk_eh + 1 - blk_sh)
    for e in STATE.entries:
        if (e["desc"] or "").strip() != "睡觉":
            continue
        if e["start_dt"] < blk_start < e["end_dt"]:
            end = min(e["end_dt"], blk_end, cutoff)
            mins = int((end - blk_start).total_seconds() // 60)
            if mins >= 1:
                return {"start_dt": blk_start, "time_str": f"{end:%H:%M}",
                        "label": "睡觉", "style": project_style(e["project_id"]),
                        "dur_min": mins}
    return None


def _block_gaps(blk_sh, blk_eh, cutoff) -> list[dict]:
    """Untracked stretches >= GAP_MIN minutes inside a past block's window,
    chronological. Sweeps raw STATE.entries (not the merged display spans,
    which join same-desc neighbours and would hide stop/resume gaps). A gap
    straddling a block boundary is split, each block reporting its share."""
    blk_start = cutoff.replace(hour=blk_sh, minute=0, second=0, microsecond=0)
    blk_end = blk_start + dt.timedelta(hours=blk_eh + 1 - blk_sh)

    def gap_item(start, end):
        mins = int((end - start).total_seconds() // 60)
        if mins < GAP_MIN:
            return None
        return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": "",
                "style": "", "dur_min": mins, "is_gap": True}

    items = []
    pos = blk_start
    for e in STATE.entries:  # sorted by start_dt
        s, en = e["start_dt"], min(e["end_dt"], cutoff)
        if en <= pos:
            continue
        if s >= blk_end:
            break
        if s > pos and (g := gap_item(pos, min(s, blk_end))):
            items.append(g)
        pos = max(pos, en)
        if pos >= blk_end:
            return items
    if (g := gap_item(pos, blk_end)):
        items.append(g)
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
            "label": event_title(ev),
            "style": project_style(gcal_project_code(ev)),
            "dur_min": mins,
        })
    items.sort(key=lambda x: x["dur_min"], reverse=True)
    items = items[:4]
    items.sort(key=lambda x: x["start_dt"])
    return items


def _block_gcal_cont(blk_sh, ref) -> dict[tuple[int, int], str]:
    """Half-hour marks of a block covered by a gcal event → project style.

    Block picks only see what STARTS in the block, so an event flowing
    through it (a 4h workshop started two blocks earlier, or one spanning the
    whole block) used to leave the block looking empty. Covered marks instead
    draw the focus band's ◇ │ continuation glyphs — in future blocks and,
    through the event's end, in past blocks too."""
    out: dict[tuple[int, int], str] = {}
    for hh, mm in ((blk_sh, 0), (blk_sh, 30), (blk_sh + 1, 0), (blk_sh + 1, 30)):
        t = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for ev in STATE.events:
            if ev.get("transparency") == "transparent" or ev.get("all_day"):
                continue
            if ev["start_dt"] <= t < ev["end_dt"]:
                out[(hh, mm)] = project_style(gcal_project_code(ev))
                break
    return out


def _block_sleep_cont(blk_sh, ref) -> dict[tuple[int, int], str]:
    """Half-hour marks of a past block covered by an overnight 睡觉 entry → style.

    The sleep counterpart to _block_gcal_cont. _block_sleep_item only fills the
    header row, so a late wake-up sleeping clean through a block (e.g. 辰) left
    the rows below it blank. Marking the covered half-hours lets the compact
    renderer draw the ◇ │ continuation after the spillover header, so the block
    reads as 'still asleep' rather than empty."""
    out: dict[tuple[int, int], str] = {}
    for hh, mm in ((blk_sh, 0), (blk_sh, 30), (blk_sh + 1, 0), (blk_sh + 1, 30)):
        t = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for e in STATE.entries:
            if (e["desc"] or "").strip() != "睡觉":
                continue
            if e["start_dt"] <= t < e["end_dt"]:
                out[(hh, mm)] = project_style(e["project_id"])
                break
    return out


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
        out.append(("class:dim", pts_str))
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
        sleep = _block_sleep_item(blk_sh, blk_eh, cutoff)
        if sleep:
            picks = ([sleep] + picks)[:4]
        gaps = _block_gaps(blk_sh, blk_eh, cutoff)
        full_block = (blk_eh + 1 - blk_sh) * 60
        # Drop a single gap that spans the whole (untracked) block — it would
        # just restate the empty grid the body already draws.
        if len(gaps) == 1 and gaps[0]["dur_min"] >= full_block:
            gaps = []
        # The header is now the bare :00 slot, so every entry and gap is a body
        # row. _compact_block_lines merges them with the empty half-hour marks
        # and caps at 3 rows.
        body = picks + gaps
        body.sort(key=lambda x: x["start_dt"])
        cont = {**_block_sleep_cont(blk_sh, cutoff), **_block_gcal_cont(blk_sh, cutoff)}
        out += _compact_block_lines(blk_name, blk_sh, body, pts,
                                    bo_emojis.get(blk_name, ""), cont=cont,
                                    is_future=False)
    return out


def _current_block_running_pts() -> int:
    """Running 分 for the in-progress block. Its 0分 G:O cell is the live residual
    formula =D-SUM(locked), which fetch_points skips, so block_points never holds
    the current block. fetch_points reconstructs it as Σ_today minus the locked
    literal blocks — rounded ONCE in full precision (STATE.block_running_pts) so it
    matches the sheet's residual cell — and under sequential block-locking that
    residual is exactly the current block's earnings (future blocks are 0)."""
    return STATE.block_running_pts


def _block_display_pts(name: str) -> int:
    """Per-block 分 mirrored from Neon's 0分 G:O cells. fetch_points now reads the
    live residual block's VALUE straight from the sheet, so block_points holds
    every nonzero block — locked literals and the in-progress block alike — and
    the displayed number always matches what Neon shows. No Σ−locked re-
    attribution to the clock block (which left 申 at 0 while Neon showed 90 when
    未 locked ahead of the clock). A block the sheet shows empty returns 0.

    The reconstruction survives only as a cold-start fallback: before the first
    successful Neon read block_points is empty, so show the current clock block's
    running total rather than a blank header."""
    if STATE.block_points:
        return STATE.block_points.get(name, 0)
    cur_now = hour_to_block(view_now().hour)
    if cur_now and name == cur_now[0]:
        return _current_block_running_pts()
    return 0


def _detail_merge_past(win_start, win_end) -> list[dict]:
    """Toggl entries overlapping [win_start, win_end), clipped to the window and
    with consecutive same-desc fragments (stop/resume splits ≤60s apart) merged
    into one span. Chronological."""
    segs: list[dict] = []
    for e in STATE.entries:
        if e.get("running"):
            continue  # the live timer is the now-row's tail, not a past span
        s = max(e["start_dt"], win_start)
        en = min(e["end_dt"], win_end)
        if en <= s:
            continue
        desc = display_desc(e["desc"]) or "(blank)"
        if (segs and segs[-1]["desc"] == desc
                and (s - segs[-1]["end"]).total_seconds() <= 60):
            segs[-1]["end"] = en
        else:
            segs.append({"start": s, "end": en, "desc": desc, "pid": e["project_id"]})
    return segs


def _detail_span_row(s, e, body_cls, body_text) -> list[tuple[str, str]]:
    """One focus-band row keyed by an actual HH:MM-HH:MM span (not a 15-min
    grid slot), so completed entries and gaps read at their real boundaries."""
    span = f" {s:%H:%M}-{e:%H:%M} │ "
    space = max(1, WIDTH_HINT - dwidth(span) - 1)
    return [("class:time", span), (body_cls, truncate(body_text, space) + "\n")]


def _detail_gap_row(s, e) -> list[tuple[str, str]]:
    """A flashing untracked-time row keyed by its real HH:MM-HH:MM span."""
    fill_cls = "class:no_entry" if _gap_alarm_on() else "class:idle"
    span = f" {s:%H:%M}-{e:%H:%M} │ "
    fill = "┄" * max(1, WIDTH_HINT - dwidth(span) - 1)
    return [("class:time", span), (fill_cls, fill + "\n")]


def _detail_rule_row(s, label, cls) -> list[tuple[str, str]]:
    """The 'you are here' now-row, anchored to its real start time (ongoing),
    drawn as a full-width rule so it stands out from the spans above."""
    prefix = f" {s:%H:%M}- "
    trail = max(0, WIDTH_HINT - dwidth(prefix) - dwidth(label) - 1)
    return [("class:time", prefix), (cls, f"{label} " + "─" * trail + "\n")]


def _detail_past_rows(win_start, win_end, now, live) -> list[tuple[str, str]]:
    """The focus band's elapsed region as REAL entry spans + flashing gap rows
    (no 15-min rounding). Each tracked entry shows its actual start-end; every
    untracked stretch ≥ GAP_MIN is its own ``HH:MM-HH:MM`` row pulsing red↔grey.
    When ``live``, the tail is the now-row: the running timer anchored to its
    real start with ticking elapsed, or the idle NO-TIME-ENTRY alarm. Otherwise
    (a past day) the trailing untracked stretch is drawn as a gap row."""
    out: list[tuple[str, str]] = []
    cursor = win_start
    for seg in _detail_merge_past(win_start, win_end):
        if (seg["start"] - cursor).total_seconds() >= GAP_MIN * 60:
            out += _detail_gap_row(cursor, seg["start"])
        code = proj_code(seg["pid"])
        label = seg["desc"] + (f"  · {code}" if code else "")
        sty = (_placeholder_style() if _is_placeholder(seg["desc"])
               else (project_style(seg["pid"]) or "class:past"))
        out += _detail_span_row(seg["start"], seg["end"], sty, label)
        cursor = max(cursor, seg["end"])

    if not live:
        if (win_end - cursor).total_seconds() >= GAP_MIN * 60:
            out += _detail_gap_row(cursor, win_end)
        return out

    # Live tail (today, now inside the window).
    if STATE.current:
        try:
            rs = dt.datetime.fromisoformat(STATE.current.get("start", "")).astimezone(TZ)
        except Exception:
            rs = cursor
        rs = max(rs, win_start)
        if (rs - cursor).total_seconds() >= GAP_MIN * 60:
            out += _detail_gap_row(cursor, rs)
        desc = display_desc(STATE.current.get("description") or "")
        code = proj_code(STATE.current.get("project_id"))
        el = max(0.0, (now - rs).total_seconds())
        m, s = divmod(int(el), 60)
        label = f"▶ {desc}" + (f" · {code}" if code else "") + f"  {m}m{s:02d}.{int((el % 1) * 10)}s"
        cls = (f"bold {_placeholder_style(now)}" if _is_placeholder(desc)
               else (f"bold {project_style(STATE.current.get('project_id'))}".strip() or "class:running"))
        out += _detail_rule_row(rs, label, cls)
    elif STATE.current_known:
        # No timer: flashing NO-TIME-ENTRY alarm anchored at the last entry's end.
        since = _idle_since(now) or cursor
        el = max(0.0, (now - since).total_seconds())
        m, s = divmod(int(el), 60)
        curblk = "█" if int(now.timestamp() * 2) % 2 == 0 else " "
        label = f"{curblk} NO TIME ENTRY  {m}m{s:02d}.{int((el % 1) * 10)}s"
        out += _detail_rule_row(since, label, "class:no_entry")
    # else: timer state unconfirmed (rate-limited fetch) → no alarm row.
    return out


def render_detail() -> list[tuple[str, str]]:
    start, end = detail_window()
    now = view_now()
    viewing_today = STATE.day_offset == 0
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
    top_pts = _block_display_pts(top_name)
    top_label = f"{top_name}{' ' + top_emojis if top_emojis else ''}"
    top_label += scroll_suffix
    out: list[tuple[str, str]] = section_rule(top_label, focus=True, pts=top_pts)

    # Elapsed region: real entry spans + flashing gap rows (+ the live now-row
    # tail). Today renders [start, now]; a past day renders the whole window.
    now_in = start <= now <= end
    past_win_end = min(now, end) if viewing_today else end
    out += _detail_past_rows(start, past_win_end, now, live=(viewing_today and now_in))

    # Future region keeps the 15-min gcal preview grid, from the next slot
    # boundary at/after now (skipped entirely on a past-day view).
    gcal_shown: set[str] = set()
    if viewing_today:
        fut = max(now, start)
        slot = fut.replace(minute=(fut.minute // SLOT_MIN) * SLOT_MIN,
                           second=0, microsecond=0)
        if slot < fut:
            slot += dt.timedelta(minutes=SLOT_MIN)
        while slot < end:
            slot_end = slot + dt.timedelta(minutes=SLOT_MIN)
            label, gcal_sty = _slot_label_gcal(slot, slot_end)
            # Show a gcal title only on its first slot, then just the color bar.
            if label and label.startswith("◇ "):
                raw_title = label[2:].strip()
                if raw_title in gcal_shown:
                    label = "◇ │"
                else:
                    gcal_shown.add(raw_title)
            time_str = f"{slot:%H:%M}"
            full = max(1, WIDTH_HINT - len(time_str) - 4)
            space = full if label.startswith("◇ ") else min(DESC_MAX, full)
            content = f" │ {truncate(label or '·', space)}\n"
            out.append(("class:time", f" {time_str}"))
            out.append((gcal_sty or "class:future", content))
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
        bot_pts = _block_display_pts(bot_name)
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
        ev = overlapping[0]
        sty = project_style(gcal_project_code(ev))
        # Exact start time: slots are 15-min, so a 09:05 start would otherwise
        # read as 09:00 off the time column.
        return f"◇ {ev['start_dt']:%H:%M} {event_title(ev)}", sty
    dominant = max(overlapping, key=lambda ev: (min(ev["end_dt"], slot_e) - max(ev["start_dt"], slot_s)).total_seconds())
    sty = project_style(gcal_project_code(dominant))
    titles = ", ".join(event_title(ev) for ev in overlapping)
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
        cont = _block_gcal_cont(sh, cutoff)
        out += _compact_block_lines(name, sh, picks, 0, bo_emojis.get(name, ""),
                                    cont=cont, is_future=True)
    # Sleep marker
    rule_text = " 睡觉 "
    trail = max(0, WIDTH_HINT - 1 - len(rule_text))
    out.append(("class:rule", "─"))
    out.append((f"fg:{PROJECT_COLORS.get('睡觉', '#666666')}", rule_text))
    out.append(("class:rule", "─" * trail + "\n"))
    return out


def render_current_bottom() -> list[tuple[str, str]]:
    """Mirror of the running timer, pinned above the footer so it's always visible.
    The ticking elapsed timer (tenths heartbeat) leads on the LEFT — where the eye
    looks for it — then the desc; the wall clock is right-justified."""
    now = dt.datetime.now(TZ)
    clock = f"{now:%H:%M:%S} "  # wall clock: no sub-second; heartbeat lives on the task timer
    cur = STATE.current
    if not cur:
        return [("class:idle", " (no timer)"),
                ("class:time", f"{clock:>{max(0, WIDTH_HINT - len(' (no timer)'))}}\n")]
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
    left = f" ▶ {dur}  {desc}"
    if code:
        left += f" · {code}"
    pad = max(0, WIDTH_HINT - dwidth(left) - len(clock))
    style = project_style(pid) or "class:running"
    return [
        (f"bold {style}".strip(), left),
        ("class:time", f"{'':>{pad}}{clock}\n"),
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


# NB: the current-timer mirror (render_current_bottom) is pinned ABOVE the input
# box; the key-hint/flash line (render_footer) is pinned BELOW it — mirroring
# Claude Code, where the shortcut hints sit under the prompt. See the root
# HSplit for the ordering.


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
        _bg_fetch(event.app, fetch_points)  # slowest; repaints itself when done

    event.app.create_background_task(_refresh())


@kb.add("c-j")  # scroll the detail band forward (toward later blocks)
def _(event):
    STATE.scroll_min += 30


@kb.add("c-k")  # scroll back (toward earlier blocks)
def _(event):
    STATE.scroll_min -= 30


def _reload_day(app):
    """Re-fetch the viewed day's Toggl entries, calendar, and points after a day
    change, then repaint. Mirrors the c-r refresh but for the new day."""
    async def _r():
        await asyncio.to_thread(fetch_today)
        await asyncio.to_thread(fetch_gcal, True)
        app.invalidate()
        _bg_fetch(app, fetch_points)  # slowest; repaints itself when done
    app.create_background_task(_r())


@kb.add("c-left")  # view the previous day (to fill in missed time entries)
def _(event):
    STATE.day_offset -= 1
    STATE.scroll_min = 0
    flash(f"◀ {view_now():%a %-m/%-d}")
    _reload_day(event.app)


@kb.add("c-right")  # view the next day, capped at today
def _(event):
    if STATE.day_offset >= 0:
        flash("already on today")
        return
    STATE.day_offset += 1
    STATE.scroll_min = 0
    flash("today" if STATE.day_offset == 0 else f"◀ {view_now():%a %-m/%-d}")
    _reload_day(event.app)


@kb.add("escape")  # snap the detail band back to now; reset to today if browsing
def _(event):
    STATE.scroll_min = 0
    if STATE.day_offset != 0:
        STATE.day_offset = 0
        flash("today")
        _reload_day(event.app)


main_window = Window(
    content=FormattedTextControl(render_all, focusable=False),
    wrap_lines=False,
    width=Dimension(preferred=WIDTH_HINT),
)

# Pinned current-timer mirror, just above the input box (never scrolls).
bottom_bar = Window(
    content=FormattedTextControl(render_current_bottom),
    height=1,  # timer line only; key hints render below the input
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

# Key-hint / flash line pinned BELOW the input box (Claude Code style).
footer_window = Window(content=FormattedTextControl(render_footer), height=1, wrap_lines=False)

from prompt_toolkit.layout import VSplit  # noqa: E402

input_row = VSplit([prompt_window, input_window])
root = HSplit([main_window, bottom_bar, rule_window, input_row, footer_window])

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


# mouse_support=True so prompt_toolkit OWNS the terminal mouse mode (enables
# 1000/1006 on start, parses + consumes wheel/click events, disables on exit).
# Without it, the mouse-tracking mode left enabled by the spawning app (Claude
# Code / cmux) leaks raw SGR wheel packets straight into the input buffer on
# scroll — a stream of junk characters (regression 2026-06-27). fzf and Claude
# Code avoid this the same way: by claiming the mouse rather than ignoring it.
app = Application(layout=Layout(root, focused_element=input_window),
                  key_bindings=kb, full_screen=True, style=style,
                  style_transformation=_NoTimerFlash(),
                  mouse_support=True,
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
        fetch_current(cached=True)  # ride the shared cache; bursts stay live
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
