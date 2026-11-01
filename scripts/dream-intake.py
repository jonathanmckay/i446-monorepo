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
    _CREDS["gmail_tokens_path"] = eml_dir / "tokens-gmail.json"
    _CREDS["gmail_secrets_path"] = eml_dir / "gcp-oauth-gmail.keys.json"

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

@source("gmail_inbox", "Unread Gmail messages with age flags")
def _gmail_inbox():
    creds = _build_google_creds(
        _CREDS["gmail_tokens_path"],
        _CREDS["gmail_secrets_path"],
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    service = _google_service("gmail", "v1", creds)

    resp = service.users().messages().list(
        userId="me", q="is:unread in:inbox", maxResults=50
    ).execute()

    messages = resp.get("messages", [])
    if not messages:
        return {"unread_count": 0, "messages": []}

    now = time.time()
    results = []
    # Batch fetch metadata
    for msg_stub in messages[:50]:
        msg = service.users().messages().get(
            userId="me", id=msg_stub["id"], format="metadata",
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

    stale_count = sum(1 for r in results if r["stale"])

    return {
        "unread_count": len(results),
        "stale_count": stale_count,
        "messages": results,
    }


# ---------------------------------------------------------------------------
# Source 7: google_calendar
# ---------------------------------------------------------------------------

_SKIP_CAL_SUFFIXES = ("#holiday@group.v.calendar.google.com", "#weeknum@group.v.calendar.google.com")


def _gcal_accounts():
    """Account keys in the google-calendar-mcp tokens file (e.g. jbm, m5c7)."""
    with open(_CREDS["gcal_tokens_path"]) as f:
        data = json.load(f)
    if isinstance(data, dict) and "access_token" not in data:
        return [k for k, v in data.items() if isinstance(v, dict) and v.get("refresh_token")]
    return [None]  # flat single-account token file


def _gcal_event_start_pt_date(start):
    """PT calendar date of an event start ('YYYY-MM-DD' all-day or RFC3339)."""
    if len(start) == 10:
        return start
    dt = _to_pt(start)
    return dt.date().isoformat() if dt else start[:10]


@source("google_calendar", "Today + tomorrow (PT) events across every Google account and calendar")
def _google_calendar():
    today = _pt_today()
    tomorrow = today + datetime.timedelta(days=1)
    time_min = _pt_midnight(today).isoformat()
    time_max = _pt_midnight(today + datetime.timedelta(days=2)).isoformat()

    accounts_out, calendars_out, errors = [], [], []
    all_events, seen = [], set()
    for acct in _gcal_accounts():
        label = acct or "default"
        try:
            creds = _build_google_creds(
                _CREDS["gcal_tokens_path"], _CREDS["gcal_secrets_path"],
                scopes=["https://www.googleapis.com/auth/calendar.readonly"],
                account_key=acct,
            )
            service = _google_service("calendar", "v3", creds)
            cals, page = [], None
            while True:
                resp = service.calendarList().list(pageToken=page).execute()
                cals.extend(resp.get("items", []))
                page = resp.get("nextPageToken")
                if not page:
                    break
        except Exception as e:
            errors.append({"account": label, "error": f"{type(e).__name__}: {e}"})
            accounts_out.append({"account": label, "ok": False})
            continue

        acct_count = 0
        for cal in cals:
            cal_id = cal.get("id", "")
            name = cal.get("summaryOverride") or cal.get("summary") or cal_id
            skip = None
            if cal.get("deleted"):
                skip = "deleted"
            elif cal.get("hidden"):
                skip = "hidden"
            elif cal_id.endswith(_SKIP_CAL_SUFFIXES):
                skip = "holidays/weeknum"
            cal_rec = {"account": label, "id": cal_id, "name": name,
                       "primary": bool(cal.get("primary")), "events": 0}
            if skip:
                cal_rec["skipped"] = skip
                calendars_out.append(cal_rec)
                continue
            try:
                items, page = [], None
                while True:
                    resp = service.events().list(
                        calendarId=cal_id, timeMin=time_min, timeMax=time_max,
                        singleEvents=True, orderBy="startTime", maxResults=250,
                        pageToken=page,
                    ).execute()
                    items.extend(resp.get("items", []))
                    page = resp.get("nextPageToken")
                    if not page:
                        break
            except Exception as e:
                cal_rec["error"] = f"{type(e).__name__}: {e}"
                errors.append({"account": label, "calendar": cal_id, "error": cal_rec["error"]})
                calendars_out.append(cal_rec)
                continue

            for ev in items:
                if ev.get("status") == "cancelled":
                    continue
                attendees = ev.get("attendees", []) or []
                me = next((a for a in attendees if a.get("self")), None)
                if me and me.get("responseStatus") == "declined":
                    continue
                start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
                end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
                # Recurring instances share an iCalUID, so key on the instance start too.
                orig = ev.get("originalStartTime", {})
                inst = orig.get("dateTime", orig.get("date", "")) or start
                key = (ev.get("iCalUID") or ev.get("id"), inst)
                if key in seen:
                    continue
                seen.add(key)
                cal_rec["events"] += 1
                acct_count += 1
                org = ev.get("organizer", {}) or {}
                all_events.append({
                    "account": label,
                    "calendar": cal_id,
                    "calendar_name": name,
                    "id": ev.get("id"),
                    "ical_uid": ev.get("iCalUID"),
                    "summary": ev.get("summary", "(no title)"),
                    "start": start,
                    "end": end,
                    "all_day": "date" in ev.get("start", {}) and "dateTime" not in ev.get("start", {}),
                    "status": ev.get("status", ""),
                    "location": ev.get("location", ""),
                    "attendees": len(attendees),  # int, kept for backward compat
                    "attendee_list": [
                        {"email": a.get("email", ""), "responseStatus": a.get("responseStatus", ""),
                         **({"self": True} if a.get("self") else {}),
                         **({"organizer": True} if a.get("organizer") else {})}
                        for a in attendees
                    ],
                    "organizer": org.get("email", ""),
                    "self_response": (me or {}).get("responseStatus", ""),
                })
            calendars_out.append(cal_rec)
        accounts_out.append({"account": label, "ok": True, "calendars": len(cals), "events": acct_count})

    if accounts_out and not any(a["ok"] for a in accounts_out):
        raise RuntimeError(f"all Google accounts failed: {errors}")

    def _sort_key(e):
        s = e["start"]
        return (_gcal_event_start_pt_date(s), "" if len(s) == 10 else _to_pt(s).isoformat())
    all_events.sort(key=_sort_key)
    today_events, tomorrow_events = [], []
    for ev in all_events:
        d = _gcal_event_start_pt_date(ev["start"])
        if d == tomorrow.isoformat():
            tomorrow_events.append(ev)
        else:
            today_events.append(ev)  # incl. multi-day events that began before today

    out = {
        "date": today.isoformat(),
        "time_min": time_min,
        "time_max": time_max,
        "today_count": len(today_events),
        "tomorrow_count": len(tomorrow_events),
        "today": today_events,
        "tomorrow": tomorrow_events,
        "accounts": accounts_out,
        "calendars": calendars_out,
    }
    if errors:
        out["errors"] = errors
    return out


# ---------------------------------------------------------------------------
# Source 8: syncthing_status
# ---------------------------------------------------------------------------

@source("syncthing_status", "Syncthing sync status and conflict files")
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
    conflicts = glob_mod.glob(str(vault_path / "**" / "*.sync-conflict-*"), recursive=True)

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
# Source 9: browser_history
# ---------------------------------------------------------------------------

@source("browser_history", "Safari browsing history (last 24h)")
def _browser_history():
    src = Path.home() / "Library" / "Safari" / "History.db"
    if not src.exists():
        return {"error": "Safari History.db not found"}

    # Copy to /tmp to avoid lock
    tmp_db = Path("/tmp/dream_safari_history.db")
    shutil.copy2(src, tmp_db)

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

def _on_ix() -> bool:
    """True on the ix mac mini (same test as did-fast/_on_ix)."""
    import socket
    host = socket.gethostname().lower()
    return host in ("ix", "ix.local") or host.startswith("ix.") or "mac-mini" in host


def _outlook_skip(server_name: str):
    """Skip result for an Agency source that cannot authenticate here, or None.

    On ix the dream run is headless: MSAL's keychain read fails (-61) and
    Agency falls back to interactive sign-in that nobody completes, so every
    Outlook source sat until the 30s source timeout (v160-v166). Unless an
    AGENCY_REMOTE_<NAME>_PORT tunnel delegates to a host that can auth,
    return immediately instead of hanging."""
    if not _on_ix():
        return None
    env_key = f"AGENCY_REMOTE_{server_name.upper()}_PORT"
    if os.environ.get(env_key):
        return None
    return {"status": "skipped",
            "reason": f"on ix without {env_key}: headless MSAL (keychain -61) "
                      f"cannot complete interactive auth"}


@source("outlook_inbox", "Outlook unread emails and recent sent (last 24h)")
def _outlook_inbox():
    skip = _outlook_skip("mail")
    if skip:
        return skip
    sys.path.insert(0, str(Path.home() / "i446-monorepo/tools/ibx"))
    import agency_mcp as mcp
    from datetime import timezone
    cutoff = (datetime.datetime.now(timezone.utc) - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Unread inbox
    query = (
        f"?$top=50"
        f"&$filter=isRead eq false and receivedDateTime ge {cutoff}"
        f"&$select=id,subject,from,receivedDateTime,bodyPreview"
        f"&$orderby=receivedDateTime desc"
    )
    # 5s timeout (was 30s). The outlook MCP has been timing out reliably for
    # weeks; burning 30s per call costs ~60s of every intake. If the MCP is
    # responsive, 5s is plenty for a single SearchMessages. v51 self-improvement.
    raw = mcp.call_tool("mail", "SearchMessagesQueryParameters", {
        "queryParameters": query
    }, timeout=5)
    unread = []
    if raw and raw.get("content"):
        for item in raw["content"]:
            text = item.get("text", "")
            try:
                data = json.loads(text)
                messages = data if isinstance(data, list) else data.get("value", [])
                for msg in messages:
                    unread.append({
                        "subject": msg.get("subject", ""),
                        "from": (msg.get("from", {}).get("emailAddress", {}).get("name", "")
                                 or msg.get("from", {}).get("emailAddress", {}).get("address", "")),
                        "received": msg.get("receivedDateTime", ""),
                        "preview": msg.get("bodyPreview", "")[:200],
                    })
            except (json.JSONDecodeError, TypeError):
                pass

    # Recent sent (last 24h) for context on what JM communicated
    sent_query = (
        f"?$top=20"
        f"&$filter=sentDateTime ge {cutoff}"
        f"&$select=subject,toRecipients,sentDateTime,bodyPreview"
        f"&$orderby=sentDateTime desc"
    )
    raw_sent = mcp.call_tool("mail", "SearchMessagesQueryParameters", {
        "queryParameters": f"/me/mailFolders/SentItems/messages{sent_query}"
    }, timeout=5)
    sent = []
    if raw_sent and raw_sent.get("content"):
        for item in raw_sent["content"]:
            text = item.get("text", "")
            try:
                data = json.loads(text)
                messages = data if isinstance(data, list) else data.get("value", [])
                for msg in messages:
                    to_list = [r.get("emailAddress", {}).get("name", "")
                               for r in msg.get("toRecipients", [])]
                    sent.append({
                        "subject": msg.get("subject", ""),
                        "to": to_list,
                        "sent": msg.get("sentDateTime", ""),
                        "preview": msg.get("bodyPreview", "")[:200],
                    })
            except (json.JSONDecodeError, TypeError):
                pass

    return {"unread": unread, "unread_count": len(unread),
            "sent": sent, "sent_count": len(sent)}


@source("outlook_calendar", "Outlook calendar events (yesterday + today + tomorrow)")
def _outlook_calendar():
    skip = _outlook_skip("calendar")
    if skip:
        return skip
    sys.path.insert(0, str(Path.home() / "i446-monorepo/tools/ibx"))
    import agency_mcp as mcp
    from datetime import timezone
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    day_after = (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
    raw = mcp.call_tool("calendar", "ListEvents", {
        "startDateTime": yesterday,
        "endDateTime": day_after,
        "top": "50",
    }, timeout=5)
    events = []
    if raw and raw.get("content"):
        for item in raw["content"]:
            text = item.get("text", "")
            # Strip "Events retrieved successfully.\n" status prefix
            idx = min((text.find(c) for c in "{[" if text.find(c) != -1), default=-1)
            if idx > 0:
                text = text[idx:]
            try:
                data = json.loads(text)
                ev_list = data if isinstance(data, list) else data.get("value", [])
                for ev in ev_list:
                    events.append({
                        "subject": ev.get("subject", ""),
                        "start": ev.get("start", {}).get("dateTime", ""),
                        "end": ev.get("end", {}).get("dateTime", ""),
                        "organizer": ev.get("organizer", {}).get("emailAddress", {}).get("name", ""),
                        "is_online": bool(ev.get("onlineMeeting")),
                    })
            except (json.JSONDecodeError, TypeError):
                pass
    return {"events": events, "count": len(events)}


@source("slack", "Slack status (stub)")
def _slack():
    return {"status": "not_configured"}


@source("annual_goals_4g", "4g annual strategic goals + progress (JM's yearly bets) — the leverage frame for cards")
def _annual_goals_4g():
    """Read the current-year sheet of `4g s.xlsx` (annual goals). Read-only so it
    is safe even while Excel has the file open. Each goal row is: domain | goal |
    target_fen | pct | earned_fen | q1..q4. Dream should bias cards toward
    advancing/unblocking these (especially goals behind pace) over loose-thread
    nits — see dream-ai-context.md 'Goal-leverage orientation'."""
    import openpyxl  # available in the intake env
    path = Path.home() / "OneDrive" / "vault-excel" / "4g s.xlsx"
    try:
        if not path.exists():
            raise FileNotFoundError(path)
        if _is_dataless(path):
            # v174 (2026-10-31): OneDrive Files-On-Demand had evicted the file to a
            # cloud placeholder. A launchd-context read cannot hydrate it
            # (PermissionError at 03:00; BadZipFile interactively), so say why and
            # still anchor the run on the vault narrative.
            msg = (f"{path.name} is a OneDrive cloud-only placeholder (dataless); "
                   "fix: Finder > right-click ~/OneDrive/vault-excel > Always Keep on This Device")
            out = _annual_goals_4g_vault_fallback(msg)
            out["dataless"] = True
            return out
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
    except Exception as e:
        # OneDrive under macOS TCC routinely denies headless/cron reads
        # ("Operation not permitted") — which left Dream with NO annual frame
        # at all (source returned null). Degrade gracefully to the readable
        # vault narrative so every run still anchors to the year's bets.
        # Permanent fix: grant Full Disk Access to the intake's python on ix
        # (one setting that also unlocks Safari history, screen-time, iMessage).
        return _annual_goals_4g_vault_fallback(str(e))


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
            skipped = isinstance(data, dict) and data.get("status") == "skipped"
            return name, {
                "status": "skipped" if skipped else "ok",
                "elapsed_ms": round(elapsed * 1000),
                "data": data,
            }
        except Exception as e:
            elapsed = time.time() - t0
            return name, {
                "status": "error",
                "elapsed_ms": round(elapsed * 1000),
                "data": None,
                "error": f"{type(e).__name__}: {e}",
            }

    # No `with` block: its __exit__ joins every worker, so one hung source
    # (2026-10: an Agency start stuck in interactive auth) held the whole intake
    # for 2h despite the per-source timeout. One shared deadline; stragglers are
    # abandoned and main() exits with os._exit so atexit can't join them either.
    pool = ThreadPoolExecutor(max_workers=8)
    deadline = time.time() + timeout
    futures = {}
    for name, info in sources_to_run.items():
        fut = pool.submit(_run_one, name, info)
        futures[fut] = name

    for fut in futures:
        try:
            name, result = fut.result(timeout=max(deadline - time.time(), 0))
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

    pool.shutdown(wait=False, cancel_futures=True)
    return results


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
        elif status == "timeout":
            status_str = "\033[33mtimeout\033[0m"

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

    load_credentials()

    selected = args.sources.split(",") if args.sources else None

    t0 = time.time()
    results = run_sources(selected=selected, timeout=30)
    duration_ms = round((time.time() - t0) * 1000)

    if args.test:
        print_test_table(results)
        return

    import socket
    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "generated_on": socket.gethostname(),
        "duration_ms": duration_ms,
        "sources": results,
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Written to {out_path} ({duration_ms}ms)")
    else:
        json.dump(output, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
    # Hard exit: concurrent.futures registers an atexit hook that joins all
    # worker threads, which would re-block on any source that blew its timeout.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
