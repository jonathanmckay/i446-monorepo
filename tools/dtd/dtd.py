#!/usr/bin/env python3
"""
dtd — Do The Damn thing. iPhone-first mirror of the `dtd` fzf TUI.

Same data source (task-queue.json), same domain colors, same short names,
same right-justified (time)[value]{bonus} estimates — but as a swipeable
web list. Swipe a card right → runs the real /did (did-fast.py): closes the
Todoist task AND writes its points to Neon.

Run:   python3 dtd.py            (binds 0.0.0.0:5560)
Open:  http://ix:5560            (from the phone, same as the dashboard)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------
PORT = 5560
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
TG_FAST = Path.home() / "i446-monorepo/tools/tg/tg-fast.py"
DEFER_FAST = Path.home() / "i446-monorepo/tools/did/defer-fast.py"
EDIT_FAST = Path.home() / "i446-monorepo/tools/did/edit-fast.py"
SPLIT_FAST = Path.home() / "i446-monorepo/tools/did/split-fast.py"
FORMS_PY = Path(__file__).resolve().parent / "dtd_forms.py"
FORMS_TMP = Path.home() / ".cache/dtd-forms"   # values/result/log per phone form submit
STATE_DIR = Path.home() / ".local/state/jm"
CACHE = STATE_DIR / "task-queue.json"
DONE_FILE = STATE_DIR / "completed-today.json"
SNOOZE_FILE = STATE_DIR / "dtd-block-snooze.json"
DEFERRED_DIR = Path.home() / ".cache/jm"
CACHE_MAX_AGE = 180  # seconds; refresh from Todoist if staler
SUMMARY_MAX_AGE = 45  # seconds; day-total (points + done) cache

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
import daytime  # noqa: E402  — device-priority 'today' (lib/where.py), not Ix's own clock

# Survey forms (2026-10-22): card -> form map + field specs, loaded by path
# (tests load this file by path too, so a plain import can't rely on cwd).
import importlib.util as _ilu  # noqa: E402
_fspec = _ilu.spec_from_file_location("dtd_forms", Path(__file__).resolve().parent / "dtd_forms.py")
dtd_forms = _ilu.module_from_spec(_fspec)
_fspec.loader.exec_module(dtd_forms)


def _now() -> "_dt.datetime":
    """Now in JM's zone (daytime: device priority, never backwards), via
    _dt.datetime so tests that freeze dtd._dt.datetime still control it."""
    return _dt.datetime.now(daytime.active_zone())


def _today() -> "_dt.date":
    return _now().date()


def _parse_scheduled(s: str) -> "_dt.datetime | None":
    """A Todoist timed due (`due_datetime`, RFC3339 — may be 'Z', an offset, or
    floating with no zone) as a tz-aware datetime in JM's zone, or None if it
    carries no time / can't be parsed. Floating values are read as local."""
    if not s:
        return None
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=daytime.active_zone())
    return dt.astimezone(daytime.active_zone())
TODOIST_TOKEN_FILE = Path.home() / ".config/todoist/token"

# Neon domain palette — mirrors tools/did/dtd.sh COLORS (RGB → hex).
COLORS = {
    "g245": "#00e676", "h335": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
    "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
    "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "m828": "#9b0023",
    "hcb": "#f81d78",
    "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
    "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#ff4136",
    "睡觉": "#666666",
}
DEFAULT_COLOR = "#bdbdbd"  # unlabelled / f693 / i446 → terminal default fg

# 地支 block → start hour. Shared by build_tasks() (block-label hide) and
# snooze_to_next_block() (delay-to-next-block button) — mirrors dtd.sh.
BLOCK_HOURS = {"卯": 4, "辰": 6, "巳": 8, "午": 10, "未": 12,
              "申": 14, "酉": 16, "戌": 18, "亥": 20}

# ---------------------------------------------------------------------------
# Parsing helpers (mirror dtd.sh)
# ---------------------------------------------------------------------------
_ANN = re.compile(r" *\(\d*\)| *\[\d*G?\]| *\{\d*\}")
_TIME = re.compile(r"\((\d+)\)")
_VAL = re.compile(r"\[(\d+)G?\]")
_BONUS = re.compile(r"\{(\d+)\}")

_MDLINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_TAG = re.compile(r"@(\S+)")


def strip_ann(s: str) -> str:
    # Markdown links (/todo stores URLs as "[(link)](https://…)") collapse to
    # their visible text so the card shows a 🔗 instead of the raw URL
    # (was "(link)" until 2026-10-30, shortened to match terminal dtd).
    s = _MDLINK.sub(lambda m: (m.group(1) or "(link)").replace("(link)", "🔗"), s)
    return re.sub(r"  +", " ", _ANN.sub("", s)).strip()

# Variable-input tasks (2026-08-11): terminal dtd prompts for a value on
# completion for these — the typed number gets appended to the name and
# routed to the task's own 0n column (e.g. "hiit" -> "hiit 25"). dtd web had
# NO equivalent at all: swiping any of these complete silently dropped the
# value. Mirrors did-fast.py's VARIABLE_0N (daily 0neon habits whose points
# derive from duration) plus dtd.sh's cpap/i444, which use a different KIND
# of prompt (quality 1-3 / count) so aren't in VARIABLE_0N itself. Scoped to
# DAILY habits only — the WEEKLY 1n+ variable-rate set (VARIABLE_1N: relax,
# 业写, s897, ...) isn't covered here yet, a separate case needing dynamic
# alias resolution (did-fast.py's ONENEON_ALIASES).
VARIABLE_INPUT = {
    "cpap": "CPAP quality (1-3)",
    "xk20": "xk20 minutes (Theo)",
    "xk22": "xk22 minutes (Ren)",
    "xk26": "xk26 minutes (Rori)",
    "xk88": "xk88 minutes",
    "i444": "i444 count (0 = none today)",
    "hiit": "hiit minutes",
    "新闻": "新闻 minutes",
    "night hcmc": "night hcmc minutes",
    "evening hcmc": "night hcmc minutes",
    "冥想": "冥想 minutes",
    "o314": "o314 minutes",
    "hcmr": "hcmr minutes",
    "其他人": "其他人 minutes",
}

# A deferred one-off copy of a daily habit carries a trailing " M.D" date
# suffix (e.g. "xk20 7.26", "长冥想 7.31" — see defer-fast / CLAUDE.md's
# "AoS 7.20" convention). did-fast parses that date off before matching
# (did-fast.py ~1194), so it still prompts; dtd web didn't, so a deferred
# variable habit swiped complete on the phone silently dropped its value
# (bug 2026-08-22: "tasks that should ask for numeric input ... don't").
_DEFER_DATE = re.compile(r"\s+\d{1,2}\.\d{1,2}$")

# Weekly 1n+ variable-rate habits (did-fast's VARIABLE_1N: s897, family,
# "1 kids nature", 业写, …) ALSO need a minutes prompt — their points are
# base + rate×minutes, so completing one with no minutes drops did-fast into
# its "how many minutes?" refusal and credits nothing (bug 2026-10-17:
# "1 kids nature" swiped done on the phone never asked, then errored without
# points). The terminal already prompts for these (dtd.sh:802-821); dtd web
# never did (the VARIABLE_INPUT comment above flagged it as not-yet-covered).
# Pull the name set from did-fast at import — same VARIABLE_1N + ONENEON_ALIASES
# resolution dtd.sh uses — so this can't drift from the routing source of truth.
def _load_variable_1n_names() -> set:
    try:
        import importlib.util
        did = Path(__file__).resolve().parent.parent / "did" / "did-fast.py"
        spec = importlib.util.spec_from_file_location("df_var1n", did)
        df = importlib.util.module_from_spec(spec)
        sys.modules["df_var1n"] = df  # dataclass decorator looks up cls.__module__
        spec.loader.exec_module(df)
        norm = {df.header_normalize(n) for n in df.VARIABLE_1N}
        names = {n.lower() for n in df.VARIABLE_1N}
        names |= {a.lower() for a, t in df.ONENEON_ALIASES.items()
                  if df.header_normalize(t) in norm}
        return {re.sub(r"\s*\{\d+\}", "", n).strip() for n in names}
    except Exception:
        return set()

VARIABLE_1N_NAMES = _load_variable_1n_names()

def variable_prompt(raw: str) -> str | None:
    """The prompt label if `raw` is a variable-input task, else None. A trailing
    " M.D" defer-copy date suffix is stripped first so a deferred variable habit
    (xk20 7.26 → xk20) still prompts, matching the terminal path. Covers both
    the daily 0₦ set (VARIABLE_INPUT) and the weekly 1n+ variable set pulled
    from did-fast (VARIABLE_1N_NAMES)."""
    key = _DEFER_DATE.sub("", strip_ann(raw).lower().strip()).strip()
    label = VARIABLE_INPUT.get(key)
    if label:
        return label
    if key in VARIABLE_1N_NAMES:
        return f"{key} minutes"
    return None

def variable_rate(raw: str) -> int | None:
    """分 per typed unit for a variable task Neon multiplies (hiit: 2/min via
    hcbi!Y), so the completion toast and running total show what is actually
    credited instead of the card's static [N] (user report 2026-10-04: "dtd
    web shows 1x"). None = keep showing the card's [N]."""
    from neon.rates import ZERO_N_MULTIPLIERS
    key = _DEFER_DATE.sub("", strip_ann(raw).lower().strip()).strip()
    return ZERO_N_MULTIPLIERS.get(key)

def parse_est(content: str) -> tuple[str, int]:
    """Return ('(30) [20]' canonical string, points)."""
    tm, vm, bm = _TIME.search(content), _VAL.search(content), _BONUS.search(content)
    toks = []
    if tm:
        toks.append("(%s)" % tm.group(1))
    if vm:
        toks.append("[%s]" % vm.group(1))
    if bm:
        toks.append("{%s}" % bm.group(1))
    pts = int(vm.group(1)) if vm else (int(bm.group(1)) if bm else 0)
    return " ".join(toks), pts

def ritual_points(raw: str, cfg: dict | None = None) -> int | None:
    """分 a -1neon ritual card (`😈 -1g`, `😈 s سمش`, …) earns, from
    config/block-rituals.json — the same table did-fast's run_ritual credits.
    The daemon creates these cards with no [N], so parse_est read 0 and the
    phone/web showed "0 分" on the -1 tier (2026-10-25, 6hqjQRhQGH98vw36).
    None for anything that isn't a ritual card name."""
    try:
        import neon_blocks as nb
        cfg = cfg or nb.load_config()
        tag = nb.ritual_card_tag(raw, cfg)
        if tag is None:
            return None
        return next((int(r["points"]) for r in cfg["rituals"] if r["tag"] == tag), None)
    except Exception:
        return None

def card_points(raw: str, labels: list | None = None, cfg: dict | None = None) -> tuple[str, int]:
    """parse_est, plus the ritual table for an un-annotated -1neon card: an
    explicit [N]/{N} on the card still wins, and the est string gains the
    "[N]" so the web row's meta column shows it too (2026-10-25)."""
    est, pts = parse_est(raw)
    if not _VAL.search(raw) and not _BONUS.search(raw) \
            and (labels is None or "-1neon" in labels):
        rp = ritual_points(raw, cfg)
        if rp:
            est, pts = (est + " [%d]" % rp).strip(), rp
    return est, pts

# Ritual cards (label '-1neon') carry no domain label of their own — resolve
# their color from the ritual tag in the name instead. Mirrors dtd.sh's own
# RITUAL_DOMAIN (tools/did/dtd.sh:1120,1352-1355) exactly.
RITUAL_DOMAIN = {"-1ibx": "i9", "-1g": "g245", "-1l": "g245", "-1t": "n156", "سمش": "hcm"}

def color_of(labels: list[str], content: str = "") -> tuple[str, str]:
    if "-1neon" in labels:
        bare = strip_ann(content).lower().replace("😈", "").strip()
        for tag, dom in RITUAL_DOMAIN.items():
            if bare == tag or tag in bare.split():
                return COLORS[dom], dom
    for lbl in labels:
        if lbl in COLORS:
            return COLORS[lbl], lbl
    return DEFAULT_COLOR, ""

def _prank(p) -> int:
    try:
        return -(int(p) or 1)
    except (TypeError, ValueError):
        return -1

# ---------------------------------------------------------------------------
# Cache load + section ordering (mirror dtd.sh list build)
# ---------------------------------------------------------------------------
_refresh_lock = threading.Lock()

def _run_refresh_subprocess():
    try:
        subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                       capture_output=True, text=True, timeout=25)
    except Exception as e:
        print("WARN refresh-cache:", e, file=sys.stderr)
    finally:
        _refresh_lock.release()

def _refresh_cache_if_stale(force: bool = False):
    """force=True (↻ button, post-add reload): the caller is explicitly
    waiting on fresh data, so refresh synchronously as before.

    force=False (every normal page load once the cache passes
    CACHE_MAX_AGE): NEVER block the request on this. It used to call the
    same synchronous subprocess.run here, so any request landing on a stale
    cache waited on a live Todoist refresh (with its own internal retries)
    before the page could render at all — "dtd web hangs on loading (>20s)"
    (2026-08-11). Reproduced live: consecutive calls measured 18.2s, 15.7s,
    then 66.8s — WORSE each time, because nothing stopped overlapping
    requests from each spawning their own refresh subprocess, piling
    concurrent Todoist calls into rate limiting and slowing every one of
    them down together (the plain script alone takes ~2s run directly).
    Now the stale case kicks the refresh off in a background thread (deduped
    by _refresh_lock — at most one in flight) and returns immediately; the
    request serves whatever's already cached (at most a few minutes old),
    and the next request picks up the freshened cache once it lands."""
    stale = force
    if not stale:
        try:
            d = json.loads(CACHE.read_text())
            upd = _dt.datetime.fromisoformat(d.get("updated", "1970-01-01"))
            stale = (_dt.datetime.now() - upd).total_seconds() > CACHE_MAX_AGE
        except Exception:
            stale = True
    if not stale:
        return
    if force:
        try:
            subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                           capture_output=True, text=True, timeout=25)
        except Exception as e:
            print("WARN refresh-cache:", e, file=sys.stderr)
        return
    if _refresh_lock.acquire(blocking=False):
        threading.Thread(target=_run_refresh_subprocess, daemon=True).start()
    # else: a refresh is already in flight — don't stack another, just serve
    # the current cache below.

MIRROR_DIR = Path.home() / "vault" / "z_ibx"


def _remote_completed_ids(today: str) -> set[str]:
    """Ids from OTHER hosts' synced completed-today-<host>.json mirrors, read
    live on every request (mobile web is stateless/request-driven, unlike
    dtd.sh's persistent loop — see below — so there is no watcher to keep an
    overlay warm; a direct read each time is cheap: a handful of small local
    JSON files, no network).

    Bug (2026-09-07): "-1n [rituals] shown even though already done" — done on
    another host (e.g. /inbound on Straylight), which writes ITS OWN
    ~/.local/state/jm/task-queue.json and completed-today.json, neither of
    which mobile web (served from Ix) reads. did-fast.py's own refresh
    (`_refresh_task_queue_inner`) DOES call mc.absorb_remote() and re-verifies
    every -1neon card live against Todoist — but only Ix's OWN cache refresh
    triggers that, gated behind CACHE_MAX_AGE (180s) and then run
    fire-and-forget in a background thread (see _refresh_cache_if_stale), so a
    ritual closed seconds ago on another host can read as still-open here for
    minutes. dtd.sh sidesteps this with a persistent background loop that
    polls the mirror files' mtimes and folds them in continuously
    (tools/did/dtd.sh's cross-machine completions watcher); dtd.py has no
    persistent process to run that loop in, so it does the equivalent merge
    inline, per-request, instead."""
    ids: set[str] = set()
    try:
        # Harmless to include this host's own mirror (if it writes one) — it's
        # a subset of DONE_FILE, unioned in again below, idempotent.
        for p in MIRROR_DIR.glob("completed-today-*.json"):
            try:
                remote = json.loads(p.read_text())
            except Exception:
                continue
            if not isinstance(remote, dict) or today > remote.get("date", ""):
                continue  # strictly-older remote date: stale, ignore (equal/newer merges)
            ids |= {str(v) for v in (remote.get("ids") or {}).values()}
    except Exception:
        pass
    return ids


def _completed_ids() -> set[str]:
    """Todoist ids completed today (from completed-today.json `ids` map, plus
    every other host's synced mirror — see _remote_completed_ids).

    Hiding is by id ONLY, never by name. Names are unreliable: -1neon block
    rituals (سمش / -1g / -1ibx) are deleted+recreated with identical names at
    every 2h boundary, so a stale name-only completion (e.g. a goal-set that
    recorded '😈 -1g' with no id) would wrongly suppress the new block's card.
    Genuinely-closed tasks drop out of the cache on refresh regardless; this id
    set only guards the window between a completion and the next cache refresh.
    """
    today = _today().isoformat()
    ids = _remote_completed_ids(today)
    try:
        d = json.loads(DONE_FILE.read_text())
        if d.get("date") == today:
            ids |= {str(v) for v in (d.get("ids") or {}).values()}
    except Exception:
        pass
    return ids

def _host_slug() -> str:
    """This host's slug for the snooze mirror filename — same scheme as
    mark-completed.py's completed-today mirror so the two families of
    per-host files look consistent in ~/vault/z_ibx."""
    return re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")[:24]

def _snooze_mirror_path() -> Path:
    return MIRROR_DIR / f"dtd-block-snooze-{_host_slug()}.json"

def _snoozed_from(data: dict, now: _dt.datetime) -> set[str]:
    """Still-snoozed ids from one {date, snoozes} dict. Only today's file
    counts (a stale prior-day date voids everything). A block delay is a plain
    int hour-of-day; a minute/freeform delay is an absolute epoch float —
    isinstance(v, float) tells them apart (json round-trips a float with a
    decimal point), same distinction as dtd.sh's reader. (Comparing an epoch
    float against now_hour via int(v) is always true — the 2026-09-07 bug.)"""
    if not isinstance(data, dict) or data.get("date") != now.date().isoformat():
        return set()
    out: set[str] = set()
    for k, v in (data.get("snoozes") or {}).items():
        try:
            still = (now.timestamp() < v) if isinstance(v, float) else (now.hour < int(v))
        except (TypeError, ValueError):
            still = False
        if still:
            out.add(str(k))
    return out

def _fired_from(data: dict, now: _dt.datetime) -> set[str]:
    """Ids whose delay in this {date, snoozes} dict already passed TODAY
    (the complement of _snoozed_from within today's entries)."""
    if not isinstance(data, dict) or data.get("date") != now.date().isoformat():
        return set()
    return {str(k) for k in (data.get("snoozes") or {})} - _snoozed_from(data, now)


def _snooze_files() -> list[Path]:
    files = [SNOOZE_FILE]
    try:
        files += list(MIRROR_DIR.glob("dtd-block-snooze-*.json"))
    except Exception:
        pass
    return files


def _woken_ids(still_snoozed: set[str]) -> set[str]:
    """Ids whose delay fired today on any host and that no host still holds
    (2026-10-05): build_tasks floats them to the top of their tier, mirroring
    the terminal dtd's _woke()."""
    now = _now()
    ids: set[str] = set()
    for p in _snooze_files():
        try:
            ids |= _fired_from(json.loads(p.read_text()), now)
        except Exception:
            continue
    return ids - still_snoozed


def _snoozed_ids() -> set[str]:
    """Ids block-snoozed (ctrl-v / freeform / minute delay), hidden until their
    block hour or timestamp passes — UNION of this host's local snooze file AND
    every other host's synced mirror (dtd-block-snooze-<host>.json in the vault).

    Cross-host is the whole point (2026-09-27): ~/.local/state/jm is host-local
    and NOT synced, so a delay made in the terminal dtd on Straylight never
    reached dtd web on Ix — the local read alone only ever saw Ix's own file.
    This mirrors the completed-today cross-host solution (_remote_completed_ids):
    each host writes its own mirror into ~/vault/z_ibx (Syncthing-synced), and
    every reader unions all of them. Union semantics are correct for snoozes —
    an id is hidden if ANY host says it's still snoozed; each entry self-expires
    by its own value; clearing a snooze drops it from that host's mirror."""
    now = _now()
    ids: set[str] = set()
    try:
        ids |= _snoozed_from(json.loads(SNOOZE_FILE.read_text()), now)
    except Exception:
        pass
    try:
        for p in MIRROR_DIR.glob("dtd-block-snooze-*.json"):
            try:
                ids |= _snoozed_from(json.loads(p.read_text()), now)
            except Exception:
                continue  # a mid-sync/partial mirror — skip, don't fail the request
    except Exception:
        pass
    return ids

def _deferred_habit_ids() -> set[str]:
    """Recurring 0neon/夜neon habit-parent ids deferred (/defer) today,
    hidden for the rest of today. Mirrors tools/did/dtd.sh's read of the same
    per-day marker file (same gap as _snoozed_ids above)."""
    day = _today().isoformat()
    # Local marker plus every host's synced mirror in z_ibx (2026-10-22: a
    # habit deferred in desktop dtd only marked Straylight's file, so the
    # phone kept showing it).
    paths = [DEFERRED_DIR / f"habits-deferred-{day}.ids"]
    paths += list(MIRROR_DIR.glob(f"habits-deferred-*-{day}.ids"))
    ids: set[str] = set()
    for p in paths:
        try:
            ids |= {l.strip() for l in p.read_text().splitlines() if l.strip()}
        except OSError:
            pass
    return ids


def _patch_cache_due(task_id: str, target: str) -> None:
    """After a successful defer, move the task's due in the local cache to
    `target` so the next /api/tasks hides it at once. Without this a reload
    inside the cache window (≤180 s, or longer if a background refresh is
    slow) served the pre-defer due and the delayed task popped back onto the
    phone (bug 2026-10-22, "still seeing delayed tasks"). The next real
    refresh overwrites this with Todoist's own value, which agrees."""
    try:
        if not target or target <= _today().isoformat():
            return
        d = json.loads(CACHE.read_text())
        hit = False
        for k, v in d.items():
            if isinstance(v, list):
                for t in v:
                    if isinstance(t, dict) and str(t.get("id")) == str(task_id):
                        t["due"] = target; t["due_datetime"] = ""; hit = True
        if hit:
            tmp = CACHE.with_suffix(".patch.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False))
            tmp.replace(CACHE)
    except Exception as e:
        print("WARN patch cache:", e, file=sys.stderr)

def build_tasks(force_refresh: bool = False) -> list[dict]:
    _refresh_cache_if_stale(force=force_refresh)
    try:
        d = json.loads(CACHE.read_text())
    except Exception as e:
        print("WARN read cache:", e, file=sys.stderr)
        return []

    today = _today().isoformat()

    def sec(key, bound):
        return [t for t in d.get(key, []) if isinstance(t, dict)
                and t.get("due") and t["due"] <= bound]

    today_tasks = [t for t in d.get("today", []) if isinstance(t, dict)
                   and t.get("due") and t["due"] <= today]
    has = lambda t, lab: lab in t.get("labels", [])

    rituals = [t for t in today_tasks if has(t, "-1neon")]
    neg1g = [t for t in today_tasks if has(t, "#-1g") and not has(t, "-1neon")]
    _deferred_ids = _deferred_habit_ids()
    # Match the CLI's date cutoff, including deferred one-off habit copies.
    zeroneon = [t for t in sec("0neon", today) + sec("夜neon", today)
                if t.get("id") not in _deferred_ids]
    oneneon = sec("1neon", today)
    zerog = [t for t in today_tasks
             if has(t, "#0g") and not has(t, "-1neon") and not has(t, "#-1g")]
    critical = sec("关键路径", today)
    placed = lambda t: has(t, "-1neon") or has(t, "#-1g") or has(t, "#0g")
    rest = sorted([t for t in today_tasks if not placed(t)],
                  key=lambda t: _prank(t.get("priority")))
    completed_ids = _completed_ids()
    form_hidden = _form_hidden_ids(d.get("updated", ""))
    snoozed_ids = _snoozed_ids()
    try:  # ritual 分 table, read once per list (card_points)
        import neon_blocks as _nb
        _ritual_cfg = _nb.load_config()
    except Exception:
        _ritual_cfg = None
    # Block labels (地支 glyph from /todo, 2026-07-27): hidden until that
    # block's hour arrives — mirrors the desktop dtd list generator.
    block_hours = BLOCK_HOURS
    now_hour = _now().hour
    # Woken (2026-10-05): a delay or block label that passed today floats the
    # task to the top of its tier (stable), same as terminal dtd's _float().
    woken = _woken_ids(snoozed_ids)
    # Agent claims (/claim, 2026-10-05): ids a Claude session is mid-turn on,
    # drawn grey with 😈 (same store as terminal dtd; see did/agent_claims.py).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "did"))
        import agent_claims
        _claims = agent_claims.load()
        agent_ids = {i for i, r in _claims.items() if r["working"]}
        # agent thinks it's finished (2026-10-22) → ✅ on the left
        agent_done = {i for i, r in _claims.items() if r.get("done")}
        # the claiming session's cmux number, drawn before 😈 (2026-10-21)
        agent_pane = {i: str(r["pane"]) for i, r in _claims.items() if r.get("pane") is not None}
    except Exception:
        agent_ids, agent_pane, agent_done = set(), {}, set()

    # Running timer (2026-10-20, phone: "status icon when I'm working on
    # something"): the task whose Toggl timer is live, read the way terminal
    # dtd does -- lib/running_timer's shared observation first, else the
    # dtd-running-hint file (janus /d, dtd Enter) when under 4h old. Matched
    # by task id, falling back to the annotation-stripped name.
    running_id, running_clean = _running_task()

    def is_woke(t):
        if t.get("id") is not None and str(t["id"]) in woken:
            return True
        bh = next((block_hours[l] for l in t.get("labels", []) if l in block_hours), None)
        return bh is not None and now_hour >= bh

    def flt(tier):
        return sorted(tier, key=lambda t: not is_woke(t))

    ordered = (flt(rituals) + flt(neg1g) + flt(zeroneon) + flt(oneneon)
               + flt(zerog) + flt(critical + rest))
    seen = set()
    out = []
    # One running row per timer (2026-10-23: "the recurrance should not also
    # have an abacus"): an id match is exact; only with no id does the name
    # match apply, and then to the first visible card only — the recurring
    # parent and its same-named one-off copy both lit up before.
    run_taken = False
    for t in ordered:
        tid = t.get("id")
        if not t.get("content") or tid in seen:
            continue
        seen.add(tid)
        if tid is not None and str(tid) in completed_ids:
            continue
        if tid is not None and str(tid) in form_hidden:
            continue
        # Block-snoozed (ctrl-v in desktop dtd): hidden until the chosen
        # block's hour arrives — same file/semantics as terminal dtd.
        if tid is not None and str(tid) in snoozed_ids:
            continue
        blk = next((block_hours[l] for l in t.get("labels", []) if l in block_hours), None)
        if blk is not None and now_hour < blk:
            continue
        # Later-today defer (2026-10-22): a task whose Todoist due carries a
        # time-of-day still ahead of now is hidden until that moment arrives —
        # the hide the `scheduled` field's comment always promised but that no
        # code actually did (the recurring bug: xk87 tasks deferred to later
        # today kept showing on dtd web/phone). A date-only due has no datetime
        # and is never hidden here; a timed due already past today still shows.
        sched_dt = _parse_scheduled(t.get("due_datetime") or "")
        if sched_dt is not None and sched_dt > _now():
            continue
        raw = t["content"]
        if running_id:
            is_running = tid is not None and str(tid) == running_id
        else:
            is_running = bool(running_clean and strip_ann(raw).lower() == running_clean and not run_taken)
            run_taken = run_taken or is_running
        display = t.get("short") or raw
        title = strip_ann(display) or raw
        est, pts = card_points(raw, t.get("labels", []), _ritual_cfg)
        color, dom = color_of(t.get("labels", []), raw)
        out.append({
            "id": tid,
            "raw": raw,
            "title": title,
            "est": est,
            "points": pts,
            "color": color,
            "domain": dom,
            "recurring": bool(t.get("recurring")),
            "variablePrompt": variable_prompt(raw),
            "variableRate": variable_rate(raw),
            # Todoist due datetime (RFC3339) when the task carries a time-of-day,
            # else "". The phone/web hide it until that time arrives today
            # (later-today defer); a date-only due sends "" and is never hidden.
            "scheduled": t.get("due_datetime") or "",
            "agent": tid is not None and str(tid) in agent_ids,
            "agentPane": agent_pane.get(str(tid), "") if tid is not None else "",
            "agentDone": tid is not None and str(tid) in agent_done,
            "running": is_running,
            # Survey card (2026-10-22): the form Enter opens in terminal dtd
            # (dtd_forms.CARD_FORMS), "" for a plain task; the phone opens
            # GET /api/form for these instead of starting a timer.
            "form": dtd_forms.form_for(raw),
        })
    return out


RUNNING_HINT = STATE_DIR / "dtd-running-hint"


def _running_task(now: float | None = None) -> tuple[str, str]:
    """(task_id, clean_name) of the task whose timer is live, or ("", "").
    Same precedence as dtd.sh's list generator: the shared Toggl observation
    (lib/running_timer.observed) wins when it is newer than the hint; the
    hint alone is trusted for 4h. An observation that says nothing is running
    (empty desc) is believed, which is what clears a stale ▶."""
    import time as _t
    now = _t.time() if now is None else now
    hint_id, hint_clean, hint_ts = "", "", 0
    try:
        parts = RUNNING_HINT.read_text().strip().split("\t")
        ts = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if ts and now - ts < 4 * 3600:
            hint_id, hint_clean, hint_ts = (parts[2].strip() if len(parts) > 2 else ""), parts[0].strip().lower(), ts
    except (OSError, IndexError, ValueError):
        pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
        import running_timer
        obs = running_timer.observed(now=now)
    except Exception:
        obs = None
    if obs and obs.get("ts", 0) > hint_ts:
        return (obs.get("task_id") or ""), (obs.get("desc") or "").strip().lower()
    return hint_id, hint_clean

# ---------------------------------------------------------------------------
# Complete via did-fast (real /did: closes Todoist + writes Neon)
# ---------------------------------------------------------------------------
def complete(content: str, task_id: str | None = None) -> dict:
    # Same call terminal dtd's worker makes: the annotation-stripped name plus
    # --task-id. Sending the raw card ("i447 (15) [5]") made did-fast read [5]
    # as a deliberate override and keep "(15)" in the name, so the 0n header
    # never matched: the card closed and +5 went to a domain column while the
    # habit stayed unmarked (bug 2026-10-05: 早餐, wake up, i444, i447, charge).
    args = ["/usr/bin/python3", str(DID_FAST)]
    if task_id:
        args += ["--task-id", str(task_id)]
    try:
        proc = subprocess.run(args + [strip_ann(content)],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data, brace = None, out.find("{")
    if brace >= 0:
        try:
            data = json.loads(out[brace:])
        except Exception:
            data = None
    closed = False
    step = None
    if data and data.get("results"):
        r0 = data["results"][0]
        step = r0.get("step")
        closed = bool((r0.get("todoist") or {}).get("closed"))
    # did-fast exits 0 when it can't route an item (agent_needed, e.g. an
    # @m828 task: no 0分 column), having written nothing. Treating that as
    # success toasted "+N 分" for points that never landed (2026-10-04).
    agent = (data or {}).get("agent_needed") or []
    if agent and not (data or {}).get("results"):
        return {"ok": False, "closed": False, "step": "needs_agent",
                "error": "not logged: " + (agent[0].get("reason") or "no route"),
                "stderr_tail": proc.stderr.strip()[-300:]}
    return {
        "ok": proc.returncode == 0,
        "closed": closed,
        "step": step,
        "future_skipped": bool(data and data.get("future_skipped")) if data else False,
        "stderr_tail": proc.stderr.strip()[-300:],
    }

# ---------------------------------------------------------------------------
# Quick-add (+ button): same (N)/[N]/@tag syntax as /todo, parsed with plain
# regex — this is a bare Flask process with no LLM access, so unlike /todo's
# own inference of missing time/value/domain, omitted modifiers here just
# stay omitted rather than being guessed. Explicit only.
# ---------------------------------------------------------------------------
def parse_add_input(raw: str) -> tuple[str, list[str]]:
    """('description (N) [N]', ['tag', ...]) — tags stripped from content,
    (N)/[N] left in place (dtd's own parse_est reads them back out for
    display, same as every other task in the list)."""
    labels = _TAG.findall(raw)
    content = re.sub(r"  +", " ", _TAG.sub("", raw)).strip()
    return content, labels

def create_todoist_task(content: str, labels: list[str]) -> dict:
    """POST a new task: Inbox (no project_id), due today, priority 1 (=p4,
    Todoist's default/lowest — confirmed against a live task's raw API
    value, not assumed)."""
    try:
        tok = TODOIST_TOKEN_FILE.read_text().strip()
    except Exception:
        return {"ok": False, "error": "no Todoist token"}
    body = json.dumps({"content": content, "labels": labels,
                       "due_string": "today", "priority": 1}).encode()
    req = urllib.request.Request(
        "https://api.todoist.com/api/v1/tasks", data=body, method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            task = json.loads(resp.read())  # validate it's a real task response
        # The new id goes back to the caller (2026-10-22): the phone's offline
        # outbox swaps it into a done queued for the same task right after,
        # so that done closes THIS task by id instead of a fuzzy name match.
        return {"ok": True, "content": content, "id": str(task.get("id") or "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------------------------------------------------------------------------
# Start timer (swipe left): exactly mirrors terminal dtd's `enter` binding
# (tools/did/dtd.sh's DTD_START heredoc) — same project resolution, same
# stop-then-start via the same toggl_cli.py, so behavior can't drift between
# the two UIs. Needs only the task's raw content, no Todoist label lookup.
# ---------------------------------------------------------------------------
def start_timer(raw: str) -> dict:
    clean = strip_ann(raw)
    bare = clean.replace("😈", "").strip()
    project = ""
    for tag, dom in RITUAL_DOMAIN.items():
        if bare == tag or tag in bare.split():
            project = dom
            break
    if not project:
        try:
            proc = subprocess.run(["/usr/bin/python3", str(TG_FAST), "--resolve", bare],
                                  capture_output=True, text=True, timeout=15)
            project = proc.stdout.strip()
        except Exception as e:
            print("WARN start_timer resolve:", e, file=sys.stderr)
    try:
        subprocess.run(["/usr/bin/python3", str(TOGGL_CLI), "stop"],
                       capture_output=True, text=True, timeout=15)
        args = ["/usr/bin/python3", str(TOGGL_CLI), "start", bare]
        if project:
            args.append(project)
        proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-300:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "clean": bare, "project": project}

# ---------------------------------------------------------------------------
# Delay (day) / delay (block): swipe-left menu actions #2 and #3, mirroring
# terminal dtd's ctrl-d (defer) and ctrl-v (block-snooze) bindings.
# ---------------------------------------------------------------------------
def defer_task(task_id: str, days=None) -> dict:
    """ctrl-d equivalent: shell out to defer-fast.py, which owns all the
    recurring-vs-non-recurring branching, dated-copy creation, etc. — with no
    extra args it defers to today+1 day at 2 claimed points, its own
    documented default (same as a terminal ctrl-d with a blank prompt).
    `days` mirrors the CLI schedule screen: 0 = next occurrence (recurring) /
    one day (one-off), N = N days out."""
    cmd = ["/usr/bin/python3", str(DEFER_FAST), "--id", task_id]
    if days is not None:
        cmd.append(str(days))
    try:
        proc = subprocess.run(cmd,
                              capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-300:]}
        data = json.loads(proc.stdout)
        _patch_cache_due(task_id, data.get("target_date", ""))
        return {"ok": True, "target_date": data.get("target_date", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def edit_get(task_id: str) -> dict:
    """ctrl-g prefill: shell out to edit-fast.py --current, which prints the
    editable fields (name/domain/points/description) plus the full allowed
    domain list as shlex-quoted shell assignments. Parse them back into JSON
    for the mobile edit sheet. The description is NOT in build_tasks()' task
    objects, so this on-demand fetch is the only way the sheet gets it."""
    import shlex
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(EDIT_FAST), "--current", "--id", task_id, str(CACHE)],
            capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-300:] or "task not found"}
        fields: dict = {}
        for line in proc.stdout.splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            toks = shlex.split(v)          # shlex.quote emits exactly one token
            fields[k] = toks[0] if toks else ""
        return {
            "ok": True,
            "name": fields.get("cur_name", ""),
            "domain": fields.get("cur_domain", ""),
            "points": fields.get("cur_points", ""),
            "desc": fields.get("cur_desc", ""),
            "domains": (fields.get("domains", "") or "").split(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def edit_apply(task_id: str, name: str, domain: str, points: str, desc: str) -> dict:
    """ctrl-g submit: shell out to edit-fast.py --fields. edit-fast diffs each
    field against the current prefill and applies only what changed (empty
    name/domain/points mean "leave it"; an empty desc clears it), writing both
    Todoist and the task-queue cache in place. We pass all four fields; the
    diff lives in edit-fast so both surfaces behave identically."""
    try:
        args = ["/usr/bin/python3", str(EDIT_FAST), "--fields", "--id", task_id, str(CACHE),
                "--name", name, "--domain", domain, "--points", points, "--desc", desc]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or out.startswith("✗"):
            return {"ok": False, "error": (out or proc.stderr or "edit failed")[-300:]}
        return {"ok": True, "message": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _cache_task(task_id: str) -> dict | None:
    try:
        d = json.loads(CACHE.read_text())
    except Exception:
        return None
    for v in d.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and str(t.get("id")) == str(task_id):
                    return t
    return None


def _patch_cache_split(task_id: str, new_content: str, rem_date: str, closed: bool) -> None:
    """After a split, make the cache show the remainder at once (same reason
    as _patch_cache_due): rename it in place, or drop the row when the task
    was closed (keep=false) or its remainder moved past today. The next real
    refresh overwrites this with Todoist's own state, which agrees."""
    try:
        d = json.loads(CACHE.read_text())
        hit = False
        gone = closed or (rem_date and rem_date > _today().isoformat())
        for k, v in d.items():
            if not isinstance(v, list):
                continue
            keep = []
            for t in v:
                if isinstance(t, dict) and str(t.get("id")) == str(task_id):
                    hit = True
                    if gone:
                        continue
                    t["content"] = new_content
                    if rem_date:
                        t["due"] = rem_date; t["due_datetime"] = ""
                keep.append(t)
            d[k] = keep
        if hit:
            tmp = CACHE.with_suffix(".patch.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False))
            tmp.replace(CACHE)
    except Exception as e:
        print("WARN patch cache (split):", e, file=sys.stderr)


def split_task(task_id: str, pts, minutes, name: str = "", keep: bool = True) -> dict:
    """ctrl-p "done so far" (2026-10-22): shell out to split-fast.py, the
    standalone port of dtd.sh's split.sh (posthoc done record + remainder
    rename/close + did-fast --points-only credit). The phone sends one pts
    number: on a {N}-only task (a -1g goal) that is the bonus done, the one
    field terminal's edit form shows there; otherwise it is [N] pts done."""
    t = _cache_task(task_id)
    content = (t or {}).get("content", "")
    has_sq = bool(re.search(r"\[\d+\]", content))
    has_curly = bool(re.search(r"\{\d+\}", content))
    as_bonus = has_curly and not has_sq
    # edit-form.validate()'s bounds, so the phone can't carve more than exists
    total = re.search(r"\{(\d+)\}" if as_bonus else r"\[(\d+)\]", content)
    if total and pts > int(total.group(1)):
        return {"ok": False, "error": f"pts done {pts} > current {total.group(1)}"}
    dur = re.search(r"\((\d+)\)", content)
    if minutes is not None and dur and minutes > int(dur.group(1)):
        return {"ok": False, "error": f"min done {minutes} > current {dur.group(1)}"}
    args = ["/usr/bin/python3", str(SPLIT_FAST), "--id", task_id, "--cache", str(CACHE),
            "--bonus" if as_bonus else "--pts", str(pts)]
    if minutes is not None:
        args += ["--minutes", str(minutes)]
    if name:
        args += ["--name", name]
    if not keep:
        args.append("--no-keep")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:
            data = {}
        if not data.get("ok"):
            return {"ok": False, "error": (data.get("error") or proc.stderr or "split failed").strip()[-300:]}
        _patch_cache_split(task_id, data.get("new_content", ""), data.get("rem_date", ""),
                           bool(data.get("closed")))
        return {"ok": True, "message": data.get("message", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Survey forms (2026-10-22): GET /api/form describes the card's form, POST
# runs dtd_forms.py in a subprocess, which writes through each form script's
# own writer path (see dtd_forms' docstring). Excel round-trips can outlast a
# phone request, so the POST waits FORM_WAIT seconds and then answers "still
# saving" while the child finishes on its own (detached; log in FORMS_TMP).
# ---------------------------------------------------------------------------
FORM_WAIT = 50
_FORM_LOCK = threading.Lock()
_FORM_BUSY: set = set()          # task ids with a submit in flight
_FORM_DONE: dict = {}            # task id -> (date, message): retried POST = no second write
# Form-closed cards hidden from /api/tasks from the moment the POST is accepted
# (2026-10-25, 6hqjQRh6rQfHwGMc: "after form filled the task needs to
# disappear faster"). task id -> [date, finished_at|None]: hidden while the
# writer runs, then until a cache refresh newer than its finish lands (the
# form's own did-fast close is what really drops it). A failed writer unhides.
_FORM_HIDDEN: dict = {}


def _form_hide(task_id: str) -> None:
    with _FORM_LOCK:
        _FORM_HIDDEN[task_id] = [_today().isoformat(), None]


def _form_settle(task_id: str, ok: bool) -> None:
    with _FORM_LOCK:
        if not ok:
            _FORM_HIDDEN.pop(task_id, None)
        elif task_id in _FORM_HIDDEN:
            _FORM_HIDDEN[task_id][1] = time.time()


def _form_hidden_ids(cache_updated: str = "") -> set:
    try:
        upd = _dt.datetime.fromisoformat(cache_updated).timestamp()
    except (TypeError, ValueError):
        upd = 0.0
    today = _today().isoformat()
    with _FORM_LOCK:
        return {i for i, (day, fin) in _FORM_HIDDEN.items()
                if day == today and (fin is None or upd <= fin)}


def _form_watch(task_id: str, proc, rf: Path) -> None:
    """Past FORM_WAIT the POST has answered "pending"; settle the hide (and
    the retry guard) when the detached writer really finishes, so a failed
    write brings the card back instead of losing it for the day."""
    try:
        proc.wait()
        ok = bool(json.loads(rf.read_text()).get("ok"))
    except Exception:
        ok = False
    if not ok:
        _FORM_DONE.pop(task_id, None)
    _form_settle(task_id, ok)
    if ok and _refresh_lock.acquire(blocking=False):
        threading.Thread(target=_run_refresh_subprocess, daemon=True).start()


def form_get(task_id: str) -> dict:
    t = _cache_task(task_id)
    form = dtd_forms.form_for((t or {}).get("content", ""))
    sp = dtd_forms.spec(form, _today()) if form else None
    if not sp:
        return {"ok": False, "form": None}
    # keepsCard / closesIf tell the phone whether saving removes the row at
    # once (dtd_forms.closes_card is the server's own copy of the rule).
    return {"ok": True, **sp, "keepsCard": form in dtd_forms.KEEPS_CARD,
            "closesIf": dtd_forms.CLOSES_IF.get(form, {})}


def form_submit(task_id: str, form: str, values: dict) -> tuple[dict, int]:
    t = _cache_task(task_id)
    want = dtd_forms.form_for((t or {}).get("content", ""))
    today = _today().isoformat()
    done = _FORM_DONE.get(task_id)
    if done and done[0] == today and done[1] == form:
        return {"ok": True, "already": True, "message": done[2]}, 200
    if not want:
        return {"ok": False, "error": "no form for this card"}, 400
    if form != want:
        return {"ok": False, "error": f"card's form is {want}, not {form}"}, 400
    sp = dtd_forms.spec(form, _today())
    if not sp:
        return {"ok": False, "error": f"{form}: form script missing on this host"}, 400
    keys = [f["key"] for f in sp["fields"]]
    vals = {k: str(values.get(k) if values.get(k) is not None else "") for k in keys}
    err = dtd_forms.validate(form, vals)
    if err:
        return {"ok": False, "error": err}, 400
    with _FORM_LOCK:
        if task_id in _FORM_BUSY:
            return {"ok": False, "error": "already saving this form"}, 409
        _FORM_BUSY.add(task_id)
    closes = dtd_forms.closes_card(form, vals)
    if closes:
        _form_hide(task_id)
    settled = False
    try:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        d = FORMS_TMP / f"{stamp}-{form.strip('-')}-{task_id}"
        d.mkdir(parents=True, exist_ok=True)
        vf, rf = d / "values.json", d / "result.json"
        vf.write_text(json.dumps(vals, ensure_ascii=False))
        with open(d / "log.txt", "ab") as log:
            proc = subprocess.Popen(["/usr/bin/python3", str(FORMS_PY), "run", form, str(vf), str(rf)],
                                    stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        try:
            proc.wait(timeout=FORM_WAIT)
        except subprocess.TimeoutExpired:
            msg = f"{form}: still saving in the background (log {d}/log.txt)"
            _FORM_DONE[task_id] = (today, form, msg)
            settled = True  # the watcher owns the hide from here
            threading.Thread(target=_form_watch, args=(task_id, proc, rf), daemon=True).start()
            return {"ok": True, "pending": True, "message": msg}, 200
        try:
            res = json.loads(rf.read_text())
        except Exception:
            res = {"ok": False, "error": f"{form} exited {proc.returncode} (log {d}/log.txt)"}
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "form failed"}, 200
        _FORM_DONE[task_id] = (today, form, res.get("message", ""))
        _form_settle(task_id, True); settled = True
        # the form's own close refreshed the cache; pull it in for the reload
        if _refresh_lock.acquire(blocking=False):
            threading.Thread(target=_run_refresh_subprocess, daemon=True).start()
        return {"ok": True, "message": res.get("message", "")}, 200
    finally:
        if closes and not settled:
            _form_settle(task_id, False)  # failed or raised: the card comes back
        with _FORM_LOCK:
            _FORM_BUSY.discard(task_id)


def _write_snooze(task_id: str, value) -> None:
    """Read-modify-write dtd-block-snooze.json for one task id. Shared by
    every delay-to-later-today path (block-by-hour, next-block, minute-
    granularity) — value is either a plain int hour (block delay) or a
    float epoch timestamp (minute delay), matching terminal dtd's
    DTD_BLOCKAPPLY so either surface can read what the other wrote. A stale
    (not-today) file is discarded first, same forward-only date gate as the
    terminal's."""
    try:
        sn = json.loads(SNOOZE_FILE.read_text())
    except Exception:
        sn = {}
    today = _today().isoformat()
    if sn.get("date") != today:
        sn = {"date": today, "snoozes": {}}
    sn.setdefault("snoozes", {})[str(task_id)] = value
    SNOOZE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNOOZE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sn))
    tmp.replace(SNOOZE_FILE)
    # Cross-host mirror into the synced vault so a delay made HERE is seen by
    # dtd on other machines too (symmetry with the CLI's mirror write and with
    # completed-today). Best-effort — a mirror miss must not fail the delay.
    try:
        MIRROR_DIR.mkdir(parents=True, exist_ok=True)
        mpath = _snooze_mirror_path()
        mtmp = mpath.with_suffix(".tmp")
        mtmp.write_text(json.dumps(sn))
        mtmp.replace(mpath)
    except Exception:
        pass


def remaining_blocks_today() -> list[dict]:
    """地支 blocks whose start hour hasn't arrived yet, chronological — the
    same set terminal dtd's ctrl-v picker offers (DTD_BLOCKARM's `n=... h >
    now.hour` count). Mirrors BLOCK_HOURS' glyph order via a start-hour
    sort, not dict insertion order, since BLOCK_HOURS itself is already
    chronological here but this must stay correct even if that changes."""
    now_hour = _now().hour
    return sorted(
        ({"glyph": g, "hour": h} for g, h in BLOCK_HOURS.items() if h > now_hour),
        key=lambda b: b["hour"])


# Minute-granularity delay options offered alongside the block picker —
# always available regardless of hour, exactly mirroring terminal dtd's
# DTD_BLOCKARM/DTD_BLOCKAPPLY "+10m/+30m/+1h" trio (the "always-available
# minute delays" its own comment calls them out as, independent of the
# block list above).
MINUTE_DELAY_OPTIONS = [10, 30, 60]


def snooze_to_block(task_id: str, hour: int) -> dict:
    """ctrl-v-then-pick-a-block equivalent: delay to an EXPLICIT block's
    start hour, not just the next one. `hour` must be one of BLOCK_HOURS'
    values — the caller (api_delay_hour) validates against
    remaining_blocks_today() so a stale/tampered request can't schedule a
    block that's already passed today."""
    try:
        _write_snooze(task_id, hour)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "hour": hour}


def snooze_minutes(task_id: str, minutes: int) -> dict:
    """+10m/+30m/+1h equivalent: stored as an absolute epoch float (not a
    bare hour) so the reader (dtd.sh's list generator / build_tasks below)
    can tell a minute-delay apart from a block-delay on read — exactly
    terminal dtd's DTD_BLOCKAPPLY encoding."""
    if minutes not in MINUTE_DELAY_OPTIONS:
        return {"ok": False, "error": f"unsupported minute delay: {minutes}"}
    until = _now().timestamp() + minutes * 60
    try:
        _write_snooze(task_id, until)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "minutes": minutes}


def _parse_freeform_delay(text: str):
    """Parse a freeform 'delay until' string into an absolute epoch float for
    LATER TODAY, or (None, error). Today-only by design (user choice
    2026-09-27) — no cross-midnight, no future dates. Two forms:
      - relative duration: '90m', '45 min', '2h', '3 hrs'  -> now + that
      - clock time today:  '1830', '18:30', '6:05'         -> today at HH:MM
    Stored as a float epoch so the reader (_snoozed_ids) treats it as a
    timestamp, same encoding as snooze_minutes. dtd web has no LLM, so this is
    deliberately a small deterministic parser, not natural-language."""
    s = (text or "").strip().lower()
    if not s:
        return None, "enter a time (e.g. 18:30 or 90m)"
    now = _now()
    m = re.fullmatch(r"(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)", s)
    if m:
        n = int(m.group(1))
        mins = n * 60 if m.group(2).startswith("h") else n
        if mins <= 0:
            return None, "must be more than 0"
        if mins > 18 * 60:
            return None, "too far out — today only"
        return now.timestamp() + mins * 60, None
    m = re.fullmatch(r"(\d{1,2}):?([0-5]\d)", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23:
            return None, "not a valid time"
        target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now:
            return None, "that time already passed today"
        return target.timestamp(), None
    return None, "use HH:MM (today) or a duration like 90m / 2h"


def skip_recurrence(task_id: str) -> dict:
    """'Skip to next occurrence, without repeat-duplicate' (user request
    2026-09-27) for a RECURRING task: shell defer-fast.py in skip mode
    (`--id <id> 0`), which advances the recurring parent to its next natural
    occurrence with NO one-off dated copy and the recurrence rule intact — and
    marks the parent deferred-today so dtd hides it for the rest of today. The
    frontend only offers this for tasks flagged recurring; a non-recurring task
    has no 'next occurrence' (defer-fast would treat '0' as today), so guard
    on the returned `recurring` flag and report if it wasn't recurring."""
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(DEFER_FAST), "--id", task_id, "0"],
            capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-300:]}
        data = json.loads(proc.stdout)
        if data.get("error"):
            return {"ok": False, "error": data["error"]}
        if not data.get("recurring"):
            return {"ok": False, "error": "not a recurring task — use a normal delay"}
        return {"ok": True, "next": data.get("next_recurrence") or data.get("target_date", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delay_auto(task_id: str) -> dict:
    """The CLI schedule screen's '↻ 0 / next occurrence / one day' row
    (BLOCK:dauto → defer-fast 'auto'; user request 2026-10-18 "need to have
    the 0 delay option like in CLI"): a recurring task skips to its next
    natural occurrence with NO dated copy; a non-recurring task gets
    defer-fast's default +1 day. One button that does the right thing for
    either kind, so the web sheet matches tools/did/dtd.sh exactly."""
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(DEFER_FAST), "--id", task_id, "auto"],
            capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-300:]}
        data = json.loads(proc.stdout)
        if data.get("error"):
            return {"ok": False, "error": data["error"]}
        return {"ok": True, "recurring": bool(data.get("recurring")),
                "date": data.get("next_recurrence") or data.get("target_date", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snooze_to_next_block(task_id: str) -> dict:
    """ctrl-v equivalent, scoped to just the NEXT block. Kept as the
    quick-action fast path (still wired to /api/delay-block); the swipe
    menu's ⏰ button now opens the full picker (snooze_to_block /
    snooze_minutes) instead of calling this directly — see
    remaining_blocks_today(). Falls back to defer_task (delay a day) if
    there's no next block left today (already in 亥, 20:00-21:59)."""
    blocks = remaining_blocks_today()
    if not blocks:
        return defer_task(task_id)
    return snooze_to_block(task_id, blocks[0]["hour"])

# ---------------------------------------------------------------------------
# Day totals for the header: points so far today + tasks completed today.
# Both derived from cross-machine-durable sources (Excel Σ on Ix + Todoist +
# the Todoist-due-date cache) rather than the machine-local completed-today.json,
# so a completion made on the desktop still counts here. MUST run on Ix (the
# Excel daemon is local and the cache reflects advanced habit due dates).
# ---------------------------------------------------------------------------
_SUMMARY = {"at": 0.0, "val": None}

def _todoist_completed_today() -> int:
    """Count of Todoist completions today. NOTE: recurring habits are absent —
    completing a recurring task reschedules it, so it never lands in the
    completed ledger. Those are added back from the cache in day_summary()."""
    try:
        tok = TODOIST_TOKEN_FILE.read_text().strip()
    except Exception:
        return 0
    if not tok:
        return 0
    today = _today().isoformat()
    url = ("https://api.todoist.com/api/v1/tasks/completed/by_completion_date"
           "?since=%sT00:00:00&until=%sT23:59:59&limit=200" % (today, today))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=10) as r:
        return len(json.loads(r.read()).get("items", []))

_summary_lock = threading.Lock()

def _compute_summary() -> dict:
    """The actual (slow, Excel/Todoist-bound) computation — always runs to
    completion and updates _SUMMARY. Split out of day_summary() so force=True
    can call it directly (blocking, as before) while the stale/background
    case below can run it off-thread."""
    today = _today()
    iso = today.isoformat()

    # points so far today = 0分 Σ (col D), the grand total for today's row.
    points = 0
    try:
        from neon import excel
        r = excel.read("0分", "D", date="%d/%d" % (today.month, today.day))
        if r.get("ok"):
            points = int(float(r.get("value") or 0))
    except Exception as e:
        print("WARN day_summary points:", e, file=sys.stderr)

    # tasks completed today = Todoist-completed (non-recurring tasks/rituals/goals)
    # + daily habits whose due date advanced past today (recurring completions
    # Todoist hides). Matches the felt count; independent of which machine did it.
    done = 0
    try:
        done += _todoist_completed_today()
    except Exception as e:
        print("WARN day_summary todoist:", e, file=sys.stderr)
    try:
        d = json.loads(CACHE.read_text())
        done += sum(1 for key in ("0neon", "夜neon") for t in d.get(key, [])
                    if isinstance(t, dict) and t.get("recurring")
                    and t.get("due") and t["due"] > iso)
    except Exception as e:
        print("WARN day_summary advanced:", e, file=sys.stderr)

    val = {"points": points, "done": done}
    _SUMMARY.update(at=time.time(), val=val)
    return val

def _compute_summary_async():
    try:
        _compute_summary()
    finally:
        _summary_lock.release()

def day_summary(force: bool = False) -> dict:
    """force=True: compute synchronously, as before (the caller is
    deliberately waiting on fresh data).

    force=False once the SUMMARY_MAX_AGE cache expires: never block the
    request on this either (2026-08-11, same bug class/fix as
    _refresh_cache_if_stale above). Confirmed live: even after fixing the
    Todoist refresh path, /api/tasks was STILL taking 15s+, because this
    function's excel.read() call blocks on the excel-http daemon on ix,
    which itself was taking ~15s just to return its own osascript_timeout
    error. A slow/wedged Excel daemon must not translate into a hung page
    load — recompute in the background (deduped, at most one in flight) and
    serve the last known value, or a zeroed fallback on a cold start with
    nothing computed yet (matches the existing graceful-degrade contract)."""
    now = time.time()
    fresh = _SUMMARY["val"] is not None and now - _SUMMARY["at"] < SUMMARY_MAX_AGE
    if not force and fresh:
        return _SUMMARY["val"]
    if force:
        return _compute_summary()
    if _summary_lock.acquire(blocking=False):
        threading.Thread(target=_compute_summary_async, daemon=True).start()
    return _SUMMARY["val"] or {"points": 0, "done": 0}

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route("/api/tasks")
def api_tasks():
    try:
        force = request.args.get("refresh") == "1"
        return jsonify({"ok": True, "tasks": build_tasks(force_refresh=force),
                        "summary": day_summary(force=force)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/summary")
def api_summary():
    try:
        return jsonify({"ok": True, **day_summary(force=request.args.get("refresh") == "1")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/where", methods=["POST"])
def api_where():
    """Phone beacon (lib/where.py): the browser's zone, attributed to the
    Tailscale device behind the request IP. Non-Tailscale IPs are ignored."""
    import where
    tz = (request.get_json(silent=True) or {}).get("tz", "")
    dev = where.record_peer(_peer_ip(), str(tz))
    return jsonify({"ok": bool(dev), "device": dev})

def _peer_ip() -> str:
    """The phone's IP as the where beacon needs it. Behind `tailscale serve`
    (https://ix.tail9c51d5.ts.net:5560, 2026-10-19) the proxy connects from
    loopback and carries the real tailnet peer in X-Forwarded-For; a direct
    http://100.x hit still arrives as remote_addr. Only loopback is trusted
    to forward — a spoofed header from a real peer is ignored."""
    ip = request.remote_addr or ""
    if ip in ("127.0.0.1", "::1"):
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if fwd:
            ip = fwd
    return ip

@app.route("/api/done", methods=["POST"])
def api_done():
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    task_id = str(body.get("id") or "").strip()
    if task_id and task_id in _completed_ids():
        # Idempotent: a retried swipe (phone locked mid-POST, tailnet blip,
        # native client's retry) must not run did-fast twice — the second
        # run would credit Neon again. Same shape the first answer had.
        return jsonify({"ok": True, "already": True, "closed": True,
                        "points": card_points(content)[1]})
    res = complete(content, body.get("id"))
    res["points"] = card_points(content)[1]
    return jsonify(res)

@app.route("/api/start", methods=["POST"])
def api_start():
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    return jsonify(start_timer(content))

@app.route("/api/delay-day", methods=["POST"])
def api_delay_day():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    days = body.get("days")
    if days is not None:
        # 0 = next occurrence / one day, 1..366 = N days out (CLI schedule screen).
        if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 366:
            return jsonify({"ok": False, "error": "days must be 0-366"}), 400
    return jsonify(defer_task(task_id) if days is None else defer_task(task_id, days))

@app.route("/api/delay-block", methods=["POST"])
def api_delay_block():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    return jsonify(snooze_to_next_block(task_id))

@app.route("/api/delay-options")
def api_delay_options():
    """Populates the ⏰ picker sheet: remaining 地支 blocks today plus the
    fixed minute-delay trio — fetched fresh each time the sheet opens so
    the block list is never stale from an earlier page load."""
    return jsonify({"ok": True, "blocks": remaining_blocks_today(),
                    "minutes": MINUTE_DELAY_OPTIONS})

@app.route("/api/delay-hour", methods=["POST"])
def api_delay_hour():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    hour = body.get("hour")
    if not task_id or not isinstance(hour, int):
        return jsonify({"ok": False, "error": "no id/hour"}), 400
    # Reject an hour that's already passed (or isn't a real block start) —
    # a stale sheet left open across a block boundary, or a tampered
    # request, must not silently schedule a "delay" into the past.
    valid_hours = {b["hour"] for b in remaining_blocks_today()}
    if hour not in valid_hours:
        return jsonify({"ok": False, "error": "that block has already started"}), 400
    return jsonify(snooze_to_block(task_id, hour))

@app.route("/api/delay-minutes", methods=["POST"])
def api_delay_minutes():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    minutes = body.get("minutes")
    if not task_id or not isinstance(minutes, int):
        return jsonify({"ok": False, "error": "no id/minutes"}), 400
    return jsonify(snooze_minutes(task_id, minutes))

@app.route("/api/delay-freeform", methods=["POST"])
def api_delay_freeform():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    text = str(body.get("text") or "")
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    epoch, err = _parse_freeform_delay(text)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        _write_snooze(task_id, float(epoch))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "until": f"{_dt.datetime.fromtimestamp(epoch, daytime.active_zone()):%H:%M}"})

@app.route("/api/delay-auto", methods=["POST"])
def api_delay_auto():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    return jsonify(delay_auto(task_id))

@app.route("/api/skip-recurrence", methods=["POST"])
def api_skip_recurrence():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    return jsonify(skip_recurrence(task_id))

@app.route("/api/add", methods=["POST"])
def api_add():
    body = request.get_json(force=True, silent=True) or {}
    raw = (body.get("content") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "no content"}), 400
    content, labels = parse_add_input(raw)
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    return jsonify(create_todoist_task(content, labels))

@app.route("/api/edit-get")
def api_edit_get():
    task_id = str(request.args.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    res = edit_get(task_id)
    return jsonify(res), (200 if res.get("ok") else 404)

@app.route("/api/edit", methods=["POST"])
def api_edit():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    res = edit_apply(task_id,
                     str(body.get("name") or ""),
                     str(body.get("domain") or ""),
                     str(body.get("points") or ""),
                     str(body.get("desc") or ""))
    return jsonify(res), (200 if res.get("ok") else 400)

def _opt_int(v):
    """None, or a non-negative int (JSON bools are not ints here)."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError
    return v

@app.route("/api/split", methods=["POST"])
def api_split():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "no id"}), 400
    try:
        pts, minutes = _opt_int(body.get("pts")), _opt_int(body.get("minutes"))
    except ValueError:
        return jsonify({"ok": False, "error": "pts/minutes must be whole numbers"}), 400
    if pts is None:
        # split.sh cancels without pts (edit-form: "min done needs pts too")
        return jsonify({"ok": False, "error": "split needs pts done"}), 400
    keep = body.get("keep", True)
    res = split_task(task_id, pts, minutes, " ".join(str(body.get("name") or "").split()),
                     keep is not False)
    return jsonify(res), (200 if res.get("ok") else 400)

@app.route("/api/form", methods=["GET"])
def api_form_get():
    task_id = str(request.args.get("id") or "").strip()
    if not task_id:
        return jsonify({"ok": False, "form": None, "error": "no id"}), 400
    return jsonify(form_get(task_id))

@app.route("/api/form", methods=["POST"])
def api_form_post():
    body = request.get_json(force=True, silent=True) or {}
    task_id = str(body.get("id") or "").strip()
    form = str(body.get("form") or "").strip()
    values = body.get("values")
    if not task_id or not form or not isinstance(values, dict):
        return jsonify({"ok": False, "error": "need id, form, values"}), 400
    res, code = form_submit(task_id, form, values)
    return jsonify(res), code

@app.route("/")
def index():
    return render_template_string(PAGE)

# ---------------------------------------------------------------------------
# Frontend — terminal-styled, single file, no external deps
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>dtd</title>
<style>
  :root { --bg:#1b1b1b; --dim:#777; --go:#00e676; --start:#29b6f6; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:#cfcfcf;
    font:15px/1.2 ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono",monospace;
    -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5;
    padding:calc(env(safe-area-inset-top) + 9px) 14px 8px;
    background:#1b1b1bee; backdrop-filter:blur(6px);
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #2a2a2a; }
  header .brand { font-weight:700; letter-spacing:1px; color:#cfcfcf; }
  header .brand b { color:var(--go); }
  .tally { color:var(--dim); font-variant-numeric:tabular-nums; }
  .tally b { color:var(--go); }
  #reload { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  header { flex-wrap:wrap; }
  #filterWrap { flex-basis:100%; display:flex; gap:6px; margin-top:7px; }
  #filterInput { flex:1; background:#111; color:#cfcfcf; border:1px solid #333;
    border-radius:6px; padding:5px 9px; font-family:inherit; font-size:15px; }
  #filterInput::placeholder { color:#555; }
  #filterClear { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  #filterClear[hidden] { display:none; }
  main { padding:2px 0 calc(env(safe-area-inset-bottom) + 60px); }
  .row { position:relative; overflow:hidden; }
  .row.agent .line { color:#6e6e6e !important; }
  #agentSpin { color:var(--dim); display:none; }
  #agentSpin.on { display:inline; }
  #agentSpin i { display:inline-block; font-style:normal; animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .row .track { position:absolute; inset:0; background:var(--go); color:#003; font-weight:800;
    display:flex; align-items:center; padding-left:16px; opacity:0; }
  .row .track::before { content:"✓ done  +" ; white-space:pre; }
  .row .track .p { font-weight:800; }
  .row .actions { position:absolute; top:0; right:0; bottom:0; display:flex; z-index:0; }
  .row .actions .act { width:72px; border:none; color:#fff; font:700 13px/1.3 inherit;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1px; }
  .act-day { background:#546e7a; }
  .act-block { background:#8e6fce; }
  .act-edit { background:#c0873d; }
  .act-start { background:var(--start); color:#001a26; }
  .editDesc { width:100%; min-height:70px; resize:vertical; background:#1b1b1b; color:#cfcfcf;
    border:1px solid #3a3a3a; border-radius:8px; padding:11px 12px; font:15px/1.35 inherit;
    margin-top:10px; }
  .editDesc:focus { outline:none; border-color:var(--go); }
  select.sheetInput { margin-top:10px; -webkit-appearance:none; appearance:none; }
  .line { position:relative; display:flex; align-items:center; gap:10px;
    padding:14px; background:var(--bg); min-height:52px;
    transform:translateX(0); transition:transform .05s linear; will-change:transform;
    touch-action:pan-y; }
  .line.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .ttl { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .est { white-space:nowrap; font-variant-numeric:tabular-nums; opacity:.92; }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 20px);
    transform:translateX(-50%) translateY(16px); background:var(--go); color:#003;
    font-weight:700; padding:9px 18px; border-radius:8px; opacity:0; transition:.22s;
    z-index:20; font-family:inherit; pointer-events:none; max-width:90vw; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); pointer-events:auto; }
  .toast.err { background:#ff4081; color:#2a0010; }
  #fab { position:fixed; right:calc(env(safe-area-inset-right) + 18px);
    bottom:calc(env(safe-area-inset-bottom) + 22px); width:52px; height:52px;
    border-radius:50%; background:var(--go); color:#003; border:none;
    font:400 26px/52px ui-monospace,monospace; text-align:center; padding:0;
    box-shadow:0 3px 10px #0007; z-index:15; }
  #fab:active { transform:scale(.94); }
  .sheetWrap { position:fixed; inset:0; background:#000a; z-index:25;
    display:flex; align-items:flex-end; opacity:0; pointer-events:none;
    transition:opacity .18s; }
  .sheetWrap.show { opacity:1; pointer-events:auto; }
  .sheet { width:100%; background:#232323; border-top:1px solid #333;
    padding:16px 14px calc(env(safe-area-inset-bottom) + 16px);
    transform:translateY(12px); transition:transform .18s; }
  .sheetWrap.show .sheet { transform:translateY(0); }
  .sheetLabel { color:var(--dim); font-size:13px; margin-bottom:8px; }
  .sheetInput { width:100%; background:#1b1b1b; color:#cfcfcf; border:1px solid #3a3a3a;
    border-radius:8px; padding:11px 12px; font:15px/1.3 inherit; }
  .sheetInput:focus { outline:none; border-color:var(--go); }
  .sheetRow { display:flex; gap:10px; margin-top:10px; }
  .sheetRow button { flex:1; padding:10px; border-radius:8px; border:1px solid #3a3a3a;
    background:#1b1b1b; color:#cfcfcf; font:700 15px inherit; }
  .sheetRow button.go { background:var(--go); color:#003; border-color:var(--go); }
  .sheetRow input { flex:2; padding:10px; border-radius:8px; border:1px solid #3a3a3a;
    background:#111; color:#eee; font:600 15px inherit; }
  .delayGrid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .delayBtn { padding:15px 8px; min-height:54px; border-radius:10px; border:1px solid #3a3a3a;
    background:#1b1b1b; color:#cfcfcf; font:700 17px/1.2 inherit; }
  .delayBtn:active { background:#2a2a2a; }
  .delayBtn.skipBtn { grid-column:1/-1; background:#152a15; border-color:#2e5e2e; color:#9fe09f; }
</style>
</head>
<body>
<header>
  <div class="brand">d<b>t</b>d</div>
  <div class="tally"><b id="tot">0</b> 分 · <span id="cnt">0</span> done <span id="agentSpin"><i>◐</i> 😈<span id="agentN"></span></span></div>
  <button id="reload">↻</button>
  <div id="filterWrap">
    <input id="filterInput" placeholder="filter: words or a domain (xk87)" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
    <button id="filterClear" hidden>✕</button>
  </div>
</header>
<main id="list"><div class="loading">loading…</div></main>
<button id="fab">+</button>
<div id="addWrap" class="sheetWrap">
  <div class="sheet">
    <input id="addInput" class="sheetInput" placeholder="task (10) [5] @tag" autocomplete="off" autocapitalize="off">
    <div class="sheetRow">
      <button id="addCancel">cancel</button>
      <button id="addGo" class="go">add</button>
    </div>
  </div>
</div>
<div id="valWrap" class="sheetWrap">
  <div class="sheet">
    <div class="sheetLabel" id="valLabel">value</div>
    <input id="valInput" class="sheetInput" inputmode="numeric" autocomplete="off" autocapitalize="off">
    <div class="sheetRow">
      <button id="valSkip">skip</button>
      <button id="valGo" class="go">log</button>
    </div>
  </div>
</div>
<div id="delayWrap" class="sheetWrap">
  <div class="sheet">
    <div class="sheetLabel" id="delayLabel">delay until…</div>
    <div id="delayOptions" class="delayGrid"></div>
    <div class="sheetRow">
      <input id="delayFreeform" inputmode="text" autocomplete="off"
             placeholder="or type: 18:30 · 90m · 2h">
      <button id="delayFreeGo" class="go">go</button>
    </div>
    <div class="sheetRow">
      <button id="delayCancel">cancel</button>
    </div>
  </div>
</div>
<div id="editWrap" class="sheetWrap">
  <div class="sheet">
    <div class="sheetLabel" id="editLabel">edit task</div>
    <input id="editName" class="sheetInput" placeholder="title" autocomplete="off" autocapitalize="off">
    <select id="editDomain" class="sheetInput"></select>
    <input id="editPoints" class="sheetInput" inputmode="numeric" placeholder="points" autocomplete="off"
           style="margin-top:10px">
    <textarea id="editDesc" class="editDesc" placeholder="description" autocomplete="off"></textarea>
    <div class="sheetRow">
      <button id="editCancel">cancel</button>
      <button id="editGo" class="go">save</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
// where beacon (lib/where.py): this phone's zone decides "today" everywhere.
function whereBeacon(){ try { fetch('/api/where', {method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({tz: Intl.DateTimeFormat().resolvedOptions().timeZone})}).catch(()=>{}); } catch(e){} }
whereBeacon();
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') whereBeacon(); });
const list = document.getElementById('list');
const toastEl = document.getElementById('toast');
let total = 0, count = 0;
let allTasks = [];   // last /api/tasks payload, unfiltered

// Filter box (2026-10-17, "dtd web has a text box like dtd so I can filter"):
// every whitespace-separated word must match the row's title, its raw
// Todoist content, or its domain code -- so typing a bare code (xk87) scopes
// the list to that domain exactly like terminal dtd's domain search, and a
// word scopes it like fzf's query. Case-insensitive. The query survives a
// reload/refresh (filtered re-render) and is remembered per browser.
// Fuzzy (wave 2 #1, 2026-10-22): each word matches as a subsequence of the
// haystack — "ktsch" finds "kids school", a dropped letter still hits — not
// just a contiguous substring. A contiguous hit short-circuits (the common
// case, and the strongest signal); otherwise fall back to in-order chars.
function fuzzyWord(w, hay){
  if(hay.includes(w)) return true;
  let i = 0;
  for(let k = 0; k < hay.length && i < w.length; k++) if(hay[k] === w[i]) i++;
  return i === w.length;
}
function matchesFilter(t, q){
  const words = (q||'').toLowerCase().split(/\s+/).filter(Boolean);
  if(!words.length) return true;
  const hay = ((t.title||'')+' '+(t.raw||'')+' '+(t.domain||'')).toLowerCase();
  return words.every(w => fuzzyWord(w, hay));
}
function filterTasks(tasks, q){ return tasks.filter(t => matchesFilter(t, q)); }

// Errors linger long enough to screenshot (user request 2026-10-18); either
// kind dismisses on tap. pointer-events gating in .toast CSS keeps the hidden
// toast from eating taps on the rows underneath it.
function toast(msg, err){
  toastEl.textContent = msg;
  toastEl.classList.toggle('err', !!err);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), err ? 8000 : 1500);
}
toastEl.onclick = ()=>{ clearTimeout(toastEl._t); toastEl.classList.remove('show'); };

function setTally(p, c){
  total = p||0; count = c||0;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
}

// In-place reload (2026-10-18 "prevent a full refresh on every change"): the
// loading placeholder only ever shows before the FIRST paint — after that the
// existing rows stay interactive while the fetch runs and are re-rendered in
// place when it lands. The ↻ button spins instead (a force refresh blocks on
// Todoist and can take seconds). loadSeq drops out-of-order responses so a
// slow background load can't overwrite a newer forced one.
let loadSeq = 0;
async function load(refresh){
  const seq = ++loadSeq;
  const rl = document.getElementById('reload');
  if(!allTasks.length) list.innerHTML = '<div class="loading">loading…</div>';
  if(refresh){ rl.disabled = true; rl.textContent = '…'; }
  try {
    const r = await fetch('/api/tasks' + (refresh?'?refresh=1':''));
    const d = await r.json();
    if(seq !== loadSeq) return;
    if(!d.ok) throw new Error(d.error||'fetch failed');
    allTasks = d.tasks;
    render(filterTasks(allTasks, filterInput.value));
    if(d.summary) setTally(d.summary.points, d.summary.done);
  } catch(e){ if(seq === loadSeq) list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
  finally { if(refresh){ rl.disabled = false; rl.textContent = '↻'; } }
}

function render(tasks){
  const nAgent = tasks.filter(t=>t.agent).length;
  const spin = document.getElementById('agentSpin');
  if(spin){ spin.classList.toggle('on', nAgent>0);
    document.getElementById('agentN').textContent = nAgent>1 ? ' ×'+nAgent : ''; }
  if(!tasks.length){
    list.innerHTML = '<div class="empty">' + (filterInput.value.trim() && allTasks.length
      ? 'nothing matches “' + filterInput.value.trim() + '”' : '🎉 nothing left for today') + '</div>';
    return; }
  list.innerHTML = '';
  for(const t of tasks) list.appendChild(makeRow(t));
}

function makeRow(t){
  const row = document.createElement('div');
  row.className = 'row' + (t.agent ? ' agent' : '');
  const track = document.createElement('div');
  track.className = 'track';
  // Variable-input tasks (user report 2026-10-18): the credit depends on the
  // value typed AFTER the swipe (minutes/quality/count — Neon computes the
  // points), so a fixed "+30 分" in the reveal is a lie. Show "? 分" instead.
  track.innerHTML = '<span class="p">'+(t.variablePrompt ? '?' : (t.points||0))+' 分</span>';
  row.appendChild(track);

  // Swipe-left reveal panel: three buttons mirroring terminal dtd's ctrl-d
  // (delay a day) / ctrl-v (delay to next block) / enter (start) bindings.
  const actions = document.createElement('div');
  actions.className = 'actions';
  actions.innerHTML =
    '<button class="act act-day">🗓<br>+1d</button>' +
    '<button class="act act-block">⏰<br>delay</button>' +
    '<button class="act act-edit">✏️<br>edit</button>' +
    '<button class="act act-start">▶<br>start</button>';
  row.appendChild(actions);

  const line = document.createElement('div');
  line.className = 'line';
  line.style.color = t.color;
  const ttl = document.createElement('span');
  ttl.className = 'ttl';
  ttl.textContent = (t.agentDone?'✅ ':'') + (t.agent?(t.agentPane||'')+'😈 ':'') + (t.recurring?'↻ ':'') + t.title;
  const est = document.createElement('span');
  est.className = 'est';
  est.textContent = t.est || '';
  line.appendChild(ttl); line.appendChild(est);
  row.appendChild(line);

  const panel = bindSwipe(row, line, track, actions, t);
  actions.querySelector('.act-day').onclick = ()=> runDelay('day', row, t, panel);
  actions.querySelector('.act-block').onclick = ()=> openDelayMenu(t, row, panel);
  actions.querySelector('.act-edit').onclick = ()=> openEditMenu(t, panel);
  actions.querySelector('.act-start').onclick = ()=> runStart(t, panel);
  return row;
}

// Swipe RIGHT keeps the original threshold-fly gesture (unchanged) — one
// clear action, decisive fling. Swipe LEFT is a reveal panel instead: drag
// exposes the action buttons underneath `line`, release snaps fully open or
// closed depending on how far you dragged (no in-between). Dragging always
// starts from wherever the row currently sits (`baseX`), so closing an
// open panel with a rightward drag doesn't get mistaken for the done-fly
// gesture — that's only reachable from the fully-closed resting position.
// Returns {close()} so button handlers (defined in makeRow, outside this
// closure) can snap the panel shut after their action completes.
function bindSwipe(row, line, track, actions, t){
  let x0=null, dx=0, dragging=false, baseX=0, moved=false;
  const W = () => row.offsetWidth;
  const AW = () => actions.offsetWidth || 288;
  const start = x=>{ x0=x; dragging=true; moved=false; line.classList.remove('snap'); };
  const move = x=>{
    if(!dragging) return;
    if(Math.abs(x-x0) > 4) moved = true;
    dx = Math.min(W()*0.9, Math.max(-AW(), baseX + (x - x0)));
    line.style.transform = 'translateX('+dx+'px)';
    track.style.opacity = dx>0 ? Math.min(1, dx/(W()*0.4)) : 0;
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    line.classList.add('snap');
    if(!moved){
      if(baseX !== 0){ baseX = 0; line.style.transform='translateX(0)'; }
      track.style.opacity = 0;
      return;
    }
    if(dx > W()*0.42){
      if(t.variablePrompt){ line.style.transform='translateX(0)'; track.style.opacity=0; openVal(t, row, line); }
      else fly(row, line, t);
      return;
    }
    baseX = (dx < -AW()*0.4) ? -AW() : 0;
    line.style.transform = 'translateX('+baseX+'px)';
    track.style.opacity = 0;
  };
  line.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  line.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  line.addEventListener('touchend', end);
  line.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
  return { close(){ baseX = 0; line.classList.add('snap'); line.style.transform = 'translateX(0)'; } };
}

function collapseRow(row){
  row.style.height = row.offsetHeight+'px';
  requestAnimationFrame(()=>{ row.style.transition='.2s'; row.style.height='0'; row.style.opacity='0'; });
  setTimeout(()=>{ row.remove();
    if(!document.querySelector('.row')) list.innerHTML='<div class="empty">🎉 nothing left for today</div>';
  }, 210);
}

function fly(row, line, t){
  line.style.transform = 'translateX('+(row.offsetWidth+40)+'px)';
  setTimeout(()=>collapseRow(row), 170);
  commit(t);
}

async function runStart(t, panel){
  panel.close();
  try {
    const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({content:t.raw})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'start failed', true); return; }
    toast('▶ '+d.clean+(d.project?' → '+d.project:''));
  } catch(e){ toast('offline · not started', true); }
}

// Unlike start (task stays), delaying a day/block hides the task until its
// delayed time — the row is removed, same as completing it.
async function runDelay(kind, row, t, panel){
  panel.close();
  try {
    const url = kind==='day' ? '/api/delay-day' : '/api/delay-block';
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'delay failed', true); return; }
    toast(kind==='day' ? ('🗓 → '+(d.target_date||'tomorrow'))
                        : ('⏰ → '+String(d.hour).padStart(2,'0')+':00'));
    collapseRow(row);
  } catch(e){ toast('offline · not delayed', true); }
}

// ⏰ picker sheet: delay to any remaining 地支 block today, or +10m/30m/1h —
// mirrors terminal dtd's ctrl-v block-snooze picker (previously the web
// menu could only delay to the immediate next block; user request
// 2026-09-07). Options are fetched fresh from /api/delay-options each time
// the sheet opens rather than baked into the task list, so the remaining-
// block set is never stale from an earlier page load.
const delayWrap = document.getElementById('delayWrap');
const delayLabel = document.getElementById('delayLabel');
const delayOptions = document.getElementById('delayOptions');
let delayCtx = null;

function minuteLabel(m){ return m<60 ? ('+'+m+'m') : ('+'+(m/60)+'h'); }

function openDelayMenu(t, row, panel){
  panel.close();
  delayCtx = {t, row};
  delayLabel.textContent = 'delay "'+t.title+'" until…';
  delayOptions.innerHTML = '<div class="loading">loading…</div>';
  document.getElementById('delayFreeform').value = '';
  delayWrap.classList.add('show');
  fetch('/api/delay-options').then(r=>r.json()).then(d=>{
    if(!delayCtx) return;  // sheet closed while the fetch was in flight
    if(!d.ok){ delayOptions.innerHTML=''; toast(d.error||'could not load options', true); return; }
    delayOptions.innerHTML = '';
    for(const b of d.blocks){
      const btn = document.createElement('button');
      btn.className = 'delayBtn';
      btn.textContent = b.glyph+' '+String(b.hour).padStart(2,'0')+':00';
      btn.onclick = ()=> submitDelayHour(b.hour);
      delayOptions.appendChild(btn);
    }
    for(const m of d.minutes){
      const btn = document.createElement('button');
      btn.className = 'delayBtn';
      btn.textContent = minuteLabel(m);
      btn.onclick = ()=> submitDelayMinutes(m);
      delayOptions.appendChild(btn);
    }
    // ↻ 0: the CLI schedule screen's top row (defer-fast 'auto') — a
    // recurring task skips to its next occurrence with no dated duplicate
    // (subsumes the old recurring-only "⏭ skip to next"), anything else
    // defers one day. Always rendered, like the CLI's.
    const zero = document.createElement('button');
    zero.className = 'delayBtn skipBtn';
    zero.textContent = delayCtx.t.recurring ? '↻ 0 · skip to next occurrence'
                                            : '↻ 0 · tomorrow';
    zero.onclick = ()=> submitDelayAuto();
    delayOptions.appendChild(zero);
  }).catch(()=>{ if(delayCtx) { delayOptions.innerHTML=''; toast('offline · could not load', true); } });
}

async function submitFreeform(){
  if(!delayCtx) return;
  const {t, row} = delayCtx;
  const text = document.getElementById('delayFreeform').value.trim();
  if(!text) return;
  closeDelayMenu();
  try {
    const r = await fetch('/api/delay-freeform', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, text})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'delay failed', true); return; }
    toast('⏰ → '+(d.until||text));
    collapseRow(row);
  } catch(e){ toast('offline · not delayed', true); }
}

async function submitDelayAuto(){
  if(!delayCtx) return;
  const {t, row} = delayCtx;
  closeDelayMenu();
  try {
    const r = await fetch('/api/delay-auto', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'delay failed', true); return; }
    toast((d.recurring ? '⏭ next ' : '🗓 → ')+(d.date||'tomorrow'));
    collapseRow(row);
  } catch(e){ toast('offline · not delayed', true); }
}

function closeDelayMenu(){ delayWrap.classList.remove('show'); delayCtx = null; }

async function submitDelayHour(hour){
  if(!delayCtx) return;
  const {t, row} = delayCtx;
  closeDelayMenu();
  try {
    const r = await fetch('/api/delay-hour', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, hour})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'delay failed', true); return; }
    toast('⏰ → '+String(hour).padStart(2,'0')+':00');
    collapseRow(row);
  } catch(e){ toast('offline · not delayed', true); }
}

async function submitDelayMinutes(minutes){
  if(!delayCtx) return;
  const {t, row} = delayCtx;
  closeDelayMenu();
  try {
    const r = await fetch('/api/delay-minutes', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, minutes})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'delay failed', true); return; }
    toast('⏰ → '+minuteLabel(minutes));
    collapseRow(row);
  } catch(e){ toast('offline · not delayed', true); }
}

document.getElementById('delayCancel').onclick = closeDelayMenu;
document.getElementById('delayFreeGo').onclick = submitFreeform;
document.getElementById('delayFreeform').addEventListener('keydown', e=>{
  if(e.key==='Enter') submitFreeform();
  if(e.key==='Escape') closeDelayMenu();
});
delayWrap.addEventListener('click', e=>{ if(e.target===delayWrap) closeDelayMenu(); });

async function commit(t){
  total += (t.points||0); count += 1;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
  try {
    const r = await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, content:t.raw})});
    const d = await r.json();
    if(!d.ok){
      total -= (t.points||0);
      document.getElementById('tot').textContent = total;
      toast(d.error || 'saved · Neon write failed', true); return;
    }
    if(d.closed) toast('+'+(t.points||0)+' 分 ✓');
    else if(d.future_skipped) toast('+'+(t.points||0)+' 分 · recurring');
    else toast('+'+(t.points||0)+' 分');
  } catch(e){ toast('offline · not synced', true); }
}

document.getElementById('reload').onclick = ()=> load(true);
const filterInput = document.getElementById('filterInput');
const filterClear = document.getElementById('filterClear');
function applyFilter(){
  const q = filterInput.value;
  filterClear.hidden = !q;
  try { localStorage.setItem('dtd.filter', q); } catch(e){}
  render(filterTasks(allTasks, q));
}
filterInput.addEventListener('input', applyFilter);
filterInput.addEventListener('keydown', e => {
  if(e.key === 'Escape'){ filterInput.value=''; applyFilter(); filterInput.blur(); return; }
  // Refill (wave 2 #1, 2026-10-22): Enter on a query that matches nothing turns
  // the search into a new task — the add sheet opens pre-filled with what was
  // typed, mirroring the terminal dtd box's type-to-add. A query that DOES
  // match just leaves the filtered list (Enter is a no-op there).
  if(e.key === 'Enter'){
    const q = filterInput.value.trim();
    if(q && !filterTasks(allTasks, q).length){ openAdd(q); }
  }
});
filterClear.onclick = ()=> { filterInput.value=''; applyFilter(); };
try { filterInput.value = localStorage.getItem('dtd.filter') || ''; filterClear.hidden = !filterInput.value; } catch(e){}

const addWrap = document.getElementById('addWrap');
const addInput = document.getElementById('addInput');

function openAdd(prefill){
  addWrap.classList.add('show');
  addInput.value = typeof prefill === 'string' ? prefill : '';
  setTimeout(()=>{ addInput.focus(); if(addInput.value) addInput.select(); }, 50);
}
function closeAdd(){ addWrap.classList.remove('show'); addInput.blur(); }

document.getElementById('fab').onclick = ()=> openAdd();
document.getElementById('addCancel').onclick = closeAdd;
addWrap.addEventListener('click', e=>{ if(e.target===addWrap) closeAdd(); });
addInput.addEventListener('keydown', e=>{
  if(e.key==='Enter') submitAdd();
  if(e.key==='Escape') closeAdd();
});
document.getElementById('addGo').onclick = submitAdd;

async function submitAdd(){
  const content = addInput.value.trim();
  if(!content) return;
  closeAdd();
  try {
    const r = await fetch('/api/add', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({content})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'add failed', true); return; }
    toast('+ added');
    load(true);
  } catch(e){ toast('offline · not added', true); }
}

// Variable-input tasks (2026-08-11): terminal dtd prompts for a value on
// completion (e.g. "hiit" -> minutes) and appends it to the name before
// routing to did-fast, which credits the task's own 0n column. Swipe-right
// past the complete threshold opens this instead of flying immediately when
// t.variablePrompt is set (see build_tasks()'s VARIABLE_INPUT map).
const valWrap = document.getElementById('valWrap');
const valInput = document.getElementById('valInput');
const valLabel = document.getElementById('valLabel');
let valCtx = null;

function openVal(t, row, line){
  valCtx = {t, row, line};
  valLabel.textContent = t.variablePrompt;
  valInput.value = '';
  valWrap.classList.add('show');
  setTimeout(()=>valInput.focus(), 50);
}
// Cancel (tap outside): abort the completion entirely, snap the row back —
// distinct from "skip", which still completes with no value (matches
// terminal dtd's documented "blank -> completes with no score").
function cancelVal(){
  if(!valCtx) return;
  const {line} = valCtx;
  valWrap.classList.remove('show'); valInput.blur(); valCtx = null;
  line.classList.add('snap'); line.style.transform = 'translateX(0)';
}
function submitVal(skip){
  if(!valCtx) return;
  const {t, row, line} = valCtx;
  const v = skip ? '' : valInput.value.replace(/[^0-9]/g, '').trim();
  valWrap.classList.remove('show'); valInput.blur(); valCtx = null;
  const pts = (v && t.variableRate) ? Math.round(Number(v) * t.variableRate) : t.points;
  fly(row, line, {...t, raw: v ? (t.title + ' ' + v) : t.title, points: pts});
}

document.getElementById('valSkip').onclick = ()=> submitVal(true);
document.getElementById('valGo').onclick = ()=> submitVal(false);
valWrap.addEventListener('click', e=>{ if(e.target===valWrap) cancelVal(); });
valInput.addEventListener('keydown', e=>{
  if(e.key==='Enter') submitVal(false);
  if(e.key==='Escape') cancelVal();
});

// ✏️ edit sheet (swipe-left → edit): mobile port of terminal dtd's ctrl-g
// one-screen editor. Same editable fields — title, domain, points [N],
// description — fetched fresh via /api/edit-get (description isn't carried in
// the task list) and submitted to /api/edit, which diffs against the prefill
// and applies only what changed. (time) and {bonus} tags are left untouched.
const editWrap = document.getElementById('editWrap');
const editName = document.getElementById('editName');
const editDomain = document.getElementById('editDomain');
const editPoints = document.getElementById('editPoints');
const editDesc = document.getElementById('editDesc');
let editCtx = null;

function closeEdit(){ editWrap.classList.remove('show'); editCtx = null; }

function openEditMenu(t, panel){
  panel.close();
  editCtx = {t};
  editName.value = t.title || '';
  editPoints.value = t.points || '';
  editDesc.value = '';
  editDomain.innerHTML = '<option value="">(loading…)</option>';
  document.getElementById('editLabel').textContent = 'edit "'+t.title+'"';
  editWrap.classList.add('show');
  fetch('/api/edit-get?id='+encodeURIComponent(t.id)).then(r=>r.json()).then(d=>{
    if(!editCtx || editCtx.t.id !== t.id) return;  // sheet closed/reopened mid-fetch
    if(!d.ok){ toast(d.error||'could not load task', true); closeEdit(); return; }
    editName.value = d.name || '';
    editPoints.value = d.points || '';
    editDesc.value = d.desc || '';
    const doms = (d.domains && d.domains.length) ? d.domains.slice() : [];
    if(d.domain && !doms.includes(d.domain)) doms.unshift(d.domain);
    editDomain.innerHTML = '<option value="">(no domain)</option>' +
      doms.map(x=>'<option'+(x===d.domain?' selected':'')+'>'+x+'</option>').join('');
  }).catch(()=>{ toast('offline · could not load', true); closeEdit(); });
}

async function submitEdit(){
  if(!editCtx) return;
  const id = editCtx.t.id;
  const payload = { id,
    name: editName.value.trim(),
    domain: editDomain.value,
    points: editPoints.value.replace(/[^0-9]/g,'').trim(),
    desc: editDesc.value };
  closeEdit();
  try {
    const r = await fetch('/api/edit', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'edit failed', true); return; }
    toast('✏️ saved');
    load(true);
  } catch(e){ toast('offline · not saved', true); }
}

document.getElementById('editCancel').onclick = closeEdit;
document.getElementById('editGo').onclick = submitEdit;
editWrap.addEventListener('click', e=>{ if(e.target===editWrap) closeEdit(); });

load(false);
</script>
</body>
</html>"""

_SRC_MTIME = os.path.getmtime(__file__)

def _watch_own_source():
    """Exit when dtd.py changes on disk so launchd (KeepAlive=true) relaunches
    a FRESH process — the durable fix for the recurring "server is stale" bug
    (2026-09-19 and 2026-09-27: the long-running server kept executing days-old
    in-memory code after Syncthing updated dtd.py, so delay-hiding/UI fixes on
    disk silently never ran until a manual kickstart). launchd WatchPaths alone
    can't do this: it (re)starts a job that's DOWN, but never restarts one
    that's already running. A source-mtime self-check + os._exit is the
    reliable way to guarantee the live server always matches the file. Daemon
    thread, 5s poll; os._exit(0) so KeepAlive treats it as a clean relaunch
    (ThrottleInterval=10 caps the churn if a sync rewrites repeatedly)."""
    while True:
        time.sleep(5)
        try:
            if os.path.getmtime(__file__) != _SRC_MTIME:
                print("dtd.py changed on disk — exiting for launchd relaunch",
                      file=sys.stderr, flush=True)
                os._exit(0)
        except OSError:
            pass  # transient stat failure mid-sync — try again next tick

if __name__ == "__main__":
    threading.Thread(target=_watch_own_source, daemon=True).start()
    # threaded=True: Werkzeug's dev server is single-threaded by default, so
    # a single slow request (a force=True refresh, or /api/done's did-fast
    # subprocess) blocked every OTHER client's request behind it too —
    # contributing to "dtd web hangs on loading" across tabs/devices even
    # after the staleness-triggered refresh above stopped blocking on its own.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
