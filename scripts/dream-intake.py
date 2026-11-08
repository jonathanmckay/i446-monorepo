#!/usr/bin/env python3
"""dream-intake: Gather context from ~15 sources into a single JSON snapshot.

Used by dream-launch.sh to feed the daily briefing agent. Runs all sources
concurrently with a 30s per-source timeout, targeting <60s total (<6s typical).
"""

import argparse
import base64
import datetime
import glob as glob_mod
import json
import os
import re
import shutil
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_SOURCES: dict[str, dict] = {}


def source(name: str, desc: str):
    """Decorator to register a source function."""
    def decorator(fn):
        _SOURCES[name] = {"fn": fn, "desc": desc}
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

_CREDS: dict = {}


def load_credentials():
    global _CREDS
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        with open(claude_json) as f:
            cfg = json.load(f)
        servers = cfg.get("mcpServers", {})

        # Toggl
        toggl_env = servers.get("toggl_server", {}).get("env", {})
        _CREDS["toggl_api_key"] = toggl_env.get("TOGGL_API_KEY", "")
        _CREDS["toggl_workspace_id"] = toggl_env.get("TOGGL_WORKSPACE_ID", "")

        # AppFolio
        af_env = servers.get("appfolio-mcp", {}).get("env", {})
        _CREDS["af_vhost"] = af_env.get("VHOST", "")
        _CREDS["af_username"] = af_env.get("USERNAME", "")
        _CREDS["af_password"] = af_env.get("PASSWORD", "")

    # Gmail OAuth
    eml_dir = Path.home() / ".config" / "eml"
    # Same account table as tools/ibx/ibx.py. tokens.json is mckay@m5c7.com
    # (m5x2/m5c7 aliases land there); tokens-gmail.json is the personal
    # jonathan.b.mckay@gmail.com.
    _CREDS["gmail_accounts"] = [
        {"name": "m5c7", "tokens": eml_dir / "tokens.json",
         "secrets": eml_dir / "gcp-oauth.keys.json"},
        {"name": "jbm", "tokens": eml_dir / "tokens-gmail.json",
         "secrets": eml_dir / "gcp-oauth-gmail.keys.json"},
    ]

    # Google Calendar OAuth
    gcal_dir = Path.home() / ".config" / "google-calendar-mcp"
    _CREDS["gcal_tokens_path"] = gcal_dir / "tokens.json"
    _CREDS["gcal_secrets_path"] = gcal_dir / "gcp-oauth.keys.json"

    # Syncthing
    st_config = (
        Path.home() / "Library" / "Application Support" / "Syncthing" / "config.xml"
    )
    _CREDS["syncthing_config_path"] = st_config


def _toggl_auth():
    key = _CREDS.get("toggl_api_key", "")
    return "Basic " + base64.b64encode(f"{key}:api_token".encode()).decode()


def _af_auth():
    u = _CREDS.get("af_username", "")
    p = _CREDS.get("af_password", "")
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


def _http_request(url, method="GET", headers=None, body=None, timeout=25):
    """Simple HTTP helper using urllib (no requests dependency)."""
    data = None
    if body is not None:
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError(f"Non-JSON response ({len(raw)} bytes) from {url}")


# ---------------------------------------------------------------------------
# Google OAuth helper
# ---------------------------------------------------------------------------

def _build_google_creds(tokens_path, secrets_path, scopes=None, account_key=None):
    """Build google.oauth2.credentials.Credentials from on-disk tokens."""
    from google.oauth2.credentials import Credentials

    with open(tokens_path) as f:
        tokens_data = json.load(f)

    # Tokens file may be a dict keyed by account name, or a flat credential dict
    if account_key and account_key in tokens_data:
        token_info = tokens_data[account_key]
    elif isinstance(tokens_data, dict) and "access_token" not in tokens_data:
        # Try known keys
        for try_key in (account_key, "m5c7", "jbm"):
            if try_key and try_key in tokens_data:
                token_info = tokens_data[try_key]
                break
        else:
            # Use first key
            token_info = tokens_data[next(iter(tokens_data))]
    else:
        token_info = tokens_data

    # Load client secrets
    with open(secrets_path) as f:
        secrets = json.load(f)
    client_cfg = secrets.get("installed", secrets.get("web", {}))

    # Don't pass scopes to avoid RefreshError when the token was authorized
    # with different scopes than what we request here
    creds = Credentials(
        token=token_info.get("access_token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=client_cfg.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_info.get("client_id", client_cfg.get("client_id")),
        client_secret=token_info.get("client_secret", client_cfg.get("client_secret")),
    )
    return creds


def _google_service(service_name, version, creds):
    """Build a Google API service client."""
    from googleapiclient.discovery import build
    return build(service_name, version, credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Todoist + Pacific-time helpers
# ---------------------------------------------------------------------------

def _pt_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/Los_Angeles")


def _pt_today():
    return datetime.datetime.now(_pt_tz()).date()


def _pt_midnight(day):
    """Aware datetime for 00:00 America/Los_Angeles on `day` (DST-correct)."""
    return datetime.datetime.combine(day, datetime.time(0), tzinfo=_pt_tz())


def _to_pt(iso):
    """Parse an ISO timestamp (Z/offset/naive-UTC) to an aware PT datetime."""
    if not iso:
        return None
    s = iso.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        # Python 3.9 fromisoformat rejects 6+ digit fractions with offsets oddly; trim
        m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(.*)", s)
        if not m:
            return None
        dt = datetime.datetime.fromisoformat(m.group(1) + (m.group(3) or ""))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(_pt_tz())


def _utc_z(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_TODOIST_BASE = "https://api.todoist.com/api/v1"


def _todoist_token():
    p = Path.home() / ".config" / "todoist" / "token"
    tok = p.read_text().strip() if p.exists() else ""
    if not tok:
        raise RuntimeError(f"Todoist token missing at {p}")
    return tok


def _todoist_get(path):
    return _http_request(
        f"{_TODOIST_BASE}{path}",
        headers={"Authorization": f"Bearer {_todoist_token()}"},
        timeout=20,
    )


def _todoist_completed_between(since_dt, until_dt, max_pages=5):
    """Non-recurring completions (Todoist's completed ledger). Recurring tasks
    never land here: completing one just reschedules it."""
    items, cursor = [], None
    for _ in range(max_pages):
        q = f"since={_utc_z(since_dt)}&until={_utc_z(until_dt)}&limit=200"
        if cursor:
            q += f"&cursor={cursor}"
        raw = _todoist_get(f"/tasks/completed/by_completion_date?{q}")
        items.extend(raw.get("items", []))
        cursor = raw.get("next_cursor")
        if not cursor:
            break
    return items


def _todoist_completion_events(since_dt, max_pages=5):
    """Activity-log completion events since `since_dt` (includes recurring)."""
    since = _utc_z(since_dt)
    events, cursor = [], None
    for _ in range(max_pages):
        q = "object_type=item&event_type=completed&limit=100"
        if cursor:
            q += f"&cursor={cursor}"
        raw = _todoist_get(f"/activities?{q}")
        batch = raw.get("results", []) if isinstance(raw, dict) else raw
        events.extend(batch)
        if not batch or min(e.get("event_date", "") for e in batch) < since:
            break
        cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
        if not cursor:
            break
    return [e for e in events if e.get("event_date", "") >= since]


_POINTS_RE = re.compile(r"\[(\d+)\]")


def _parse_points(content):
    """[N] 分 value from task content; last bracket wins (matches /did)."""
    m = _POINTS_RE.findall(content or "")
    return int(m[-1]) if m else None


# ---------------------------------------------------------------------------
# Source 1: completed_today
# ---------------------------------------------------------------------------

def _completed_today_file():
    """The /did-maintained names list. It is only rewritten on the first
    completion of a day, so at 04:00 PT it still holds YESTERDAY's names."""
    import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
    ct_path = _sp.COMPLETED_TODAY
    if not ct_path.exists():
        return None, None, None
    with open(ct_path) as f:
        data = json.load(f)
    names = data if isinstance(data, list) else data.get("names", data.get("tasks", data.get("items", [])))
    file_date = data.get("date") if isinstance(data, dict) else None
    age_hours = (time.time() - ct_path.stat().st_mtime) / 3600
    return names, file_date, round(age_hours, 1)


@source("completed_today", "Today's (PT) completed tasks, live from the Todoist activity log")
def _completed_today():
    today = _pt_today()
    out = {"date": today.isoformat()}
    try:
        file_names, file_date, file_age = _completed_today_file()
    except Exception as e:
        file_names, file_date, file_age = None, None, None
        out["file_error"] = f"{type(e).__name__}: {e}"
    out.update({"file_date": file_date, "file_age_hours": file_age})

    try:
        evs = _todoist_completion_events(_pt_midnight(today))
        tasks = []
        for e in sorted(evs, key=lambda e: e.get("event_date", "")):
            ex = e.get("extra_data") or {}
            content = ex.get("content", "")
            tasks.append({
                "id": e.get("object_id"),
                "content": content,
                "completed_at": e.get("event_date"),
                "points": _parse_points(content),
                "recurring": bool(ex.get("is_recurring")),
                "labels": ex.get("labels", []),
            })
        out.update({"source": "todoist_live", "count": len(tasks), "tasks": tasks,
                    "stale": False, "age_hours": 0.0})
        return out
    except Exception as e:
        out["live_error"] = f"{type(e).__name__}: {e}"

    # Fallback: the local file, but only if it is actually today's.
    if file_names is not None and file_date == today.isoformat():
        out.update({"source": "file", "count": len(file_names), "tasks": file_names,
                    "stale": (file_age or 0) > 2, "age_hours": file_age})
    else:
        out.update({"source": "none", "count": 0, "tasks": [], "stale": True,
                    "age_hours": file_age,
                    "note": (f"live Todoist failed; completed-today.json is from {file_date}, "
                             f"not {today.isoformat()} — ignored")})
    return out


# ---------------------------------------------------------------------------
# Source 1b: completed_yesterday_high_value
# ---------------------------------------------------------------------------

HIGH_VALUE_MIN_POINTS = 500
# Created-and-completed-same-day is the "real work that appeared and got done"
# signal. Automation churns out same-day tasks that are not that: 😈 block
# rituals, `deferred:` posthoc eval records, and #-1g/#0g goal tasks.
_NOISE_PREFIXES = ("😈", "deferred:")
_NOISE_LABELS = {"#-1g", "#0g", "posthoc"}


def _is_ritual_noise(content, labels):
    return (content or "").lstrip().startswith(_NOISE_PREFIXES) or bool(_NOISE_LABELS & set(labels or []))


@source("completed_yesterday_high_value",
        "Todoist completions in the last 36h worth >=500 分 or created+completed the same PT day")
def _completed_yesterday_high_value():
    now = datetime.datetime.now(_pt_tz())
    since = now - datetime.timedelta(hours=36)
    tasks, seen = [], set()

    def _add(tid, content, added_at, completed_at, recurring, labels, project_id):
        if tid in seen:
            return
        pts = _parse_points(content)
        a, c = _to_pt(added_at), _to_pt(completed_at)
        same_day = bool(a and c and a.date() == c.date())
        reasons = []
        if pts is not None and pts >= HIGH_VALUE_MIN_POINTS:
            reasons.append(f"points>={HIGH_VALUE_MIN_POINTS}")
        if same_day and not recurring and not _is_ritual_noise(content, labels):
            reasons.append("created_and_completed_same_day")
        if not reasons:
            return
        seen.add(tid)
        tasks.append({
            "id": tid, "content": content, "points": pts,
            "added_at": added_at, "completed_at": completed_at,
            "completed_date_pt": c.date().isoformat() if c else None,
            "recurring": recurring, "labels": labels or [], "project_id": project_id,
            "reasons": reasons,
        })

    for it in _todoist_completed_between(since, now):
        _add(it.get("id"), it.get("content", ""), it.get("added_at"), it.get("completed_at"),
             bool((it.get("due") or {}).get("is_recurring")), it.get("labels"), it.get("project_id"))

    # Recurring high-value completions are invisible to the completed ledger.
    events_error = None
    try:
        for e in _todoist_completion_events(since):
            ex = e.get("extra_data") or {}
            if ex.get("is_recurring"):
                _add(e.get("object_id"), ex.get("content", ""), ex.get("date_added"),
                     e.get("event_date"), True, ex.get("labels"), None)
    except Exception as e:
        events_error = f"{type(e).__name__}: {e}"

    tasks.sort(key=lambda t: t.get("completed_at") or "")
    out = {
        "window_start": since.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds"),
        "min_points": HIGH_VALUE_MIN_POINTS,
        "count": len(tasks),
        "total_points": sum(t["points"] or 0 for t in tasks),
        "tasks": tasks,
    }
    if events_error:
        out["recurring_events_error"] = events_error
    return out


# ---------------------------------------------------------------------------
# Source 2: d357_transcripts
# ---------------------------------------------------------------------------

@source("d357_transcripts", "Today's meeting transcripts and action items")
def _d357_transcripts():
    # Vault transcript filenames use YYYY.MM.DD (dots), not ISO YYYY-MM-DD (hyphens)
    today = datetime.date.today().strftime("%Y.%m.%d")
    d357_root = Path.home() / "vault" / "d357"
    pattern = str(d357_root / "**" / f"{today}*.md")
    files = glob_mod.glob(pattern, recursive=True)

    results = []
    for fp in files:
        with open(fp) as f:
            content = f.read()
        # Extract action items (unchecked checkboxes)
        actions = re.findall(r"^- \[ \] (.+)$", content, re.MULTILINE)
        results.append({
            "file": os.path.basename(fp),
            "path": fp,
            "action_items": actions,
            "size_bytes": len(content),
        })

    return {
        "count": len(results),
        "transcripts": results,
        "total_action_items": sum(len(r["action_items"]) for r in results),
    }


# ---------------------------------------------------------------------------
# Source 3: toggl_today
# ---------------------------------------------------------------------------

@source("toggl_today", "Today's Toggl time entries grouped by project")
def _toggl_today():
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    # Import toggl_api from the MCP server module
    toggl_path = Path.home() / "i446-monorepo" / "mcp"
    if str(toggl_path) not in sys.path:
        sys.path.insert(0, str(toggl_path))

    # Set env vars for toggl_api
    os.environ.setdefault("TOGGL_API_KEY", _CREDS.get("toggl_api_key", ""))
    os.environ.setdefault("TOGGL_WORKSPACE_ID", _CREDS.get("toggl_workspace_id", ""))

    from toggl_server import toggl_api
    entries = toggl_api.get_entries(start_date=today, end_date=tomorrow) or []

    # Group by project
    by_project: dict[str, list] = {}
    total_seconds = 0
    for e in entries:
        pid = e.get("project_id") or "no_project"
        by_project.setdefault(str(pid), []).append({
            "description": e.get("description", ""),
            "duration_sec": e.get("duration", 0),
            "start": e.get("start", ""),
            "stop": e.get("stop", ""),
        })
        dur = e.get("duration", 0)
        if dur > 0:
            total_seconds += dur

    return {
        "entry_count": len(entries),
        "total_hours": round(total_seconds / 3600, 2),
        "by_project": by_project,
    }


# ---------------------------------------------------------------------------
# Source 4: appfolio_rent_roll
# ---------------------------------------------------------------------------

# Properties excluded from core portfolio occupancy metric
_AF_EXCLUDE = {
    "Escrow Checking Corp Prop", "Funds to Be Received", "R202 Corp Prop",
    "l925", "l912 (Community Association)", "c616", "h604", "sf21", "w225",
}


def _parse_money(x):
    """Parse an AppFolio money string ('1,025.00', '$930') to float, else None."""
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


def _appfolio_avg_rent(vhost: str, as_of: str) -> dict:
    """Mean in-place rent per property code from the plain rent_roll report.

    Returns {property_code: avg_rent_int}. Occupied units only: a tenant is on
    the lease and Rent > 0. The rent_roll report is served on the v1 API and
    only answers to GET (POST 404s), unlike the v2 occupancy report. Any error
    yields an empty dict so the caller still emits occupancy data."""
    url = (
        f"https://{vhost}.appfolio.com/api/v1/reports/rent_roll.json"
        f"?as_of_date={as_of}"
    )
    try:
        data = _http_request(url, method="GET", headers={"Authorization": _af_auth()})
    except Exception:
        return {}  # leave avg_rent as None; occupancy data still emits

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in data.get("results", []):
        if not (row.get("Tenant") or "").strip():
            continue  # vacant unit — exclude from in-place average
        rent = _parse_money(row.get("Rent"))
        if rent is None or rent <= 0:
            continue
        prop = str(row.get("Property", "Unknown")).split(" - ")[0].strip()
        sums[prop] = sums.get(prop, 0.0) + rent
        counts[prop] = counts.get(prop, 0) + 1
    return {prop: round(sums[prop] / counts[prop]) for prop in sums}


@source("appfolio_rent_roll", "AppFolio occupancy + in-place avg rent per property")
def _appfolio_rent_roll():
    vhost = _CREDS.get("af_vhost", "")
    if not vhost:
        return {"error": "AppFolio credentials not configured"}

    url = f"https://{vhost}.appfolio.com/api/v2/reports/occupancy_summary.json"
    today = datetime.date.today().isoformat()
    body = {"as_of_to": today}
    headers = {
        "Authorization": _af_auth(),
        "Content-Type": "application/json",
    }

    data = _http_request(url, method="POST", headers=headers, body=body)
    results = data.get("results", [])

    # Group by property (results are per unit-type, need aggregation)
    by_property: dict[str, dict] = {}
    for row in results:
        prop_full = row.get("property", "Unknown")
        prop = prop_full.split(" - ")[0].strip()
        if prop not in by_property:
            by_property[prop] = {
                "units": 0, "occupied": 0, "vacant_rented": 0,
                "vacant_unrented": 0, "notice_rented": 0, "notice_unrented": 0,
            }
        p = by_property[prop]
        p["units"] += row.get("number_of_units", 0)
        p["occupied"] += row.get("occupied", 0)
        p["vacant_rented"] += row.get("vacant_rented", 0)
        p["vacant_unrented"] += row.get("vacant_unrented", 0)
        p["notice_rented"] += row.get("notice_rented", 0)
        p["notice_unrented"] += row.get("notice_unrented", 0)

    # Per-property in-place average rent, from the rent roll's authoritative
    # `Rent` column (actual base rent of the current lease). We use the plain
    # rent_roll report, NOT rent_roll_itemized: the itemized report has no clean
    # rent field — `total - monthly_charges` only matches the real Rent ~70% of
    # the time (concessions/credits skew it). avg_rent is the mean over occupied
    # units only (a tenant on the lease and Rent > 0), so vacants don't drag it
    # down. A rent-roll failure must not break the occupancy output.
    avg_rent_by_prop = _appfolio_avg_rent(vhost, today)
    for prop, p in by_property.items():
        p["avg_rent"] = avg_rent_by_prop.get(prop)  # None = no occupied units / unavailable

    # Compute per-property metrics
    for prop, p in by_property.items():
        p["rented"] = p["occupied"] + p["vacant_rented"]
        p["occupancy_pct"] = round(p["occupied"] / p["units"] * 100, 1) if p["units"] else 0
        p["rented_pct"] = round(p["rented"] / p["units"] * 100, 1) if p["units"] else 0

    # Core portfolio (exclude non-operational properties)
    core = {k: v for k, v in by_property.items() if k not in _AF_EXCLUDE}
    core_units = sum(p["units"] for p in core.values())
    core_occupied = sum(p["occupied"] for p in core.values())
    core_rented = sum(p["rented"] for p in core.values())
    core_vu = sum(p["vacant_unrented"] for p in core.values())

    # All properties
    all_units = sum(p["units"] for p in by_property.values())
    all_occupied = sum(p["occupied"] for p in by_property.values())

    return {
        "as_of": today,
        "all_units": all_units,
        "all_occupied": all_occupied,
        "all_occupancy_pct": round(all_occupied / all_units * 100, 1) if all_units else 0,
        "core_units": core_units,
        "core_occupied": core_occupied,
        "core_occupancy_pct": round(core_occupied / core_units * 100, 1) if core_units else 0,
        "core_rented": core_rented,
        "core_rented_pct": round(core_rented / core_units * 100, 1) if core_units else 0,
        "core_vacant_unrented": core_vu,
        "excluded": sorted(_AF_EXCLUDE),
        "by_property": by_property,
    }


# ---------------------------------------------------------------------------
# Source 5: appfolio_work_orders
# ---------------------------------------------------------------------------

# AppFolio's work_order report status enum. A WO counts as "open" unless it is
# in a terminal (closed) state. See _appfolio_work_orders for the full history.
_WO_CLOSED_STATUSES = {"completed", "complete", "completed no need to bill", "work done",
                       "canceled", "cancelled", "closed"}


def _wo_is_open(status: str) -> bool:
    """True if an AppFolio work-order status represents an open WO.

    The open statuses (New/Scheduled/Assigned/Estimated/In Progress/On Hold)
    share no common substring, so detection is by exclusion of the closed set.
    """
    s = (status or "").strip().lower()
    return bool(s) and s not in _WO_CLOSED_STATUSES


@source("appfolio_work_orders", "AppFolio open/overdue work orders by property")
def _appfolio_work_orders():
    vhost = _CREDS.get("af_vhost", "")
    if not vhost:
        return {"error": "AppFolio credentials not configured"}

    url = f"https://{vhost}.appfolio.com/api/v2/reports/work_order.json"
    headers = {
        "Authorization": _af_auth(),
        "Content-Type": "application/json",
    }

    # AppFolio's default work_order report omits some statuses (e.g. Waiting),
    # and its statuses are New/Assigned/Scheduled/Waiting/Estimated/Work Done/
    # Completed/Canceled -- none contain "open", so the old substring test
    # always reported 0 open. Ask for every status (0-9) over a 60-day window (120 days hit the 5,000-row page cap)
    # and treat anything not closed as open.
    today = datetime.date.today()
    body = {
        "work_order_statuses": [str(i) for i in range(10)],
        "status_date": "created_at",
        "status_date_range_from": (today - datetime.timedelta(days=60)).isoformat(),
        "status_date_range_to": today.isoformat(),
    }
    data = _http_request(url, method="POST", headers=headers, body=body)
    results = data.get("results", [])

    by_property: dict[str, dict] = {}
    open_by_status: dict[str, int] = {}
    urgent_open = []
    for row in results:
        prop = row.get("property_name", row.get("property", "Unknown"))
        if prop not in by_property:
            by_property[prop] = {"open": 0, "overdue": 0, "total": 0}
        p = by_property[prop]
        p["total"] += 1

        status = (row.get("status", "") or "").strip()
        if not _wo_is_open(status):
            continue
        p["open"] += 1
        open_by_status[status] = open_by_status.get(status, 0) + 1
        # "overdue" = open and either scheduled in the past or created > 7 days ago
        ref = row.get("scheduled_start") or row.get("created_at") or ""
        try:
            ref_date = datetime.date.fromisoformat(str(ref)[:10])
            if (row.get("scheduled_start") and ref_date < today) or (
                not row.get("scheduled_start") and (today - ref_date).days > 7
            ):
                p["overdue"] += 1
        except ValueError:
            pass
        if (row.get("priority", "") or "").lower() in ("urgent", "emergency"):
            urgent_open.append({
                "property": prop,
                "unit": row.get("unit_name") or row.get("unit") or "",
                "status": status,
                "summary": (row.get("job_description") or row.get("description") or "")[:80],
                "created": str(row.get("created_at", ""))[:10],
            })

    total_open = sum(p["open"] for p in by_property.values())
    total_overdue = sum(p["overdue"] for p in by_property.values())

    return {
        "total_work_orders": len(results),
        "total_open": total_open,
        "total_overdue": total_overdue,
        "open_by_status": open_by_status,
        "truncated": len(results) >= 5000,
        "urgent_open": urgent_open[:40],
        "by_property": by_property,
    }


# ---------------------------------------------------------------------------
# Source 6: gmail_inbox
# ---------------------------------------------------------------------------

# Bug (v176): the source read only tokens-gmail.json, which is the personal
# jonathan.b.mckay@gmail.com, and reported its 2 unread as "Gmail unread" while
# mckay@m5c7.com (where all m5x2 mail lands) had 19 non-promo unread. Now
# every account in _CREDS["gmail_accounts"]; Promotions excluded; counts come
# from paginated ids, not the 50-message metadata cap.
GMAIL_QUERY = "is:unread in:inbox -category:promotions"
GMAIL_METADATA_CAP = 30  # per account


def _gmail_account_unread(service, query=GMAIL_QUERY, cap=GMAIL_METADATA_CAP):
    ids, page = [], None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page or len(ids) >= 500:
            break
    now = time.time()
    results = []
    for mid in ids[:cap]:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        internal_ts = int(msg.get("internalDate", "0")) / 1000
        age_hours = (now - internal_ts) / 3600 if internal_ts else 0
        results.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "age_hours": round(age_hours, 1),
            "stale": age_hours > 24,
        })
    return len(ids), results


@source("gmail_inbox", "Unread non-promo Gmail, every account (m5c7 + jbm), with age flags")
def _gmail_inbox():
    by_account, messages, errors = {}, [], {}
    for acct in _CREDS.get("gmail_accounts", []):
        try:
            creds = _build_google_creds(acct["tokens"], acct["secrets"])
            service = _google_service("gmail", "v1", creds)
            count, msgs = _gmail_account_unread(service)
        except Exception as e:
            errors[acct["name"]] = f"{type(e).__name__}: {e}"
            continue
        by_account[acct["name"]] = {
            "unread_count": count,
            "stale_count": sum(1 for m in msgs if m["stale"]),
            "listed": len(msgs),
        }
        messages += [{**m, "account": acct["name"]} for m in msgs]
    if not by_account:
        raise RuntimeError(f"all Gmail accounts failed: {errors}")
    out = {
        "query": GMAIL_QUERY,
        "unread_count": sum(a["unread_count"] for a in by_account.values()),
        "stale_count": sum(1 for m in messages if m["stale"]),
        "by_account": by_account,
        "messages": messages,
    }
    if errors:
        out["errors"] = errors
    return out


# ---------------------------------------------------------------------------
# Source 7: google_calendar
# ---------------------------------------------------------------------------

# Bug (v176): the source queried only the m5c7 "primary" calendar (plus a
# "work" one that does not exist) with a UTC window "today T00:00Z" ..
# "+2d T00:00Z", i.e. 16:00 PST yesterday onward, so Sunday-evening events
# were labelled "today" on Monday and jbm / m5x2 Cal / 3494 House / CAIS
# School / 家 Meals were missing. Now delegates to janus's gcal_client: every
# calendar on the m5c7 account (LX's filtered, same as janus), local-midnight
# windows from lib/daytime, and it shares janus's 5-min cache
# (~/.cache/janus/gcal-YYYY-MM-DD.json), which is also the fallback.
_TG_DIR = Path(__file__).resolve().parent.parent / "tools" / "tg"
_IMPORT_CAL_SUFFIX = "@import.calendar.google.com"


def _gcal_client():
    if str(_TG_DIR) not in sys.path:
        sys.path.insert(0, str(_TG_DIR))
    import gcal_client
    return gcal_client


def _gcal_day(gc, day_start, day_end):
    try:
        return gc.list_events(day_start, day_end), "live-or-janus-cache"
    except Exception as e:
        cache = gc.CACHE_DIR / f"gcal-{day_start.date().isoformat()}.json"
        if not cache.exists():
            raise
        return gc._hydrate(json.loads(cache.read_text()), day_start, day_end), \
            f"janus cache fallback ({type(e).__name__})"


def _gcal_slim(ev):
    cal = ev.get("calendar", "")
    if (ev.get("calendar_id") or "").endswith(_IMPORT_CAL_SUFFIX):
        cal = f"{cal} (Outlook ICS mirror)"
    return {
        "calendar": cal,
        "summary": ev.get("title", "(no title)"),
        "start": ev["start_dt"].isoformat(),
        "end": ev["end_dt"].isoformat(),
        "all_day": ev.get("all_day", False),
        "transparency": ev.get("transparency", "opaque"),
    }


@source("google_calendar", "Today + tomorrow events, all JM calendars (local-day windows)")
def _google_calendar():
    gc = _gcal_client()
    tz = gc._tz()
    d0 = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out, via = {}, {}
    for label, off in (("today", 0), ("tomorrow", 1)):
        # Midnight-to-midnight in the active zone, built from the date so a
        # DST changeover day is 23/25h, not a fixed 24h offset.
        day = (d0 + datetime.timedelta(days=off)).date()
        start = datetime.datetime.combine(day, datetime.time(), tz)
        end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(), tz)
        evs, via[label] = _gcal_day(gc, start, end)
        out[label] = [_gcal_slim(e) for e in evs]
        out[f"{label}_date"] = day.isoformat()
    calendars = {}
    for ev in out["today"] + out["tomorrow"]:
        calendars[ev["calendar"]] = calendars.get(ev["calendar"], 0) + 1
    return {
        "today_date": out["today_date"],
        "tomorrow_date": out["tomorrow_date"],
        "today_count": len(out["today"]),
        "tomorrow_count": len(out["tomorrow"]),
        "calendars": calendars,
        "via": via,
        "today": out["today"],
        "tomorrow": out["tomorrow"],
    }
    if errors:
        out["errors"] = errors
    return out


# ---------------------------------------------------------------------------
# Source 8: syncthing_status
# ---------------------------------------------------------------------------

def _live_conflicts(paths, vault_path):
    """Drop conflict files under vault/z_old/ (archived; v180 counted 132
    conflicts, all there, which drowned any live one)."""
    old = Path(vault_path) / "z_old"
    return [p for p in paths if Path(p) != old and old not in Path(p).parents]


@source("syncthing_status", "Syncthing sync status and conflict files (excluding z_old/)")
def _syncthing_status():
    # Extract API key from config.xml
    config_path = _CREDS.get("syncthing_config_path")
    api_key = ""
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            content = f.read()
        m = re.search(r"<apikey>([^<]+)</apikey>", content)
        if m:
            api_key = m.group(1)

    if not api_key:
        return {"error": "Syncthing API key not found"}

    headers = {"X-API-Key": api_key}
    base = "http://localhost:8384"

    try:
        status = _http_request(f"{base}/rest/system/status", headers=headers)
    except Exception as e:
        return {"error": f"Syncthing not reachable: {e}"}

    try:
        connections = _http_request(f"{base}/rest/system/connections", headers=headers)
    except Exception:
        connections = {}

    # Check for sync conflict files in vault
    vault_path = Path.home() / "vault"
    conflicts = _live_conflicts(
        glob_mod.glob(str(vault_path / "**" / "*.sync-conflict-*"), recursive=True),
        vault_path,
    )

    conn_info = connections.get("connections", {})
    connected_devices = sum(1 for d in conn_info.values() if d.get("connected"))

    return {
        "my_id": status.get("myID", "")[:12] + "...",
        "uptime_seconds": status.get("uptime", 0),
        "connected_devices": connected_devices,
        "total_devices": len(conn_info),
        "conflict_files": len(conflicts),
        "conflict_paths": conflicts[:10],  # Cap at 10
    }


# ---------------------------------------------------------------------------
# Full Disk Access diagnostics (v175)
# ---------------------------------------------------------------------------
# Safari History.db, knowledgeC.db and chat.db are TCC-protected. Under cron
# macOS attributes the read to the *responsible* process (cron), not to
# python3, so granting FDA to python3 alone does nothing for the 03:00 run.
# Every night since 10-25 these three sources returned "authorization denied"
# / PermissionError with no hint of which binary needs the grant.

FDA_SOURCES = ("browser_history", "screen_time", "imessage_recent")
_FDA_MARKERS = ("authorization denied", "operation not permitted", "permissionerror")


def _process_chain(pid=None, limit=12):
    """[(pid, comm)] from this process up to (excluding) launchd."""
    import subprocess
    pid = pid or os.getpid()
    chain = []
    for _ in range(limit):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            break
        if not out:
            break
        ppid, comm = out.split(None, 1)
        chain.append((pid, comm.strip()))
        pid = int(ppid)
        if pid <= 1:
            break
    return chain


def fda_binary(chain=None):
    """The binary that needs Full Disk Access for this run to read TCC DBs."""
    chain = _process_chain() if chain is None else chain
    for _, comm in chain:
        if os.path.basename(comm) == "cron":
            return "/usr/sbin/cron"
    for _, comm in reversed(chain):  # outermost app (Terminal, iTerm, sshd...)
        if ".app/" in comm:
            return comm.split(".app/")[0] + ".app"
    return os.path.realpath(chain[-1][1]) if chain else os.path.realpath(sys.executable)


def _is_fda_denial(text) -> bool:
    t = str(text or "").lower()
    return any(m in t for m in _FDA_MARKERS)


def fda_diagnostic(results):
    """One line naming the binary to grant FDA, or None if nothing was denied."""
    denied = []
    for name in FDA_SOURCES:
        r = results.get(name) or {}
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        if _is_fda_denial(r.get("error")) or _is_fda_denial(data.get("error")):
            denied.append(name)
    if not denied:
        return None
    return (f"FDA: {', '.join(denied)} denied by macOS privacy (TCC). Grant Full Disk "
            f"Access to {fda_binary()} (System Settings > Privacy & Security > "
            f"Full Disk Access), then re-run.")


# ---------------------------------------------------------------------------
# Source 9: browser_history
# ---------------------------------------------------------------------------

@source("browser_history", "Safari browsing history (last 24h)")
def _browser_history():
    src = Path.home() / "Library" / "Safari" / "History.db"
    if not src.exists():
        return {"error": "Safari History.db not found"}

    # Copy to /tmp to avoid lock. A PermissionError here is TCC (no Full Disk
    # Access), not a lock: report it as data so fda_diagnostic can name the
    # binary, instead of a bare traceback string (v175).
    tmp_db = Path("/tmp/dream_safari_history.db")
    try:
        shutil.copy2(src, tmp_db)
    except PermissionError as e:
        return {"error": f"authorization denied: {e}"}

    conn = sqlite3.connect(str(tmp_db))
    try:
        # Core Data epoch: seconds since 2001-01-01
        # Unix epoch offset: 978307200
        epoch_offset = 978307200
        cutoff = time.time() - 86400 - epoch_offset  # 24h ago in Core Data epoch

        cursor = conn.execute("""
            SELECT
                hi.url,
                hv.title,
                hv.visit_time
            FROM history_items hi
            JOIN history_visits hv ON hi.id = hv.history_item
            WHERE hv.visit_time > ?
            ORDER BY hv.visit_time DESC
            LIMIT 200
        """, (cutoff,))

        rows = cursor.fetchall()

        # Filter trivial URLs
        trivial = {"about:blank", "favorites://", "about:newtab"}
        trivial_domains = {"google.com/search", "localhost", "127.0.0.1"}

        results = []
        seen_urls = set()
        for url, title, visit_time in rows:
            if url in trivial or url in seen_urls:
                continue
            skip = False
            for td in trivial_domains:
                if td in url:
                    skip = True
                    break
            if skip:
                continue
            seen_urls.add(url)
            ts = visit_time + epoch_offset
            results.append({
                "url": url,
                "title": title or "",
                "visited_at": datetime.datetime.fromtimestamp(ts).isoformat(),
            })

        return {
            "unique_urls": len(results),
            "history": results[:100],  # Cap output
        }
    finally:
        conn.close()
        tmp_db.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Source 10: screen_time
# ---------------------------------------------------------------------------

@source("screen_time", "App usage from knowledgeC.db (>10min)")
def _screen_time():
    db_path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Knowledge"
        / "knowledgeC.db"
    )
    if not db_path.exists():
        return {"error": "knowledgeC.db not found"}

    try:
        conn = sqlite3.connect(str(db_path))
        # Core Data epoch
        epoch_offset = 978307200
        cutoff = time.time() - 86400 - epoch_offset

        cursor = conn.execute("""
            SELECT
                ZOBJECT.ZVALUESTRING AS app,
                SUM(ZOBJECT.ZENDDATE - ZOBJECT.ZSTARTDATE) AS total_seconds
            FROM ZOBJECT
            WHERE ZSTREAMNAME = '/app/usage'
              AND ZOBJECT.ZSTARTDATE > ?
              AND ZOBJECT.ZVALUESTRING IS NOT NULL
            GROUP BY ZOBJECT.ZVALUESTRING
            HAVING total_seconds > 600
            ORDER BY total_seconds DESC
        """, (cutoff,))

        rows = cursor.fetchall()
        conn.close()

        apps = []
        for app_id, seconds in rows:
            # Clean up bundle ID to readable name
            name = app_id.split(".")[-1] if "." in app_id else app_id
            apps.append({
                "bundle_id": app_id,
                "name": name,
                "minutes": round(seconds / 60, 1),
            })

        return {
            "app_count": len(apps),
            "apps": apps,
            "total_minutes": round(sum(a["minutes"] for a in apps), 1),
        }
    except sqlite3.OperationalError as e:
        return {"error": f"SIP-blocked or DB locked: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Source 11: imessage_recent
# ---------------------------------------------------------------------------

@source("imessage_recent", "Recent iMessage conversations (metadata only, last 24h)")
def _imessage_recent():
    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        return {"error": "chat.db not found"}

    try:
        conn = sqlite3.connect(str(db_path))
        # Apple epoch: nanoseconds since 2001-01-01
        # But in chat.db, message.date is in nanoseconds since 2001-01-01
        epoch_offset_ns = 978307200 * 1_000_000_000
        cutoff_ns = (int(time.time()) - 86400) * 1_000_000_000 - epoch_offset_ns

        cursor = conn.execute("""
            SELECT
                c.chat_identifier,
                c.display_name,
                COUNT(m.rowid) AS msg_count,
                MAX(m.date) AS last_msg_date
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.date > ?
            GROUP BY c.chat_identifier
            ORDER BY last_msg_date DESC
            LIMIT 30
        """, (cutoff_ns,))

        rows = cursor.fetchall()
        conn.close()

        conversations = []
        for chat_id, display_name, count, last_date in rows:
            # Convert last_date back to unix timestamp
            last_ts = (last_date + epoch_offset_ns) / 1_000_000_000
            conversations.append({
                "chat_id": chat_id,
                "display_name": display_name or chat_id,
                "message_count": count,
                "last_message_at": datetime.datetime.fromtimestamp(last_ts).isoformat(),
            })

        return {
            "conversation_count": len(conversations),
            "conversations": conversations,
        }
    except sqlite3.OperationalError as e:
        return {"error": f"DB access denied: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Source 12: slack
# ---------------------------------------------------------------------------

# Outlook sources read Straylight's cache, never the live Agency MCP.
# Ix cron has no work auth (Agency CLI absent; login keychain locked to cron,
# MSAL -61), so the live calls only ever burned the 30s source timeout
# (v51..v176: "outlook_inbox timeout" every night). Straylight's
# tools/tg/outlook-cache-daemon.py writes ~/.cache/janus/outlook-YYYY-MM-DD.json
# (calendar) and outlook-inbox-YYYY-MM-DD.json (unread, last 48h) every 5 min
# and rsyncs them to ix:~/.cache/janus/. Missing file = "missing", instantly.
JANUS_CACHE = Path.home() / ".cache" / "janus"
OUTLOOK_STALE_HOURS = 6


def _local_today() -> datetime.date:
    return datetime.datetime.now().astimezone().date()


def _file_age_hours(path: Path) -> float:
    return round((time.time() - path.stat().st_mtime) / 3600, 1)


@source("outlook_inbox", "Outlook unread emails, last 48h (Straylight cache snapshot)")
def _outlook_inbox():
    today = _local_today()
    # Today's snapshot, else yesterday's (Straylight asleep since evening).
    for day in (today, today - datetime.timedelta(days=1)):
        path = JANUS_CACHE / f"outlook-inbox-{day.isoformat()}.json"
        if not path.exists():
            continue
        try:
            snap = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            return {"_status": "error", "error": f"unreadable {path.name}: {e}"}
        age = _file_age_hours(path)
        msgs = snap.get("messages", [])
        unread = [{
            "subject": m.get("subject", ""),
            "from": m.get("sender", ""),
            "from_email": m.get("sender_email", ""),
            "received": m.get("received", ""),
            "preview": m.get("preview", ""),
            "importance": m.get("importance", "normal"),
            "conversation_id": m.get("conversationId", ""),
        } for m in msgs if not m.get("isRead")]
        return {
            "_status": "stale" if age > OUTLOOK_STALE_HOURS else "ok",
            "cache_file": str(path),
            "cache_age_hours": age,
            "snapshot_at": snap.get("generated_at", ""),
            "window_hours": snap.get("window_hours"),
            "unread": unread,
            "unread_count": len(unread),
            "high_importance": sum(1 for m in unread if m["importance"] == "high"),
        }
    return {
        "_status": "missing",
        "error": (f"no outlook-inbox-{today}.json or previous day in {JANUS_CACHE}; "
                  "Straylight outlook-cache-daemon not running or asleep"),
    }


def _janus_outlook_calendar(today=None):
    today = today or _local_today()
    events, files, ages = [], [], []
    for off in (-1, 0, 1):
        day = today + datetime.timedelta(days=off)
        path = JANUS_CACHE / f"outlook-{day.isoformat()}.json"
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        files.append(path.name)
        ages.append(_file_age_hours(path))
        for ev in raw if isinstance(raw, list) else []:
            events.append({
                "day": day.isoformat(),
                "subject": ev.get("subject", ""),
                "start": ev.get("start", ""),
                "end": ev.get("end", ""),
                "tz": ev.get("start_tz", ""),
                "all_day": ev.get("is_all_day", False),
            })
    if not files:
        return {"_status": "missing",
                "error": f"no outlook-YYYY-MM-DD.json around {today} in {JANUS_CACHE}"}
    today_file = f"outlook-{today.isoformat()}.json"
    stale = today_file not in files or ages[files.index(today_file)] > OUTLOOK_STALE_HOURS
    return {"_status": "stale" if stale else "ok", "cache_files": files,
            "cache_age_hours": max(ages), "events": events, "count": len(events),
            "note": "Graph times; tz field is the Windows zone name (UTC if blank)"}


# v180 (2026-11-06): when Straylight sleeps the janus cache is missing or
# stale (11-06 03:00: today's file was 7 h old), and Dream had no work
# calendar. The Google import calendar "MSFT (Slow Sync)" on the m5c7 account
# mirrors the Outlook calendar (titles + times only) and is reachable from
# cron with the existing calendar token. Fresh cache wins; otherwise the
# import calendar; if that fails too, the stale/missing cache result stands.
MSFT_IMPORT_CAL = "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com"


def _gcal_import_events(today, service=None):
    if service is None:
        creds = _build_google_creds(
            _CREDS["gcal_tokens_path"], _CREDS["gcal_secrets_path"],
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
            account_key="m5c7",
        )
        service = _google_service("calendar", "v3", creds)
    tz = datetime.datetime.now().astimezone().tzinfo
    first = today - datetime.timedelta(days=1)
    t_min = datetime.datetime.combine(first, datetime.time(), tz)
    t_max = datetime.datetime.combine(today + datetime.timedelta(days=2), datetime.time(), tz)
    resp = service.events().list(
        calendarId=MSFT_IMPORT_CAL, timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=100,
    ).execute()
    events = []
    for ev in resp.get("items", []):
        if ev.get("status") == "cancelled":
            continue
        st, en = ev.get("start", {}), ev.get("end", {})
        start = st.get("dateTime", st.get("date", ""))
        events.append({
            "day": start[:10],
            "subject": ev.get("summary", "(no title)"),
            "start": start,
            "end": en.get("dateTime", en.get("date", "")),
            "tz": st.get("timeZone", ""),
            "all_day": "date" in st,
        })
    return events


@source("outlook_calendar", "Outlook calendar, yesterday..tomorrow (Straylight cache, else MSFT Slow Sync import calendar)")
def _outlook_calendar(today=None, cache=None, gcal=None):
    today = today or _local_today()
    cache = cache or _janus_outlook_calendar
    gcal = gcal or _gcal_import_events
    res = cache(today)
    if res.get("_status") == "ok":
        return {**res, "via": "janus_cache"}
    try:
        events = gcal(today)
    except Exception as e:
        return {**res, "via": "janus_cache", "gcal_import_error": f"{type(e).__name__}: {e}"}
    if not events:
        return {**res, "via": "janus_cache", "gcal_import_error": "no events"}
    return {"_status": "ok", "via": "gcal_import", "calendar_id": MSFT_IMPORT_CAL,
            "events": events, "count": len(events),
            "cache_status": res.get("_status"), "cache_error": res.get("error"),
            "note": "MSFT (Slow Sync) import calendar: titles and times only, ISO times with offset"}


@source("slack", "Slack status (stub)")
def _slack():
    return {"status": "not_configured"}


# v182 (2026-11-08): the 03:00 PermissionError on `4g s.xlsx` is NOT Full Disk
# Access. Every vault-excel workbook except the three Excel keeps open (Neon,
# xk887, m5x2/) is a OneDrive Files-On-Demand placeholder (`stat -f %Sf` =
# "compressed,dataless"); non-dataless files in the same folder read fine from
# the cron chain, and neither cron nor a gui/ LaunchAgent may hydrate a
# placeholder (EPERM / EDEADLK). Two fixes: (1) every successful read caches the
# parsed goals, so a later eviction degrades to last-good data instead of the
# narrative-only fallback; (2) `onedrive_dataless` names every evicted workbook.
ANNUAL_4G_CACHE = Path.home() / ".cache" / "dream" / "annual-goals-4g.json"
_PIN_FIX = ("Finder > ~/OneDrive/vault-excel > right-click > Always Keep on This Device "
            "(the 03:00 cron run cannot download OneDrive placeholders)")


def _parse_4g_workbook(path: Path) -> dict:
    import openpyxl  # available in the intake env
    yy = datetime.date.today().strftime("%y")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next((s for s in wb.sheetnames if s.strip().startswith(yy) and "4g" in s),
                     wb.sheetnames[0])
        ws = wb[sheet]
        goals, tldr = [], None
        for row in ws.iter_rows(values_only=True):
            cells = list(row)
            c0 = str(cells[0]).strip() if cells and cells[0] is not None else ""
            c1 = str(cells[1]).strip() if len(cells) > 1 and cells[1] is not None else ""
            blob = (c0 + " " + c1).lower()
            if blob.startswith("tl;dr") or "tl;dr" in blob[:8]:
                tldr = (c1 or c0)
                continue
            # A goal row: a domain/category in col0, prose goal in col1, numeric target 分 in col2.
            if c0 and c1 and len(cells) > 2 and isinstance(cells[2], (int, float)):
                pct = cells[3] if len(cells) > 3 and isinstance(cells[3], (int, float)) else None
                earned = cells[4] if len(cells) > 4 and isinstance(cells[4], (int, float)) else None
                goals.append({
                    "domain": c0,
                    "goal": c1,
                    "target_fen": cells[2],
                    "pct": round(pct, 3) if isinstance(pct, (int, float)) else None,
                    "earned_fen": round(earned) if isinstance(earned, (int, float)) else None,
                })
        return {"sheet": sheet, "tldr": tldr, "goal_count": len(goals), "goals": goals}
    finally:
        wb.close()


def _write_4g_cache(result: dict, path: Path, cache: Path) -> None:
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "cached_at": time.time(),
            "xlsx_mtime": os.stat(path).st_mtime,
            "result": result,
        }, default=str), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass  # cache is best-effort; never fail the source over it


def _read_4g_cache(cache: Path, err: str):
    try:
        blob = json.loads(cache.read_text(encoding="utf-8"))
        res = dict(blob["result"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age_days = round((time.time() - float(blob.get("cached_at", 0))) / 86400, 1)
    res.update({
        "source": "last-good-cache",
        "cache_file": str(cache),
        "cache_age_days": age_days,
        "xlsx_mtime": datetime.datetime.fromtimestamp(
            float(blob.get("xlsx_mtime", 0))).isoformat(timespec="minutes"),
        "error": f"4g xlsx unreadable ({err}); served last-good cache ({age_days} d old)",
    })
    return res


@source("annual_goals_4g", "4g annual strategic goals + progress (JM's yearly bets) — the leverage frame for cards")
def _annual_goals_4g(path=None, cache=None):
    """Read the current-year sheet of `4g s.xlsx` (annual goals). Read-only so it
    is safe even while Excel has the file open. Each goal row is: domain | goal |
    target_fen | pct | earned_fen | q1..q4. Dream should bias cards toward
    advancing/unblocking these (especially goals behind pace) over loose-thread
    nits — see dream-ai-context.md 'Goal-leverage orientation'.
    Unreadable workbook: last-good cache, else the vault narrative."""
    path = path or Path.home() / "OneDrive" / "vault-excel" / "4g s.xlsx"
    cache = cache or ANNUAL_4G_CACHE
    dataless = False
    try:
        if not path.exists():
            raise FileNotFoundError(path)
        if _is_dataless(path):
            # v174 (2026-10-31): OneDrive Files-On-Demand had evicted the file to a
            # cloud placeholder. A launchd-context read cannot hydrate it
            # (PermissionError at 03:00; BadZipFile interactively).
            dataless = True
            raise PermissionError(f"{path.name} is a OneDrive cloud-only placeholder "
                                  f"(dataless); fix: {_PIN_FIX}")
        result = _parse_4g_workbook(path)
        _write_4g_cache(result, path, cache)
        return result
    except Exception as e:
        err = str(e)
        out = _read_4g_cache(cache, err) or _annual_goals_4g_vault_fallback(err)
        if dataless:
            out["dataless"] = True
        return out


@source("onedrive_dataless", "vault-excel workbooks evicted to OneDrive cloud placeholders (unreadable by cron)")
def _onedrive_dataless(root=None):
    """Names every top-level vault-excel file that is a dataless placeholder.
    Dream, /2s, /3s and the 4g source read these at night; an evicted file
    fails with a misleading 'Operation not permitted'."""
    root = root or Path.home() / "OneDrive" / "vault-excel"
    if not root.exists():
        return {"_status": "missing", "error": f"{root} not found"}
    evicted = sorted(p.name for p in root.iterdir()
                     if p.is_file() and not p.name.startswith(".") and _is_dataless(p))
    out = {"count": len(evicted), "dataless": evicted}
    if evicted:
        out["fix"] = _PIN_FIX
    return out


def dataless_diagnostic(results):
    """One line for the brief when OneDrive evicted workbooks Dream reads."""
    d = (results.get("onedrive_dataless") or {}).get("data") or {}
    if not d.get("count"):
        return None
    names = d["dataless"]
    shown = ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
    return f"OneDrive: {d['count']} vault-excel workbooks are cloud-only ({shown}). {_PIN_FIX}."


def _annual_goals_4g_vault_fallback(err):
    """Coarse annual frame from the vault Current-State note when the OneDrive
    4g workbook is unreadable. Narrative only (no per-goal 分 targets)."""
    year = datetime.date.today().year
    doc = Path.home() / "vault" / "g245" / "5e4" / f"4⎡{year} Current State.md"
    out = {"error": f"4g xlsx unreadable ({err}); using vault fallback",
           "source": "vault-fallback", "tldr": None, "year_bets": [], "goals": []}
    if not doc.exists():
        return out
    try:
        lines = doc.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    # tl;dr line (e.g. "**tl;dr:** ...")
    for ln in lines:
        low = ln.lower()
        if "tl;dr" in low:
            out["tldr"] = ln.split(":", 1)[-1].strip(" *") if ":" in ln else ln.strip(" *")
            break
    # Current-year bullet block under "# Years" (a top-level "- <year>" item
    # followed by indented sub-bullets).
    collecting = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("-") and stripped.lstrip("- ").startswith(str(year)):
            collecting = True
            continue
        if collecting:
            # stop at the next top-level bullet or a new heading
            if (ln and not ln[0].isspace() and stripped.startswith("-")) or stripped.startswith("#"):
                break
            if stripped.startswith("-"):
                out["year_bets"].append(stripped.lstrip("- ").strip())
    return out


@source("weekly_goals_1g", "this week's 1g goals from the g245 2g+1g narrative (the authoritative mirror; the Neon 1g tab is a stale template)")
def _weekly_goals_1g(today=None, root=None):
    """Return the current week's block (and next week's, if set) from
    vault/g245/5e2/<YYYY.MM> 2g+1g-narrative.md. Week headers look like
    `- **Oct 18 – Oct 24** —`. Without this, every Dream run spent a subagent
    rediscovering the goals (v163 needed 3 subagents to resolve one bullet)."""
    today = today or datetime.date.today()
    root = Path(root) if root else Path.home() / "vault" / "g245" / "5e2"
    hdr = re.compile(r"^\s*- \*\*([A-Z][a-z]{2}) (\d{1,2}) [–-] ([A-Z][a-z]{2}) (\d{1,2})\*\*")
    weeks = []
    for f in sorted(root.glob("*2g+1g-narrative.md"))[-2:]:
        year = int(f.name[:4])
        cur = None
        for line in f.read_text().splitlines():
            m = hdr.match(line)
            if m:
                try:
                    start = datetime.datetime.strptime(f"{m[1]} {m[2]} {year}", "%b %d %Y").date()
                    end = datetime.datetime.strptime(f"{m[3]} {m[4]} {year}", "%b %d %Y").date()
                except ValueError:
                    cur = None
                    continue
                if end < start:  # week crosses New Year
                    end = end.replace(year=year + 1)
                cur = {"start": start.isoformat(), "end": end.isoformat(), "lines": [], "file": f.name}
                weeks.append(cur)
            elif line.startswith("#"):
                cur = None
            elif cur is not None and line.strip() and line.strip() != "-":
                cur["lines"].append(line.rstrip())
    iso = today.isoformat()
    current = next((w for w in weeks if w["start"] <= iso <= w["end"]), None)
    nxt = next((w for w in weeks if w["start"] > iso), None)
    # dream-meta (v174) resolver: scans last/this/next month's docs, so weeks that
    # straddle a month boundary resolve, and flags `stale` when no block covers today.
    try:
        resolved = current_1g_week(root.parent.parent, today)
    except Exception as e:  # noqa: BLE001
        resolved = {"error": str(e)}
    return {"current": current, "next": nxt,
            "next_is_empty": nxt is not None and not nxt["lines"],
            "resolved": resolved}


_SF_DATALESS = 0x40000000  # <sys/stat.h>: file content lives in the cloud, not on disk


def _is_dataless(path: Path) -> bool:
    try:
        return bool(getattr(os.stat(path), "st_flags", 0) & _SF_DATALESS)
    except OSError:
        return False


_1G_HEADER = re.compile(r"^- \*\*([A-Z][a-z]{2}) (\d{1,2}) [–-] ([A-Z][a-z]{2}) (\d{1,2})\*\*(.*)$")
_MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_1g_weeks(text: str, year: int) -> list[dict]:
    """Parse '- **Oct 25 – Oct 31** — note' week blocks from a 2g+1g narrative doc.
    Returns [{start, end, note, lines}] with dates as datetime.date; `lines` is the
    indented goal outline under the header with <!--1g--> markers stripped."""
    weeks, cur = [], None
    for raw in text.splitlines():
        m = _1G_HEADER.match(raw)
        if m:
            m1, d1, m2, d2, note = m.groups()
            if m1 not in _MON or m2 not in _MON:
                cur = None
                continue
            start = datetime.date(year, _MON[m1], int(d1))
            end_year = year + 1 if _MON[m2] < _MON[m1] else year
            cur = {"start": start, "end": datetime.date(end_year, _MON[m2], int(d2)),
                   "note": note.strip(" —-").strip(), "lines": []}
            weeks.append(cur)
            continue
        if cur is None:
            continue
        if raw.startswith(("#", "- ")) or (raw and not raw[0].isspace()):
            cur = None  # next top-level bullet or heading ends the week block
            continue
        line = re.sub(r"\s*<!--1g-->", "", raw).rstrip()
        if line.strip():
            cur["lines"].append(line.replace("\t", "  "))
    return weeks


def current_1g_week(vault_dir: Path, today: datetime.date) -> dict:
    """Find this week's 1g block in g245/5e2/<YYYY.MM> 2g+1g-narrative.md. Weeks
    straddling a month live in the month they start in, so scan last, this and
    next month's docs. Falls back to the latest week that has started."""
    months = []
    for delta in (-1, 0, 1):
        y, m = today.year, today.month + delta
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        months.append((y, m))
    weeks, files = [], []
    for y, m in months:
        f = vault_dir / "g245" / "5e2" / f"{y}.{m:02d} 2g+1g-narrative.md"
        if f.exists():
            files.append(str(f))
            for w in parse_1g_weeks(f.read_text(encoding="utf-8"), y):
                w["file"] = str(f)
                weeks.append(w)
    if not weeks:
        return {"error": "no 2g+1g narrative doc found", "files": files}
    hit = next((w for w in weeks if w["start"] <= today <= w["end"]), None)
    stale = hit is None
    if stale:
        started = [w for w in weeks if w["start"] <= today]
        hit = max(started, key=lambda w: w["start"]) if started else min(weeks, key=lambda w: w["start"])
    return {"week": f"{hit['start'].isoformat()}..{hit['end'].isoformat()}",
            "note": hit["note"], "goals": hit["lines"], "empty": not hit["lines"],
            "stale": stale, "source": hit["file"]}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_sources(selected=None, timeout=30):
    """Run sources concurrently. Returns dict of source results."""
    sources_to_run = {}
    if selected:
        for name in selected:
            if name in _SOURCES:
                sources_to_run[name] = _SOURCES[name]
            else:
                raise ValueError(f"Unknown source: {name}. Available: {', '.join(_SOURCES)}")
    else:
        sources_to_run = _SOURCES

    results = {}

    def _run_one(name, info):
        t0 = time.time()
        try:
            data = info["fn"]()
            elapsed = time.time() - t0
            # A source may report its own status ("missing", "stale") via a
            # "_status" key instead of raising (cache-backed sources).
            status = "ok"
            if isinstance(data, dict) and "_status" in data:
                status = data.pop("_status")
            out = {
                "status": status,
                "elapsed_ms": round(elapsed * 1000),
                "data": data,
            }
            if status not in ("ok", "stale") and isinstance(data, dict) and "error" in data:
                out["error"] = data["error"]
            return name, out
        except Exception as e:
            elapsed = time.time() - t0
            return name, {
                "status": "error",
                "elapsed_ms": round(elapsed * 1000),
                "data": None,
                "error": f"{type(e).__name__}: {e}",
            }

    # No `with`: its exit is shutdown(wait=True), which waited out a source
    # hung past its timeout (agency sign-in: 15-30 min a night, 2026-11-03).
    # One deadline for all: max_workers=8 queues the rest, so allow one extra
    # timeout window for the second wave rather than 30s per future in turn.
    pool = ThreadPoolExecutor(max_workers=8)
    deadline = time.time() + timeout * 2
    futures = {}
    for name, info in sources_to_run.items():
        fut = pool.submit(_run_one, name, info)
        futures[fut] = name
    try:
        for fut in futures:
            try:
                name, result = fut.result(timeout=max(0.0, deadline - time.time()))
                results[name] = result
            except FuturesTimeout:
                name = futures[fut]
                results[name] = {
                    "status": "timeout",
                    "elapsed_ms": timeout * 1000,
                    "data": None,
                    "error": f"Exceeded {timeout}s timeout",
                }
            except Exception as e:
                name = futures[fut]
                results[name] = {
                    "status": "error",
                    "elapsed_ms": 0,
                    "data": None,
                    "error": f"{type(e).__name__}: {e}",
                }
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return results


def _exit_now_if_hung():
    """Executor threads are non-daemon: the interpreter joins them at exit,
    so one hung source would still hold the launcher after the JSON is
    written. Hard-exit in that case (atexit hooks skipped on purpose: a hung
    thread may hold agency_mcp's _lock, which its stop_all needs)."""
    import threading
    if any(t.is_alive() for t in threading.enumerate()
           if t is not threading.main_thread() and not t.daemon):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_test_table(results):
    """Print a human-readable test summary table."""
    print(f"\n{'Source':<25} {'Status':<10} {'Time':>8}  Notes")
    print("-" * 70)
    for name in sorted(results):
        r = results[name]
        status = r["status"]
        elapsed = f"{r['elapsed_ms']}ms"
        notes = ""
        if r["status"] == "error":
            notes = r.get("error", "")[:50]
        elif r["data"]:
            # Summarize data
            d = r["data"]
            if isinstance(d, dict):
                if "count" in d:
                    notes = f"count={d['count']}"
                elif "unread_count" in d:
                    notes = f"unread={d['unread_count']}"
                elif "entry_count" in d:
                    notes = f"entries={d['entry_count']}, hours={d.get('total_hours', '?')}"
                elif "total_units" in d:
                    notes = f"units={d['total_units']}, occ={d.get('overall_occupancy_pct', '?')}%"
                elif "total_open" in d:
                    notes = f"open={d['total_open']}, overdue={d.get('total_overdue', 0)}"
                elif "today_count" in d:
                    notes = f"today={d['today_count']}, tomorrow={d.get('tomorrow_count', 0)}"
                elif "connected_devices" in d:
                    notes = f"devices={d['connected_devices']}, conflicts={d.get('conflict_files', 0)}"
                elif "unique_urls" in d:
                    notes = f"urls={d['unique_urls']}"
                elif "app_count" in d:
                    notes = f"apps={d['app_count']}, total={d.get('total_minutes', '?')}min"
                elif "conversation_count" in d:
                    notes = f"conversations={d['conversation_count']}"
                elif "status" in d:
                    notes = d.get("reason", d["status"])[:50]
                else:
                    notes = f"keys={list(d.keys())[:3]}"

        # Color status
        status_str = status
        if status == "ok":
            status_str = "\033[32mok\033[0m"
        elif status == "error":
            status_str = "\033[31merror\033[0m"
        elif status in ("timeout", "stale", "missing"):
            status_str = f"\033[33m{status}\033[0m"

        print(f"{name:<25} {status_str:<19} {elapsed:>8}  {notes}")

    ok = sum(1 for r in results.values() if r["status"] == "ok")
    err = sum(1 for r in results.values() if r["status"] not in ("ok", "skipped"))
    total_ms = sum(r["elapsed_ms"] for r in results.values())
    print("-" * 70)
    print(f"Total: {ok} ok, {err} failed, {total_ms}ms cumulative\n")


def main():
    parser = argparse.ArgumentParser(description="Gather context from multiple sources into a JSON snapshot")
    parser.add_argument("--output", "-o", type=str, help="Output JSON file path")
    parser.add_argument("--sources", type=str, help="Comma-separated list of sources to run")
    parser.add_argument("--test", action="store_true", help="Run all sources and print a summary table")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without executing")
    parser.add_argument("--run-dir", type=str, help="Directory for run artifacts")
    args = parser.parse_args()

    if args.dry_run:
        selected = args.sources.split(",") if args.sources else list(_SOURCES.keys())
        print("Would run these sources:")
        for name in selected:
            info = _SOURCES.get(name, {})
            desc = info.get("desc", "unknown") if info else "UNKNOWN"
            print(f"  {name}: {desc}")
        return

    # Never let an intake timeout write ~/.config/agency/login-pending.json:
    # it would refuse every Agency caller on this host for 3 h (v175).
    os.environ["AGENCY_UNATTENDED"] = "1"

    load_credentials()

    selected = args.sources.split(",") if args.sources else None

    t0 = time.time()
    results = run_sources(selected=selected, timeout=30)
    duration_ms = round((time.time() - t0) * 1000)

    diags = [d for d in (fda_diagnostic(results), dataless_diagnostic(results)) if d]

    if args.test:
        print_test_table(results)
        for d in diags:
            print(d)
        _exit_now_if_hung()
        return

    import socket
    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "generated_on": socket.gethostname(),
        "duration_ms": duration_ms,
        "sources": results,
    }
    if diags:
        output["diagnostics"] = diags
        for d in diags:
            print(d, file=sys.stderr)  # lands in .dream-launch.log / agent-run.log

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Written to {out_path} ({duration_ms}ms)")
    else:
        json.dump(output, sys.stdout, indent=2, default=str)
        print()
    _exit_now_if_hung()


if __name__ == "__main__":
    main()
    # Hard exit: concurrent.futures registers an atexit hook that joins all
    # worker threads, which would re-block on any source that blew its timeout.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
