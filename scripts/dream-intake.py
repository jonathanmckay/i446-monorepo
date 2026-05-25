#!/usr/bin/env python3
"""Dream nightly agent: consolidated data source aggregator.

Collects data from local files, APIs, and databases into a single JSON
document consumed by downstream Dream stages (ranker, rem1, vault-hygiene).

Usage:
    python3 dream-intake.py                     # write to default output
    python3 dream-intake.py --test              # run all, print timing table
    python3 dream-intake.py --dry-run           # write to stdout
    python3 dream-intake.py --sources toggl_today,gmail_inbox
"""

import argparse
import base64
import datetime
import glob as globmod
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------
_SOURCES: Dict[str, dict] = {}


def source(name: str, desc: str):
    """Decorator that registers a data source function."""
    def decorator(fn):
        _SOURCES[name] = {"fn": fn, "desc": desc}
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------
_CREDS: dict = {}


def load_credentials():
    """Load all credentials once at startup. Populates the global _CREDS dict."""
    global _CREDS
    home = Path.home()

    # --- ~/.claude.json ---
    claude_path = home / ".claude.json"
    if claude_path.exists():
        claude = json.loads(claude_path.read_text())
        servers = claude.get("mcpServers", {})

        toggl_env = servers.get("toggl_server", {}).get("env", {})
        _CREDS["toggl_api_key"] = toggl_env.get("TOGGL_API_KEY", "")
        _CREDS["toggl_workspace_id"] = int(toggl_env.get("TOGGL_WORKSPACE_ID", "2092616"))

        af_env = servers.get("appfolio-mcp", {}).get("env", {})
        _CREDS["af_vhost"] = af_env.get("VHOST", "")
        _CREDS["af_username"] = af_env.get("USERNAME", "")
        _CREDS["af_password"] = af_env.get("PASSWORD", "")

    # --- Gmail OAuth ---
    eml_tokens = home / ".config/eml/tokens.json"
    eml_keys = home / ".config/eml/gcp-oauth.keys.json"
    if eml_tokens.exists():
        _CREDS["gmail_tokens"] = json.loads(eml_tokens.read_text())
    if eml_keys.exists():
        _CREDS["gmail_keys"] = json.loads(eml_keys.read_text()).get("installed", {})

    # --- Google Calendar OAuth ---
    gcal_tokens = home / ".config/google-calendar-mcp/tokens.json"
    gcal_keys = home / ".config/google-calendar-mcp/gcp-oauth.keys.json"
    if gcal_tokens.exists():
        _CREDS["gcal_tokens"] = json.loads(gcal_tokens.read_text()).get("m5c7", {})
    if gcal_keys.exists():
        _CREDS["gcal_keys"] = json.loads(gcal_keys.read_text()).get("installed", {})

    # --- Syncthing ---
    syncthing_cfg = home / "Library/Application Support/Syncthing/config.xml"
    if syncthing_cfg.exists():
        xml = syncthing_cfg.read_text()
        m = re.search(r"<apikey>([^<]+)</apikey>", xml)
        _CREDS["syncthing_api_key"] = m.group(1) if m else ""


# ---------------------------------------------------------------------------
# HTTP helpers (urllib only, no requests dependency)
# ---------------------------------------------------------------------------

def _http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_post_form(url, data: dict, headers=None, timeout=25):
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_post_json(url, payload: dict, headers=None, timeout=25):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


import urllib.parse  # ensure available for _http_post_form


def _basic_auth(user, password):
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {creds}"


# ---------------------------------------------------------------------------
# Google OAuth helper
# ---------------------------------------------------------------------------

def _build_google_creds(tokens: dict, keys: dict):
    """Build google.oauth2.credentials.Credentials, refreshing if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri=keys.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=tokens.get("client_id") or keys.get("client_id"),
        client_secret=tokens.get("client_secret") or keys.get("client_secret"),
    )
    if creds.expired or not creds.valid:
        creds.refresh(Request())
    return creds


# ---------------------------------------------------------------------------
# Source: completed_today
# ---------------------------------------------------------------------------
@source("completed_today", "Completed habits/tasks from today")
def _completed_today():
    home = Path.home()
    today_str = datetime.datetime.now(TZ).strftime("%Y-%m-%d")

    result = {"today": today_str, "current": None, "archive": None, "stale": False}

    # Current file
    current_path = home / "vault/z_ibx/completed-today.json"
    if current_path.exists():
        data = json.loads(current_path.read_text())
        result["current"] = data
        if data.get("date") != today_str:
            result["stale"] = True

    # Archive for today
    archive_path = home / f"vault/z_ibx/completed-archive/{today_str}.json"
    if archive_path.exists():
        result["archive"] = json.loads(archive_path.read_text())

    # Merge names
    names = set()
    if result["current"]:
        names.update(result["current"].get("names", []))
    if result["archive"]:
        names.update(result["archive"].get("names", []))
    result["total_completed"] = len(names)

    return result


# ---------------------------------------------------------------------------
# Source: d357_transcripts
# ---------------------------------------------------------------------------
@source("d357_transcripts", "Meeting transcripts from today")
def _d357_transcripts():
    home = Path.home()
    today_dot = datetime.datetime.now(TZ).strftime("%Y.%m.%d")

    files = []
    total_actions = 0

    pattern = str(home / f"vault/d357/**/{today_dot}*.md")
    for path in sorted(globmod.glob(pattern, recursive=True)):
        content = Path(path).read_text(errors="replace")

        # Extract title from frontmatter
        title = None
        fm_match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip("\"'")
                    break
        if not title:
            title = Path(path).stem

        # Extract action items (unchecked checkboxes)
        actions = re.findall(r"^- \[ \] (.+)$", content, re.MULTILINE)
        total_actions += len(actions)

        rel = str(Path(path).relative_to(home / "vault"))
        files.append({"path": rel, "title": title, "action_items": actions})

    return {"files": files, "total_action_items": total_actions}


# ---------------------------------------------------------------------------
# Source: toggl_today
# ---------------------------------------------------------------------------
@source("toggl_today", "Toggl time entries for today")
def _toggl_today():
    api_key = _CREDS.get("toggl_api_key")
    if not api_key:
        return {"status": "error", "error": "No Toggl API key configured"}

    workspace_id = _CREDS.get("toggl_workspace_id", 2092616)

    # Import project names from toggl_server config
    toggl_path = str(Path.home() / "i446-monorepo/mcp/toggl_server")
    if toggl_path not in sys.path:
        sys.path.insert(0, toggl_path)
    try:
        from config import PROJECT_NAMES
    except ImportError:
        PROJECT_NAMES = {}

    today = datetime.datetime.now(TZ)
    start = today.strftime("%Y-%m-%d")
    end = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    auth = _basic_auth(api_key, "api_token")
    url = f"https://api.track.toggl.com/api/v9/me/time_entries?start_date={start}&end_date={end}"
    entries_raw = _http_get(url, {"Authorization": auth})

    entries = []
    by_project: Dict[str, float] = {}
    total_sec = 0

    for e in (entries_raw or []):
        dur = e.get("duration", 0)
        if dur < 0:  # running timer
            dur = int(time.time()) - int(
                datetime.datetime.fromisoformat(
                    e["start"].replace("Z", "+00:00")
                ).timestamp()
            )
        proj_id = e.get("project_id")
        proj_name = PROJECT_NAMES.get(proj_id, str(proj_id) if proj_id else "no_project")
        dur_min = round(dur / 60, 1)
        total_sec += dur

        entries.append({
            "desc": e.get("description", ""),
            "project": proj_name,
            "start": e.get("start"),
            "stop": e.get("stop"),
            "duration_min": dur_min,
        })
        by_project[proj_name] = by_project.get(proj_name, 0) + dur_min

    return {
        "entries": entries,
        "total_hours": round(total_sec / 3600, 2),
        "by_project": dict(sorted(by_project.items(), key=lambda x: -x[1])),
    }


# ---------------------------------------------------------------------------
# Source: appfolio_rent_roll
# ---------------------------------------------------------------------------
@source("appfolio_rent_roll", "AppFolio rent roll (occupancy, vacancy, loss-to-lease)")
def _appfolio_rent_roll():
    vhost = _CREDS.get("af_vhost")
    user = _CREDS.get("af_username")
    pw = _CREDS.get("af_password")
    if not all([vhost, user, pw]):
        return {"status": "error", "error": "AppFolio credentials not configured"}

    url = f"https://{vhost}.appfolio.com/api/v2/reports/rent_roll_itemized.json"
    auth = _basic_auth(user, pw)
    today_str = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    data = _http_post_json(url, {"as_of_date": today_str, "unit_visibility": "active"}, {"Authorization": auth})

    rows = data.get("results", [])
    props: Dict[str, dict] = {}

    for row in rows:
        prop = row.get("property_name", "Unknown")
        if prop not in props:
            props[prop] = {
                "name": prop,
                "total_units": 0,
                "occupied": 0,
                "vacancy_count": 0,
                "loss_to_lease": 0.0,
                "past_due": 0.0,
            }
        p = props[prop]
        p["total_units"] += 1

        status = (row.get("status") or "").lower()
        if status in ("current", "occupied", "notice"):
            p["occupied"] += 1
        else:
            p["vacancy_count"] += 1

        # loss_to_lease = market_rent - actual_rent
        market = float(row.get("market_rent") or 0)
        actual = float(row.get("rent") or row.get("charge_amount") or 0)
        if market > actual:
            p["loss_to_lease"] += round(market - actual, 2)

        past_due = float(row.get("balance") or row.get("past_due") or 0)
        if past_due > 0:
            p["past_due"] += round(past_due, 2)

    property_list = []
    total_units = total_occupied = 0
    for p in props.values():
        p["occupancy_pct"] = round(p["occupied"] / p["total_units"] * 100, 1) if p["total_units"] else 0
        p["loss_to_lease"] = round(p["loss_to_lease"], 2)
        p["past_due"] = round(p["past_due"], 2)
        property_list.append(p)
        total_units += p["total_units"]
        total_occupied += p["occupied"]

    return {
        "properties": sorted(property_list, key=lambda x: x["name"]),
        "portfolio_summary": {
            "total_units": total_units,
            "total_occupied": total_occupied,
            "occupancy_pct": round(total_occupied / total_units * 100, 1) if total_units else 0,
            "total_vacancy": total_units - total_occupied,
        },
    }


# ---------------------------------------------------------------------------
# Source: appfolio_work_orders
# ---------------------------------------------------------------------------
@source("appfolio_work_orders", "AppFolio open work orders")
def _appfolio_work_orders():
    vhost = _CREDS.get("af_vhost")
    user = _CREDS.get("af_username")
    pw = _CREDS.get("af_password")
    if not all([vhost, user, pw]):
        return {"status": "error", "error": "AppFolio credentials not configured"}

    url = f"https://{vhost}.appfolio.com/api/v2/reports/work_order.json"
    auth = _basic_auth(user, pw)
    data = _http_post_json(url, {}, {"Authorization": auth})

    rows = data.get("results", [])
    now = datetime.datetime.now(TZ)
    open_statuses = {"open", "in progress", "on hold", "pending", "scheduled"}

    total_open = 0
    overdue_count = 0
    by_property: Dict[str, int] = {}
    overdue_list = []

    for row in rows:
        status = (row.get("status") or "").lower()
        if status not in open_statuses:
            continue

        total_open += 1
        prop = row.get("property_name", "Unknown")
        by_property[prop] = by_property.get(prop, 0) + 1

        # Check overdue (>30 days open)
        created = row.get("created_at") or row.get("date_created") or ""
        if created:
            try:
                dt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
                if (now - dt.astimezone(TZ)).days > 30:
                    overdue_count += 1
                    overdue_list.append({
                        "id": row.get("id"),
                        "description": row.get("description", "")[:100],
                        "property": prop,
                        "days_open": (now - dt.astimezone(TZ)).days,
                    })
            except (ValueError, TypeError):
                pass

    return {
        "total_open": total_open,
        "overdue_count": overdue_count,
        "by_property": dict(sorted(by_property.items(), key=lambda x: -x[1])),
        "overdue_list": sorted(overdue_list, key=lambda x: -x.get("days_open", 0)),
    }


# ---------------------------------------------------------------------------
# Source: gmail_inbox
# ---------------------------------------------------------------------------
@source("gmail_inbox", "Gmail unread inbox messages")
def _gmail_inbox():
    tokens = _CREDS.get("gmail_tokens")
    keys = _CREDS.get("gmail_keys")
    if not tokens or not keys:
        return {"status": "error", "error": "Gmail OAuth not configured"}

    creds = _build_google_creds(tokens, keys)

    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    result = service.users().messages().list(
        userId="me", q="in:inbox is:unread", maxResults=50
    ).execute()

    messages_meta = result.get("messages", [])
    now = datetime.datetime.now(TZ)

    messages = []
    for msg_ref in messages_meta:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Parse date
        age_hours = None
        flagged_old = False
        date_str = headers.get("Date", "")
        if date_str:
            # Try parsing common email date formats
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(date_str)
                age_hours = round((now - dt.astimezone(TZ)).total_seconds() / 3600, 1)
                flagged_old = age_hours > 24
            except Exception:
                pass

        messages.append({
            "subject": headers.get("Subject", "(no subject)"),
            "sender": headers.get("From", ""),
            "date": date_str,
            "age_hours": age_hours,
            "flagged_old": flagged_old,
        })

    return {
        "unread_count": result.get("resultSizeEstimate", len(messages_meta)),
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Source: google_calendar
# ---------------------------------------------------------------------------
@source("google_calendar", "Google Calendar events for today and tomorrow")
def _google_calendar():
    tokens = _CREDS.get("gcal_tokens")
    keys = _CREDS.get("gcal_keys")
    if not tokens or not keys:
        return {"status": "error", "error": "Google Calendar OAuth not configured"}

    creds = _build_google_creds(tokens, keys)

    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    now = datetime.datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = (today_start + datetime.timedelta(days=2))

    calendar_ids = ["primary"]
    # Try to find "Work" calendar
    try:
        cal_list = service.calendarList().list().execute()
        for cal in cal_list.get("items", []):
            if "work" in (cal.get("summary", "") or "").lower():
                calendar_ids.append(cal["id"])
                break
    except Exception:
        pass

    today_events = []
    tomorrow_events = []
    tomorrow_start = today_start + datetime.timedelta(days=1)

    for cal_id in calendar_ids:
        try:
            events_result = service.events().list(
                calendarId=cal_id,
                timeMin=today_start.isoformat(),
                timeMax=tomorrow_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
        except Exception:
            continue

        cal_name = cal_id if cal_id != "primary" else "primary"
        for evt in events_result.get("items", []):
            start = evt.get("start", {}).get("dateTime") or evt.get("start", {}).get("date", "")
            end = evt.get("end", {}).get("dateTime") or evt.get("end", {}).get("date", "")
            entry = {
                "summary": evt.get("summary", "(no title)"),
                "start": start,
                "end": end,
                "calendar": cal_name,
            }

            # Determine if today or tomorrow
            try:
                evt_dt = datetime.datetime.fromisoformat(start)
                if evt_dt.date() >= tomorrow_start.date():
                    tomorrow_events.append(entry)
                else:
                    today_events.append(entry)
            except (ValueError, TypeError):
                today_events.append(entry)

    return {"today": today_events, "tomorrow": tomorrow_events}


# ---------------------------------------------------------------------------
# Source: syncthing_status
# ---------------------------------------------------------------------------
@source("syncthing_status", "Syncthing sync state and conflict files")
def _syncthing_status():
    api_key = _CREDS.get("syncthing_api_key")
    if not api_key:
        return {"status": "error", "error": "Syncthing API key not found"}

    headers = {"X-API-Key": api_key}
    base = "http://localhost:8384"

    try:
        sys_status = _http_get(f"{base}/rest/system/status", headers, timeout=5)
    except Exception as e:
        return {"status": "error", "error": f"Syncthing not reachable: {e}"}

    try:
        connections = _http_get(f"{base}/rest/system/connections", headers, timeout=5)
    except Exception:
        connections = {}

    conn_data = connections.get("connections", {})
    total_devices = len(conn_data)
    connected = sum(1 for d in conn_data.values() if d.get("connected"))

    # Scan for sync conflict files in vault
    vault = Path.home() / "vault"
    conflict_files = []
    if vault.exists():
        for f in vault.rglob("*.sync-conflict-*"):
            conflict_files.append(str(f.relative_to(vault)))

    return {
        "connected_devices": connected,
        "total_devices": total_devices,
        "conflict_files": conflict_files[:50],  # cap for sanity
        "conflict_count": len(conflict_files),
        "my_id": sys_status.get("myID", "")[:12] + "...",
    }


# ---------------------------------------------------------------------------
# Source: browser_history
# ---------------------------------------------------------------------------
@source("browser_history", "Safari browsing history (last 24h)")
def _browser_history():
    src = Path.home() / "Library/Safari/History.db"
    if not src.exists():
        return {"status": "error", "error": "Safari History.db not found"}

    tmp_copy = Path("/tmp/safari-intake-copy.db")
    shutil.copy2(src, tmp_copy)

    # Safari/Core Data epoch: 2001-01-01
    core_data_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now_utc - datetime.timedelta(hours=24) - core_data_epoch).total_seconds()

    trivial = {"google.com", "www.google.com", "localhost", "github.com",
               "www.github.com", "127.0.0.1", "about:blank"}

    conn = sqlite3.connect(str(tmp_copy))
    try:
        rows = conn.execute("""
            SELECT hi.url, hv.title, hv.visit_time
            FROM history_visits hv
            JOIN history_items hi ON hi.id = hv.history_item
            WHERE hv.visit_time > ?
            ORDER BY hv.visit_time DESC
        """, (cutoff,)).fetchall()
    except sqlite3.OperationalError as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()
        tmp_copy.unlink(missing_ok=True)

    urls = []
    seen = set()
    for url, title, ts in rows:
        # Filter trivials
        try:
            host = url.split("/")[2].lower()
        except (IndexError, AttributeError):
            continue
        if host in trivial:
            continue
        if url in seen:
            continue
        seen.add(url)

        visit_dt = core_data_epoch + datetime.timedelta(seconds=ts)
        urls.append({
            "url": url,
            "title": title or "",
            "visit_time": visit_dt.astimezone(TZ).isoformat(),
        })

    return {"urls": urls[:200], "count": len(urls)}


# ---------------------------------------------------------------------------
# Source: screen_time
# ---------------------------------------------------------------------------
@source("screen_time", "macOS screen time by app (last 24h)")
def _screen_time():
    db_path = Path.home() / "Library/Application Support/Knowledge/knowledgeC.db"
    if not db_path.exists():
        return {"status": "error", "error": "knowledgeC.db not found"}

    # Core Data epoch
    core_data_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now_utc - datetime.timedelta(hours=24) - core_data_epoch).total_seconds()

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("""
            SELECT
                ZOBJECT.ZVALUESTRING,
                (ZOBJECT.ZENDDATE - ZOBJECT.ZSTARTDATE) as duration
            FROM ZOBJECT
            WHERE ZSTREAMNAME = '/app/usage'
              AND ZSTARTDATE > ?
        """, (cutoff,)).fetchall()
        conn.close()
    except sqlite3.OperationalError as e:
        return {"status": "error", "error": f"SIP may block access: {e}"}

    # Aggregate by app
    by_app: Dict[str, float] = {}
    for bundle_id, duration in rows:
        if bundle_id and duration and duration > 0:
            by_app[bundle_id] = by_app.get(bundle_id, 0) + duration

    # Convert to minutes, filter >10min
    apps = []
    total_min = 0
    for bid, sec in sorted(by_app.items(), key=lambda x: -x[1]):
        minutes = round(sec / 60, 1)
        if minutes >= 10:
            apps.append({"bundle_id": bid, "minutes": minutes})
        total_min += minutes

    return {"apps": apps, "total_minutes": round(total_min, 1)}


# ---------------------------------------------------------------------------
# Source: imessage_recent
# ---------------------------------------------------------------------------
@source("imessage_recent", "iMessage conversation metadata (last 24h)")
def _imessage_recent():
    db_path = Path.home() / "Library/Messages/chat.db"
    if not db_path.exists():
        return {"status": "error", "error": "chat.db not found"}

    # Apple epoch: nanoseconds since 2001-01-01 (stored as seconds * 1e9)
    core_data_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff_ns = int((now_utc - datetime.timedelta(hours=24) - core_data_epoch).total_seconds() * 1e9)

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("""
            SELECT
                h.id as contact,
                COUNT(m.rowid) as msg_count,
                MAX(m.date) as last_date,
                SUM(CASE WHEN m.is_from_me = 0 AND m.date = (
                    SELECT MAX(m2.date) FROM message m2
                    JOIN chat_message_join cmj2 ON cmj2.message_id = m2.rowid
                    JOIN chat_handle_join chj2 ON chj2.chat_id = cmj2.chat_id
                    WHERE chj2.handle_id = h.rowid
                ) THEN 1 ELSE 0 END) as last_is_inbound
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.rowid
            JOIN chat_handle_join chj ON chj.chat_id = cmj.chat_id
            JOIN handle h ON h.rowid = chj.handle_id
            WHERE m.date > ?
            GROUP BY h.id
            ORDER BY last_date DESC
        """, (cutoff_ns,)).fetchall()
        conn.close()
    except sqlite3.OperationalError as e:
        return {"status": "error", "error": f"Cannot read chat.db: {e}"}

    conversations = []
    for contact, count, last_date, last_inbound in rows:
        last_dt = core_data_epoch + datetime.timedelta(seconds=last_date / 1e9)
        conversations.append({
            "contact": contact,
            "message_count": count,
            "last_message_time": last_dt.astimezone(TZ).isoformat(),
            "has_unanswered": bool(last_inbound),
        })

    return {"conversations": conversations}


# ---------------------------------------------------------------------------
# Source: slack (stub)
# ---------------------------------------------------------------------------
@source("slack", "Slack workspace messages (stub)")
def _slack():
    return {"status": "not_configured", "data": None, "error": "No SLACK_BOT_TOKEN configured"}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_source(name: str, timeout_sec: int = 30) -> dict:
    """Execute a single source with timeout and error handling."""
    entry = _SOURCES[name]
    t0 = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(entry["fn"])
            data = future.result(timeout=timeout_sec)
        elapsed = round((time.monotonic() - t0) * 1000)
        return {"status": "ok", "elapsed_ms": elapsed, "data": data}
    except FuturesTimeout:
        elapsed = round((time.monotonic() - t0) * 1000)
        return {"status": "timeout", "elapsed_ms": elapsed, "data": None, "error": f"Timed out after {timeout_sec}s"}
    except Exception as e:
        elapsed = round((time.monotonic() - t0) * 1000)
        return {"status": "error", "elapsed_ms": elapsed, "data": None, "error": str(e)}


def run_all(source_names: Optional[List[str]] = None, timeout_sec: int = 30) -> dict:
    """Run all (or selected) sources in parallel."""
    names = source_names or list(_SOURCES.keys())
    t0 = time.monotonic()

    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(run_source, name, timeout_sec): name for name in names}
        for future in futures:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = {"status": "error", "elapsed_ms": 0, "data": None, "error": str(e)}

    total_ms = round((time.monotonic() - t0) * 1000)

    return {
        "generated_at": datetime.datetime.now(TZ).isoformat(),
        "generated_on": platform.node().split(".")[0],
        "duration_ms": total_ms,
        "sources": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_timing_table(output: dict):
    """Print a formatted timing table for --test mode."""
    print(f"\n{'Source':<25} {'Status':<10} {'Time':>8}  Notes")
    print("-" * 70)
    for name, result in sorted(output["sources"].items()):
        status = result["status"]
        ms = result.get("elapsed_ms", 0)
        time_str = f"{ms:>6}ms"
        notes = ""
        if status == "error":
            notes = result.get("error", "")[:40]
        elif status == "ok" and result.get("data"):
            d = result["data"]
            if isinstance(d, dict):
                # Show a useful summary stat
                for key in ["total_completed", "total_action_items", "total_hours",
                            "total_open", "unread_count", "count", "total_minutes",
                            "connected_devices", "conflict_count"]:
                    if key in d:
                        notes = f"{key}={d[key]}"
                        break
                if not notes and "conversations" in d:
                    notes = f"conversations={len(d['conversations'])}"
                if not notes and "today" in d and isinstance(d["today"], list):
                    notes = f"today={len(d['today'])} events"
                if not notes and "properties" in d:
                    notes = f"properties={len(d['properties'])}"

        status_display = {"ok": "OK", "error": "ERR", "timeout": "TIMEOUT"}.get(status, status)
        print(f"  {name:<23} {status_display:<10} {time_str}  {notes}")

    print("-" * 70)
    print(f"  {'TOTAL':<23} {'':10} {output['duration_ms']:>6}ms")
    print(f"\n  Generated: {output['generated_at']}")
    print(f"  Host: {output['generated_on']}\n")


def main():
    parser = argparse.ArgumentParser(description="Dream intake: consolidated data source aggregator")
    parser.add_argument("--output", default=str(Path.home() / "vault/i447/i446/dream-runs/dream-intake-latest.json"))
    parser.add_argument("--sources", help="Comma-separated source names (default: all)")
    parser.add_argument("--test", action="store_true", help="Run all sources, print timing table, don't write")
    parser.add_argument("--dry-run", action="store_true", help="Write output to stdout instead of file")
    parser.add_argument("--run-dir", help="Also write a timestamped copy to this directory")
    args = parser.parse_args()

    load_credentials()

    source_names = args.sources.split(",") if args.sources else None
    if source_names:
        invalid = [s for s in source_names if s not in _SOURCES]
        if invalid:
            print(f"Unknown sources: {', '.join(invalid)}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(_SOURCES.keys()))}", file=sys.stderr)
            sys.exit(1)

    output = run_all(source_names)

    if args.test:
        print_timing_table(output)
        # Count successes
        ok = sum(1 for r in output["sources"].values() if r["status"] == "ok")
        total = len(output["sources"])
        print(f"  {ok}/{total} sources succeeded\n")
        sys.exit(0 if ok > 0 else 1)

    output_json = json.dumps(output, indent=2, ensure_ascii=False)

    if args.dry_run:
        print(output_json)
        sys.exit(0)

    # Write main output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output_json)
    print(f"Wrote {out_path} ({len(output_json)} bytes)")

    # Write timestamped copy if --run-dir
    if args.run_dir:
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(TZ).strftime("%Y.%m.%d-%H%M%S")
        ts_path = run_dir / f"dream-intake-{ts}.json"
        ts_path.write_text(output_json)
        print(f"Wrote {ts_path}")


if __name__ == "__main__":
    main()
