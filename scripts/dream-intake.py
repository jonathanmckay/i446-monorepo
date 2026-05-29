#!/usr/bin/env python3
"""dream-intake: Gather context from 12 sources into a single JSON snapshot.

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
# Source 1: completed_today
# ---------------------------------------------------------------------------

@source("completed_today", "Today's completed tasks from ibx queue")
def _completed_today():
    ct_path = Path.home() / "vault" / "z_ibx" / "completed-today.json"
    if not ct_path.exists():
        return {"tasks": [], "note": "completed-today.json not found"}

    with open(ct_path) as f:
        data = json.load(f)

    tasks = data if isinstance(data, list) else data.get("tasks", data.get("items", []))

    # Check for staleness (file older than 2 hours)
    mtime = ct_path.stat().st_mtime
    age_hours = (time.time() - mtime) / 3600
    stale = age_hours > 2

    # Look for archive
    archive_path = ct_path.with_name("completed-today-archive.json")
    archive_count = 0
    if archive_path.exists():
        try:
            with open(archive_path) as f:
                archive = json.load(f)
            archive_items = archive if isinstance(archive, list) else archive.get("tasks", [])
            archive_count = len(archive_items)
            # Merge: add archived items not already present
            existing_ids = {t.get("id") for t in tasks if isinstance(t, dict)}
            for item in archive_items:
                if isinstance(item, dict) and item.get("id") not in existing_ids:
                    tasks.append(item)
        except Exception:
            pass

    return {
        "count": len(tasks),
        "tasks": tasks,
        "stale": stale,
        "age_hours": round(age_hours, 1),
        "archive_merged": archive_count,
    }


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


@source("appfolio_rent_roll", "AppFolio occupancy summary with core portfolio metrics")
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

    data = _http_request(url, method="POST", headers=headers, body={})
    results = data.get("results", [])

    today = datetime.date.today()
    by_property: dict[str, dict] = {}
    for row in results:
        prop = row.get("property_name", row.get("property", "Unknown"))
        if prop not in by_property:
            by_property[prop] = {"open": 0, "overdue": 0, "total": 0}
        p = by_property[prop]
        p["total"] += 1

        status = (row.get("status", "") or "").lower()
        if "open" in status or "in progress" in status or "pending" in status:
            p["open"] += 1
            # Check overdue
            due = row.get("due_date", "")
            if due:
                try:
                    due_date = datetime.date.fromisoformat(due[:10])
                    if due_date < today:
                        p["overdue"] += 1
                except ValueError:
                    pass

    total_open = sum(p["open"] for p in by_property.values())
    total_overdue = sum(p["overdue"] for p in by_property.values())

    return {
        "total_work_orders": len(results),
        "total_open": total_open,
        "total_overdue": total_overdue,
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

@source("google_calendar", "Today + tomorrow events from Google Calendar")
def _google_calendar():
    creds = _build_google_creds(
        _CREDS["gcal_tokens_path"],
        _CREDS["gcal_secrets_path"],
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        account_key="m5c7",
    )
    service = _google_service("calendar", "v3", creds)

    today = datetime.date.today()
    day_after_tomorrow = today + datetime.timedelta(days=2)
    time_min = f"{today.isoformat()}T00:00:00Z"
    time_max = f"{day_after_tomorrow.isoformat()}T00:00:00Z"

    calendar_ids = ["primary"]
    # Try to find "Work" calendar
    try:
        cal_list = service.calendarList().list().execute()
        for cal in cal_list.get("items", []):
            summary = (cal.get("summary", "") or "").lower()
            if summary == "work" or "work" in summary:
                calendar_ids.append(cal["id"])
                break
    except Exception:
        pass

    all_events = []
    for cal_id in calendar_ids:
        try:
            events_resp = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            for ev in events_resp.get("items", []):
                start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
                end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
                all_events.append({
                    "calendar": cal_id,
                    "summary": ev.get("summary", "(no title)"),
                    "start": start,
                    "end": end,
                    "status": ev.get("status", ""),
                    "attendees": len(ev.get("attendees", [])),
                })
        except Exception:
            pass

    # Split into today / tomorrow
    today_events = []
    tomorrow_events = []
    tomorrow_str = (today + datetime.timedelta(days=1)).isoformat()
    for ev in all_events:
        if ev["start"].startswith(tomorrow_str):
            tomorrow_events.append(ev)
        else:
            today_events.append(ev)

    return {
        "today_count": len(today_events),
        "tomorrow_count": len(tomorrow_events),
        "today": today_events,
        "tomorrow": tomorrow_events,
    }


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

@source("outlook_inbox", "Outlook unread emails and recent sent (last 24h)")
def _outlook_inbox():
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
    raw = mcp.call_tool("mail", "SearchMessagesQueryParameters", {
        "queryParameters": query
    }, timeout=30)
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
    }, timeout=30)
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
    sys.path.insert(0, str(Path.home() / "i446-monorepo/tools/ibx"))
    import agency_mcp as mcp
    from datetime import timezone
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    day_after = (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
    raw = mcp.call_tool("mail", "GetCalendarEvents", {
        "startDateTime": yesterday,
        "endDateTime": day_after,
        "$top": "50",
    }, timeout=30)
    events = []
    if raw and raw.get("content"):
        for item in raw["content"]:
            text = item.get("text", "")
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
            return name, {
                "status": "ok",
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

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for name, info in sources_to_run.items():
            fut = pool.submit(_run_one, name, info)
            futures[fut] = name

        for fut in futures:
            try:
                name, result = fut.result(timeout=timeout)
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
                    notes = d["status"]
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
    err = sum(1 for r in results.values() if r["status"] != "ok")
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
