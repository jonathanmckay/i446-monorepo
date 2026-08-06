#!/usr/bin/env python3
"""janus — narrow Toggl + Calendar TUI.

Sits next to dtd in the right half of a terminal. Three jobs:
  1. Switch / stop the running Toggl entry (press `c`, type as if /tg)
  2. Show ±2h around now in 15-min detail (Toggl past + gcal future)
  3. Show rest-of-day overview (morning = Toggl, evening = gcal)

Keys: c=change  s=stop  r=refresh  j/k=scroll detail  [/]=prev/next day  q=quit
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
from mcp.toggl_server import throttle as toggl_throttle  # noqa: E402
from mcp.toggl_server.config import PROJECT_NAMES  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from prompt_toolkit import Application  # noqa: E402
from prompt_toolkit.buffer import Buffer  # noqa: E402
from prompt_toolkit.filters import Condition  # noqa: E402
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES  # noqa: E402
from prompt_toolkit.key_binding import KeyBindings  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402
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
# janus must still boot if the did tooling (or lib/state_paths) is broken.
try:
    sys.path.insert(0, str(Path("~/i446-monorepo/tools/did").expanduser()))
    import shorten as _dtd_shorten  # noqa: E402
except Exception:
    _dtd_shorten = None

TZ = ZoneInfo("America/Los_Angeles")
TG_FAST = str(Path("~/i446-monorepo/tools/tg/tg-fast.py").expanduser())
DID_FAST = str(Path("~/i446-monorepo/tools/did/did-fast.py").expanduser())
# Layout widths track the actual pane. janus is the narrow companion to dtd:
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
EVENT_SHORTS = Path("~/.cache/janus/event-shortnames.json").expanduser()
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
DETAIL_MIN = 5      # focus band: entries shorter than this are absorbed (no row)
DETAIL_ROWS = 8     # old detail band (dormant, kept for its tests): target rows per block
FOCUS_ROWS = 8      # current+next block compact cards: body rows (vs. 3 elsewhere)
TOGGL_MIN_INTERVAL = 20   # s — coalesce bursty non-forced fetch_today calls
RATE_LIMIT_COOLDOWN = 60  # s — back off all Toggl reads after a 402

# Staleness self-check: a long-lived janus keeps running the code it loaded at
# launch, so a shipped fix is invisible until restart — which has repeatedly
# masked fixes (stuck 4227 block points, old residual reconstruction, …). Capture
# the source mtime at import; render_header warns when the file on disk is newer.
try:
    _SRC = Path(__file__).resolve()
    _SRC_MTIME = _SRC.stat().st_mtime
except (OSError, NameError):
    _SRC, _SRC_MTIME = None, 0.0
_stale_state = {"checked": 0.0, "stale": False}


def _code_is_stale(now=None) -> bool:
    """True when janus.py on disk is newer than this running process loaded.
    Cached ~5s so the 0.1s repaint doesn't stat the file every frame."""
    if _SRC is None:
        return False
    t = now if now is not None else time.monotonic()
    if t - _stale_state["checked"] > 5.0:
        _stale_state["checked"] = t
        try:
            _stale_state["stale"] = _SRC.stat().st_mtime > _SRC_MTIME + 1.0
        except OSError:
            _stale_state["stale"] = False
    return _stale_state["stale"]
BUILD_ORDER = Path.home() / "vault/g245/5e-1/build-order.md"
BLOCK_EMOJIS = ["☀️", "📧", "🎯", "⏱️", "✅", "😈"]

# Ritual emoji → -1₦ points, from the canonical config (fallback matches its
# 2026-07 values). 😈 is the auto-card marker, not a ritual — never scores.
_RITUAL_PTS_FALLBACK = {"☀️": 1, "📧": 3, "🎯": 3, "⏱️": 3, "✅": 3}
try:
    _rj = json.loads((Path.home() / "i446-monorepo/config/block-rituals.json").read_text())
    RITUAL_PTS = {r["emoji"]: r["points"] for r in _rj["rituals"]}
except Exception:
    RITUAL_PTS = dict(_RITUAL_PTS_FALLBACK)


def _ritual_pts_label(emojis: str) -> str:
    """Block-header -1₦ score: sum of the stamped rituals' points, as a BARE
    number (``7``, ``13`` = all five) — the ₦ glyph was dropped because it
    costs a character (user request 2026-07-21); the red-on-white chip style
    (NEON_PTS_STYLE) is what marks it as the -1n score now. Replaces the raw
    emoji string in headers (user request 2026-07-20). Empty when nothing
    scored, so an unstamped header stays bare like before."""
    pts = sum(p for e, p in RITUAL_PTS.items() if e in emojis)
    return f"{pts}" if pts else ""

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
    "家":   "#00b8d4",    # Pool Party (family)
}

# Radioactive — the palette's one unassigned signature neon. The ₦ accent:
# block-header -1₦ scores render in it (user request 2026-07-21: "the neon
# colors in Janus (0n or -1n)... both").
NEON_ACCENT = "#c3fc0d"
# Block-line -1n score chip: red background, white text (user request
# 2026-07-21, replacing the lime ₦N accent).
NEON_PTS_STYLE = "bold bg:#b3261e #ffffff"


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
    title_lower = event.get("title", "").lower()
    # A literal "m5x2" in the title is unambiguous and wins over the calendar
    # map: lx@m5c7.com carries BOTH Louisa's personal invites (→ xk88) and
    # m5x2 business meetings (she's on the m5x2 team), so the calendar-level
    # default can't tell them apart — "m5x2 Strat (1|1|1)" rendered xk88-orange
    # despite saying "m5x2" right in its name (regression 2026-07-12).
    if "m5x2" in title_lower:
        return "m5x2"
    cal = event.get("calendar", "")
    code = CALENDAR_PROJECT_MAP.get(cal)
    if code:
        return code
    for keywords, kw_code in EVENT_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return kw_code
    return ""


def project_style(pid_or_code) -> str:
    """Return a prompt_toolkit style string for a project id or code."""
    code = pid_or_code if isinstance(pid_or_code, str) else proj_code(pid_or_code)
    hexv = PROJECT_COLORS.get(code)
    return f"fg:{hexv}" if hexv else ""


def toggl_project_code(pid, desc: str | None = None) -> str:
    """Resolve a Toggl entry's project code, with the same literal-title
    override gcal_project_code already applies to calendar events: "m5x2" in
    the description wins even when the entry has no (or the wrong) project
    attached. Without this, a continued/restarted Toggl entry that drops its
    project (observed live 2026-08-02: "m5x2 Strat" losing @m5x2 on
    continuation) renders uncolored despite saying "m5x2" right in its name —
    same regression class as gcal_project_code's 2026-07-12 fix, just never
    applied on the Toggl-entry side."""
    if desc and "m5x2" in desc.lower():
        return "m5x2"
    return proj_code(pid)


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
        # Whether STATE.entries reflects a CONFIRMED Toggl read — same idea as
        # current_known. False until the first successful fetch_today, and
        # reset on a failed one (402 rate limit, network error). Gates gap
        # ("empty → HH:MM") flashing: an unconfirmed/never-fetched entries
        # list is EMPTY, same as a genuinely tracked-nothing block, and
        # without this flag the two are indistinguishable — a cold-start 402
        # right after restart rendered a confident "empty" gap over time
        # Toggl actually had filled (2026-07-15).
        self.entries_known = False
        self.entries_yday: list[dict] = []  # yesterday's (for 卯 sleep total)
        self.events: list[dict] = []  # today's combined calendar events (gcal + outlook)
        self.scroll_min = 0  # detail band scroll (minutes offset from now)
        self.day_offset = 0  # 0=today, -1=yesterday, … (≤0; for filling gaps)
        self.flash = ""  # one-line status
        self.flash_until = 0.0
        self.flash_style = ""  # optional override style for flash
        self.today_points = 0  # 分 earned today
        self.block_points: dict[str, int] = {}  # per-block 分, straight from Neon's G:O cells
        # Today's nonzero 0₦ (Neon habits) row: [(habit_name, value), ...] in
        # sheet column order — the two-line habit strip under the header
        # (user request 2026-07-20: "show the neon habits for today... render
        # them similar to the way I do in neon itself").
        # value is None for a blank (not-yet-done) cell -- an EXPLICIT zero is
        # excluded from this list entirely at fetch time (deliberately marked
        # N/A, distinct from "not done yet").
        self.habits_today: list[tuple[str, float | None]] = []
        # YTD standing for minimum-commitment habits (o314/冥想/其他人):
        # {name: signed value}, read from the same 0n summary cells the jm
        # dashboard header cards use. Rendered as ±N chips instead of daily
        # done/pending chips (user request 2026-07-21: "not about doing every
        # day per se, but holding up a minimum commitment").
        self.habits_ytd: dict[str, float] = {}
        # Today's ص (prayer) count from the 0n row — rendered as its own
        # labeled "ص N" counter chip, never a bare done/pending chip
        # (user request 2026-07-27). None until a successful fetch.
        self.prayer_count: float | None = None
        self.last_toggl_fetch = 0.0
        self.last_gcal_fetch = 0.0
        self.last_current_fetch = 0.0
        self.last_points_fetch = 0.0
        self.points_day = None  # date the points state belongs to (cross-day guard)
        # When Toggl returns a 402 (free-tier rate limit), back off until this
        # monotonic time instead of hammering every tick/keypress — continuing to
        # call during the limit only prolongs it.
        self.toggl_blocked_until = 0.0
        self.day_reload_token = 0  # debounce guard for Ctrl+←/→ day scrubbing
        self.sigusr1_token = 0  # debounce guard for a burst of mutation nudges
        # Event cursor (dtd-style highlight, scoped to the current block's
        # gcal event rows — "turn a calendar event into a time entry with one
        # shortcut", user request 2026-07-15). visible_events is the exact
        # list _compact_block_lines rendered for the current block (populated
        # from its own post-slice `rows`, so Tab can never land on an event
        # that got trimmed by the row cap). event_sel is a (start_dt, title)
        # KEY, not an index — an index would silently point at a different
        # event after the list resizes (block rollover, day-nav, an event
        # ending and dropping out of the "not yet ended" filter); a key that
        # goes missing just reads as "nothing selected," never "wrong thing
        # selected." No selection is armed by default — the user must Tab
        # first, so a bare Enter on an empty input line never surprises with
        # an unintended timer start.
        self.visible_events: list[dict] = []
        self.event_sel: tuple | None = None
        # Armed by Enter on a selected real-entry row: {"ids": [...], "date":
        # the pick's own date} for the NEXT Enter to update (rename/
        # reproject/retime) instead of creating something new (user request
        # 2026-07-17: "select toggl time entries as well... edit description/
        # project"; 2026-07-18: a retyped HHMM-HHMM must retime, not get
        # swallowed into the description). `date` anchors an edited HHMM-HHMM
        # to the entry's OWN day (a past day being browsed via day-nav), not
        # whatever day janus happens to render on next. Consumed
        # unconditionally at the very top of the enter handler — the one
        # chokepoint that keeps a stale target from ever leaking into an
        # unrelated later submission (cancel via empty text, Escape, or a
        # day-nav all clear it the same way event_sel already does).
        self.edit_target: dict | None = None
        # Armed by ^P on a selected completed entry: {"id", "date", "desc",
        # "project_id", "tags", "start_dt", "end_dt"}. The next Enter reads
        # the input line as the HHMM split point and cuts the entry in two.
        self.split_target: dict | None = None
        # True while a past-event → did-fast conversion subprocess is running
        # (Enter on a selected ALREADY-ENDED event). did-fast is a ~10-45s,
        # Excel-writing call — unlike tg-fast's plain Toggl start, a second
        # concurrent invocation could double-create the entry/points and race
        # on the same ix-osa Excel write. True while the serial work queue's
        # consumer is mid-job; tells ticker_points to skip a beat so it
        # doesn't read Neon mid-write.
        self.conversion_in_flight = False
        # Serial FIFO queue for did/tg jobs (user request 2026-07-30: "I want
        # it to be able to enqueue tasks" — pressing ⌥↵ on a second row used
        # to bounce with "still converting the last one"). Jobs still run one
        # at a time (the Excel-write race the old gate guarded against is
        # real); they just wait their turn instead of being rejected.
        self.work_q = None          # asyncio.Queue, created on first use
        self.queued_cmds = set()    # dedupe keys of queued + running jobs
        # Active d357 meeting recording started from janus (user request
        # 2026-08-02): {"desc": toggl/meeting title, "start_dt": datetime}.
        # None = not recording. Mirrors ~/.local/state/jm/d357-state.json.
        self.recording = None


STATE = State()


# ─── Data fetchers ─────────────────────────────────────────────────────────

def _toggl_blocked() -> bool:
    """True while inside a post-402 cooldown — skip Toggl reads entirely. Honors
    BOTH this process's own back-off AND the shared cross-process cooldown, so a
    402 tripped by any /tg/0t backfill silences janus's pollers for the window
    (instead of them dribbling GETs through and re-tripping the limit)."""
    return time.monotonic() < STATE.toggl_blocked_until or toggl_throttle.cooling_down()


def _note_rate_limit():
    """Enter the back-off window and tell the user how long we're holding off."""
    STATE.toggl_blocked_until = time.monotonic() + RATE_LIMIT_COOLDOWN
    flash(f"toggl: rate limited — backing off {RATE_LIMIT_COOLDOWN}s", RATE_LIMIT_COOLDOWN)


def fetch_current(cached=False):
    """Refresh the running timer. cached=True rides the shared current cache
    (used by the steady 30s ticker, so janus and every open dtd picker share
    ~one fetch per window); the post-command bursts pass cached=False to force a
    live read that beats Toggl's /current propagation lag.

    Skipped entirely during a rate-limit cooldown so a 402 isn't made worse by
    the 30s ticker continuing to poll."""
    if _toggl_blocked():
        return
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
            _note_rate_limit()
        else:
            flash(f"toggl current err: {e}")


def fetch_today(force=False):
    """Reload the viewed day's Toggl entries.

    force=True is a deliberate single action (just ran a command, ctrl-r, SIGUSR1)
    and bypasses the burst throttle. force=False (the 5-min ticker, day-nav keys)
    coalesces: it's skipped if another fetch landed within TOGGL_MIN_INTERVAL, so
    rapid Ctrl+←/→ scrubbing or back-to-back commands don't each hit the API. All
    fetches are skipped during a post-402 cooldown."""
    if _toggl_blocked():
        return
    # Coalesce bursts — but a 0 sentinel means "never fetched", so the first
    # (startup) read always runs even when monotonic() is still small.
    if (not force and STATE.last_toggl_fetch
            and (time.monotonic() - STATE.last_toggl_fetch) < TOGGL_MIN_INTERVAL):
        return
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
                "tags": e.get("tags") or [],
            })
        out.sort(key=lambda x: x["start_dt"])
        STATE.entries = out
        STATE.entries_known = True
        STATE.entries_yday = yout
        STATE.last_toggl_fetch = time.monotonic()
        try:
            _resolve_pending_tag_credits()
        except Exception:  # noqa: BLE001 — credits never break the fetch
            pass
    except Exception as e:
        # Fetch failed → we no longer know today's entries are current. Leave
        # STATE.entries as-is (last known) but mark it unconfirmed so gap
        # flashing doesn't treat "haven't fetched yet" as "confirmed empty".
        STATE.entries_known = False
        if "402" in str(e):
            _note_rate_limit()
        else:
            flash(f"toggl today err: {e}")


# ── ^X event delete / hide (user request 2026-07-30) ───────────────────────
# Google-hosted events (m5x2/m5c7 calendars) are really deleted via the API;
# Outlook-sourced rows (Agency fetch, or the read-only "MSFT (Slow Sync)"
# ICS import) can't be — they're hidden locally instead, keyed the same way
# fetch_gcal's cross-calendar dedupe is so both copies of a mirrored meeting
# stay gone.

HIDDEN_EVENTS = Path.home() / ".local/state/jm/janus-hidden-events.json"
HIDDEN_EVENTS_KEEP_DAYS = 30
_hidden_ev_cache: dict = {"mtime": None, "keys": set()}


def _hidden_event_key(ev: dict) -> tuple[str, str, str]:
    return ((ev.get("title") or "").strip().lower(),
            ev["start_dt"].isoformat(), ev["end_dt"].isoformat())


def _load_hidden_events() -> set:
    """mtime-cached — checked on every fetch_gcal."""
    try:
        mtime = HIDDEN_EVENTS.stat().st_mtime
    except OSError:
        return set()
    if _hidden_ev_cache["mtime"] != mtime:
        try:
            data = json.loads(HIDDEN_EVENTS.read_text())
            _hidden_ev_cache["keys"] = {tuple(k) for k in data.get("hidden", [])
                                        if isinstance(k, list) and len(k) == 3}
        except Exception:
            _hidden_ev_cache["keys"] = set()
        _hidden_ev_cache["mtime"] = mtime
    return _hidden_ev_cache["keys"]


def _hide_event(ev: dict) -> None:
    """Persist the event's hide key; prunes entries older than
    HIDDEN_EVENTS_KEEP_DAYS so the file can't grow without bound."""
    keys = set(_load_hidden_events())
    keys.add(_hidden_event_key(ev))
    cutoff = (dt.date.today() - dt.timedelta(days=HIDDEN_EVENTS_KEEP_DAYS)).isoformat()
    keep = [list(k) for k in sorted(keys) if k[1] >= cutoff]
    HIDDEN_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    HIDDEN_EVENTS.write_text(json.dumps({"hidden": keep}))
    _hidden_ev_cache["mtime"] = None  # force reload next read


def _event_gcal_deletable(ev: dict) -> bool:
    """True when the event lives on a Google calendar the API can delete
    from: it carries an id (fresh fetch — pre-2026-07-30 caches don't), and
    its calendar isn't a read-only ICS import (the Outlook mirror)."""
    cid = ev.get("calendar_id") or ""
    return bool(ev.get("id")) and bool(cid) and \
        not cid.endswith("@import.calendar.google.com")


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
        # Dedupe cross-calendar copies: the same meeting arrives from BOTH
        # Outlook (Agency) and the "MSFT (Slow Sync)" Google import. Hidden
        # while covered events were suppressed; the reclaim feature surfaced
        # them as triple rows (2026-07-29: "Potrero PT" ×3 filled 巳's card).
        seen_ev = set()
        deduped = []
        for e in combined:
            k = ((e.get("title") or "").strip().lower(), e["start_dt"], e["end_dt"])
            if k in seen_ev:
                continue
            seen_ev.add(k)
            deduped.append(e)
        hidden = _load_hidden_events()
        deduped = [e for e in deduped if _hidden_event_key(e) not in hidden]
        STATE.events = deduped
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


def _event_key(ev: dict):
    """Stable identity for the event cursor: (start_dt, raw title). Gcal
    events aren't guaranteed a usable id through this pipeline, and a
    (start, title) pair is unique enough in practice — collisions would
    require two same-titled events at the identical start time, which
    Google Calendar doesn't produce for one person's merged view."""
    return (ev.get("start_dt"), ev.get("title"))


def _sel_key(item: dict):
    """Stable identity for ANY selectable row in STATE.visible_events — a
    calendar event (the original, unwrapped shape — kept exactly as
    _event_key already returns it, so every existing event-cursor call site
    and test keeps working untouched), a real tracked Toggl entry ("kind":
    "entry", wrapping one or more merged entry ids), or an untracked gap
    ("kind": "empty", wrapping the gap's own start/duration). The three
    shapes can never collide: an event key's first element is always a
    datetime, never the literal string "entry"/"empty"."""
    kind = item.get("kind")
    if kind == "entry":
        return ("entry", item["start_dt"], tuple(item["entry_ids"]))
    if kind == "empty":
        return ("empty", item["start_dt"])
    return _event_key(item)


def _entry_edit_prefill(item: dict) -> str:
    """The editable text Enter loads into the input line for a selected real
    entry: "<desc> @<code> HHMM-HHMM" (range only for a single completed
    entry — a merged row can't retime and a running one has no end yet),
    matching what the user would type to recreate it via the ordinary
    typed-command path. Having the CURRENT times in the line makes retiming
    a matter of editing digits (user request 2026-07-28: "change the start /
    end times of a task"); resubmitting unchanged re-applies the same range,
    which is a harmless no-op (trim excludes the entry's own ids)."""
    code = proj_code(item.get("project_id"))
    suffix = f" @{code}" if code else ""
    rng = ""
    if (len(item.get("entry_ids") or []) == 1 and not item.get("running")
            and item.get("dur_min")):
        end = item["start_dt"] + dt.timedelta(minutes=item["dur_min"])
        rng = f" {item['start_dt']:%H%M}-{end:%H%M}"
    return f"{item['raw_desc']}{suffix}{rng}"


def _empty_gap_prefill(item: dict) -> str:
    """The editable text Enter loads for a selected untracked gap: a
    "HHMM-HHMM " time-range prefix spanning the gap's own tracked-empty
    window, ready for the user to just type a description (and optional
    @code) and hit Enter — the EXISTING typed time-range path (tg-fast.py's
    "<desc> <start>-<end> @<project>") creates it, so this needs no new
    backend at all."""
    end = item["start_dt"] + dt.timedelta(minutes=item["dur_min"])
    return f"{item['start_dt']:%H%M}-{end:%H%M} "


_TIME_RANGE_RE = re.compile(r"\b(\d{4})-(\d{4})\b")


def _parse_edit_text(text: str) -> tuple[str | None, str | None,
                                         tuple[str, str] | None, list[str]]:
    """Parse retyped edit text into (description, project_code, time_range,
    tags) — the first three can be None, meaning "leave that field alone"
    (user request 2026-07-18: "if I edit an event with a new time series
    [HHMM-HHMM] it updates the time not the description" — a bare time range
    must NOT get swallowed into the description text).

    "#tag" tokens are pulled out first (user request 2026-07-28: "add -2 tag
    ... to the current 'eat' entry" — value tags -1/-2/-3 also trigger the
    媒分 credit, see TAG_POINTS). A trailing "@code" is stripped next (only
    ever the last token — an entry edit doesn't need tg-fast's fuller
    shortcode grammar). An embedded "HHMM-HHMM" is then pulled out of
    whatever remains, from anywhere in the string, since the natural retype
    is either "0930-1000" alone (time only) or "new desc 0930-1000" (both).
    Whatever text is left is the new description — empty means "unchanged",
    not "clear the description"."""
    tags = re.findall(r"(?:^|\s)#(\S+)", text)
    text = re.sub(r"(?:^|\s)#\S+", "", text).strip()
    # Range BEFORE the trailing-@code match: the retime prefill is
    # "desc @code HHMM-HHMM", which leaves @code mid-string — matching the
    # code first would swallow it into the description.
    time_range = None
    m_time = _TIME_RANGE_RE.search(text)
    if m_time:
        time_range = (m_time.group(1), m_time.group(2))
        text = (text[:m_time.start()] + " " + text[m_time.end():]).strip()
    m_code = re.match(r"^(.*?)(?:\s+@(\S+))?$", text.strip())
    body = (m_code.group(1) or "").strip() if m_code else text.strip()
    code = m_code.group(2) if m_code else None
    return (body or None), code, time_range, tags


# ── Value-tag media-minute credits (user request 2026-07-28) ────────────────
# Media-tier Toggl tags (-1/-2/-3) each have their OWN minutes column in the
# 0n sheet (AV/AW/AX as of 2026-07-28) whose formulas apply the ratio
# (0.1/m, 0.5/m, 1/m) to turn minutes into 分 — so janus writes MINUTES to
# the tag's column, never points ("put the number of minutes in column AW,
# which will then apply the multiplier"). Credits fire ONLY for tags added
# through janus's edit flow — /tg shortcodes auto-tag some entries (睡觉 -3,
# hiit -2) and blanket-crediting every tagged entry would hand sleep
# ~400m/day of media minutes. A tag on a RUNNING entry queues until the
# timer stops (minutes unknown until then); fetch_today resolves the queue.
# Credited (id, tag) pairs are journaled date-gated so a re-edit can't
# double-credit.
VALUE_TAGS = ("-1", "-2", "-3")
TAG_CREDITS = Path.home() / ".local/state/jm/janus-tag-credits.json"


def _tag_credit_load() -> dict:
    try:
        d = json.loads(TAG_CREDITS.read_text())
        if d.get("date") == dt.date.today().isoformat():
            return d
    except Exception:
        pass
    return {"date": dt.date.today().isoformat(), "credited": [], "pending": []}


def _tag_credit_save(d: dict) -> None:
    try:
        TAG_CREDITS.parent.mkdir(parents=True, exist_ok=True)
        TAG_CREDITS.write_text(json.dumps(d, ensure_ascii=False))
    except OSError:
        pass


def _tag_col(tag: str) -> str:
    """0n column letter for a value tag's minutes, via neon-cols (never
    hardcoded — the 2026-04-28 reshuffle rule). Falls back to the
    AppleScript-stringified key ("-2.0") for configs regenerated before the
    numeric-header normalization landed in regen-neon-cols.py."""
    from neon import cols as neon_cols
    try:
        return neon_cols.col("0n", tag)
    except KeyError:
        return neon_cols.col("0n", f"{tag}.0")


def _apply_tag_credit(tag: str, mins: int, day: dt.date) -> int | None:
    """Append the entry's MINUTES to the tag's own 0n column for the entry's
    day — the sheet's formulas apply the tag ratio to produce the 分."""
    if mins <= 0:
        return None
    neon_excel.append("0n", _tag_col(tag), date=f"{day.month}/{day.day}",
                      value=f"+{mins}")
    return mins


def _find_entry(entry_ids: list) -> dict | None:
    for e in STATE.entries + STATE.entries_yday:
        if e.get("id") in entry_ids:
            return e
    return None


def _resolve_pending_tag_credits() -> None:
    """Credit queued value tags whose entry has since stopped. Runs at the
    tail of fetch_today (already off the event loop). An entry that vanished
    (deleted) drops its pending credit; a still-running one stays queued."""
    st = _tag_credit_load()
    if not st.get("pending"):
        return
    remaining = []
    for p in st["pending"]:
        ent = _find_entry([p.get("id")])
        if ent is None:
            continue
        if ent.get("running"):
            remaining.append(p)
            continue
        mins = int((ent["end_dt"] - ent["start_dt"]).total_seconds() // 60)
        try:
            credited = _apply_tag_credit(p["tag"], mins, ent["start_dt"].date())
        except Exception:
            remaining.append(p)  # ix unreachable etc. — retry next fetch
            continue
        if credited:
            st["credited"].append(p["key"])
            flash(f"#{p['tag']} +{credited}m → 0n ({display_desc(ent['desc'])})", 6.0)
    st["pending"] = remaining
    _tag_credit_save(st)


def _hhmm_to_dt(ref_date: dt.date, hhmm: str) -> dt.datetime:
    return dt.datetime(ref_date.year, ref_date.month, ref_date.day,
                       int(hhmm[:2]), int(hhmm[2:]), tzinfo=TZ)


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


# Module-level so tests can monkeypatch it to a tmp_path instead of appending
# to the real shared file (bug 2026-07-30: test_janus_points_total_guard.py's
# torn-read fixtures -- literally D=-46/4351/1523 -- wrote straight into this
# hardcoded path, so every test run polluted live diagnostics with fake
# "rejected read" entries indistinguishable from genuine production torn
# reads, e.g. three lines at the SAME wall-clock second with wildly different
# candidates).
POINTS_REJECTED_LOG = Path("/tmp/janus-points-rejected.log")


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

        # Cross-day guard: points state from another day's row must never
        # display as this day's. "Keep last good on a rejected read" is right
        # WITHIN a day, but every read of a new day can be rejected for hours
        # (torn D during active writes), which kept YESTERDAY's Σ and blocks
        # on screen until mid-afternoon (990 shown at 16:11 on a 323分 day,
        # 2026-07-07). On rollover/day-nav, blank the state; empty renders as
        # no points until the first clean read of the new day's row.
        if STATE.points_day is not None and STATE.points_day != now.date():
            STATE.today_points = 0
            STATE.block_points = {}
        STATE.points_day = now.date()

        # Read the Σ total (column D) AND the per-block columns (G:O, headed
        # 卯辰巳午未申酉戌亥) for today's row in one ix-osa call. G:O is the
        # authoritative per-block distribution — reconstructing blocks from
        # completed-today.json logging timestamps lumps batch-logged points
        # into whichever block they were *recorded* in, not earned in.
        bp_excel: dict[str, int] = {}
        read_ok = False
        total_ok = False
        raw_out = ""
        try:
            import subprocess as _sp
            IX_OSA = str(Path.home() / ".claude/skills/_lib/ix-osa.sh")
            script = f'''tell application "Microsoft Excel"
    set ws to sheet "0分" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with i from 2 to 500
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
            # Stale-view guard: day-nav spawns a _bg_fetch per settled press,
            # and two presses ~1s apart leave two of these racing over ssh.
            # Whichever finished LAST used to win, so day −1's numbers could
            # land while day −2 was on screen (bug 2026-07-24: "going back
            # two days, header doesn't fully update"). If the viewed day
            # changed while we were reading, this result is for a day no
            # longer shown — drop it; the newer fetch owns the header.
            if view_now().date() != now.date():
                return
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
            if (total_ok and _blocks_consistent(STATE.today_points, bp_excel)
                    and _blocks_plausible(bp_excel)):
                STATE.block_points = bp_excel
            else:
                # Torn read (implausible total, or daemon lock / did-fast append
                # in flight): keep last good values and leave evidence for diagnosis.
                try:
                    with open(POINTS_REJECTED_LOG, "a") as fh:
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
    # Absolute ceiling FIRST, before the sum_py cross-check: a mid-recalc snapshot
    # can tear D and its own P:Y input range to the SAME spike (both read from the
    # poisoned formula cache), so agreement alone isn't proof — D=5064 with sum_py
    # =5064 passed the ±1 check and set Σ=5064, which the cold-start current-block
    # reconstruction (Σ−locked) then showed as 5064分 (2026-07-03). No real day
    # tops _MAX_PLAUSIBLE_TOTAL, so reject above it whether or not sum_py agrees.
    if candidate > _MAX_PLAUSIBLE_TOTAL:
        return False
    if sum_py is not None:
        return abs(candidate - sum_py) <= 1  # ±1 for float rounding
    return True


def _blocks_consistent(total: int, bp: dict[str, int]) -> bool:
    """Reject 0分 block reads that claim more points than the day's Σ total.

    While any residual formula (=D-SUM(locked)) is live in G:O, sum(blocks)
    == Σ exactly, so sum > Σ means the row was sampled mid-write (2026-06-12:
    未 read 975 of a 728分 day and stuck on screen). After the 22:00 lock all
    blocks are literals and Σ may legitimately exceed their sum, so only the
    sum > Σ direction is rejected."""
    return sum(bp.values()) <= total + 2


def _blocks_plausible(bp: dict[str, int]) -> bool:
    """No single block may exceed the heaviest realistic DAY total.

    The first-unlocked block's G:O cell is the residual =D-SUM(locked); when D is
    read mid-recalc and spikes, that residual spikes with it (辰 read 4227 on a
    429分 day, 2026-06-30). _blocks_consistent can't catch it — the residual makes
    sum==Σ by construction — and if the torn Σ slipped past _total_trustworthy too,
    the spike stuck on screen. A single block topping _MAX_PLAUSIBLE_TOTAL is
    impossible on a real day, so reject the whole read and keep the last good."""
    return all(v <= _MAX_PLAUSIBLE_TOTAL for v in bp.values())


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
# cache; janus shows the same labels so a timer reads identically in both. The
# Toggl description is the task content minus (N)/[N]/{N} annotations, so we map
# normalized-cleaned content → cleaned short and look entries up by description.

import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
TASK_QUEUE = _sp.TASK_QUEUE
SHORT_NAMES: dict[str, str] = {}  # normalized cleaned content → cleaned short
# 0neon (daily habit) card due dates from the same cache: normalized cleaned
# content → [due ISO strings]. Matched by CONTENT, not labels — card labels
# are mostly domain codes ('i447' rides the charge card too), so a label
# match would tie one habit's deferral to another habit's card.
HABIT_DUES: dict[str, list[str]] = {}
# Same 0neon cards keyed the same way, but carrying (todoist id, due) — the
# id is what dtd-block-snooze.json is keyed by (same-day block delays).
HABIT_CARDS: dict[str, list[tuple[str, str]]] = {}


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
    # Same read also refreshes the 0neon due-date map (deferral detection for
    # the habit strip). A card without a due date maps to "" so it can never
    # satisfy the strictly-after-today test — it blocks hiding instead.
    dues: dict[str, list[str]] = {}
    cards: dict[str, list[tuple[str, str]]] = {}
    for t in data.get("0neon", []) or []:
        c = t.get("content")
        if c:
            dues.setdefault(_norm_key(c), []).append(t.get("due") or "")
            cards.setdefault(_norm_key(c), []).append(
                (str(t.get("id") or ""), t.get("due") or ""))
    HABIT_DUES.clear()
    HABIT_DUES.update(dues)
    HABIT_CARDS.clear()
    HABIT_CARDS.update(cards)


def display_desc(desc: str) -> str:
    """Map a Toggl description to dtd's short name when one exists, else the
    description unchanged (habits and ad-hoc timers have no cached short)."""
    if not desc:
        return desc
    return SHORT_NAMES.get(_norm_key(desc), desc)


# ─── Renderers ─────────────────────────────────────────────────────────────

def render_header() -> list[tuple[str, str]]:
    now = dt.datetime.now(TZ)
    # int(round()) at every 分 render site: the sheet now carries fractional
    # cells (variable-task minutes/7 → D=695.357142857143 live 2026-07-07), and
    # a float leaking into an f-string prints its full repr next to 分 — which
    # reads as concatenated garbage digits on the rule line.
    pts = STATE.today_points
    pts_str = f" · {int(round(pts))}分" if pts else ""
    # The running process is behind the file on disk → tell the user to restart;
    # the whole header goes red so it can't be missed.
    if _code_is_stale():
        title = f" janus · ⚠ RESTART — code updated{pts_str} "
        line = title + "─" * max(0, WIDTH_HINT - len(title))
        return [("class:no_entry", line + "\n")]
    if STATE.day_offset == 0:
        title = f" janus · {now:%a %H:%M:%S}{pts_str} "
        line = title + "─" * max(0, WIDTH_HINT - len(title))
        return [("class:header", line + "\n")]
    # Viewing a past day: badge the date so it's never mistaken for today.
    viewed = view_now()
    title = f" janus · ◀ {viewed:%a %-m/%-d}{pts_str} · ⎋ today "
    line = title + "─" * max(0, WIDTH_HINT - len(title))
    return [("class:no_entry", line + "\n")]


# 0₦ (Neon habit) column → domain code, for coloring the habit strip the
# same way the rest of janus colors everything else (project_style/
# PROJECT_COLORS). This map is the color SOURCE — do not "fix" it by reading
# fills from the workbook: the only colored 0n row is row 2 (⊖分), whose red
# fills are penalty markers, not habit colors (tried 2026-07-21, turned
# 0l/0g red; user: "obviously green"). Seeded from did-fast.py's
# HABIT_PROJECT (the canonical 0₦→Toggl-project map) and extended with the
# rest of the 0n sheet's real columns; a few purely-internal bookkeeping
# columns (N color, ⎣∀clr, #) are skipped entirely rather than guessed at.
HABIT_COLOR_DOMAIN = {
    "睡觉": "睡觉", "cpap": "hcb", "wake up": "hcb", "i444": "i444", "i447": "i447",
    "charge": "infra", "tmrw": "g245", "2nd hci": "hci", "1st hci": "hci",
    "ibx s897": "s897", "新闻": "hcmc", "词汇": "hcmc", "night hcmc": "hcmc",
    "0t": "n156", "₦156": "n156", "0l": "g245", "0g": "g245",
    "stats i9": "i9", "notes": "i9", "ibx i9": "i9", "push": "i9", "teams": "i9",
    "slack github": "i9", "slack m5x2": "m5x2", "ibx m5x2": "m5x2", "m5x2 stats": "m5x2",
    "早餐": "hcb", "hiit": "hcb", "问学": "家", "xk20": "xk88", "xk22": "xk88",
    "xk26": "xk88", "qft": "hcm", "xk88": "xk88", "nvc + e": "hcm", "ص": "hcm",
    "o314": "hcm", "冥想": "hcm", "其他人": "hcm",
}
_HABIT_STRIP_SKIP = {
    "mee", "日", "n color", "⎣∀clr", "#",
    # User-requested exclusions (2026-07-20) — tracked in Neon, just not
    # wanted on this strip.
    "词汇", "slack github", "问学",
}

# Minimum-commitment habits: shown as a YTD standing (±N) instead of daily
# done/pending chips (user request 2026-07-21). The cells are the 0n summary
# cells the jm dashboard "2026" header cards read — keep in sync with
# CACHE_CARDS in tools/personal-dashboard/dashboard.py.
HABIT_YTD_CELLS = {
    "o314": "AQ375",
    "冥想": "AR375",
    "其他人": "AS375",
}
# Chip backgrounds: each its own purple, the same hues as the dashboard's
# cards (user request 2026-07-21: "different shades of purple", not
# standing-red/green).
HABIT_YTD_COLORS = {
    "o314": "#7c4dff",
    "冥想": "#aa00ff",
    "其他人": "#673ab7",
}


def _ytd_applescript_lines() -> str:
    """AppleScript snippet reading each HABIT_YTD_CELLS summary cell; appended
    to fetch_habits_today's script after a '||' marker, one '|'-terminated
    value per cell in dict order (a failed read contributes an empty field)."""
    lines = []
    for cell in HABIT_YTD_CELLS.values():
        lines.append(f'''    set yv to ""
    try
        set yv to (value of range "{cell}" of ws) as text
    end try
    set out to out & yv & "|"''')
    return "\n".join(lines)


def fetch_habits_today():
    """Read today's 0₦ (Neon habits) row: EVERY habit (done and not-yet-done
    alike), in column order, for the two-row habit strip under the header
    (user request 2026-07-20). Best-effort: any failure just leaves the
    strip empty/stale, same tolerance as fetch_points."""
    try:
        now = view_now()
        IX_OSA = str(Path.home() / ".claude/skills/_lib/ix-osa.sh")
        # BULK range reads only — the old shape (a per-row date loop up to
        # r500 + per-cell header/value reads) was ~580 individual AppleEvents
        # over ssh, which is the "habit strip takes ~2 minutes" class of slow
        # (same disease the ص skill had; fixed the same way 2026-07-21). This
        # shape is ~6 AppleEvents and returns in about a second.
        script = f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set targetMonth to {now.month}
    set targetDay to {now.day}
    set dateVals to value of range "C3:C500" of ws
    set todayRow to 0
    repeat with i from 1 to (count of dateVals)
        set cv to item 1 of (item i of dateVals)
        if cv is not missing value then
            try
                if (month of cv as integer) = targetMonth and (day of cv) = targetDay then
                    set todayRow to i + 2
                    exit repeat
                end if
            on error
                try
                    if (cv as text) = (targetMonth as text) & "/" & (targetDay as text) then
                        set todayRow to i + 2
                        exit repeat
                    end if
                end try
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERR"
    -- Two-step unwrap on purpose: `item 1 of (value of range ... of ws)`
    -- compiles as an element specifier dispatched TO Excel and errors; a
    -- local temporary keeps the `item 1 of` in plain AppleScript.
    set tmpH to value of range "D1:AS1" of ws
    set hdrVals to item 1 of tmpH
    set tmpR to value of range ("D" & todayRow & ":AS" & todayRow) of ws
    set rowVals to item 1 of tmpR
    set out to ""
    repeat with i from 1 to (count of hdrVals)
        set hv to ""
        try
            set hv to (item i of hdrVals) as text
        end try
        set vv to ""
        try
            set vv to (item i of rowVals) as text
        end try
        set out to out & hv & "\\t" & vv & "|"
    end repeat
    set out to out & "|"
{_ytd_applescript_lines()}
    return out
end tell'''
        proc = subprocess.run([IX_OSA], input=script, capture_output=True, text=True, timeout=15)
        # Stale-view guard (same race as fetch_points, 2026-07-24): if the
        # user navigated to another day while this ssh read was in flight,
        # committing would overwrite the newer day's strip with this one's.
        if view_now().date() != now.date():
            return
        if proc.returncode != 0 or proc.stdout.strip() in ("", "ERR"):
            return
        raw, _, ytd_raw = proc.stdout.strip().partition("||")
        ytd: dict[str, float] = {}
        for name, val in zip(HABIT_YTD_CELLS, ytd_raw.split("|")):
            try:
                ytd[name] = float(val.strip())
            except ValueError:
                pass
        STATE.habits_ytd = ytd
        habits = []
        prayer: float | None = 0.0  # blank cell = 0 prayers so far, not "unknown"
        for chunk in raw.split("|"):
            if not chunk.strip():
                continue
            name, _, val = chunk.partition("\t")
            name = name.strip()
            # ص is a COUNTER (prayers so far today), not a done/pending
            # habit: captured for its own labeled "ص N" chip instead.
            if name == "ص":
                try:
                    prayer = float(val.strip())
                except ValueError:
                    pass
                continue
            # YTD-standing habits render as ±N chips (render_habits_today),
            # never as daily done/pending chips.
            if not name or name.lower() in _HABIT_STRIP_SKIP or name.lower() in HABIT_YTD_CELLS:
                continue
            val = val.strip()
            if not val:
                habits.append((name, None))  # blank -> not done yet (pending row)
                continue
            try:
                v = float(val)
            except ValueError:
                habits.append((name, None))  # unparseable -> treat as pending, not done
                continue
            if v == 0:
                # An EXPLICIT zero (as opposed to a blank cell) means the habit
                # was deliberately marked N/A today -- distinct from "not done
                # yet" (user request 2026-07-20: "if CPAP is marked zero in
                # neon (not blank) it shouldn't show up"). Excluded from BOTH
                # rows entirely, not just the done row.
                continue
            habits.append((name, v))
        STATE.habits_today = habits
        STATE.prayer_count = prayer
    except Exception:
        pass


def _habit_chip_style(name: str) -> str:
    """Solid background chip, white text — the color alone (no name label)
    is the identifier, so ~20-30 habits fit across two lines instead of ~10
    (user request 2026-07-20: "don't want... the names... make the color
    the background color... white [text]... fit the ~20-30 categories").
    Fills come from the DOMAIN map (0l/0g green as g245, etc.), NOT the
    workbook: the 0n sheet's row-2 (⊖分) red fills looked like per-habit
    colors but are penalty markers — sourcing them turned 0l/0g red (tried
    and reverted 2026-07-21). An unmapped habit still gets a visible
    (neutral gray) chip rather than no background at all — a value with no
    chip around it would read as plain text, breaking the "everything here
    is a colored chip" scan."""
    hexv = PROJECT_COLORS.get(HABIT_COLOR_DOMAIN.get(name.lower(), ""))
    return f"bold bg:{hexv or '#444444'} #ffffff"


def _habit_deferred(name: str) -> bool:
    """True when the habit's 0neon Todoist card(s) have ALL been pushed past
    today — i.e. the habit was deferred, so it can't be completed today and
    shouldn't clutter the pending row (user request 2026-07-21: "if a task
    has been deferred (such as xk22, xk20 today) it doesn't show up").

    Only the strictly-after-today direction hides: a card due today or
    overdue is still doable. A habit with a VALUE today never reaches this
    check (it renders in the done row regardless). Recurring cards completed
    today also sit at due=tomorrow, but their habit has a value by then, so
    the pending row never asks about them. Cards match by cleaned content;
    the deferred one-off copy ("xk22 7.21") drops out of the match, which is
    fine — the advanced parent card carries the decision. Past-day views
    skip the check: the cache only describes today."""
    if STATE.day_offset != 0:
        return False
    dues = HABIT_DUES.get(_norm_key(name))
    if not dues:
        return False
    today = dt.datetime.now(TZ).date().isoformat()
    return all(d[:10] > today for d in dues)


BLOCK_SNOOZE = Path.home() / ".local/state/jm/dtd-block-snooze.json"
_snooze_cache: dict = {"mtime": None, "map": {}}


def _load_block_snoozes() -> dict[str, int]:
    """dtd's same-day block delays: {todoist card id: block start hour},
    date-gated to today. mtime-cached — this is read on every repaint."""
    try:
        mtime = BLOCK_SNOOZE.stat().st_mtime
    except OSError:
        return {}
    if _snooze_cache["mtime"] != mtime:
        try:
            data = json.loads(BLOCK_SNOOZE.read_text())
            ok = data.get("date") == dt.date.today().isoformat()
            _snooze_cache["map"] = ({str(k): int(v) for k, v in
                                     (data.get("snoozes") or {}).items()}
                                    if ok else {})
        except Exception:
            _snooze_cache["map"] = {}
        _snooze_cache["mtime"] = mtime
    return _snooze_cache["map"]


def _habit_block_snoozed(name: str) -> bool:
    """True when every one of the habit's cards still due today has been
    block-delayed (dtd ctrl-v) to a block that hasn't started yet — same
    hide-until-the-hour-arrives rule dtd's own list applies, mirrored onto
    the habit strip's pending row (user request 2026-07-27: "tasks that are
    delayed for a different block don't show up in the 1st few rows").
    Reappears on its own once the chosen block starts (now.hour >= hour)."""
    if STATE.day_offset != 0:
        return False
    snoozes = _load_block_snoozes()
    if not snoozes:
        return False
    today = dt.date.today().isoformat()
    todays = [cid for cid, due in HABIT_CARDS.get(_norm_key(name), [])
              if cid and (due or "")[:10] <= today]
    if not todays:
        return False
    now_h = dt.datetime.now(TZ).hour
    return all(cid in snoozes and now_h < snoozes[cid] for cid in todays)


def _habit_row(chips: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Fit as many chips as WIDTH_HINT allows onto ONE row; drop the rest
    (each of the two habit rows is its own single line, not a wrap group)."""
    row: list[tuple[str, str]] = []
    w = 0
    for sty, text in chips:
        cw = dwidth(text)
        if w + cw > WIDTH_HINT:
            break
        row.append((sty, text))
        w += cw
    return row


def render_habits_today() -> list[tuple[str, str]]:
    """Two-row strip of today's Neon habits, split by DONE-ness rather than
    wrapped across two lines of the same thing (2026-07-20 follow-up): row 1
    is every habit with a value today, as solid-background chips — just the
    number, one trailing space (not two) between chips; row 2 is every
    habit WITHOUT a value yet, as the bare habit NAME in the same domain-
    colored chip style, so it reads as "still open." Each row independently
    drops whatever doesn't fit in WIDTH_HINT — the ask was two lines, not a
    scrolling list."""
    if not STATE.habits_today and not STATE.habits_ytd:
        return []
    # An explicit zero is already filtered out at fetch time -- here it's
    # just "has a value" (done) vs. "blank" (v is None, pending) that split
    # the two rows.
    # -1n leads the done row as ONE chip: the current block's ritual score so
    # far, a bare number on the red chip — never a strip of ritual emojis
    # (user request 2026-07-21: "numbers with colors [rather] than emojis").
    # Local build-order read, same source as the block-line scores.
    neon_chip: list[tuple[str, str]] = []
    if STATE.day_offset == 0:
        blk = hour_to_block(view_now().hour)
        label = _read_block_emojis().get(blk[0]) if blk else ""
        if label:
            neon_chip = [(NEON_PTS_STYLE, f"{label} ")]
    done_chips = neon_chip + [(_habit_chip_style(name), f"{v:g} ")
                              for name, v in STATE.habits_today if v is not None]
    # Minimum-commitment habits: one ±N YTD-standing chip each (same numbers
    # as the jm dashboard "2026" header cards) on the second line AFTER the
    # pending 0neon names, each in its own purple (HABIT_YTD_COLORS —
    # user-requested order + palette 2026-07-21). Only shown while the queue
    # is at or below zero — a positive standing means nothing is owed, and
    # hiding it keeps the second line a pure "what's left to do" list (user
    # request 2026-07-24).
    pending_chips = [(_habit_chip_style(name), f"{name} ")
                     for name, v in STATE.habits_today
                     if v is None and not _habit_deferred(name)
                     and not _habit_block_snoozed(name)]
    pending_chips += [(f"bold bg:{HABIT_YTD_COLORS.get(name, '#7c4dff')} #ffffff",
                       f"{name} {v:+g} ")
                      for name, v in STATE.habits_ytd.items() if v <= 0]
    # ص prayer counter: always-visible labeled chip ("ص 3") closing the
    # second line, after the 其他人 YTD chip — a count toward 5, so the
    # bare-number done-chip format (or disappearing into the pending names)
    # never fit it (user request 2026-07-27; placement follow-up same day).
    if STATE.prayer_count is not None:
        pending_chips.append((_habit_chip_style("ص"),
                              f"ص {STATE.prayer_count:g} "))
    out: list[tuple[str, str]] = []
    for chips in (_habit_row(done_chips), _habit_row(pending_chips)):
        if chips:
            out.extend(chips)
            out.append(("", "\n"))
    return out


def render_current() -> list[tuple[str, str]]:
    cur = STATE.current
    if not cur:
        return [("class:idle", " (no timer running)\n")]
    desc = cur.get("description") or "(no description)"
    pid = cur.get("project_id")
    code = toggl_project_code(pid, desc)
    try:
        st = dt.datetime.fromisoformat(cur.get("start", "")).astimezone(TZ)
        elapsed_s = int((dt.datetime.now(TZ) - st).total_seconds())
    except Exception:
        elapsed_s = 0
    line = f" ▶ {desc}"
    if code:
        line += f"  · {code}"
    line += f"   {fmt_dur_live(elapsed_s)}\n"
    style = project_style(code) or "class:running"
    return [(f"bold {style}".strip(), line)]


def section_rule(label: str, focus: bool = False, pts: int = 0) -> list[tuple[str, str]]:
    """Full-width rule line. If pts is given, render it right-justified in
    bold white so the day's 分 are scannable at a glance."""
    s = f"─ {label} "
    cls = "class:focus_rule" if focus else "class:rule"
    pts_str = f" {int(round(pts))}分" if pts else ""  # never print a float repr
    trail = max(0, WIDTH_HINT - dwidth(s) - dwidth(pts_str))
    out: list[tuple[str, str]] = [(cls, s + "─" * trail)]
    if pts_str:
        out.append(("bold #ffffff", pts_str))
    out.append((cls, "\n"))
    return out


def _read_block_emojis(now: dt.datetime | None = None) -> dict[str, str]:
    """Read build order file, return {branch_char: ``₦N`` points label} for
    today's blocks (the stamped rituals' -1₦ score — see _ritual_pts_label).

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
                label = _ritual_pts_label(emojis)
                if label:
                    result[branch] = label
    return result


def _gap_alarm_on(now: dt.datetime | None = None) -> bool:
    """Half-second on/off toggle for the past-untracked-time alarm. Animated by
    the app's 0.1s refresh_interval — same cadence as the NO TIME ENTRY cursor.
    Past empty stretches pulse a solid red block↔plain red text on this so
    untracked time nags."""
    t = (now or dt.datetime.now(TZ)).timestamp()
    return int(t * 2) % 2 == 0


def _gap_label(end_dt: dt.datetime, dur_min: int) -> str:
    """'empty → HH:MM (Nm)': says the word "empty" and states the gap's END
    time explicitly, so a gap row is never just inferred from dash texture
    (user report 2026-07-11: "doesn't tell me when the empty block began or
    end, just infers with flashing"). The START time is deliberately omitted —
    every caller's own time column/prefix already shows it, and repeating it
    ate the label's width budget in narrow panes, truncating away the one new
    fact (the end time) this label exists to add."""
    return f"empty → {end_dt:%H:%M} ({fmt_dur(dur_min)})"


def _gap_fill(label: str, width: int) -> str:
    """The gap label, truncated to width, then a ┄ dash fill for the
    remainder — not blank padding. The 2026-07-11 redesign (see _gap_label)
    dropped the dash fill entirely, and the label sitting in an otherwise
    blank row read as floating text rather than a highlighted bar (user
    report 2026-07-12: "add back the lines for each block"). Restores the
    dash texture while keeping the explicit "empty → HH:MM (Nm)" wording."""
    t = truncate(label, width)
    rem = width - dwidth(t)
    if rem <= 1:
        return t + " " * max(0, rem)
    return t + " " + "┄" * (rem - 1)


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


def _gutter(hh: int, mm: int, slot_min: int) -> tuple[str, str]:
    """1-char busy-bar cell for a row's slot: ▍ when an opaque calendar
    event covers any of it, blank when meeting-free — the day's meeting load
    as a scannable barcode down the left of every card (user request
    2026-07-27, "suggestion 3"). Replaces the single space that always sat
    between the time column and the row body, so no width budget changes."""
    try:
        s = view_now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return ("class:time", " ")
    e = s + dt.timedelta(minutes=slot_min)
    busy = any(ev["start_dt"] < e and ev["end_dt"] > s
               for ev in STATE.events
               if not (ev.get("transparency") == "transparent" or ev.get("all_day")))
    return ("class:gutter_busy", "▍") if busy else ("class:time", " ")


def _abbrev_tcol(hh: int, mm: int, prev_hour: int | None) -> tuple[str, int]:
    """Time column for a body row, abbreviated. Full ``HH:MM`` when the hour
    differs from the row above; otherwise minutes-only ``  :MM`` indented two so
    the colon aligns under the hour column. No leading pad (col 0). Returns
    (text, hour-to-carry-forward)."""
    if prev_hour is not None and hh == prev_hour:
        return f"  :{mm:02d}", prev_hour
    return f"{hh:02d}:{mm:02d}", hh


def _compact_block_lines(blk_name, blk_sh, picks, pts, emojis, cont=None,
                         is_future=False, max_rows: int = 3,
                         track_selection: bool = False) -> list[tuple[str, str]]:
    """Render one block as a header (``午:00``) + ``max_rows`` body rows.

    Header is the block's :00 slot. A FUTURE block carries its dominant upcoming
    event inline with the duration as ``(N)`` minutes (``午:00 standup (60)``);
    a PAST block shows points right-aligned and keeps the header bare. The
    block's -1₦ score (``₦N``, Radioactive) rides the RIGHT edge — beside the
    分 on a past block, after the duration on a future head. Body rows
    are the block's later slot marks (30-min at the default max_rows=3, matching
    the original 3-row card; 15-min when max_rows > 3 — the focus band's wider
    cards, which also include the :00 slot in the marks since a real entry there
    no longer has the header to lean on): each shows an entry (label + duration)
    when one starts in that slot, else just the faint time. The
    hour prints only when it rolls over (``13:00`` then ``  :30``). Future
    durations read ``(N)``, past ones ``Nm``. Labels arrive Haiku-shortened
    (event_title / dtd short names); truncate is only the width fallback.

    picks: normalized items {start_dt, time_str, label, style, dur_min[, is_gap]},
    chronological. cont is accepted for signature compatibility but no longer
    drawn — empty marks render as just the time (per the block redesign).
    """
    out: list[tuple[str, str]] = []
    # 15/30-min grid resolution — needed by the busy-bar gutter cells on the
    # header rows too, so computed before either header branch.
    slot_min = 15 if max_rows > 3 else 30
    # Set by the past-block branch below when its head0 rides the header from
    # a slot other than :00 (2026-08-06); stays None/blk_sh on the is_future
    # branch, which never vacates :00. Must default here (not just in that
    # branch) — the body/mark-fill section past the header if/else, and the
    # prev_hour seed for body-row abbreviation, are both shared by both
    # branches.
    vacated_00 = None
    header_hour = blk_sh

    # ── header: the block's :00 slot ──
    # Free rows never ride the header: the header's event slot belongs to the
    # block's dominant MEETING; a fully-free future block keeps its bare
    # header and shows its free stretch as an ordinary body row.
    non_free = [p for p in picks if not p.get("is_free")] if is_future else []
    if is_future and non_free:
        head = non_free[0]
        body_picks = [p for p in picks if p is not head]
        left = f"{blk_name}:00 "
        neon_tail = f" {emojis}" if emojis else ""
        dur = f"({head['dur_min']})"
        # The header row is labelled :00, but the dominant event riding it may
        # start later in the block — without its own time an 11:00 meeting
        # reads as filling 午 from 10:00. Print the full start time whenever
        # the event isn't exactly at the block's :00.
        hs = head["start_dt"]
        tpfx = "" if (hs.hour, hs.minute) == (blk_sh, 0) else f"{hs:%H:%M} "
        avail = max(1, WIDTH_HINT - dwidth(left) - dwidth(tpfx) - dwidth(dur)
                    - dwidth(neon_tail) - 1)
        label = truncate(head["label"], avail)
        # The event cursor must reach the HEAD pick too, not just body rows:
        # a future block's single/dominant event is riding the header line
        # (this branch), never `rows` — without this it was structurally
        # unselectable (user report 2026-07-15: "still can't select... future
        # calendar entries", since the next focus block's only event is
        # almost always its head).
        head_selected = (track_selection and head.get("is_event")
                        and STATE.event_sel == _event_key(head["event"]))
        if track_selection and head.get("is_event"):
            STATE.visible_events.append(head["event"])
        if head_selected:
            head_sty = f"bold {head['style']}".strip() + " bg:#3a3a3a" if head["style"] else "class:selected_bg"
            left_sty = "class:selected_accent"
            time_sty = "class:selected_accent"
            dur_sty = "class:selected_bg"
        else:
            head_sty = _placeholder_style() if _is_placeholder(head["label"]) else (head["style"] or "class:future")
            left_sty = "class:dim"
            time_sty = "class:time"
            dur_sty = "class:dim"
        # Duration sits right after the label: `午:00 11:00 GamePass sync (60)`.
        # ₦N trails at the line's end in Radioactive — right side with the
        # numbers, not after the block name (user request 2026-07-21), and it
        # keeps its accent even under the selected-header styling.
        # The gutter cell replaces the space after "巳:00" (budgeted via
        # dwidth(left), which still includes it — same 1-char width).
        out.append((left_sty, f"{blk_name}:00"))
        out.append(_gutter(blk_sh, 0, slot_min))
        # Header-riding events right-justify like every other calendar row
        # (user request 2026-07-28): block name left, event at the right edge.
        out.append(("class:selected_bg" if head_selected else "",
                    " " * max(0, avail - dwidth(label))))
        if tpfx:
            out.append((time_sty, tpfx))
        out.append((head_sty, label))
        out.append((dur_sty, f" {dur}"))
        if neon_tail:
            out.append((NEON_PTS_STYLE, neon_tail))
        out.append((dur_sty, "\n"))
    else:
        body_picks = picks
        pts_str = f"{int(round(pts))}分" if pts else ""  # never print a float repr
        # The -1n score sits at the RIGHT edge beside the block's 分, not after the block
        # name (user request 2026-07-21: "in the block lines... not in the
        # header"): `辰:00              ₦9 73分`.
        right = f"{emojis} {pts_str}" if emojis and pts_str else (emojis or pts_str)
        # The FIRST real item in the block rides the header line instead of
        # duplicating a body row right under it (user request 2026-07-30:
        # "Shouldn't XBOX Developer be on the 未 line rather than repeating
        # it?"; widened 2026-08-06: "every hour/block :00 doesn't need to be
        # its own line" — a block whose :00 slot was a now-deleted calendar
        # event used to leave a bare "未:00" header AND demote the real next
        # entry (e.g. -1g at 12:15) to an abbreviated "  :15" body row two
        # lines down. Now the header adopts whichever real entry is
        # chronologically first (picks arrive pre-sorted), labelled with ITS
        # OWN start time (`未:15`, or `未 13:05` if it falls in the block's
        # second hour) instead of always the literal `blk:00`. Deliberately
        # NOT the is_future branch's "blk:00 HH:MM label" convention above —
        # the user's own example was explicit that the block name's own
        # suffix changes, not an inline time prefix.
        # Free/gap bars keep their own body rows — the header's slot belongs
        # to a tracked entry, a meeting, or the sleep-spillover item.
        head0 = next(
            (p for p in picks if not p.get("is_free") and not p.get("is_gap")), None)
        # Tracks the block's own :00 slot when head0 was promoted from a
        # LATER slot, so the mark-fill grid below (FOCUS_ROWS' include_00)
        # doesn't re-materialize a phantom "  :00" row under a header that
        # now displays a later time — the exact "duplicate :00 line" class
        # the head0 mechanism exists to prevent, just from the other side.
        # (vacated_00/header_hour both default at the top of the function.)
        if head0 is not None:
            hs0 = head0["start_dt"]
            if (hs0.hour, hs0.minute) == (blk_sh, 0):
                left = f"{blk_name}:00"
            elif hs0.hour == blk_sh:
                left = f"{blk_name}:{hs0.minute:02d}"
                vacated_00 = (blk_sh, 0)
            else:
                left = f"{blk_name} {hs0.hour:02d}:{hs0.minute:02d}"
                vacated_00 = (blk_sh, 0)
            header_hour = hs0.hour
        else:
            left = f"{blk_name}:00"
        if head0 is not None:
            body_picks = [p for p in picks if p is not head0]
            running = bool(head0.get("is_running"))
            if head0.get("is_event"):
                my_key = _sel_key(head0["event"])
                if track_selection:
                    STATE.visible_events.append(head0["event"])
            elif head0.get("entry_ids"):
                reg = {"kind": "entry", "start_dt": head0["start_dt"],
                       "entry_ids": head0["entry_ids"],
                       "raw_desc": head0["raw_desc"],
                       "project_id": head0["project_id"],
                       "dur_min": head0.get("dur_min"), "running": running}
                my_key = _sel_key(reg)
                if track_selection:
                    STATE.visible_events.append(reg)
            else:
                my_key = None  # synthetic pick (sleep spillover) — unselectable
            is_sel = my_key is not None and STATE.event_sel == my_key
            dur = f"({head0['dur_min']})" if head0.get("is_event") else fmt_dur(head0["dur_min"])
            prefix = "▶ " if running else ""
            # d357 recording live → 🎙 rides RIGHT of the task name
            # (user request 2026-08-02: "move the mic to be to the right
            # of the task"). A doc already filed for this entry → 📝 instead
            # (mutually exclusive in practice: a doc isn't filed until well
            # after the live recording ends).
            if running and _rec_active_for(head0.get("raw_desc")):
                rec_sfx = " 🎙"
            elif _has_d357_doc(head0.get("raw_desc")):
                rec_sfx = " 📝"
            else:
                rec_sfx = ""
            vtags = " ".join(f"#{t}" for t in (head0.get("tags") or [])
                             if t in VALUE_TAGS)
            base_sty = head0.get("style") or ""
            if running:
                base_sty = f"bold {base_sty}".strip() if base_sty else "bold class:running"
            elif _is_placeholder(head0["label"]):
                base_sty = _placeholder_style()
            if is_sel:
                sty = f"{base_sty} bg:#3a3a3a".strip() if base_sty else "class:selected_bg"
                left_sty = "class:selected_accent"
                dur_sty = "class:selected_bg"
            else:
                sty = base_sty
                left_sty = "class:dim"
                dur_sty = "class:dim"
            # head0's OWN slot, not always the block's :00 (2026-08-06) — the
            # busy-bar cell must reflect whatever 15/30-min window the header
            # is actually displaying now that it can ride a later slot.
            gsty, gch = _gutter(hs0.hour, hs0.minute, slot_min)
            if is_sel:
                gsty = f"{gsty} bg:#3a3a3a"
            tail_w = (dwidth(right) + 1) if right else 0
            space = max(1, WIDTH_HINT - dwidth(left) - 1 - dwidth(prefix)
                        - dwidth(dur) - 1 - (dwidth(vtags) + 1 if vtags else 0)
                        - tail_w)
            out.append((left_sty, left))
            out.append((gsty, gch))
            txt = truncate(head0["label"], max(1, space - dwidth(rec_sfx))) + rec_sfx
            if head0.get("is_event"):
                out.append((sty, " " * max(0, space - dwidth(txt)) + txt))
            else:
                out.append((sty, prefix + pad(txt, space)))
            if vtags:
                out.append((dur_sty, f" {vtags}"))
            out.append((dur_sty, f" {dur}"))
            if right:
                out.append(("class:dim", " "))
                if emojis:
                    out.append((NEON_PTS_STYLE, emojis))
                    if pts_str:
                        out.append(("class:dim", " "))
                if pts_str:
                    out.append(("bold #ffffff", pts_str))
            out.append(("class:dim", "\n"))
        else:
            trail = max(1, WIDTH_HINT - dwidth(left) - dwidth(right))
            out.append(("class:dim", left))
            out.append(_gutter(blk_sh, 0, slot_min))
            out.append(("class:dim", " " * max(0, trail - 1)))
            if emojis:
                out.append((NEON_PTS_STYLE, emojis))
                if pts_str:
                    out.append(("class:dim", " "))
            if pts_str:
                out.append(("bold #ffffff", pts_str))
            out.append(("class:dim", "\n"))

    # ── body: later slot marks after :00, entries on their slots ──
    # Empty marks always render (this is what fixes a future block like 午
    # dropping its 10:30 / 11:00 rows: the old code blank-padded partially-filled
    # blocks instead of showing the remaining grid). At the default max_rows=3
    # this is 30-min marks AFTER :00 (matching the 4-line card exactly as
    # before); max_rows > 3 (the focus band's wider cards) switches to 15-min
    # marks and includes :00 too — a real entry landing there no longer has
    # the header's bare-pts line to fall back on for visibility.
    include_00 = max_rows > 3

    def _slot(p):
        mm = (p["start_dt"].minute // slot_min) * slot_min
        return (p["start_dt"].hour, mm)
    # Entries (and gaps) win the rows; empty marks only fill what's left, so a
    # real entry is never crowded out by a gridline.
    entry_rows = sorted(
        ((p["start_dt"].hour * 60 + p["start_dt"].minute,
          p["start_dt"].hour, p["start_dt"].minute, p) for p in body_picks),
        key=lambda r: r[0])
    if len(entry_rows) > max_rows:
        # Over-full block: keep the IMPORTANT rows (running first, then by
        # duration), not the chronologically-first ones — the old [:max_rows]
        # slice dropped a 23m run in favor of two sub-10m entries because it
        # started latest (user report 2026-07-27). Chronological order is
        # restored after the cut.
        keep = sorted(entry_rows,
                      key=lambda r: (not r[3].get("is_running"),
                                     bool(r[3].get("is_event")),
                                     -(r[3].get("dur_min") or 0)))[:max_rows]
        rows = sorted(keep, key=lambda r: r[0])
    else:
        rows = entry_rows
    if len(rows) < max_rows:
        # Occupied slots come from ALL picks (not just body_picks): a pick
        # that moved up to ride the header must not leave a grid mark (·/◇ │)
        # re-materializing in the :00 row it vacated (user report 2026-07-30:
        # "the :00 line gets duplicated in a lot of places").
        occupied = {_slot(p) for p in picks}
        # head0 promoted from a LATER slot (2026-08-06): its own real slot is
        # already in `occupied` via the loop above, but the block's :00 slot
        # it vacated is not — without this, FOCUS_ROWS' include_00 grid
        # re-materializes a phantom "  :00" mark under a header that now
        # reads a later time (the mirror image of the bug this whole
        # mechanism exists to prevent).
        if vacated_00 is not None:
            occupied.add(vacated_00)
        offsets = range(0 if include_00 else slot_min, 120, slot_min)
        mark_slots = [(blk_sh + off // 60, off % 60) for off in offsets]
        marks = [(hh * 60 + mm, hh, mm, None)
                 for hh, mm in mark_slots if (hh, mm) not in occupied]
        rows = rows + marks[:max_rows - len(rows)]
        rows.sort(key=lambda r: r[0])

    if track_selection:
        # The cursor's selectable set is exactly what's ON SCREEN — computed
        # from `rows` (post-slice), so Tab can never land on something the
        # max_rows cap trimmed away. EXTEND, not assign: render_focus_compact
        # calls this for both the current AND next block with
        # track_selection=True, and clears visible_events once up front — an
        # assignment here would let the second call silently wipe out the
        # first block's items (regression 2026-07-15: "still can't
        # select... future calendar entries").
        #
        # Three kinds share this one list (user request 2026-07-17: select
        # real time entries too, and empty/untracked stretches): a raw gcal
        # event dict (unwrapped, exactly as before — every existing
        # event-cursor call site keeps working untouched), a real tracked
        # entry ("kind": "entry", only when it actually carries entry_ids —
        # a synthetic pick like the sleep-spillover row has none and stays
        # unselectable), and an untracked gap ("kind": "empty").
        for _, _, _, p in rows:
            if p is None:
                continue
            if p.get("is_event"):
                STATE.visible_events.append(p["event"])
            elif p.get("is_gap"):
                STATE.visible_events.append({"kind": "empty", "start_dt": p["start_dt"],
                                             "dur_min": p["dur_min"]})
            elif p.get("entry_ids"):
                STATE.visible_events.append({"kind": "entry", "start_dt": p["start_dt"],
                                             "entry_ids": p["entry_ids"],
                                             "raw_desc": p["raw_desc"],
                                             "project_id": p["project_id"],
                                             "dur_min": p.get("dur_min"),
                                             "running": bool(p.get("is_running"))})

    # The header established THIS hour (2026-08-06: not always blk_sh — a
    # promoted head0 in the block's second hour shifts it), so a body row in
    # the same hour abbreviates instead of redundantly repeating it.
    prev_hour = header_hour
    for _, hh, mm, p in rows:
        tcol, prev_hour = _abbrev_tcol(hh, mm, prev_hour)
        if p is None:
            if cont and (hh, mm) in cont:
                # A meeting started earlier flows through this slot: keep the
                # ◇ │ continuation rather than a bare time. Calendar coverage
                # renders at the RIGHT edge, matching the right-justified
                # event rows it continues; Toggl/sleep coverage stays left
                # (user request 2026-07-30).
                csty = cont[(hh, mm)]
                csty, c_event = csty if isinstance(csty, tuple) else (csty, False)
                out.append(("class:time", tcol))
                out.append(_gutter(hh, mm, slot_min))
                glyph = "◇ │"
                if c_event:
                    space = max(0, WIDTH_HINT - dwidth(tcol) - 1 - dwidth(glyph))
                    out.append((csty or "class:future", " " * space + "│ ◇\n"))
                else:
                    out.append((csty or "class:future", glyph + "\n"))
            else:
                # Genuinely empty: the time, then a faint "·" placeholder —
                # restoring the marker the old detail-band gcal-preview grid
                # used for an empty slot (user report 2026-07-15: "add back
                # the lines for each of the blocks... not sure why you
                # removed that"), so an empty row still reads as "checked,
                # nothing here" rather than a bare, easy-to-miss timestamp.
                out.append(("class:time", tcol))
                out.append(_gutter(hh, mm, slot_min))
                out.append(("class:idle", "·\n"))
            continue
        if p.get("is_gap"):
            # Untracked past stretch (≥ GAP_MIN): a solid-red-block↔plain-red-text
            # label pulse, filled out with a ┄ dash line (not blank padding) so
            # the row still reads as a highlighted bar. Labelled "empty → HH:MM
            # (Nm)" so the block's end time and the word "empty" are stated
            # outright, never inferred. Selectable (user request 2026-07-17:
            # "select empty components and fill them with a time entry") —
            # Enter prefills a ready-made "HHMM-HHMM " range from this exact
            # gap, no new backend needed (the typed-command path already
            # creates ranged entries).
            end = p["start_dt"] + dt.timedelta(minutes=p["dur_min"])
            label = _gap_label(end, p["dur_min"])
            gap_selected = STATE.event_sel == _sel_key({"kind": "empty", "start_dt": p["start_dt"]})
            if gap_selected:
                fill_cls = "class:selected_bg"
                time_sty = "class:selected_accent"
            else:
                fill_cls = "class:no_entry_bg" if _gap_alarm_on() else "class:no_entry"
                time_sty = "class:time"
            space = max(1, WIDTH_HINT - dwidth(tcol) - 1)
            gsty, gch = _gutter(hh, mm, slot_min)
            if gap_selected:
                gsty = f"{gsty} bg:#3a3a3a"
            out.append((time_sty, tcol))
            out.append((gsty, gch))
            out.append((fill_cls, _gap_fill(label, space) + "\n"))
            continue
        if p.get("is_free"):
            # Meeting-free future stretch: a calm green bar — the mirror of
            # the red "empty" past-gap rows, making free time first-class ink
            # instead of negative space (user request 2026-07-27: "at a
            # glance it's hard to tell how much time I have free").
            end = p["start_dt"] + dt.timedelta(minutes=p["dur_min"])
            label = f"free → {end:%H:%M} ({fmt_dur(p['dur_min'])})"
            space = max(1, WIDTH_HINT - dwidth(tcol) - 1)
            out.append(("class:time", tcol))
            out.append(_gutter(hh, mm, slot_min))
            # Right-justified like calendar rows (user request 2026-07-28) —
            # free time and the plan share the right column; tracked reality
            # keeps the left. The ┄ texture leads instead of trailing.
            ft = truncate(label, space)
            rem = space - dwidth(ft)
            fill = ("┄" * (rem - 1) + " ") if rem > 1 else " " * max(0, rem)
            out.append(("class:free", fill + ft + "\n"))
            continue
        if p.get("is_running"):
            # The live task: same row shape as any entry, but a bold "▶ "
            # marker and bold style so it still reads distinctly even though
            # it's otherwise a plain compact row (no sub-second ticking —
            # the whole-minute duration just recomputes on each repaint).
            # Selectable like any other entry — editing a running timer's
            # description/project doesn't require stopping it first.
            dur = fmt_dur(p["dur_min"])
            prefix = "▶ "
            # 🎙 sits right of the task name (user request 2026-08-02); a
            # filed doc for this entry gets 📝 instead (see _has_d357_doc).
            if _rec_active_for(p.get("raw_desc")):
                rec_sfx = " 🎙"
            elif _has_d357_doc(p.get("raw_desc")):
                rec_sfx = " 📝"
            else:
                rec_sfx = ""
            vtags = " ".join(f"#{t}" for t in (p.get("tags") or [])
                             if t in VALUE_TAGS)
            space = max(1, WIDTH_HINT - dwidth(tcol) - 1 - dwidth(prefix) - dwidth(dur) - 1
                        - (dwidth(vtags) + 1 if vtags else 0))
            running_selected = bool(p.get("entry_ids")) and STATE.event_sel == _sel_key(
                {"kind": "entry", "start_dt": p["start_dt"], "entry_ids": p["entry_ids"]})
            if running_selected:
                sty = (f"bold {p['style']}".strip() if p["style"] else "bold class:running") + " bg:#3a3a3a"
                time_sty = "class:selected_accent"
                dur_sty = "class:selected_bg"
            else:
                sty = f"bold {p['style']}".strip() if p["style"] else "bold class:running"
                time_sty = "class:time"
                dur_sty = "class:dim"
            gsty, gch = _gutter(hh, mm, slot_min)
            if running_selected:
                gsty = f"{gsty} bg:#3a3a3a"
            out.append((time_sty, tcol))
            out.append((gsty, gch))
            _txt = truncate(p["label"], max(1, space - dwidth(rec_sfx))) + rec_sfx
            out.append((sty, prefix + pad(_txt, space)))
            if vtags:
                out.append((dur_sty, f" {vtags}"))
            out.append((dur_sty, f" {dur}\n"))
            continue
        # A gcal event mixed into a non-future (current-block) card via its own
        # is_event flag still reads as "scheduled" (parenthesized duration),
        # not "tracked" (fmt_dur) — the block-level is_future flag alone can't
        # express "elapsed portion is real, remaining portion is a plan".
        dur = f"({p['dur_min']})" if (is_future or p.get("is_event")) else fmt_dur(p["dur_min"])
        # Value tags (#-1/#-2/#-3 — the ones worth points) ride the right
        # edge just before the minutes (user request 2026-07-28).
        vtags = " ".join(f"#{t}" for t in (p.get("tags") or [])
                         if t in VALUE_TAGS)
        # A filed d357 doc for this (past, non-running) entry → 📝 right of
        # the task name, same convention as the head0/is_running rows above.
        doc_sfx = " 📝" if (not p.get("is_event")
                            and _has_d357_doc(p.get("raw_desc"))) else ""
        space = max(1, WIDTH_HINT - dwidth(tcol) - 1 - dwidth(dur) - 1
                    - (dwidth(vtags) + 1 if vtags else 0))
        if p.get("is_event"):
            my_key = _sel_key(p["event"])
        elif p.get("entry_ids"):
            # A real (non-running) tracked entry — selectable for edit, same
            # as the is_running branch above.
            my_key = _sel_key({"kind": "entry", "start_dt": p["start_dt"], "entry_ids": p["entry_ids"]})
        else:
            my_key = None  # e.g. the synthetic sleep-spillover pick — no real entry behind it
        is_selected = my_key is not None and STATE.event_sel == my_key
        if is_selected:
            # The event cursor's highlight, dtd-style: a flat background band
            # across the WHOLE row (not ANSI reverse, which just inverts
            # whatever fg/bg happen to be in play and looked inconsistent
            # row to row) plus an accent color on the leading time column,
            # echoing dtd's left-edge marker. Same characters, same widths,
            # same pad()/space math as an unselected row — only the colors
            # change, so the row's horizontal position never shifts (user
            # report 2026-07-15: wanted the per-block column alignment kept).
            sty = f"bold {p['style']}".strip() + " bg:#3a3a3a" if p["style"] else "class:selected_bg"
            time_sty = "class:selected_accent"
            dur_sty = "class:selected_bg"
        else:
            sty = _placeholder_style() if _is_placeholder(p["label"]) else p["style"]
            time_sty = "class:time"
            dur_sty = "class:dim"
        gsty, gch = _gutter(hh, mm, slot_min)
        if is_selected:
            gsty = f"{gsty} bg:#3a3a3a"
        out.append((time_sty, tcol))
        out.append((gsty, gch))
        body_txt = truncate(p["label"], max(1, space - dwidth(doc_sfx))) + doc_sfx
        if p.get("is_event"):
            # Calendar entries right-justified, Toggl entries left (user
            # request 2026-07-28) — the two sources read as separate columns
            # at a glance instead of interleaving mid-line.
            out.append((sty, " " * max(0, space - dwidth(body_txt)) + body_txt))
        else:
            out.append((sty, pad(body_txt, space)))
        if vtags:
            out.append((dur_sty, f" {vtags}"))
        out.append((dur_sty, f" {dur}\n"))

    # Pad to exactly max_rows body rows so every block stays a consistent height.
    for _ in range(max_rows - len(rows)):
        out.append(("class:idle", "\n"))
    return out


def _past_block_picks(blk_name, merged, limit: int = 4) -> list[dict]:
    """Top-``limit`` Toggl entries (by duration) starting in this block,
    chronological. The RUNNING entry (if merged carries one — the focus
    band's current-block call includes it, clipped to now) always survives
    the cut regardless of its duration-so-far: it's the live task, not a
    candidate to be crowded out by longer finished ones."""
    items = []
    for m in merged:
        blk = hour_to_block(m["start_dt"].hour)
        if not blk or blk[0] != blk_name:
            continue
        mins = int((m["end_dt"] - m["start_dt"]).total_seconds() // 60)
        is_running = bool(m.get("running"))
        if mins < 1 and not is_running:
            continue
        is_sleep = (m["desc"] or "").strip() == "睡觉"
        # Project shows via row COLOR alone (user 2026-07-29: "I use colors
        # to show project, so you don't need to include project names").
        label = display_desc(m["desc"]) or "(blank)"
        items.append({
            "start_dt": m["start_dt"],
            "time_str": f"{m['end_dt']:%H:%M}" if is_sleep else f"{m['start_dt']:%H:%M}",
            "label": label,
            "style": project_style(toggl_project_code(m["project_id"], m["desc"])),
            "dur_min": mins,
            "is_running": is_running,
            # The real Toggl entry id(s) this display row was merged from —
            # empty for synthetic picks (e.g. _block_sleep_item) that don't
            # come from a real entry. A merged span (contiguous same-desc
            # entries) carries ALL of its ids: the row is visually ONE unit,
            # so an edit (rename/reproject) applies to every entry behind it,
            # not an arbitrarily-chosen first/last one.
            "entry_ids": m.get("ids", []),
            "tags": m.get("tags") or [],
            # Raw (undecorated) description + project id — for the entry-edit
            # prefill, which must retype the actual editable value, not the
            # Haiku-shortened/code-suffixed display `label` above.
            "raw_desc": m["desc"],
            "project_id": m["project_id"],
        })
    items.sort(key=lambda x: x["dur_min"], reverse=True)
    running = [x for x in items if x["is_running"]]
    rest = [x for x in items if not x["is_running"]]
    items = running + rest[:max(0, limit - len(running))]
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


def _block_spill_items(blk_sh, blk_eh, cutoff) -> list[dict]:
    """Non-sleep entries that STARTED in an earlier block and flow across
    this block's start, clipped to the block window as synthetic picks —
    the general case of _block_sleep_item, which only ever handled 睡觉.
    Without this the spilled portion renders as anonymous ◇ │ continuation
    marks with no title (user report 2026-07-27: a 19:59-21:00 run showed
    NOTHING in 亥 — "the title is missing"). Carries the real entry id, so
    the row is selectable and ⌥↵ can grant the spilled portion's points."""
    blk_start = cutoff.replace(hour=blk_sh, minute=0, second=0, microsecond=0)
    blk_end = blk_start + dt.timedelta(hours=blk_eh + 1 - blk_sh)
    out = []
    for e in STATE.entries:
        if (e["desc"] or "").strip() == "睡觉":
            continue  # _block_sleep_item's job
        if not (e["start_dt"] < blk_start < e["end_dt"]):
            continue
        end = min(e["end_dt"], blk_end, cutoff)
        mins = int((end - blk_start).total_seconds() // 60)
        if mins < 1:
            continue
        label = display_desc(e["desc"]) or "(blank)"  # color carries the project
        out.append({
            "start_dt": blk_start,
            "time_str": f"{blk_start:%H:%M}",
            "label": label,
            "style": project_style(toggl_project_code(e["project_id"], e["desc"])),
            "dur_min": mins,
            "is_running": bool(e.get("running")),
            "tags": list(e.get("tags") or []),
            "entry_ids": [e["id"]],
            "raw_desc": e["desc"],
            "project_id": e["project_id"],
        })
    return out


def _block_gaps(blk_sh, blk_eh, cutoff) -> list[dict]:
    """Untracked stretches >= GAP_MIN minutes inside a block's window,
    chronological. Sweeps raw STATE.entries (not the merged display spans,
    which join same-desc neighbours and would hide stop/resume gaps). A gap
    straddling a block boundary is split, each block reporting its share.

    The trailing gap (after the last entry) stops at min(blk_end, cutoff),
    not the bare block end: for a fully-elapsed past block cutoff is always
    >= blk_end, so this is a no-op there — but for the CURRENT, still-in-
    progress block (cutoff = now < blk_end), a bare blk_end would flash
    FUTURE, not-yet-elapsed minutes as "untracked".

    Returns nothing at all when STATE.entries isn't a CONFIRMED read
    (STATE.entries_known False — never fetched yet, or the last fetch
    failed/402'd): an empty STATE.entries is indistinguishable from a
    genuinely tracked-nothing block, and without this gate a cold-start rate
    limit rendered a confident "empty → HH:MM" flash over time Toggl had
    actually filled (2026-07-15)."""
    if not STATE.entries_known:
        return []
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
        # A running entry's end_dt froze at the last fetch_today (up to
        # 5min+cooldowns ago) — sweeping with that stale end flashed the
        # minutes since the fetch as "empty" while a timer was live (user
        # report 2026-07-21: offsite running, "1402-1424 flashing as
        # empty"). While STATE.current confirms a timer is running, a
        # running entry covers through the cutoff; when current is None
        # (confirmed idle) the stale end is the best available estimate
        # and the gap flash is legitimate.
        s = e["start_dt"]
        en = cutoff if (e.get("running") and STATE.current) else min(e["end_dt"], cutoff)
        if en <= pos:
            continue
        if s >= blk_end:
            break
        if s > pos and (g := gap_item(pos, min(s, blk_end))):
            items.append(g)
        pos = max(pos, en)
        if pos >= blk_end:
            return items
    if (g := gap_item(pos, min(blk_end, cutoff))):
        items.append(g)
    return items


FREE_MIN = 15  # meeting-free stretches shorter than this aren't worth a row


def _future_free_gaps(blk_sh, blk_eh, now) -> list[dict]:
    """Meeting-free stretches >= FREE_MIN inside a block's NOT-yet-elapsed
    window, chronological — _block_gaps' mirror for time that hasn't happened
    yet (user request 2026-07-27: "at a glance it's hard to tell how much
    time I have free"). Busy = opaque, non-all-day calendar events; the
    window starts at max(block start, now) so the current block only counts
    its remaining minutes, and a fully-elapsed block yields nothing."""
    blk_start = now.replace(hour=blk_sh, minute=0, second=0, microsecond=0)
    blk_end = blk_start + dt.timedelta(hours=blk_eh + 1 - blk_sh)
    pos = max(blk_start, now)
    if pos >= blk_end:
        return []

    def free_item(start, end):
        mins = int((end - start).total_seconds() // 60)
        if mins < FREE_MIN:
            return None
        return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": "",
                "style": "", "dur_min": mins, "is_free": True}

    busy = sorted(
        ((ev["start_dt"], ev["end_dt"]) for ev in STATE.events
         if not (ev.get("transparency") == "transparent" or ev.get("all_day"))
         and ev["end_dt"] > pos and ev["start_dt"] < blk_end),
        key=lambda t: t[0])
    items = []
    for s, e in busy:
        if s > pos and (f := free_item(pos, min(s, blk_end))):
            items.append(f)
        pos = max(pos, e)
        if pos >= blk_end:
            return items
    if (f := free_item(pos, blk_end)):
        items.append(f)
    return items


def _split_gaps_around_events(gaps: list[dict], events: list[dict]) -> list[dict]:
    """Carve an uncovered event's own window out of any gap it falls inside.

    _block_gaps only ever looks at real Toggl data, so an uncovered calendar
    event sitting inside an otherwise-untracked stretch didn't shrink the
    gap at all — a lone 11-min meeting inside a 45-min gap still flashed the
    WHOLE 45 minutes as "empty" (label "(Nm)", both parenthesized AND
    minute-suffixed — neither the tracked nor the scheduled convention)
    right next to the meeting's own correctly-labelled "(11)" row (user
    report 2026-07-20: "old events... rather than list '11m' just list
    (11)"). Splits each gap into 0-2 remaining sub-gaps (before/after the
    event), dropping whatever falls back under GAP_MIN."""
    if not events:
        return gaps
    out = []
    for g in gaps:
        segments = [(g["start_dt"], g["start_dt"] + dt.timedelta(minutes=g["dur_min"]))]
        for ev in events:
            next_segments = []
            for s, e in segments:
                ev_s, ev_e = max(ev["start_dt"], s), min(ev["end_dt"], e)
                if ev_s >= ev_e:
                    next_segments.append((s, e))
                    continue
                if ev_s > s:
                    next_segments.append((s, ev_s))
                if ev_e < e:
                    next_segments.append((ev_e, e))
            segments = next_segments
        for s, e in segments:
            mins = int((e - s).total_seconds() // 60)
            if mins >= GAP_MIN:
                out.append({**g, "start_dt": s, "time_str": f"{s:%H:%M}", "dur_min": mins})
    return out


def _future_block_picks(blk_name, events, limit: int = 4) -> list[dict]:
    """Top-``limit`` gcal events (by duration) starting in this block, chronological."""
    items = []
    for ev in events:
        blk = hour_to_block(ev["start_dt"].hour)
        if not blk or blk[0] != blk_name:
            continue
        if ev.get("transparency") == "transparent" or ev.get("all_day"):
            continue
        if _event_tracked(ev, STATE.entries):
            continue
        mins = max(1, int((ev["end_dt"] - ev["start_dt"]).total_seconds() // 60))
        items.append({
            "start_dt": ev["start_dt"],
            "time_str": f"{ev['start_dt']:%H:%M}",
            "label": event_title(ev),
            "style": project_style(gcal_project_code(ev)),
            "dur_min": mins,
            "is_event": True,
            "event": ev,  # raw event, for the event-cursor "convert to timer" action
        })
    items.sort(key=lambda x: x["dur_min"], reverse=True)
    items = items[:limit]
    items.sort(key=lambda x: x["start_dt"])
    return items


def _event_covered(ev: dict, entries: list[dict]) -> bool:
    """True if some raw (unclipped) Toggl entry overlaps this event's window
    at all. Checked against STATE.entries directly, never the per-block
    CLIPPED `merged`/`picks` lists — clipping a long entry to a 2h block
    window would make its true (possibly multi-hour) span invisible, which
    is exactly the "long running generic timer hides three real meetings"
    case this feature exists to fix."""
    return any(e["start_dt"] < ev["end_dt"] and e["end_dt"] > ev["start_dt"]
               for e in entries)


RECLAIM_MIN_ENTRY_MIN = 60  # a covering entry this long smells like a runaway clock
RECLAIM_SLACK_MIN = 30      # ...and must exceed the event by this much
TRACKED_EARLY_START_MIN = 15  # running same-title entry this early = tracking the meeting


def _norm_meeting_title(s: str) -> str:
    """Comparison key for entry-desc vs event-title matching. Routed through
    _safe_event_title on BOTH sides: a converted entry's desc already had its
    commas/whitespace collapsed at creation, and calendar copies of the same
    meeting can differ by stray double spaces (2026-07-30: running entry
    'Huddle: XBOX Developer' vs event 'Huddle:  XBOX Developer' rendered as
    a dup pair)."""
    return _safe_event_title(s).lower()


def _event_tracked(ev: dict, entries: list[dict]) -> bool:
    """A same-titled Toggl entry is already tracking THIS event instance, so
    its calendar row is a duplicate and stays hidden. Title match (normalized)
    plus time proximity: the entry overlaps the event window, or is running
    and started up to TRACKED_EARLY_START_MIN before the event starts (timers
    are usually started a few minutes early). Scoped to the instance, never
    the title globally — a recurring meeting later in the day still shows.

    Keyed on title-match rather than looser overlap so it composes with
    _event_reclaimable instead of fighting it: hanger entries with unrelated
    names still surface their swallowed meetings."""
    t = _norm_meeting_title(ev.get("title") or "")
    if not t:
        return False
    pool = list(entries) + list(getattr(STATE, "entries_yday", []))
    for e in pool:
        if _norm_meeting_title(e.get("desc") or "") != t:
            continue
        if e["start_dt"] < ev["end_dt"] and e["end_dt"] > ev["start_dt"]:
            return True
        if (e.get("running")
                and 0 <= (ev["start_dt"] - e["start_dt"]).total_seconds()
                <= TRACKED_EARLY_START_MIN * 60):
            return True
    return False


def _same_day_dup(e, pool) -> bool:
    """The covering entry RESTARTS a description already used earlier the
    same day (an earlier ≥1m entry with the same desc that ended before it
    started) — the "resumed my reading timer and it ran through the
    meeting" pattern (user request 2026-07-29: a 57m `read fy2027
    priorities` dup fell under the 60m runaway floor and hid the CosmosDB
    meeting). A dup is its own overrun signal: nobody names a meeting's
    dedicated entry after an activity they already timed that morning, so
    no size guard applies on this path."""
    d = (e.get("desc") or "").strip().lower()
    if not d:
        return False
    for o in pool:
        if o is e or (o.get("desc") or "").strip().lower() != d:
            continue
        if (o["end_dt"] <= e["start_dt"]
                and o["start_dt"].date() == e["start_dt"].date()
                and (o["end_dt"] - o["start_dt"]).total_seconds() >= 60):
            return True
    return False


def _event_reclaimable(ev, entries, now) -> bool:
    """A COVERED ended event that should still show and be selectable —
    the "clock I didn't turn off swallowed my meetings" case (user request
    2026-07-29): converting it via Enter/⌥↵ carves the covering entry
    around the meeting (did-fast's MECE trim) and grants its points.

    A covering entry counts as overrun when EITHER it is >60m AND at least
    30m longer than the event (runaway clock — a meeting deliberately
    tracked as its own entry is about the meeting's length and stays
    hidden), OR it duplicates an earlier same-day entry (_same_day_dup).
    No recency or same-day cut ("I want it to span days, because often the
    stale toggl entry is from the previous day"): yesterday-started
    overnight clocks count as covering candidates too."""
    ev_min = (ev["end_dt"] - ev["start_dt"]).total_seconds() / 60
    if ev_min <= 0:
        return False
    pool = list(entries) + list(getattr(STATE, "entries_yday", []))
    for e in pool:
        if not (e["start_dt"] < ev["end_dt"] and e["end_dt"] > ev["start_dt"]):
            continue
        # The suspect must cover ≥80% of the meeting BY ITSELF. Granular,
        # deliberate tracking (several distinct entries across the window)
        # never produces a single dominating suspect, so good Toggl data
        # stays the default and the event stays hidden (user 2026-07-29:
        # "if there are good toggl entries, then toggl should be the
        # default, it's only hanger time entries that I want to suppress" —
        # a 13-minute 冥想 dup brushing the tail of a 45m PT session was
        # resurfacing the whole event).
        overlap = ((min(e["end_dt"], ev["end_dt"])
                    - max(e["start_dt"], ev["start_dt"])).total_seconds() / 60)
        if overlap < 0.8 * ev_min:
            continue
        ent_min = (e["end_dt"] - e["start_dt"]).total_seconds() / 60
        if ent_min > RECLAIM_MIN_ENTRY_MIN and ent_min >= ev_min + RECLAIM_SLACK_MIN:
            return True
        if _same_day_dup(e, pool):
            return True
    return False


def _past_event_picks(blk_name, events, entries, now, limit: int = 4) -> list[dict]:
    """Ended gcal events in this block that are either uncovered by any
    Toggl entry (the "Toggl shows nothing/one giant blob" case, 2026-07-17)
    or covered only by a runaway long entry (_event_reclaimable,
    2026-07-29). Shares _future_block_picks' item shape (is_event=True,
    raw event carried) so the existing event-cursor/Enter-conversion
    plumbing treats an uncovered past meeting identically to an upcoming
    one; only the Enter handler's did-fast-vs-tg-fast branch cares about
    past vs. future."""
    ended = [ev for ev in events if ev["end_dt"] <= now
             and (not _event_covered(ev, entries)
                  or _event_reclaimable(ev, entries, now))]
    return _future_block_picks(blk_name, ended, limit=limit)


def _block_cont_slots(blk_sh, slot_min):
    """Every (hour, minute) mark in a 2-hour block at ``slot_min`` resolution,
    starting at :00. Shared by the _block_*_cont helpers so their granularity
    stays in lockstep with _compact_block_lines' own marks (30-min for a
    standard 3-row card, 15-min for an 8-row focus card — a mismatch would
    leave the finer marks with no continuation lookup, reading as bare gaps
    instead of ◇ │ under a long-running entry/event)."""
    return [(blk_sh + off // 60, off % 60) for off in range(0, 120, slot_min)]


def _block_gcal_cont(blk_sh, ref, slot_min: int = 30) -> dict[tuple[int, int], str]:
    """Slot marks of a block covered by a gcal event → project style.

    Block picks only see what STARTS in the block, so an event flowing
    through it (a 4h workshop started two blocks earlier, or one spanning the
    whole block) used to leave the block looking empty. Covered marks instead
    draw the focus band's ◇ │ continuation glyphs — in future blocks and,
    through the event's end, in past blocks too."""
    out: dict[tuple[int, int], str] = {}
    for hh, mm in _block_cont_slots(blk_sh, slot_min):
        t = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for ev in STATE.events:
            if ev.get("transparency") == "transparent" or ev.get("all_day"):
                continue
            if ev["start_dt"] <= t < ev["end_dt"]:
                out[(hh, mm)] = (project_style(gcal_project_code(ev)), True)
                break
    return out


def _block_sleep_cont(blk_sh, ref, slot_min: int = 30) -> dict[tuple[int, int], str]:
    """Slot marks of a past block covered by an overnight 睡觉 entry → style.

    The sleep counterpart to _block_gcal_cont. _block_sleep_item only fills the
    header row, so a late wake-up sleeping clean through a block (e.g. 辰) left
    the rows below it blank. Marking the covered slots lets the compact
    renderer draw the ◇ │ continuation after the spillover header, so the block
    reads as 'still asleep' rather than empty."""
    out: dict[tuple[int, int], str] = {}
    for hh, mm in _block_cont_slots(blk_sh, slot_min):
        t = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for e in STATE.entries:
            if (e["desc"] or "").strip() != "睡觉":
                continue
            if e["start_dt"] <= t < e["end_dt"]:
                out[(hh, mm)] = project_style(e["project_id"])
                break
    return out


def _block_toggl_cont(blk_sh, ref, slot_min: int = 30) -> dict[tuple[int, int], str]:
    """Slot marks of a past block covered by ANY Toggl entry → style.

    Generalizes _block_sleep_cont to every tracked entry: an entry renders one
    row at its start slot, so the marks a >30m entry flows through drew as bare
    gridlines — on a past-day view a 3h entry read as two hours of nothing.
    Covered marks draw the ◇ │ continuation in the entry's project color, the
    same treatment gcal events already get."""
    out: dict[tuple[int, int], str] = {}
    for hh, mm in _block_cont_slots(blk_sh, slot_min):
        t = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for e in STATE.entries:
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
    # ₦N rides the right edge with the sleep minutes, not the block name
    # (user request 2026-07-21): `─卯 睡觉 →06:00 ──── ₦4 117m`.
    neon_str = f" {emojis}" if emojis else ""
    pts_str = f" {sleep_min}m" if sleep_min else ""
    blk_style = f"bold {style}".strip() if style else "class:dim"
    out: list[tuple[str, str]] = []
    left = "─卯 "
    out.append((blk_style, left))
    label = ""
    if wake:
        label = f"睡觉 →{wake:%H:%M} "
        out.append((style or blk_style, label))
    trail = max(0, WIDTH_HINT - dwidth(left) - dwidth(label)
                - dwidth(neon_str) - dwidth(pts_str))
    out.append((blk_style, "─" * trail))
    if neon_str:
        out.append((NEON_PTS_STYLE, neon_str))
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
            merged[-1]["ids"].append(e["id"])
            merged[-1]["tags"] = sorted(set(merged[-1].get("tags") or [])
                                        | set(e.get("tags") or []))
        else:
            merged.append({"start_dt": e["start_dt"], "end_dt": end,
                           "desc": e["desc"], "project_id": e["project_id"],
                           "tags": list(e.get("tags") or []),
                           "ids": [e["id"]]})

    bo_emojis = _read_block_emojis()
    out: list[tuple[str, str]] = []
    for blk_name, blk_sh, blk_eh in BLOCKS:
        if blk_eh + 1 > cutoff.hour:
            break  # rest handled by the detail band
        pts = _block_display_pts(blk_name)  # clamped to Σ, same as the focus rules
        if blk_name == "卯":
            # Layout exception: the sleep block normally collapses to a single
            # wake-time line. But on an early wake you work through part of 卯
            # (prayer, ibx, …), and collapsing would HIDE those entries — the
            # "-1n prayer during 卯 isn't showing" bug (2026-07-03). Collapse
            # only when 卯 is genuinely all-sleep (no non-睡觉 tracked activity);
            # otherwise fall through and render it as a normal block.
            kmao_picks = _past_block_picks("卯", merged)
            if not any("睡觉" not in (p.get("label") or "") for p in kmao_picks):
                out += _mao_line(bo_emojis.get(blk_name, ""))
                continue
        picks = _past_block_picks(blk_name, merged)
        # Entries that started in an EARLIER block and flow into this one
        # get a clipped, titled row here too — not just anonymous ◇ │ marks.
        picks = _block_spill_items(blk_sh, blk_eh, cutoff) + picks
        sleep = _block_sleep_item(blk_sh, blk_eh, cutoff)
        if sleep:
            picks = ([sleep] + picks)[:4]
        # Meetings that actually happened but never got a Toggl entry (or got
        # swallowed by one giant undifferentiated timer) — "turn a calendar
        # event into a time entry" for PAST meetings too, not just the
        # current/next block (user report 2026-07-17). Only events with no
        # overlapping raw entry show; one already covered by real Toggl data
        # renders normally via `picks` above, not duplicated here.
        event_picks = _past_event_picks(blk_name, STATE.events, STATE.entries, cutoff)
        # An uncovered event's own window must be carved OUT of the gap it
        # sits inside — else it flashes as both a correctly-labelled "(11)"
        # event row AND (redundantly) part of a generic "(Nm)" untracked-gap
        # row covering the same minutes (user report 2026-07-20).
        gaps = _split_gaps_around_events(_block_gaps(blk_sh, blk_eh, cutoff),
                                         [p["event"] for p in event_picks])
        full_block = (blk_eh + 1 - blk_sh) * 60
        # Drop a single gap that spans the whole (untracked) block — it would
        # just restate the empty grid the body already draws.
        if len(gaps) == 1 and gaps[0]["dur_min"] >= full_block:
            gaps = []
        # The header is now the bare :00 slot, so every entry and gap is a body
        # row. _compact_block_lines merges them with the empty half-hour marks
        # and caps at 3 rows.
        body = picks + gaps + event_picks
        body.sort(key=lambda x: x["start_dt"])
        # Tracked reality (Toggl, incl. sleep) wins over the gcal plan on
        # elapsed blocks; merge order = ascending authority.
        cont = {**_block_sleep_cont(blk_sh, cutoff),
                **_block_gcal_cont(blk_sh, cutoff),
                **_block_toggl_cont(blk_sh, cutoff)}
        out += _compact_block_lines(blk_name, blk_sh, body, pts,
                                    bo_emojis.get(blk_name, ""), cont=cont,
                                    is_future=False, track_selection=True)
    return out


def _block_display_pts(name: str) -> int:
    """Per-block 分, mirrored directly from Neon's 0分 G:O cells — no
    reconstruction, no arithmetic guessing. fetch_points reads every block's
    VALUE straight from the sheet (a locked block's literal, or the live
    residual formula's own computed value), so block_points holds every
    nonzero block, current one included, and the displayed number always
    matches what Neon shows. A block the sheet shows empty returns 0.

    This used to also carry a SEPARATE "Σ − locked" arithmetic
    reconstruction (STATE.block_running_pts) as a cold-start fallback,
    before the first successful Neon read landed. That reconstruction was
    the actual source of at least two "impossible" block values shown on
    screen (5064分 on 2026-07-03, 6932分 on 2026-07-20) — a torn/mid-recalc
    read could poison the subtraction even when the direct-value read for
    every block individually stayed sane. User feedback (2026-07-20): "all
    you have to do is pull from neon" — so now there IS no other path:
    before the first successful read, this returns 0 (a blank header for a
    few seconds), never a guess.

    A block is a residual of the day's Σ (=D-SUM(locked) ≤ D), so it can
    NEVER exceed today_points. block_points and today_points update on
    different gates, so a stuck/torn block value can momentarily outrun Σ
    (666 shown on a 272分 day, 2026-07-02) — clamp to Σ as a second,
    independent line of defense (a block over the whole-day total is
    impossible, and capping it is strictly better than displaying nonsense)."""
    v = STATE.block_points.get(name, 0)
    return (min(v, STATE.today_points) if STATE.today_points > 0
            else min(v, _MAX_PLAUSIBLE_TOTAL))


def _detail_merge_past(win_start, win_end) -> list[dict]:
    """Toggl entries overlapping [win_start, win_end), clipped to the window and
    with consecutive same-desc fragments (stop/resume splits ≤60s apart) merged
    into one span. Chronological."""
    segs: list[dict] = []
    for e in sorted(STATE.entries, key=lambda x: x["start_dt"]):
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


def _detail_entry_row(s, body_cls, body_text) -> list[tuple[str, str]]:
    """A focus-band row keyed by its real START time only — the end is implied by
    the next row's start (entries are tracked MECE / back-to-back), so we don't
    print a redundant end time."""
    prefix = f" {s:%H:%M} │ "
    space = max(1, WIDTH_HINT - dwidth(prefix) - 1)
    return [("class:time", prefix), (body_cls, truncate(body_text, space) + "\n")]


def _detail_gap_row(s, end) -> list[tuple[str, str]]:
    """A flashing untracked-time row, keyed by the gap's start (shown via the
    HH:MM prefix) and its real end (``end``, the next row's start — or the
    window/tail boundary for a trailing gap). Labelled "empty → HH:MM (Nm)",
    same solid-red-block↔plain-red-text pulse as the compact block view."""
    dur_min = max(0, int((end - s).total_seconds() // 60))
    label = _gap_label(end, dur_min)
    fill_cls = "class:no_entry_bg" if _gap_alarm_on() else "class:no_entry"
    prefix = f" {s:%H:%M} │ "
    space = max(1, WIDTH_HINT - dwidth(prefix) - 1)
    return [("class:time", prefix), (fill_cls, _gap_fill(label, space) + "\n")]


def _detail_rule_row(s, label, cls) -> list[tuple[str, str]]:
    """The 'you are here' now-row, anchored to its real start time, drawn as a
    full-width rule so it stands out from the rows above."""
    prefix = f" {s:%H:%M} │ "
    trail = max(0, WIDTH_HINT - dwidth(prefix) - dwidth(label) - 1)
    return [("class:time", prefix), (cls, f"{label} " + "─" * trail + "\n")]


def _detail_cap_per_block(shown, gap_starts) -> list[dict]:
    """Keep the focus band near DETAIL_ROWS lines per 地支 block: within each
    block keep the longest entries (gaps in the block count against the budget),
    absorbing the rest into their neighbours' implied spans."""
    gaps_in: dict = {}
    for gs in gap_starts:
        b = hour_to_block(gs.hour)
        key = b[0] if b else None
        gaps_in[key] = gaps_in.get(key, 0) + 1
    by_block: dict = {}
    for seg in shown:
        b = hour_to_block(seg["start"].hour)
        by_block.setdefault(b[0] if b else None, []).append(seg)
    keep: list[dict] = []
    for key, segs in by_block.items():
        budget = max(1, DETAIL_ROWS - gaps_in.get(key, 0))
        segs.sort(key=lambda s: (s["end"] - s["start"]).total_seconds(), reverse=True)
        keep.extend(segs[:budget])
    return keep


def _detail_past_rows(win_start, win_end, now, live) -> list[tuple[str, str]]:
    """The focus band's elapsed region, keyed by real START times (end implied by
    the next row). Tiny entries (< DETAIL_MIN) are absorbed rather than given a
    line; each block is held near DETAIL_ROWS lines by keeping its longest
    entries. Untracked stretches ≥ GAP_MIN render an explicit "empty → HH:MM
    (Nm)" label that pulses solid-red-block↔plain-red-text. When ``live`` the
    tail is the now-row (running timer at its real start, or the idle alarm)."""
    full = _detail_merge_past(win_start, win_end)  # all entries = coverage

    # Gaps come from FULL coverage, so an absorbed tiny entry never reads as a
    # gap. Trailing gap (after the last entry) is the live/past-day tail's job.
    # gap_ends is keyed by start and computed here (against FULL coverage, not
    # the display-filtered `shown`), so a gap's stated end time is always the
    # true closing entry's start — even when that entry itself gets absorbed or
    # capped out of `shown` and never appears as its own row.
    gap_starts: list = []
    gap_ends: dict = {}
    cursor = win_start
    for seg in full:
        if (seg["start"] - cursor).total_seconds() >= GAP_MIN * 60:
            gap_starts.append(cursor)
            gap_ends[cursor] = seg["start"]
        cursor = max(cursor, seg["end"])
    tail_cursor = cursor

    shown = [s for s in full
             if (s["end"] - s["start"]).total_seconds() >= DETAIL_MIN * 60]
    shown = _detail_cap_per_block(shown, gap_starts)

    rows = ([(s["start"], "e", s) for s in shown]
            + [(g, "g", None) for g in gap_starts])
    rows.sort(key=lambda r: r[0])

    out: list[tuple[str, str]] = []
    for tstart, kind, seg in rows:
        if kind == "g":
            out += _detail_gap_row(tstart, gap_ends[tstart])
            continue
        code = proj_code(seg["pid"])
        label = seg["desc"] + (f"  · {code}" if code else "")
        sty = (_placeholder_style() if _is_placeholder(seg["desc"])
               else (project_style(seg["pid"]) or "class:past"))
        out += _detail_entry_row(seg["start"], sty, label)

    if not live:
        if (win_end - tail_cursor).total_seconds() >= GAP_MIN * 60:
            out += _detail_gap_row(tail_cursor, win_end)
        return out

    # Live tail (today, now inside the window).
    if STATE.current:
        try:
            rs = dt.datetime.fromisoformat(STATE.current.get("start", "")).astimezone(TZ)
        except Exception:
            rs = tail_cursor
        rs = max(rs, win_start)
        if (rs - tail_cursor).total_seconds() >= GAP_MIN * 60:
            out += _detail_gap_row(tail_cursor, rs)
        desc = display_desc(STATE.current.get("description") or "")
        code = proj_code(STATE.current.get("project_id"))
        el = max(0.0, (now - rs).total_seconds())
        m, s = divmod(int(el), 60)
        label = f"▶ {desc}" + (f" · {code}" if code else "") + f"  {m}m{s:02d}.{int((el % 1) * 10)}s"
        cls = (f"bold {_placeholder_style(now)}" if _is_placeholder(desc)
               else (f"bold {project_style(STATE.current.get('project_id'))}".strip() or "class:running"))
        out += _detail_rule_row(rs, label, cls)
    elif STATE.current_known:
        since = _idle_since(now) or tail_cursor
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
    start, end = detail_window()
    # When the detail band already reaches midnight (子 is the current or
    # next block, end_h=24 → end lands on the NEXT day), the day is fully
    # rendered. The old `cutoff.hour` compare saw 0 there and re-rendered
    # 卯–戌 after 子 as phantom next-day blocks (user report 2026-07-27:
    # "one day should not bleed into the next").
    if end.date() != start.date():
        return []
    cutoff = end
    bo_emojis = _read_block_emojis()
    out: list[tuple[str, str]] = []
    for name, sh, eh in BLOCKS:
        if eh + 1 <= cutoff.hour:
            continue
        if sh >= 22:
            break
        picks = sorted(
            _future_block_picks(name, STATE.events)
            + _future_free_gaps(sh, eh, view_now()),
            key=lambda p: p["start_dt"])
        cont = _block_gcal_cont(sh, cutoff)
        # track_selection: evening blocks were the one stretch of the day the
        # event cursor could not reach (render_all's reset + EXTEND contract
        # covers morning and the focus band) — Tab stopped dead after the
        # next block (user report 2026-07-30: "I can't select anything after
        # 申"), which also put late-day meetings out of ⌥↵/^X's reach.
        out += _compact_block_lines(name, sh, picks, 0, bo_emojis.get(name, ""),
                                    cont=cont, is_future=True,
                                    track_selection=True)
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
    code = toggl_project_code(pid, cur.get("description"))
    try:
        st = dt.datetime.fromisoformat(cur.get("start", "")).astimezone(TZ)
        elapsed = (now - st).total_seconds()
    except Exception:
        elapsed = 0.0
    m, s = divmod(max(0, int(elapsed)), 60)
    frac = int((elapsed % 1) * 10)  # tenths of a second
    dur = f"{m}m{s:02d}.{frac}s"
    rec_sfx = " 🎙" if _rec_active_for(cur.get("description")) else ""
    left = f" ▶ {dur}  {desc}{rec_sfx}"
    if code:
        left += f" · {code}"
    pad = max(0, WIDTH_HINT - dwidth(left) - len(clock))
    style = project_style(code) or "class:running"
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
    return [("class:hint", " type to run · Tab/↓↑ select · ↵ start/log/edit/fill · ⌥↵ did · ^P split · ^X del event · esc cancel · [/] day · ^S stop · ^R refresh · ^J/^K scroll · ^Q quit\n")]


def _current_block_lines(blk_name, blk_sh, blk_eh, now, emojis) -> list[tuple[str, str]]:
    """The current (in-progress) block, compact-card style, FOCUS_ROWS body
    rows — same picks/gaps pipeline render_morning uses for a PAST block,
    just clipped to ``now`` instead of a fixed cutoff. The running entry (if
    any) is already in STATE.entries with end_dt continuously extended to
    now by fetch_today, so it flows through the ordinary merge/pick path and
    only needs its "running" flag carried through for the ▶ marker; idle
    time since the last entry falls out of _block_gaps (now cutoff-aware)
    as an ordinary flashing gap row, same as any other block.

    The elapsed portion (real Toggl data) is only half the picture: the
    REST of the block — meetings in progress right now or scheduled later,
    before the block ends — used to be invisible, drawn only as an anonymous
    "◇ │" continuation glyph with no title (user report 2026-07-15: "it
    doesn't seem like janus is showing the other events... specifically the
    other three meetings"). Not-yet-ENDED gcal events within this block
    (end_dt > now — covers both "starting later" and "already in progress",
    the latter matters most for turning a live meeting into a time entry) are
    folded in via _future_block_picks, same source render_evening uses for
    every other future block.

    An event that already ENDED earlier in this still-open block, with no
    covering Toggl entry, used to have neither path catch it: not "upcoming"
    (already ended) and not visited by render_morning (block isn't over yet)
    — it just vanished into the generic untracked-gap row instead, which
    labels its span "(Nm)" (both parenthesized AND minute-suffixed — neither
    of the two real conventions), reading as an anonymous idle stretch
    instead of the actual meeting that happened (user report 2026-07-20:
    "old events... rather than list '11m' just list (11)"). _past_event_picks
    (the same helper render_morning uses) closes that gap here too."""
    items = [e for e in STATE.entries if e["start_dt"] < now]
    merged: list[dict] = []
    for e in items:
        end = min(e["end_dt"], now)
        if merged and merged[-1]["desc"] == e["desc"]:
            merged[-1]["end_dt"] = end
            merged[-1]["running"] = merged[-1].get("running") or e.get("running")
            merged[-1]["ids"].append(e["id"])
            merged[-1]["tags"] = sorted(set(merged[-1].get("tags") or [])
                                        | set(e.get("tags") or []))
        else:
            merged.append({"start_dt": e["start_dt"], "end_dt": end, "desc": e["desc"],
                           "project_id": e["project_id"], "running": e.get("running", False),
                           "tags": list(e.get("tags") or []),
                           "ids": [e["id"]]})
    picks = _past_block_picks(blk_name, merged, limit=FOCUS_ROWS)
    # A still-relevant entry that STARTED in the previous block (e.g. a run
    # crossing 20:00 into 亥) gets a clipped, titled, selectable row — not
    # just anonymous ◇ │ continuation marks (user report 2026-07-27).
    picks = _block_spill_items(blk_sh, blk_eh, now) + picks
    sleep = _block_sleep_item(blk_sh, blk_eh, now)
    if sleep:
        picks = ([sleep] + picks)[:FOCUS_ROWS]
    upcoming = [ev for ev in STATE.events if ev["end_dt"] > now]
    event_picks = _future_block_picks(blk_name, upcoming, limit=FOCUS_ROWS)
    ended_event_picks = _past_event_picks(blk_name, STATE.events, STATE.entries, now, limit=FOCUS_ROWS)
    gaps = _split_gaps_around_events(_block_gaps(blk_sh, blk_eh, now),
                                     [p["event"] for p in ended_event_picks])
    # The block's REMAINING minutes get free rows too (window starts at now),
    # so "how much of this block is still mine" reads directly off the card.
    free_rows = _future_free_gaps(blk_sh, blk_eh, now)
    body = picks + gaps + event_picks + ended_event_picks + free_rows
    body.sort(key=lambda x: x["start_dt"])
    cont = {**_block_sleep_cont(blk_sh, now, slot_min=15), **_block_gcal_cont(blk_sh, now, slot_min=15),
            **_block_toggl_cont(blk_sh, now, slot_min=15)}
    pts = _block_display_pts(blk_name)
    return _compact_block_lines(blk_name, blk_sh, body, pts, emojis, cont=cont,
                                is_future=False, max_rows=FOCUS_ROWS,
                                track_selection=True)


def render_focus_compact() -> list[tuple[str, str]]:
    """Current + next block, in the SAME compact-card style as the rest of
    the day (render_morning / render_evening) — just FOCUS_ROWS body rows
    instead of the usual 3. Replaces the old dash-ruled "detail band"
    (section_rule + _detail_past_rows + a 15-min gcal preview grid + a
    ticking timer bar), which the user found visually inconsistent
    (2026-07-15: "I like the way 午 is rendering... but not 巳... render in
    the same style I do for the rest of the day"). The running task still
    gets a distinct ▶ marker (_compact_block_lines' is_running branch) and
    idle time still flashes via the same gap treatment as every other
    block — folded into the ordinary picks pipeline instead of a separate
    live-row/alarm code path.

    The event cursor spans BOTH blocks here (and, via render_all's shared
    reset, the whole day): each _compact_block_lines(track_selection=True)
    call EXTENDS STATE.visible_events — the next block's events were
    previously untrackable at all (Tab could only ever reach the current
    block's), which read as "can't select future calendar entries" (user
    report 2026-07-15)."""
    now = view_now()
    cur = hour_to_block(now.hour)
    nxt = next_block(now.hour)
    bo_emojis = _read_block_emojis()
    out: list[tuple[str, str]] = []
    if cur and not nxt:
        # Last block of the day (子): detail_window anchors the band at
        # prev + current, and render_morning stops at the band start
        # trusting the band to cover the rest — but only cur/nxt rendered
        # here, so 亥 vanished entirely after 22:00 and on every past-day
        # view (user report 2026-07-27). Render the fully-elapsed 亥 the
        # same way as the current block; everything inside is clipped to
        # `now`, which is past its end, so it reads as a plain past card.
        prv = prev_block(now.hour)
        if prv:
            name, sh, eh = prv
            out += _current_block_lines(name, sh, eh, now, bo_emojis.get(name, ""))
    if cur:
        name, sh, eh = cur
        out += _current_block_lines(name, sh, eh, now, bo_emojis.get(name, ""))
    if nxt:
        name, sh, eh = nxt
        picks = sorted(
            _future_block_picks(name, STATE.events, limit=FOCUS_ROWS)
            + _future_free_gaps(sh, eh, now),
            key=lambda p: p["start_dt"])
        cont = _block_gcal_cont(sh, now, slot_min=15)
        out += _compact_block_lines(name, sh, picks, 0, bo_emojis.get(name, ""),
                                    cont=cont, is_future=True, max_rows=FOCUS_ROWS,
                                    track_selection=True)
    return out


def render_all() -> list[tuple[str, str]]:
    # Event cursor spans the WHOLE day now (past blocks' uncovered meetings,
    # the current + next block) — reset once here, before anything renders,
    # so render_morning's registrations survive render_focus_compact's (each
    # _compact_block_lines(track_selection=True) call only EXTENDs the list).
    STATE.visible_events = []
    parts: list[tuple[str, str]] = []
    parts += render_header()
    parts += render_habits_today()
    parts += render_morning()
    # The rule that used to mark a fixed 22:00 sleep boundary (removed
    # 2026-07-19 — a clock-time marker made little sense once focus blocks
    # already carry their own visual weight) moves here instead: a plain
    # divider marking where "now" actually is, right before the current/next
    # focus band — the boundary a glance actually needs.
    parts.append(("class:rule", "─" * WIDTH_HINT + "\n"))
    parts += render_focus_compact()
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


_COMPLETED_TODAY = Path(os.environ.get("XDG_STATE_HOME")
                        or (Path.home() / ".local" / "state")) / "jm" / "completed-today.json"
_ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")


def _norm_done_name(s: str) -> str:
    """Match completed-today names against a Toggl desc: annotations
    ((N)/[N]/{N}) stripped, whitespace collapsed, lowercased — a Toggl desc
    rarely equals the Todoist content byte-for-byte."""
    return " ".join(_ANNOT_RE.sub(" ", s).split()).lower()


def _points_recorded_today(desc: str) -> tuple[bool, int]:
    """(recorded, 分) for a desc completed TODAY, from completed-today.json —
    did-fast's own idempotency record. Only meaningful for today's view:
    past days keep no completion record, so callers must treat (False, 0)
    there as "unknown", not "not recorded"."""
    try:
        data = json.loads(_COMPLETED_TODAY.read_text())
    except Exception:
        return False, 0
    if data.get("date") != dt.date.today().isoformat():
        return False, 0
    target = _norm_done_name(desc)
    if not target:
        return False, 0
    for n in data.get("names", []):
        if _norm_done_name(n) == target:
            pts = data.get("points", {}).get(n, 0)
            return True, int(pts) if isinstance(pts, (int, float)) else 0
    return False, 0


def _did_summary(stdout_text: str) -> str:
    """One flash-able line from did-fast's JSON blob. The old last-line-of-
    output flash literally showed "}" for every successful conversion; pull
    out what the user actually cares about: points granted, agent-needed
    reasons, already-done skips."""
    try:
        data = json.loads(stdout_text[stdout_text.index("{"):])
    except Exception:
        lines = stdout_text.strip().splitlines()
        return lines[-1] if lines else "(no output)"
    bits = []
    for r in data.get("results", []):
        name = r.get("name", "?")
        fen = r.get("0fen") or {}
        pts = fen.get("points", 0) or 0
        pts += fen.get("bonus", 0) or 0
        if not pts and r.get("variable_1n"):
            pts = r.get("variable_value") or 0
        td = r.get("todoist") or {}
        closed = " · todoist ✓" if td.get("closed") else ""
        if pts:
            bits.append(f"✓ {name} +{pts}分{closed}")
        else:
            bits.append(f"✓ {name}{closed}")
    for a in data.get("agent_needed", []):
        bits.append(f"⚠ {a.get('name', '?')}: {a.get('reason', 'needs agent')}")
    for fs in data.get("future_skipped", []):
        bits.append(f"⚠ {fs.get('name', '?')}: {fs.get('warning', 'skipped')}")
    return "  ".join(bits) if bits else "(no results)"


def run_did_fast(text: str) -> str:
    """Like run_tg_fast, but for did-fast.py — used to convert an ALREADY-
    ENDED calendar event into a completed Toggl entry AND grant its points in
    one shot (the tg-fast path only ever starts a running timer, no points).
    did-fast is a much heavier call: an Excel write over ix-osa plus Todoist
    round-trips, commonly 10-20s and occasionally more — hence the longer
    timeout than run_tg_fast's 15s."""
    try:
        proc = subprocess.run(
            ["python3", DID_FAST, text],
            capture_output=True, text=True, timeout=45,
        )
        if proc.stdout and "{" in proc.stdout:
            return _did_summary(proc.stdout)
        out = (proc.stdout or proc.stderr or "").strip()
        return out.splitlines()[-1] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "err: did-fast timed out"
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
    """True while janus is still booting. The tty can hold queued text from
    the spawning terminal (cmux respawn-pane types the launch command into the
    pane); without this gate that text + newline reaches the enter handler and
    starts a Toggl timer named after the command line (regression 2026-06-11:
    timer 'python3 ~/i446-monorepo/tools/tg/janus.py')."""
    return time.monotonic() - STATE.boot_time < window


def _safe_event_title(title: str) -> str:
    """Conversion commands ride through tg-fast/did-fast, which SPLIT
    multi-item input on [,;，；] — a comma inside an event title
    ("CosmosDB Deprecation, Part 3") therefore became TWO items and created
    a Toggl entry literally named "Part" (2026-07-29). Separators collapse
    to spaces before the title enters a command string."""
    return re.sub(r"\s+", " ", re.sub(r"[,;，；]", " ", title or "")).strip()


def _event_to_tg_command(ev: dict, now: dt.datetime) -> str:
    """The /tg-style command string that converts a calendar event into a
    Toggl entry: backdated to the event's own start if it already began
    (the common case — "usually I'll do that in real time", i.e. mid-
    meeting), otherwise a plain start (it hasn't happened yet). Reuses
    tg-fast.py's own self-contained backdated-start handling (stop-current +
    trim-overlap + start) — nothing about that is reimplemented here."""
    title = _safe_event_title(ev.get("title"))
    code = gcal_project_code(ev)
    suffix = f" @{code}" if code else ""
    if ev["start_dt"] <= now:
        return f"{ev['start_dt']:%H%M} {title}{suffix}"
    return f"{title}{suffix}"


def _event_to_did_command(ev: dict) -> str:
    """The /did-style time-range command that backfills an ALREADY-ENDED
    calendar event as a completed Toggl entry AND grants its points in one
    shot ("grant the points for them at the same time" — user request
    2026-07-17). did-fast's Step 6 variable-task path takes duration-in-
    minutes as the points value for a "<desc> HHMM-HHMM @code" input — the
    exact format already verified manually (2026-07-16, the "asha prep"
    backfill). Never used for a still-in-progress/future event — the enter
    handler only calls this when the event has actually ended (ev["end_dt"]
    <= now); an unfinished meeting has no real end time to encode."""
    title = _safe_event_title(ev.get("title"))
    code = gcal_project_code(ev)
    suffix = f" @{code}" if code else ""
    return f"{title} {ev['start_dt']:%H%M}-{ev['end_dt']:%H%M}{suffix}"


def _enqueue_work(app, label: str, job, key: str | None = None) -> bool:
    """Add a did/tg job to the serial work queue (user request 2026-07-30:
    enqueue instead of the old "still converting the last one…" rejection).

    Jobs are async callables, run strictly one at a time in FIFO order by a
    single consumer — the one-at-a-time discipline the old gate enforced
    (concurrent did-fast runs race ix-osa writes on the same sheet and can
    double-grant points) is kept; only the rejection is gone. `key` dedupes:
    an identical command already queued or running is refused, since running
    it twice is exactly the double-grant the old gate existed to stop."""
    key = key or label
    if key in STATE.queued_cmds:
        flash(f"already queued: {label}", 3.0)
        return False
    if STATE.work_q is None:
        STATE.work_q = asyncio.Queue()
        app.create_background_task(_work_consumer(app))
    STATE.queued_cmds.add(key)
    STATE.work_q.put_nowait((label, key, job))
    waiting = STATE.work_q.qsize() - (0 if STATE.conversion_in_flight else 1)
    if waiting > 0:
        flash(f"queued (#{waiting + 1}): {label}", 3.0)
    else:
        # Idle queue: the job starts on the next loop tick — flash the command
        # NOW so the press feels immediate (the job re-flashes on start, which
        # is what a QUEUED job's turn looks like).
        flash(f"$ {label}")
    return True


async def _work_consumer(app):
    """Single consumer for STATE.work_q — the serialization point for every
    did/tg job janus fires. Lives as one background task for the app's whole
    lifetime (created lazily by the first _enqueue_work)."""
    while True:
        label, key, job = await STATE.work_q.get()
        STATE.conversion_in_flight = True
        try:
            await job()
        except Exception as e:  # noqa: BLE001 — a failed job must not kill the consumer
            flash(f"{label}: {e}", 8.0)
        finally:
            STATE.conversion_in_flight = False
            STATE.queued_cmds.discard(key)
            STATE.work_q.task_done()
            app.invalidate()


# ── d357 meeting recording (user request 2026-08-02: "hitting 'enter' on a
# meeting will also kick off a d357 recording session ... opt enter, or enter
# on another meeting, means the original one is over: close the meeting,
# finalize the notes, and record the points") ───────────────────────────────

D357_QUICK = str(Path.home() / "i446-monorepo/tools/meet/d357_quick.py")
D357_STATE = Path.home() / ".local/state/jm/d357-state.json"


def _load_recording_state() -> None:
    """Boot restore: if a janus-started d357 recording is still live (pid
    alive, started today), re-adopt it so a janus restart doesn't orphan the
    🎙 indicator and the finalize-on-next-meeting flow."""
    try:
        st = json.loads(D357_STATE.read_text())
        pid = st.get("pid")
        if not pid:
            return
        os.kill(int(pid), 0)
        started = dt.datetime.fromisoformat(st["started"])
        if started.date() != dt.date.today():
            return
        STATE.recording = {"desc": st.get("name") or "meeting",
                           "start_dt": started.astimezone(TZ) if started.tzinfo
                           else started.replace(tzinfo=TZ)}
    except Exception:
        pass


def _rec_active_for(desc: str | None) -> bool:
    return bool(STATE.recording and desc
                and desc.strip().lower() == STATE.recording["desc"].strip().lower())


# ── d357 "already filed" indicator (user request 2026-08-02: an emoji for
# Toggl entries that already have a filed d357 doc, distinct from 🎙's
# "recording right now") ────────────────────────────────────────────────────
# Real filed docs live in week subfolders (vault/d357/<M.W>/YYYY.MM.DD-<slug>.md),
# not flat under vault/d357/ — build-order-daemon.py's own D357_DIR.glob is
# flat and (confirmed 2026-08-02) matches zero real files there; this globs
# recursively instead. Time-gated (not mtime-gated): filing into an EXISTING
# week subfolder doesn't bump vault/d357/'s own mtime, so an mtime cache would
# never invalidate in the common case. A doc lands on disk via a manual/Claude
# Code-driven flow well after the janus recording session ends, so this can't
# just read STATE.recording — it has to check the filesystem.
D357_ROOT = Path.home() / "vault/d357"
_D357_DOC_CACHE_TTL = 20.0  # seconds
_d357_doc_cache: dict = {"checked": 0.0, "date": None, "tokens": []}


def _slug_tokens(stem: str) -> list[str]:
    """Meaningful tokens from a d357 file stem, minus its YYYY.MM.DD- prefix.
    Mirrors build-order-daemon.py's _slug_tokens exactly (same matching
    semantics on both ends of the d357 pipeline)."""
    name = re.sub(r'^\d{4}\.\d{2}\.\d{2}-', '', stem)
    return [t.lower() for t in name.split('-') if len(t) >= 3 and not t.isdigit()]


def _load_d357_tokens_for_today() -> list[list[str]]:
    today = dt.date.today()
    now = time.monotonic()
    if (_d357_doc_cache["date"] != today
            or now - _d357_doc_cache["checked"] > _D357_DOC_CACHE_TTL):
        prefix = today.strftime("%Y.%m.%d")
        toks = []
        try:
            for path in D357_ROOT.glob(f"**/{prefix}-*.md"):
                toks.append(_slug_tokens(path.stem))
        except OSError:
            pass
        _d357_doc_cache["tokens"] = toks
        _d357_doc_cache["date"] = today
        _d357_doc_cache["checked"] = now
    return _d357_doc_cache["tokens"]


def _has_d357_doc(raw_desc: str | None) -> bool:
    """True if a d357 doc has been filed today whose slug tokens
    substring-match raw_desc — same title-token-in-description heuristic as
    build-order-daemon.py's _try_name_fallback."""
    if not raw_desc:
        return False
    desc = raw_desc.strip().lower()
    return any(any(t in desc for t in toks) for toks in _load_d357_tokens_for_today())


def _spawn_d357_stop() -> None:
    """Fire-and-forget stop: d357_quick sends one C-c and then babysits the
    (up to 5 min) Whisper + filing wait in its own detached process — the
    serial work queue must never block on transcription."""
    try:
        subprocess.Popen(["python3", D357_QUICK, "stop"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:  # noqa: BLE001
        flash(f"d357 stop failed to launch: {e}", 8.0)


def _finalize_recording_cmd(now: dt.datetime) -> str | None:
    """The did-fast command that closes the recorded meeting: completed range
    from the meeting's start to now, project from its running entry. The MECE
    trim inside did-fast carves/stops the still-running timer itself."""
    rec = STATE.recording
    if not rec:
        return None
    desc = rec["desc"]
    start = rec["start_dt"]
    ent = next((e for e in STATE.entries
                if e.get("running") and (e.get("desc") or "").strip().lower() == desc.strip().lower()),
               None)
    if ent:
        start = ent["start_dt"]
    code = proj_code(ent.get("project_id")) if ent else ""
    cmd = f"{desc} {start:%H%M}-{now:%H%M}"
    if code:
        cmd += f" @{code}"
    return cmd


async def _finalize_recording(app) -> None:
    """Stop the d357 session (detached) and grant the meeting its points via
    did-fast. Runs INSIDE a queued job — callers must already hold the queue."""
    rec = STATE.recording
    if not rec:
        return
    now = dt.datetime.now(TZ)
    did_cmd = _finalize_recording_cmd(now)
    STATE.recording = None
    _spawn_d357_stop()
    flash(f"⏹ {rec['desc']} — finalizing notes, recording points…", 5.0)
    app.invalidate()
    if did_cmd:
        res = await asyncio.to_thread(run_did_fast, did_cmd)
        flash(res, 8.0)
        await asyncio.to_thread(fetch_today, True)
        await asyncio.to_thread(fetch_points)
    app.invalidate()


def _convert_selected_event(ev: dict, app) -> None:
    """Convert a selected calendar event into Toggl — an ENDED event runs
    did-fast (completed entry + its points in one shot, carving any runaway
    covering entry via the MECE trim); a not-yet-ended one starts a tg-fast
    timer backdated to the event's start AND kicks off a d357 recording
    session (user request 2026-08-02), finalizing any previous one first.
    Shared by plain Enter and ⌥↵ (user request 2026-07-29)."""
    now = view_now()
    is_past = ev["end_dt"] <= now
    cmd = _event_to_did_command(ev) if is_past else _event_to_tg_command(ev, now)
    if is_past and STATE.day_offset != 0:
        # Viewing a past day: did-fast's trailing M/D token targets the
        # entry + points at the event's own day, not today.
        cmd += f" {ev['start_dt'].month}/{ev['start_dt'].day}"
    STATE.event_sel = None
    label = f"{'did' if is_past else 'tg'} {cmd}"
    title = _safe_event_title(ev.get("title"))
    ev_minutes = max(0, int((ev["end_dt"] - dt.datetime.now(TZ)).total_seconds() // 60))

    async def _run_event_and_refresh():
        flash(f"$ {label}")
        app.invalidate()
        if not is_past:
            # Enter on a NEW meeting while one is recording = the old one is
            # over: finalize it (notes + points) before starting the new one.
            await _finalize_recording(app)
        runner = run_did_fast if is_past else run_tg_fast
        res = await asyncio.to_thread(runner, cmd)
        flash(res, 6.0)
        app.invalidate()
        if is_past:
            # did-fast's own Excel write + Toggl create has already
            # landed by the time the subprocess returns — no need for
            # tg-fast's tight (0.4, 0.8, 1.5) poll against Toggl's
            # propagation lag, just one forced re-read of both.
            await asyncio.to_thread(fetch_today, True)
            await asyncio.to_thread(fetch_points)
            app.invalidate()
        else:
            # Kick off the recording; the wrapper reports its audio verdict.
            args = ["python3", D357_QUICK, "start", title]
            if ev_minutes:
                args += ["--minutes", str(ev_minutes + 5)]
            r = await asyncio.to_thread(
                subprocess.run, args, capture_output=True, text=True, timeout=60)
            out = (r.stdout or "").strip().splitlines()
            line = out[-1] if out else ""
            if line.startswith("REC|"):
                STATE.recording = {"desc": title, "start_dt": dt.datetime.now(TZ)}
                flash(f"🎙 recording: {title} — {line.split('|', 2)[2]}", 8.0)
            else:
                flash(f"⚠ d357 did NOT start: {line or 'no output'}", 10.0)
            app.invalidate()
            polls = (0.4, 0.8, 1.5)
            for i, delay in enumerate(polls):
                await asyncio.sleep(delay)
                await asyncio.to_thread(fetch_current)
                if i == len(polls) - 1:
                    await asyncio.to_thread(fetch_today, True)
                app.invalidate()

    _enqueue_work(app, label, _run_event_and_refresh, key=cmd)


@kb.add("enter")
def _(event):
    if STATE.split_target is not None:
        # Same chokepoint discipline as edit_target below: consumed first,
        # empty submission = cancel. The input line holds the HHMM split
        # point (^P prefills the midpoint — editing digits is the whole UX).
        sp = STATE.split_target
        STATE.split_target = None
        text = input_buffer.text.strip()
        input_buffer.reset()
        m = re.fullmatch(r"(\d{2})(\d{2})", text) if text else None
        if not m:
            flash("split cancelled" if not text else f"not an HHMM time: {text}", 4.0)
            return
        cut = _hhmm_to_dt(sp["date"], text)
        if not (sp["start_dt"] < cut < sp["end_dt"]):
            flash(f"split point must be inside {sp['start_dt']:%H%M}-{sp['end_dt']:%H%M}", 5.0)
            return
        flash(f"$ split {sp['desc']} at {cut:%H:%M}")

        async def _split_and_refresh():
            try:
                # First half: shorten the original in place (id, tags, project
                # untouched). Second half: a fresh identical entry from the
                # cut to the original end.
                await asyncio.to_thread(
                    toggl_api.update_entry, sp["id"],
                    stop=cut.isoformat(),
                    duration=int((cut - sp["start_dt"]).total_seconds()))
                await asyncio.to_thread(
                    toggl_api.create_entry, sp["desc"], cut.isoformat(),
                    sp["end_dt"].isoformat(),
                    int((sp["end_dt"] - cut).total_seconds()),
                    sp["project_id"], sp["tags"] or None)
                flash(f"split: {sp['desc']} → {sp['start_dt']:%H:%M}-{cut:%H:%M} + "
                      f"{cut:%H:%M}-{sp['end_dt']:%H:%M}", 6.0)
            except Exception as e:  # noqa: BLE001 — surface, never crash the app
                flash(f"split failed: {e}", 6.0)
            event.app.invalidate()
            await asyncio.to_thread(fetch_today, True)
            event.app.invalidate()

        event.app.create_background_task(_split_and_refresh())
        return

    if STATE.edit_target is not None:
        # Consumed FIRST, unconditionally, before anything else about this
        # keypress is inspected — the chokepoint that stops a stale edit
        # target from ever leaking into an unrelated later Enter (e.g. the
        # user armed an edit, pressed Escape or a day-nav key instead of
        # submitting... those already clear edit_target, but if they didn't
        # this is the last line of defense: an empty submission here always
        # reads as "cancel", never "fall through to the event-conversion
        # branch below").
        target = STATE.edit_target
        STATE.edit_target = None
        ids, edit_date = target["ids"], target["date"]
        text = input_buffer.text.strip()
        input_buffer.reset()
        if not text:
            flash("edit cancelled")
            return
        desc, code, time_range, tags = _parse_edit_text(text)
        pid = PROJECT_MAP.get(code) if code else None
        if code and pid is None:
            flash(f"unknown project code: {code}", 4.0)
            return
        if time_range and len(ids) != 1:
            # A merged row (contiguous same-desc entries collapsed into one
            # display line) has no single well-defined new time to retime
            # ALL of them to — description/project edits are safe to apply
            # to every id behind it, but time is not.
            flash("can't retime a merged multi-entry row", 4.0)
            return
        fields = {}
        if desc:
            fields["description"] = desc
        if pid is not None:
            fields["project_id"] = pid
        new_value_tags: list[str] = []
        if tags:
            # Merge with the entry's existing tags (update_entry REPLACES).
            ent = _find_entry(ids)
            existing = list((ent or {}).get("tags") or [])
            fields["tags"] = sorted(set(existing) | set(tags))
            new_value_tags = [t for t in tags
                              if t in VALUE_TAGS and t not in existing]
        if time_range:
            start_dt = _hhmm_to_dt(edit_date, time_range[0])
            end_dt = _hhmm_to_dt(edit_date, time_range[1])
            if end_dt <= start_dt:
                end_dt += dt.timedelta(days=1)
            fields["start"] = start_dt.isoformat()
            fields["stop"] = end_dt.isoformat()
            fields["duration"] = int((end_dt - start_dt).total_seconds())
        if not fields:
            flash("nothing to update", 4.0)
            return
        parts = [desc or "(desc unchanged)"]
        if code:
            parts.append(f"@{code}")
        if time_range:
            parts.append(f"{time_range[0]}-{time_range[1]}")
        parts += [f"#{t}" for t in tags]
        flash("$ edit " + " ".join(parts))

        async def _apply_edit_and_refresh():
            try:
                if time_range:
                    # MECE: a retimed entry must not leave a stale overlap
                    # behind on some OTHER entry (user request 2026-07-19 —
                    # "shorten it to make room, or delete the old one if
                    # full overlap"). Exclude the entry(ies) being edited
                    # themselves — their own current position shouldn't
                    # trim itself out from under its own edit.
                    await asyncio.to_thread(toggl_api.trim_range, start_dt, end_dt, set(ids))
                for eid in ids:
                    await asyncio.to_thread(toggl_api.update_entry, eid, **fields)
                # Value-tag 媒分 credit — completed entry credits at once
                # (minutes known); a running one queues until it stops
                # (fetch_today resolves). Journaled so re-edits can't
                # double-credit.
                if new_value_tags:
                    st = _tag_credit_load()
                    ent = _find_entry(ids)
                    for tag in new_value_tags:
                        key = f"{ids[0]}:{tag}"
                        if (key in st["credited"]
                                or any(p.get("key") == key for p in st["pending"])):
                            continue
                        if ent and not ent.get("running"):
                            mins = int((ent["end_dt"] - ent["start_dt"])
                                       .total_seconds() // 60)
                            credited = await asyncio.to_thread(
                                _apply_tag_credit, tag, mins,
                                ent["start_dt"].date())
                            if credited:
                                st["credited"].append(key)
                                flash(f"#{tag} +{credited}m → 0n", 5.0)
                        else:
                            st["pending"].append(
                                {"key": key, "id": ids[0], "tag": tag})
                            flash(f"#{tag} queued — minutes credit when "
                                  "the timer stops", 5.0)
                    _tag_credit_save(st)
                else:
                    flash("updated", 4.0)
            except Exception as e:  # noqa: BLE001 — a stale/deleted id (e.g. trimmed
                # by did-fast's overlap handling since this row rendered) must flash,
                # not crash the app
                flash(f"edit failed: {e}", 6.0)
            event.app.invalidate()
            await asyncio.to_thread(fetch_current)
            await asyncio.to_thread(fetch_today, True)
            event.app.invalidate()

        event.app.create_background_task(_apply_edit_and_refresh())
        return

    text = input_buffer.text.strip()
    input_buffer.reset()
    if not text:
        # No typed command — if something is armed (Tab/arrow-selected
        # anywhere today: a past block's uncovered meeting, a real tracked
        # entry, an untracked gap, or the current/next block), Enter acts on
        # IT. Resolved synchronously (this handler runs to completion on the
        # event loop before the next 0.1s repaint can touch STATE), so
        # there's no window where visible_events/event_sel could change out
        # from under the lookup.
        sel = STATE.event_sel
        item = next((it for it in STATE.visible_events if _sel_key(it) == sel), None) if sel else None
        if not item:
            return
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind == "entry":
            # Arm the edit target and hand the user editable text instead of
            # acting immediately — "loads the description into the input
            # line so you can retype it and re-submit" (user request
            # 2026-07-17). The NEXT Enter (top of this handler) applies it.
            STATE.event_sel = None
            STATE.edit_target = {"ids": item["entry_ids"], "date": item["start_dt"].date()}
            input_buffer.text = _entry_edit_prefill(item)
            input_buffer.cursor_position = len(input_buffer.text)
            return
        if kind == "empty":
            # Prefill a ready-made time-range and let the ORDINARY typed-
            # command path create it on the next Enter — no new backend.
            STATE.event_sel = None
            input_buffer.text = _empty_gap_prefill(item)
            input_buffer.cursor_position = len(input_buffer.text)
            return
        # Anything else is a raw gcal event dict (kind absent) — the
        # convert-to-Toggl-entry path, shared with ⌥↵ (2026-07-29).
        _convert_selected_event(item, event.app)
        return
    if _boot_grace_active():
        flash(f"ignored startup input: {text[:30]}", 4.0)
        return

    # Comma splits multiple /tg calls (2026-08-05 user request) — mirrors
    # /did's own comma/semicolon split convention. Each part gets the SAME
    # day-offset resolution (viewed-day --date append / live-command
    # rejection) a lone command would; a rejected part is flashed and
    # dropped, it doesn't abort the rest. Parts run SEQUENTIALLY (one
    # tg-fast.py call at a time, not concurrently) — the same serial-not-
    # parallel choice made everywhere else timer commands batch, so this
    # doesn't reintroduce the class of race this session already spent real
    # effort closing elsewhere (d357/dtd/-1n ritual stamps): concurrent
    # Toggl calls racing to trim/split each other's entries.
    parts = [p.strip() for p in text.split(",") if p.strip()] or [text]

    def _resolve_part(part: str) -> str | None:
        if STATE.day_offset != 0:
            # Viewing another day: typed commands apply to THAT day (user
            # request 2026-07-27 — "tg calls go to yesterday if I'm viewing
            # yesterday"). Only completed HHMM-HHMM ranges can land on a
            # past day; live-timer actions (stop/current/del) still act on
            # now, and anything else would silently start a timer TODAY, so
            # warn instead of running.
            viewed = view_now().date()
            low = part.lower()
            live_ok = low in ("stop", "today", "current") or low.startswith(("del ", "--resolve "))
            has_range = re.search(r"(?:^|\s)\d{1,4}(?::\d{2})?\s*-\s*\d{1,4}(?::\d{2})?(?=\s|$)",
                                  re.sub(r"\s@\S+\s*$", "", part))
            if has_range:
                part = f"{part} --date {viewed.isoformat()}"
            elif not live_ok:
                flash(f"viewing {viewed:%-m/%-d} — use '<desc> HHMM-HHMM' to log that day "
                      "(start/stop act on today)", 6.0)
                return None
        return part

    resolved = [r for r in (_resolve_part(p) for p in parts) if r is not None]
    if not resolved:
        return
    flash(f"$ tg {' | '.join(resolved)}" if len(resolved) > 1 else f"$ tg {resolved[0]}")

    async def _run_and_refresh():
        results = []
        for cmd in resolved:
            results.append(await asyncio.to_thread(run_tg_fast, cmd))
            event.app.invalidate()
        flash(" | ".join(results) if len(results) > 1 else results[0], 6.0)
        event.app.invalidate()
        # Toggl /current has propagation lag; poll it a few times. The entries
        # list only needs ONE (forced) read — fetch it on the last poll so a
        # rapid run of commands doesn't trip the rate limit with 3× get_entries.
        polls = (0.4, 0.8, 1.5)
        for i, delay in enumerate(polls):
            await asyncio.sleep(delay)
            await asyncio.to_thread(fetch_current)
            if i == len(polls) - 1:
                await asyncio.to_thread(fetch_today, True)
            event.app.invalidate()

    event.app.create_background_task(_run_and_refresh())


_DF_TAGS_MOD = None  # lazy did-fast module, habit-name lookups only


def _habit_tags(tags: list) -> list:
    """Subset of a Toggl entry's tags that name known habits (0n/1n+ headers
    or aliases). Mirrors tools/janus/mobile.py habit_tags — meta tags (-3, 2,
    project codes…) resolve to nothing. Best-effort: failures return []."""
    global _DF_TAGS_MOD
    if not tags:
        return []
    try:
        if _DF_TAGS_MOD is None:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("df_tags", DID_FAST)
            _mod = _ilu.module_from_spec(_spec)
            sys.modules["df_tags"] = _mod
            _spec.loader.exec_module(_mod)
            _DF_TAGS_MOD = _mod
        df = _DF_TAGS_MOD
        h = df.load_headers()
        known = {df.header_normalize(k)
                 for k in list(h.get("0n", {})) + list(h.get("1n", {}))}
        known |= {df.header_normalize(a) for a in df.ONENEON_ALIASES}
        return [t for t in tags if df.header_normalize(str(t)) in known]
    except Exception:
        return []


@kb.add("escape", "enter")  # opt/alt+enter
def _(event):
    """opt+enter on a selected TRACKED entry: run /did for it — grant its
    points and close the matching Todoist task (plain Enter on the same row
    arms an edit instead). Pre-checked against completed-today.json: if the
    points were already recorded today, say so and DON'T re-run — did-fast
    has no double-append guard for 0分, so a second run would double-award
    (user request 2026-07-27). On a past-day view there's no completion
    record to check, so it runs unconditionally (that's the backfill case)."""
    sel = STATE.event_sel
    item = next((it for it in STATE.visible_events if _sel_key(it) == sel), None) if sel else None
    if STATE.recording is not None:
        # ⌥↵ while a d357 recording is live = "the meeting is over" (user
        # request 2026-08-02): stop the recording, finalize the notes, and
        # record the meeting's points. If the selection is the recorded
        # meeting itself (its entry or its event) — or nothing is selected —
        # that IS the whole gesture; otherwise fall through and also handle
        # the selected item normally (both run serially on the work queue).
        rec_desc = STATE.recording["desc"].strip().lower()
        sel_desc = ""
        if isinstance(item, dict):
            sel_desc = (item.get("raw_desc") or item.get("title") or "").strip().lower()
        _enqueue_work(event.app, f"finalize {STATE.recording['desc']}",
                      lambda: _finalize_recording(event.app),
                      key=f"finalize:{rec_desc}")
        if item is None or sel_desc == rec_desc:
            return
    if isinstance(item, dict) and item.get("kind") is None and item.get("end_dt"):
        # A raw calendar event: ⌥↵ = "did this meeting" — same conversion
        # as plain Enter (an ended event did-fasts: Toggl entry + points in
        # one shot; a live one starts a backdated timer). One gesture for
        # "give me credit" whether the row is an entry or a meeting
        # (user request 2026-07-29).
        _convert_selected_event(item, event.app)
        return
    if not (isinstance(item, dict) and item.get("kind") == "entry"):
        flash("opt+enter: select a tracked entry first", 3.0)
        return
    if item.get("running"):
        flash("timer still running — stop it (^S) before granting points", 4.0)
        return
    if not item.get("dur_min"):
        flash("no duration on this row", 3.0)
        return
    desc = item["raw_desc"]
    if STATE.day_offset == 0:
        recorded, pts = _points_recorded_today(desc)
        if recorded:
            flash(f"already recorded today: {desc} ({pts}分) — not re-running", 6.0)
            return
    start = item["start_dt"]
    end = start + dt.timedelta(minutes=item["dur_min"])
    code = proj_code(item.get("project_id"))
    date_sfx = f" {start.month}/{start.day}" if STATE.day_offset != 0 else ""
    cmd = f"{desc} {start:%H%M}-{end:%H%M}"
    if code:
        cmd += f" @{code}"
    cmd += date_sfx
    # Habit TAGS on the Toggl entry (其他人, 冥想, …) ride along as extra
    # comma-separated /did items so both ledgers get the minutes (user
    # request 2026-07-27: a run tagged 其他人 credits hcbp points AND 其他人
    # minutes). Meta tags that aren't habit names are filtered out.
    entry_tags: list = []
    for _e in STATE.entries:
        if _e.get("id") in (item.get("entry_ids") or []):
            entry_tags = _e.get("tags") or []
            break
    for _t in _habit_tags(entry_tags):
        cmd += f", {_t} {item['dur_min']}{date_sfx}"
    STATE.event_sel = None

    async def _run_did_and_refresh():
        flash(f"$ did {cmd}")
        event.app.invalidate()
        res = await asyncio.to_thread(run_did_fast, cmd)
        flash(res, 8.0)
        event.app.invalidate()
        await asyncio.to_thread(fetch_today, True)
        await asyncio.to_thread(fetch_points)
        event.app.invalidate()

    _enqueue_work(event.app, f"did {cmd}", _run_did_and_refresh, key=cmd)


@kb.add("c-p")
def _(event):
    """Split the selected tracked entry in two at a chosen point — ^P to
    match dtd's split binding (user request 2026-07-28). Arms split_target
    and prefills the MIDPOINT HHMM in the input line; edit the digits and
    Enter cuts (first half keeps the entry id; second half is a fresh
    identical entry). v1 scope: single completed entries — a merged row has
    no single well-defined timeline and a running one has no end yet."""
    sel = STATE.event_sel
    item = next((it for it in STATE.visible_events if _sel_key(it) == sel), None) if sel else None
    if not (isinstance(item, dict) and item.get("kind") == "entry"):
        flash("^P split: select a tracked entry first", 3.0)
        return
    if item.get("running"):
        flash("can't split a running timer — stop it first", 4.0)
        return
    if len(item.get("entry_ids") or []) != 1 or not item.get("dur_min"):
        flash("can't split a merged multi-entry row", 4.0)
        return
    if item["dur_min"] < 2:
        flash("nothing to split — entry is under 2 minutes", 4.0)
        return
    ent = _find_entry(item["entry_ids"])
    start = item["start_dt"]
    end = start + dt.timedelta(minutes=item["dur_min"])
    mid = start + (end - start) / 2
    STATE.event_sel = None
    STATE.split_target = {
        "id": item["entry_ids"][0], "date": start.date(),
        "desc": item["raw_desc"], "project_id": item.get("project_id"),
        "tags": list((ent or {}).get("tags") or []),
        "start_dt": start, "end_dt": end,
    }
    input_buffer.text = f"{mid:%H%M}"
    input_buffer.cursor_position = len(input_buffer.text)
    flash(f"split {item['raw_desc']} ({start:%H%M}-{end:%H%M}) — edit the "
          "cut point, ⏎ to split", 8.0)


@kb.add("c-x")
def _(event):
    """^X on a selected calendar event: delete it — ctrl+x to match dtd's
    delete binding (user request 2026-07-30). Google-hosted events (m5x2 /
    m5c7 calendars) are deleted for real via the Calendar API; Outlook rows
    (Agency fetch or the read-only MSFT Slow Sync import) only get a local
    hide — "won't affect the calendar for outlook, but will for m5x2". The
    hide is written first either way, so the row vanishes immediately and a
    mirrored copy on the other source can't resurface it."""
    sel = STATE.event_sel
    item = next((it for it in STATE.visible_events if _sel_key(it) == sel), None) if sel else None
    if not (isinstance(item, dict) and item.get("kind") is None and item.get("end_dt")):
        flash("^X delete: select a calendar event first", 3.0)
        return
    ev = item
    title = event_title(ev)
    _hide_event(ev)
    STATE.events = [e for e in STATE.events
                    if _hidden_event_key(e) != _hidden_event_key(ev)]
    STATE.event_sel = None
    if _event_gcal_deletable(ev):
        flash(f"deleting {title}…", 3.0)

        async def _delete_and_refresh():
            try:
                await asyncio.to_thread(gcal_client.delete_event,
                                        ev["calendar_id"], ev["id"])
                flash(f"deleted from {ev.get('calendar')}: {title}", 6.0)
                await asyncio.to_thread(fetch_gcal, True)
            except Exception as e:
                flash(f"calendar delete failed ({e}) — hidden locally", 8.0)
            event.app.invalidate()

        event.app.create_background_task(_delete_and_refresh())
    else:
        flash(f"hidden in janus (Outlook calendar untouched): {title}", 6.0)
    event.app.invalidate()


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
        polls = (0.4, 0.8, 1.5)
        for i, delay in enumerate(polls):
            await asyncio.sleep(delay)
            await asyncio.to_thread(fetch_current)
            if i == len(polls) - 1:
                await asyncio.to_thread(fetch_today, True)
            event.app.invalidate()

    event.app.create_background_task(_stop())


@kb.add("c-r")
def _(event):
    flash("refreshing…")

    async def _refresh():
        STATE.toggl_blocked_until = 0.0  # manual refresh clears any back-off
        await asyncio.to_thread(fetch_current)
        await asyncio.to_thread(fetch_today, True)
        await asyncio.to_thread(fetch_gcal, True)
        flash("refreshed")
        event.app.invalidate()
        _bg_fetch(event.app, fetch_points)  # slowest; repaints itself when done
        _bg_fetch(event.app, fetch_habits_today)

    event.app.create_background_task(_refresh())


@kb.add("c-j")  # scroll the detail band forward (toward later blocks)
def _(event):
    STATE.scroll_min += 30


@kb.add("c-k")  # scroll back (toward earlier blocks)
def _(event):
    STATE.scroll_min -= 30


def _reload_day(app):
    """Re-fetch the viewed day's Toggl entries, calendar, and points after a day
    change, then repaint. Debounced: rapid Ctrl+←/→ scrubbing issues just ONE
    fetch once the keys settle (~0.35s), so holding the key doesn't hammer Toggl.
    The fetch is forced (the day changed, so its data must load)."""
    STATE.day_reload_token += 1
    token = STATE.day_reload_token

    async def _r():
        await asyncio.sleep(0.35)
        if STATE.day_reload_token != token:
            return  # a newer day-nav superseded this one
        await asyncio.to_thread(fetch_today, True)
        await asyncio.to_thread(fetch_gcal, True)
        app.invalidate()
        _bg_fetch(app, fetch_points)  # slowest; repaints itself when done
        _bg_fetch(app, fetch_habits_today)
    app.create_background_task(_r())


# -/= also scrub days: Ctrl+-/Ctrl+= carry no control-char encoding in most
# terminals — they transmit the PLAIN character — so "ctrl+= to go forward"
# silently did nothing (bug 2026-07-05). Gated on an empty command line so
# typing time ranges (`05:00-05:23`) or descriptions is never intercepted.
_input_empty = Condition(lambda: not input_buffer.text)

# cmux/Ghostty actually encode Ctrl+= and Ctrl+- as CSI-u ("fixterms")
# sequences — ESC[61;5u / ESC[45;5u, verified with cat -v on 2026-07-24 —
# which prompt_toolkit doesn't parse, so a real Ctrl press never reached the
# plain-character bindings above (bug: "ctrl+= doesn't go forward one day").
# Alias the two sequences onto spare function keys and bind those too.
ANSI_SEQUENCES["\x1b[61;5u"] = Keys.F23  # Ctrl+=
ANSI_SEQUENCES["\x1b[45;5u"] = Keys.F24  # Ctrl+-


# Bare "-"/"=" are NOT bound: even gated on an empty command line they
# swallowed the FIRST keystroke of any "-"-leading description — typing
# "-1l" or "#-2" into an idle janus navigated a day back instead (user
# report 2026-07-29). Day nav keeps Ctrl+←/→, [ / ], and real Ctrl+-/=
# (the CSI-u aliases below); plain characters always type.
@kb.add("c-left")   # view the previous day (to fill in missed time entries)
@kb.add("[")        # alias: macOS grabs Ctrl+←/→ for Mission Control spaces
@kb.add("f24")      # Ctrl+- via CSI-u (see ANSI_SEQUENCES alias above)
def _day_back(event):
    STATE.day_offset -= 1
    STATE.scroll_min = 0
    STATE.event_sel = None  # a different day has different (or no) events
    if STATE.edit_target is not None or STATE.split_target is not None:
        # A different day's rows are about to render — an armed edit/split +
        # prefilled input text from THIS day would otherwise survive the
        # nav and silently update the wrong entries on the next Enter.
        STATE.edit_target = None
        STATE.split_target = None
        input_buffer.reset()
    flash(f"◀ {view_now():%a %-m/%-d}")
    _reload_day(event.app)


@kb.add("c-right")  # view the next day, capped at today
@kb.add("]")        # alias: macOS grabs Ctrl+←/→ for Mission Control spaces
@kb.add("f23")      # Ctrl+= via CSI-u (see ANSI_SEQUENCES alias above)
def _day_forward(event):
    if STATE.day_offset >= 0:
        flash("already on today")
        return
    STATE.day_offset += 1
    STATE.scroll_min = 0
    STATE.event_sel = None
    if STATE.edit_target is not None or STATE.split_target is not None:
        STATE.edit_target = None
        STATE.split_target = None
        input_buffer.reset()
    flash("today" if STATE.day_offset == 0 else f"◀ {view_now():%a %-m/%-d}")
    _reload_day(event.app)


@kb.add("tab", filter=_input_empty)
@kb.add("down", filter=_input_empty)  # arrow-key alias (user request 2026-07-17)
def _(event):
    """Cycle the selection cursor forward through everything currently on
    screen and selectable — calendar events, real tracked entries, and
    untracked gaps alike (user request 2026-07-17: "select toggl time
    entries as well" / "select empty components"). Tab/↓ to arm one, Enter
    to act on it (see the enter handler above).

    A FRESH selection starts at the most recent item (latest start <= now),
    not the day's first: starting at index 0 put the cursor on a 6 AM entry
    at the far top of the screen — ~20 presses from tonight's entries, which
    read as "can't select them at all" (user report 2026-07-27: "two
    meaningful entries in this block and last, but I can't select either")."""
    items = STATE.visible_events
    if not items:
        return
    keys = [_sel_key(it) for it in items]
    if STATE.event_sel in keys:
        i = (keys.index(STATE.event_sel) + 1) % len(keys)
    else:
        now = view_now()
        past = [j for j, it in enumerate(items)
                if it.get("start_dt") and it["start_dt"] <= now]
        i = past[-1] if past else 0
    STATE.event_sel = keys[i]


@kb.add("s-tab", filter=_input_empty)
@kb.add("up", filter=_input_empty)  # arrow-key alias (user request 2026-07-17)
def _(event):
    """Cycle the selection cursor backward. See the "tab" binding above —
    same nearest-to-now start for a fresh selection."""
    items = STATE.visible_events
    if not items:
        return
    keys = [_sel_key(it) for it in items]
    if STATE.event_sel in keys:
        i = (keys.index(STATE.event_sel) - 1) % len(keys)
    else:
        now = view_now()
        past = [j for j, it in enumerate(items)
                if it.get("start_dt") and it["start_dt"] <= now]
        i = past[-1] if past else len(keys) - 1
    STATE.event_sel = keys[i]


@kb.add("escape")  # snap the detail band back to now; reset to today if browsing;
                    # cancel an armed edit/selection
def _(event):
    if (STATE.edit_target is not None or STATE.split_target is not None
            or STATE.event_sel is not None):
        STATE.edit_target = None
        STATE.split_target = None
        STATE.event_sel = None
        input_buffer.reset()
        flash("cancelled")
        return
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
    # Horizontal border — used both above AND below the input (boxing it in,
    # user request 2026-07-19), mirroring dtd's --input-border and Claude's
    # boxed prompt. Separates the always-on command line from content above
    # and from the key-hint/flash line below.
    return [("class:rule", "─" * WIDTH_HINT + "\n")]


def render_input_prompt():
    # Permanent "❯ " prompt (Claude Code's own glyph — the explicit source of
    # truth for this box's look, 2026-07-19; dtd's plain "> " was only ever
    # an approximation). The input is always focused, so it reads as a live
    # command box (type a tg shortcode / "stop" and press Enter).
    return [("class:prompt", " ❯ ")]


rule_window = Window(content=FormattedTextControl(render_input_rule), height=1, wrap_lines=False)
rule_window_below = Window(content=FormattedTextControl(render_input_rule), height=1, wrap_lines=False)
input_window = Window(
    content=BufferControl(buffer=input_buffer, focusable=True),
    height=1,
)
prompt_window = Window(content=FormattedTextControl(render_input_prompt), height=1, width=Dimension.exact(3))

# Key-hint / flash line pinned BELOW the input box (Claude Code style).
footer_window = Window(content=FormattedTextControl(render_footer), height=1, wrap_lines=False)

from prompt_toolkit.layout import VSplit  # noqa: E402

input_row = VSplit([prompt_window, input_window])
root = HSplit([main_window, bottom_bar, rule_window, input_row, rule_window_below, footer_window])

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
    "no_entry_bg": "bold bg:#ff4444 #000000",
    "free": "#7cb87c",
    "gutter_busy": "#5f87af",
    "selected_bg": "bg:#3a3a3a",
    "selected_accent": "bold bg:#3a3a3a #ff2d78",
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


PID_FILE = Path.home() / ".cache" / "janus.pid"


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
        if STATE.conversion_in_flight:
            # A did-fast conversion is mid-write to the same Neon sheet this
            # ticker reads — skip this beat rather than risk reading it torn.
            continue
        _bg_fetch(app, fetch_points)
        _bg_fetch(app, fetch_habits_today)


async def _sigusr1_refresh():
    """Triggered by SIGUSR1: immediate full refresh (e.g. after /did starts a timer).

    Debounced: a morning backfill fires one SIGUSR1 per /tg, and each refresh does
    two Toggl GETs (fetch_current + forced fetch_today) — 15 rapid entries meant
    ~30 GETs on top of the 15 POSTs, which is what tripped the rate limit. Coalesce
    a burst into ONE refresh ~0.4s after the last nudge (the entries land together
    a heartbeat later instead of one-by-one). Skip entirely during a cooldown.

    Every fetch stays OFF the event loop: fetch_points is Excel-over-ssh
    (4-15s) and the Toggl reads are network calls. Running them inline froze
    repaints exactly when the idle nag was mid-flash — the screen stuck in
    the inverted frame until the refresh finished. fetch_current goes first
    with its own repaint so the nag clears the moment the timer is confirmed."""
    STATE.sigusr1_token += 1
    token = STATE.sigusr1_token
    await asyncio.sleep(0.4)  # debounce: collapse a burst of nudges into one refresh
    if token != STATE.sigusr1_token:
        return  # a newer nudge arrived; let the latest one do the work
    old_count = len(STATE.entries)
    await asyncio.to_thread(fetch_current)
    app.invalidate()
    await asyncio.to_thread(fetch_today, True)
    await asyncio.to_thread(fetch_short_names)  # /did may have rewritten the cache
    # If entry count grew (task completed → new entry, or timer stopped),
    # flash purple as a prayer/mindfulness prompt
    if len(STATE.entries) != old_count or STATE.current is None:
        flash("☀️", 6.0, style="bold fg:#aa00ff")
    app.invalidate()
    _bg_fetch(app, fetch_points)  # slowest, repaints itself when done
    _bg_fetch(app, fetch_habits_today)


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
    _bg_fetch(app, fetch_habits_today)


async def main():
    # Reset the terminal tab color on every launch, matching dtd.sh's own
    # startup reset (2026-08-04, "make dtd and janus background the same") —
    # without this, a color set by a PREVIOUS session (an "orange" tool-use
    # failure, or a Claude Code hook's blue/black) stays on the tab
    # indefinitely, so a fresh janus launch could look different from a
    # fresh dtd launch depending on what state the tab was left in.
    # Backgrounded: term-color.sh's TTY walk + AppleScript shouldn't add
    # latency to the first paint.
    try:
        subprocess.Popen(["bash", str(Path.home() / "i446-monorepo/scripts/term-color.sh"), "reset"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass

    # Fast fetches only (sub-second) — enough content for an instant first paint.
    fetch_current()
    fetch_today(True)  # forced: the startup load must not be throttled/coalesced
    fetch_short_names()  # dtd's abbreviated labels (local file read)

    # SIGUSR1 → instant refresh (sent by /did, /tg, /done after timer changes)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(_sigusr1_refresh()))

    # Write PID so other tools can signal us
    _assert_pid_file()

    # Re-adopt a live janus-started d357 recording across restarts
    _load_recording_state()

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
