#!/usr/bin/env python3
"""
ibx0 — Unified inbox: Gmail + iMessages + Slack in one queue.
Fetches from all sources, sorts by timestamp, presents as a single card stream.
"""

import json
import os
import queue
import re
import readline  # enables line editing (backspace, arrows) in input()
import select
import subprocess
import sys
import threading
import time
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

# ── Import source modules ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import ibx as _ibx
import imsg as _imsg
import slack as _slack
# Use the same response-time clamp as the personal dashboard so /ibx
# stats match what the project blocking dashboard reports.
sys.path.insert(0, str(Path(__file__).parent.parent / "personal-dashboard"))
try:
    from comms_response_clamp import clamp_response_hours_unix as _clamp_response_hours_unix
except (Exception, SystemExit):
    _clamp_response_hours_unix = None
try:
    import outlook_agency as _outlook
    _outlook_available = True
except (Exception, SystemExit):
    try:
        import outlook_workiq as _outlook
        _outlook_available = True
    except ImportError:
        _outlook_available = False

try:
    import teams_agency as _teams
    _teams_available = True
except (Exception, SystemExit):
    try:
        import teams_workiq as _teams
        _teams_available = True
    except ImportError:
        _teams_available = False

# ── Auto-sign integration (optional — skipped if module not found) ────────────
_AUTOSIGN_DIR = Path(__file__).parent.parent / "m5x2-automations"
sys.path.insert(0, str(_AUTOSIGN_DIR))
try:
    import lease_signer as _signer
    import automations_db as _autodb
    from config import AUTOSIGN_SENDERS, DB_PATH as _AUTODB_PATH
    _autosign_available = True
except (ImportError, SystemExit, Exception):
    _autosign_available = False

import anthropic
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()
ai = anthropic.Anthropic()


# ── /-2n module loader (filename has leading dash, can't import normally) ────
def _load_two_n():
    import importlib.util
    p = Path(__file__).parent / "-2n.py"
    spec = importlib.util.spec_from_file_location("_two_n_mod", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


_two_n = _load_two_n()

# ── Inbox habit marking ──────────────────────────────────────────────────────
_habits_marked = False  # once per session

_IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
_IBX_HABITS = ["ibx - s897", "ibx i9", "slack github", "slack m5x2", "ibx m5x2", "teams"]
_TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"


def _write_neon_habit(habit_name: str) -> bool:
    """Write 1 to a habit column in 0₦ for today, via ix-osa.sh."""
    now = datetime.now()
    script = f'''tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set theSheet to sheet "0n" of wb
    set targetMonth to {now.month}
    set targetDay to {now.day}
    set habitName to "{habit_name}"
    set habitCol to 0
    repeat with c from 1 to 60
        set cellVal to value of cell c of row 1 of theSheet
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed = habitName then
                set habitCol to c
                exit repeat
            end if
        end if
    end repeat
    if habitCol = 0 then return "ERROR: habit '" & habitName & "' not found in row 1"
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = targetMonth and d = targetDay then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date not found"
    set value of cell habitCol of row todayRow of theSheet to 1
    return "OK"
end tell'''
    try:
        result = subprocess.run(
            ["bash", str(_IX_OSA)],
            input=script, capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _close_todoist_habits():
    """Close matching 0neon-labeled Todoist tasks for inbox habits."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            "https://api.todoist.com/api/v1/tasks?label=0neon&limit=200",
            headers={"Authorization": f"Bearer {_TODOIST_TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            tasks = json.loads(resp.read())
    except Exception:
        return

    # Match tasks whose content matches an inbox habit name
    habit_set = {h.lower() for h in _IBX_HABITS}
    for task in tasks:
        content = task.get("content", "").lower().strip()
        if content in habit_set:
            try:
                close_req = urllib.request.Request(
                    f"https://api.todoist.com/api/v1/tasks/{task['id']}/close",
                    method="POST",
                    headers={"Authorization": f"Bearer {_TODOIST_TOKEN}"},
                )
                urllib.request.urlopen(close_req, timeout=10)
            except Exception:
                pass


def _mark_inbox_habits_done():
    """Mark all inbox-related habits as done in neon (0₦) and Todoist.

    Writes directly via ix-osa.sh AppleScript (no Claude CLI dependency).
    Called when inbox zero is first achieved. Idempotent within a session."""
    global _habits_marked
    if _habits_marked:
        return
    _habits_marked = True
    console.print("[dim]  marking inbox habits done...[/dim]")

    # Write to neon in parallel threads for speed
    results = {}

    def _write(habit):
        results[habit] = _write_neon_habit(habit)

    threads = [threading.Thread(target=_write, args=(h,)) for h in _IBX_HABITS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    ok = sum(1 for v in results.values() if v)
    fail = len(_IBX_HABITS) - ok
    if fail:
        console.print(f"[dim yellow]  neon: {ok}/{len(_IBX_HABITS)} written ({fail} failed)[/dim yellow]")
    else:
        console.print(f"[dim green]  ✓ neon: {ok} habits marked[/dim green]")

    # Close Todoist tasks in background (non-blocking)
    threading.Thread(target=_close_todoist_habits, daemon=True).start()


def _render_block_status():
    """Print the current 2h block's -1g status panel + response stats (idle screen)."""
    if _two_n is None or not hasattr(_two_n, "render_block_status_panel"):
        return
    try:
        block_replies = _block_reply_count()
        block_archived = _block_archive_count()
        day_avg, day_count = _day_avg_response()
        day_archived = _day_archive_count()
        stats_lines = [f"[dim]📨 {block_replies} replied · 📦 {block_archived} archived this block[/dim]"]
        day_parts = []
        if day_avg is not None:
            day_parts.append(f"📨 {day_count} replied, avg {day_avg:.0f}m")
        day_parts.append(f"📦 {day_archived} archived today")
        stats_lines.append(f"[dim]{' · '.join(day_parts)}[/dim]")
        stats_line = "\n".join(stats_lines)
        console.print(_two_n.render_block_status_panel(stats_line=stats_line))
    except Exception:
        pass

# ── Single-line fetch status ─────────────────────────────────────────────────
_fetch_status: dict[str, str] = {}  # source -> status string
_live: "Live | None" = None  # set during concurrent fetch

_SOURCE_ORDER = ["Gmail", "iMsg", "Slack", "Outlook", "Teams"]

def _status_line() -> Text:
    """Render current fetch status as a single Rich Text line."""
    parts = Text()
    for i, src in enumerate(_SOURCE_ORDER):
        if i > 0:
            parts.append(" | ", style="dim")
        status = _fetch_status.get(src, "")
        if not status:
            parts.append(src, style="dim")
        elif status.startswith("✓"):
            parts.append(f"{src} {status}", style="green")
        elif status.startswith("✗"):
            parts.append(f"{src} {status}", style="yellow")
        else:
            parts.append(f"{src} {status}", style="dim")
    return parts

def _update_status(source: str, status: str):
    """Update a source's status and refresh the live display."""
    _fetch_status[source] = status
    if _live is not None:
        _live.update(_status_line())

# ── Type badge colors ─────────────────────────────────────────────────────────
TYPE_STYLE = {
    "email": "bold green",
    "imsg":  "bold magenta",
    "slack": "bold blue",
    "outlook": "bold cyan",
    "teams": "bold yellow",
}
TYPE_LABEL = {
    "email": "EMAIL",
    "imsg":  "IMSG",
    "slack": "SLACK",
    "outlook": "OUTLOOK",
    "teams": "TEAMS",
}

# ── Archive log (SQLite) — single source of truth for all reply/archive stats ─
_ARCHIVE_DB_PATH = Path.home() / ".config" / "ibx" / "archive_log.db"
_archive_db_conn = None


def _get_archive_db():
    """Lazy-init SQLite connection for archive logging."""
    global _archive_db_conn
    if _archive_db_conn is not None:
        return _archive_db_conn
    import sqlite3
    _ARCHIVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _archive_db_conn = sqlite3.connect(str(_ARCHIVE_DB_PATH))
    _archive_db_conn.execute("""
        CREATE TABLE IF NOT EXISTS archive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            item_uid TEXT NOT NULL,
            action TEXT NOT NULL,
            message_type TEXT NOT NULL,
            response_min REAL,
            UNIQUE(item_uid, action)
        )
    """)
    # Migrate existing DBs that lack the response_min column
    try:
        _archive_db_conn.execute("ALTER TABLE archive_log ADD COLUMN response_min REAL")
    except Exception:
        pass  # column already exists
    # Prune entries older than 7 days
    _archive_db_conn.execute(
        "DELETE FROM archive_log WHERE timestamp < ?",
        (time.time() - 7 * 86400,),
    )
    _archive_db_conn.commit()
    return _archive_db_conn


def _compute_response_min(item):
    """Compute response time in minutes for an item. Returns None if unavailable."""
    received = item.get("received_at", 0.0)
    if not received:
        return None
    now = time.time()
    if _clamp_response_hours_unix is not None:
        elapsed_min = _clamp_response_hours_unix(now, received) * 60.0
    else:
        elapsed_min = (now - received) / 60
    if elapsed_min < 0 or elapsed_min > 10080:
        return None
    return elapsed_min


def _log_archive(item, action):
    """Record a confirmed archive/delete/reply action. Never raises."""
    try:
        uid = _item_uid(item)
        if not uid:
            return
        uid_str = f"{uid[0]}:{uid[1]}"
        resp_min = _compute_response_min(item) if action == "reply" else None
        db = _get_archive_db()
        db.execute(
            "INSERT OR IGNORE INTO archive_log (timestamp, item_uid, action, message_type, response_min) VALUES (?, ?, ?, ?, ?)",
            (time.time(), uid_str, action, item.get("type", ""), resp_min),
        )
        db.commit()
    except Exception:
        pass


def _block_archive_count():
    """Count distinct items archived/processed during the current 2h block."""
    if not _two_n:
        return 0
    try:
        _, _, block_start, _ = _two_n.get_current_block()
        now = datetime.now()
        sh, sm = map(int, block_start.split(":"))
        block_start_epoch = now.replace(hour=sh, minute=sm, second=0, microsecond=0).timestamp()
        db = _get_archive_db()
        cursor = db.execute(
            "SELECT COUNT(DISTINCT item_uid) FROM archive_log WHERE timestamp >= ?",
            (block_start_epoch,),
        )
        return cursor.fetchone()[0]
    except Exception:
        return 0


def _block_reply_count():
    """Count distinct replies made during the current 2h block (from archive_log)."""
    if not _two_n:
        return 0
    try:
        _, _, block_start, _ = _two_n.get_current_block()
        now = datetime.now()
        sh, sm = map(int, block_start.split(":"))
        block_start_epoch = now.replace(hour=sh, minute=sm, second=0, microsecond=0).timestamp()
        db = _get_archive_db()
        cursor = db.execute(
            "SELECT COUNT(DISTINCT item_uid) FROM archive_log WHERE timestamp >= ? AND action = 'reply'",
            (block_start_epoch,),
        )
        return cursor.fetchone()[0]
    except Exception:
        return 0


def _day_avg_response():
    """Return (avg_minutes, reply_count) for the day from archive_log. Returns (None, 0) if no replies.
    Count includes ALL replies; avg only uses those with response_min data."""
    try:
        today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        db = _get_archive_db()
        cursor = db.execute(
            "SELECT AVG(response_min), "
            "  (SELECT COUNT(DISTINCT item_uid) FROM archive_log WHERE timestamp >= ? AND action = 'reply') "
            "FROM archive_log WHERE timestamp >= ? AND action = 'reply' AND response_min IS NOT NULL",
            (today_midnight, today_midnight),
        )
        row = cursor.fetchone()
        avg, count = row[0], row[1]
        if count == 0:
            return None, 0
        return avg, count
    except Exception:
        return None, 0


def _day_archive_count():
    """Count distinct items archived today (all actions)."""
    try:
        today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        db = _get_archive_db()
        cursor = db.execute(
            "SELECT COUNT(DISTINCT item_uid) FROM archive_log WHERE timestamp >= ?",
            (today_midnight,),
        )
        return cursor.fetchone()[0]
    except Exception:
        return 0


def _parse_received_at(item) -> float:
    """Extract received epoch from any item type. Returns 0.0 if unavailable."""
    try:
        t = item["type"]
        if t in ("email", "outlook", "teams"):
            date_str = item.get("_data", {}).get("email", {}).get("date", "")
            if not date_str:
                date_str = item.get("_data", {}).get("date", "")
            if date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                return dt.timestamp()
        elif t == "slack":
            ts = item.get("ts", 0.0)
            if ts:
                return float(ts)
        elif t == "imsg":
            date_str = item.get("_data", {}).get("thread", {}).get("latest_date", "")
            if date_str:
                # Format like "apr 05, 01:30pm"
                dt = datetime.strptime(date_str, "%b %d, %I:%M%p")
                dt = dt.replace(year=datetime.now().year)
                return dt.timestamp()
    except Exception:
        pass
    return 0.0

def _print_response_stats(item):
    """Print response time for this reply and running day average.
    Data is already persisted in archive_log via _log_archive; this just prints."""
    elapsed_min = _compute_response_min(item)
    if elapsed_min is None:
        return
    # Query running average from archive_log
    day_avg, day_count = _day_avg_response()
    def fmt(m):
        return f"{m:.0f}m"
    if day_count > 1:
        console.print(
            f"[dim]⏱ Response time: {fmt(elapsed_min)} · "
            f"Day avg: {fmt(day_avg)} ({day_count} replies)[/dim]"
        )
    else:
        console.print(
            f"[dim]⏱ Response time: {fmt(elapsed_min)} (1st reply)[/dim]"
        )

# ── Normalize items ───────────────────────────────────────────────────────────

def normalize_email(msg_ref, service, account):
    """Fetch and normalize a Gmail message into a unified item."""
    try:
        email = _ibx.get_email(service, msg_ref["id"])
        email["_account"] = account
    except Exception as e:
        return None
    # Skip messages sent by the user (thread-level inbox can surface sent replies)
    from_addr = re.search(r'<([^>]+)>', email.get("from", ""))
    if from_addr and from_addr.group(1).lower() in MY_EMAILS:
        return None
    item = {
        "type": "email",
        "source": ACCOUNT_DISPLAY.get(account, account),
        "from": email.get("from", ""),
        "to": email.get("to", ""),
        "cc": email.get("cc", ""),
        "preview": email.get("subject", "(no subject)"),
        "body": email.get("body", ""),
        "ts": 0.0,  # Gmail doesn't give easy sort ts; use fetch order
        "_data": {"email": email, "service": service},
    }
    item["received_at"] = _parse_received_at(item)
    return item

def normalize_imsg(thread):
    """Normalize an iMessage thread card into a unified item."""
    try:
        # Parse latest_date like "apr 05, 01:30pm"
        ts = 0.0
    except Exception:
        ts = 0.0
    item = {
        "type": "imsg",
        "source": "iMessage",
        "from": thread.get("display_name", ""),
        "preview": thread.get("last_message", "")[:80],
        "body": thread.get("conversation", ""),
        "ts": ts,
        "_data": {"thread": thread},
    }
    item["received_at"] = _parse_received_at(item)
    return item

def normalize_slack(thread, token, workspace):
    """Normalize a Slack thread into a unified item."""
    try:
        ts = float(thread.get("latest_ts", 0))
    except Exception:
        ts = 0.0
    msgs = thread.get("messages", [])
    body = "\n".join(f"{m['sender']} ({m['time']}): {m['text']}" for m in msgs)
    item = {
        "type": "slack",
        "source": workspace,
        "from": thread.get("display_name", ""),
        "preview": msgs[-1]["text"][:80] if msgs else "",
        "body": body,
        "ts": ts,
        "_data": {"thread": thread, "token": token, "workspace": workspace},
    }
    item["received_at"] = _parse_received_at(item)
    return item

# ── Display ───────────────────────────────────────────────────────────────────

def display_card(item, idx, total):
    console.print()
    t = item["type"]
    badge = f"[{TYPE_STYLE[t]}] {TYPE_LABEL[t]} [/{TYPE_STYLE[t]}]"
    source = f"[dim]{item['source']}[/dim]"
    counter = f"[dim][{idx}/{total}][/dim]"

    header = Text()
    header.append(f"FROM:    ", style="bold dim")
    header.append(item["from"] or "(unknown)", style="bold cyan")
    if item.get("to"):
        header.append(f"\nTO:      ", style="bold dim")
        header.append(item["to"], style="dim white")
    if item.get("cc"):
        header.append(f"\nCC:      ", style="bold dim")
        header.append(item["cc"], style="dim white")
    header.append(f"\nSUBJECT: ", style="bold dim")
    header.append(item["preview"], style="white")
    # Show message count for grouped Teams threads
    msg_count = item.get("_data", {}).get("msg_count", 0)
    if msg_count > 1:
        header.append(f"  ({msg_count} messages)", style="bold yellow")

    # Show received date/time in local time if available
    date_str = item.get("_data", {}).get("date", "")
    if date_str:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            local_dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
            header.append(f"\nDATE:    ", style="bold dim")
            header.append(local_dt.strftime("%a %b %d, %I:%M %p %Z"), style="dim white")
        except Exception:
            pass

    title = f"{badge}  {source}  {counter}"
    console.print(Panel(header, title=title, border_style="dim", box=box.SIMPLE_HEAD, padding=(0, 1)))

    # Body — show last ~2000 chars, cap at 20 lines after wrapping
    # Keep the LAST lines (newest messages) when truncating
    raw_body = item["body"][-2000:] if item["body"] else "(no content)"
    import shutil
    term_width = shutil.get_terminal_size().columns - 6  # panel padding + border
    # Wrap long lines manually to control line count
    wrapped = []
    for line in raw_body.splitlines():
        while len(line) > term_width:
            wrapped.append(line[:term_width])
            line = line[term_width:]
        wrapped.append(line)
    # Store full wrapped body for expand; truncate display to last 20 lines
    item["_full_body"] = "\n".join(wrapped)
    if len(wrapped) > 20:
        wrapped = ["…"] + wrapped[-20:]
    body = "\n".join(wrapped)
    console.print(Panel(body, box=box.SIMPLE, border_style="dim", padding=(0, 1)))

def print_help():
    console.print(
        "\n[dim]Commands:[/dim]  "
        "[bold]a[/bold] archive/done  "
        "[bold]d[/bold] delete  "
        "[bold]r <text>[/bold] reply  "
        "[bold]R <text>[/bold] reply-all  "
        "[bold]p <instruction>[/bold] pipe (AI draft)  "
        "[bold]s[/bold] skip  "
        "[bold]t <text>[/bold] todo  "
        "[bold]e[/bold] expand  "
        "[bold]o[/bold] open  "
        "[bold]c[/bold] check now  "
        "[bold]f[/bold] fetch new  "
        "[bold]q[/bold] quit  "
        "[bold]R[/bold] reload  "
        "[dim]or type anything → Claude[/dim]\n"
    )

# ── Auto-sign ────────────────────────────────────────────────────────────────

_NOTIFY_THRESHOLD = 5  # send email confirmation for first N signings

def _send_signing_notification(item, meta, result, signing_count):
    """Send mckay@m5c7.com a confirmation email for the first N auto-signings."""
    try:
        import email.mime.text, base64
        svc = item["_data"]["service"]
        unit     = meta.get("unit", "unknown unit")
        tenants  = meta.get("tenants", "")
        ltype    = meta.get("lease_type", "renewal")
        status   = result.get("status", "unknown")
        body = (
            f"Auto-sign #{signing_count} completed.\n\n"
            f"Unit:    {unit}\n"
            f"Tenants: {tenants}\n"
            f"Type:    {ltype}\n"
            f"Status:  {status}\n"
            f"From:    {item.get('from', '')}\n"
            f"Subject: {item.get('preview', '')}\n\n"
            f"(Notifications stop after {_NOTIFY_THRESHOLD} successful signings.)"
        )
        msg = email.mime.text.MIMEText(body)
        msg["To"]      = "mckay@m5c7.com"
        msg["From"]    = _ibx.SEND_FROM
        msg["Subject"] = f"✓ Auto-signed lease: {unit}"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        console.print(f"[dim yellow]  notification email failed: {e}[/dim yellow]")


_autosign_threads: list = []  # tracked so we can join before exit

def _wait_for_autosign(timeout=300):
    """Block until all pending autosign threads finish (max timeout seconds)."""
    pending = [t for t in _autosign_threads if t.is_alive()]
    if pending:
        console.print(f"[dim cyan]  ⏳ waiting for {len(pending)} lease signing(s) to complete...[/dim cyan]")
        for t in pending:
            t.join(timeout=timeout)


def _autosign_item(item):
    """Sign a lease, archive the email, log to DB."""
    url = _signer.extract_appfolio_url(item.get("body", ""))
    if not url:
        # Fallback: check HTML body (forwarded emails often only have links in HTML)
        html_body = item.get("_data", {}).get("email", {}).get("html_body", "")
        url = _signer.extract_appfolio_url(html_body)
    if not url:
        console.print("[yellow]  ⚠ autosign: no AppFolio URL found in email[/yellow]")
        return
    meta = _signer.parse_email_metadata(item)
    console.print(f"  [dim cyan]⚙ auto-signing lease: {meta.get('unit', url[:60])}...[/dim cyan]")
    try:
        result = _signer.sign_lease(url, headless=True)
        status = result.get("status", "failed")
        error  = result.get("error", "")
    except Exception as exc:
        result = {}
        status = "failed"
        error  = str(exc)
        console.print(f"  [red]✗ autosign exception: {exc}[/red]")
    _autodb.log_signing(
        _AUTODB_PATH,
        property=meta.get("property", ""),
        unit=meta.get("unit", ""),
        tenants=meta.get("tenants", ""),
        lease_type=meta.get("lease_type", "renewal"),
        source_sender=item.get("from", ""),
        source_subject=item.get("preview", ""),
        appfolio_url=url,
        status=status,
    )
    icon = "✓" if status == "success" else "⚠"
    color = "green" if status == "success" else "yellow"
    suffix = f": {error}" if error and status != "success" else ""
    console.print(f"  [{color}]{icon} lease {status}{suffix}: {meta.get('unit', '')}[/{color}]")
    # Send notification email for first N successes
    if status == "success":
        signing_count = _autodb.count_successful(_AUTODB_PATH)
        if signing_count <= _NOTIFY_THRESHOLD:
            _send_signing_notification(item, meta, result, signing_count)
    # Archive the email so it clears from inbox
    try:
        _ibx.archive(item["_data"]["service"], item["_data"]["email"]["id"])
    except Exception:
        pass


# ── Actions ───────────────────────────────────────────────────────────────────

def do_archive(item):
    t = item["type"]
    if t == "email":
        _ibx.archive(item["_data"]["service"], item["_data"]["email"]["id"])
    elif t == "outlook":
        d = item["_data"]
        _outlook.archive(d["item_id"], d["email"]["subject"], d["email"]["from"])
    elif t == "imsg":
        thread = item["_data"]["thread"]
        proc = _imsg.load_processed()
        proc[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
        _imsg.save_processed(proc)
        _imsg.mark_thread_read(thread["chat_identifier"])
    elif t == "slack":
        d = item["_data"]
        _slack.mark_read(d["token"], d["thread"]["channel_id"], d["thread"]["latest_ts"])
    elif t == "teams":
        all_ids = item["_data"].get("all_item_ids")
        if all_ids and hasattr(_teams, 'archive_all'):
            _teams.archive_all(all_ids, chat_id=item["_data"].get("chat_id", ""))
        else:
            _teams.archive(item["_data"]["item_id"], chat_id=item["_data"].get("chat_id", ""))
    _log_archive(item, "archive")

def do_delete(item):
    t = item["type"]
    if t == "email":
        _ibx.delete(item["_data"]["service"], item["_data"]["email"]["id"])
    elif t == "outlook":
        d = item["_data"]
        _outlook.delete(d["item_id"], d["email"]["subject"], d["email"]["from"])
    elif t == "imsg":
        # iMessage has no true delete via our API; just mark processed
        thread = item["_data"]["thread"]
        proc = _imsg.load_processed()
        proc[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
        _imsg.save_processed(proc)
        console.print("[dim](iMessage: marked done — delete manually in Messages if needed)[/dim]")
    elif t == "slack":
        # Slack: just mark read (can't delete others' messages)
        d = item["_data"]
        _slack.mark_read(d["token"], d["thread"]["channel_id"], d["thread"]["latest_ts"])
    elif t == "teams":
        all_ids = item["_data"].get("all_item_ids")
        if all_ids and hasattr(_teams, 'delete_all'):
            _teams.delete_all(all_ids, chat_id=item["_data"].get("chat_id", ""))
        else:
            _teams.delete(item["_data"]["item_id"], chat_id=item["_data"].get("chat_id", ""))
    _log_archive(item, "delete")

def do_reply(item, reply_text):
    t = item["type"]
    if t == "email":
        _ibx.send_reply(item["_data"]["service"], item["_data"]["email"], reply_text)
    elif t == "outlook":
        d = item["_data"]
        _outlook.reply(d["item_id"], d["email"]["subject"], d["email"]["from"], reply_text)
    elif t == "imsg":
        _imsg.reply_imessage(item["_data"]["thread"], reply_text)
    elif t == "slack":
        d = item["_data"]
        _slack.send_reply(d["token"], d["thread"]["channel_id"], reply_text)
    elif t == "teams":
        chat_id = item["_data"].get("chat_id", "")
        if chat_id and hasattr(_teams, 'reply'):
            ok = _teams.reply(item["_data"]["item_id"], chat_id, reply_text)
            if not ok:
                raise RuntimeError("Teams reply failed (SendMessageToChat returned error)")
        else:
            _teams.reply_via_teams(item["_data"].get("link", ""))
            console.print("[dim](Opened Teams for reply)[/dim]")
        # Mark all grouped message IDs as processed
        all_ids = item["_data"].get("all_item_ids", [])
        for iid in all_ids:
            if iid != item["_data"]["item_id"]:
                _teams._mark_processed(iid)
    if t not in ("outlook", "teams"):  # these handle their own archiving
        do_archive(item)
    _log_archive(item, "reply")

def _notify_sent():
    """Play a send sound and show a visual confirmation."""
    console.print("[green]\u2709\ufe0f  Sent + done.[/green]")
    subprocess.Popen(
        ["afplay", "/System/Library/Sounds/Tink.aiff"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def update_last_contact(item):
    """Update d359 last_contact for the sender. Runs in a background thread."""
    def _update():
        try:
            sender = item.get("sender", "") or item.get("from", "")
            if not sender:
                return
            # Strip email addresses, keep just the name
            name = re.sub(r'<[^>]+>', '', sender).strip().strip('"')
            if not name or name.lower() in ("me", "jonathan mckay", "mckay"):
                return
            d359 = Path.home() / "vault" / "d359"
            today = datetime.now().strftime("%Y-%m-%d")
            # Find matching d359 file
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
            for p in d359.glob("*.md"):
                stem_lower = p.stem.lower().replace(' d359', '').strip()
                if name.lower() == stem_lower or slug in re.sub(r'[^a-z0-9]+', '-', p.stem.lower()):
                    text = p.read_text()
                    if 'last_contact:' in text:
                        text = re.sub(r'last_contact:.*', f'last_contact: {today}', text)
                    elif '---\n' in text:
                        # Add before closing ---
                        parts = text.split('---\n', 2)
                        if len(parts) >= 3:
                            text = f'---\n{parts[1]}last_contact: {today}\n---\n{parts[2]}'
                    p.write_text(text)
                    break
        except Exception:
            pass  # Never block or crash the UI
    threading.Thread(target=_update, daemon=True).start()


# Chrome profile directories by context
_CHROME_PROFILES = {
    "m5x2": "Profile 11",   # m5c7.com
    "msft": "Profile 1",    # MSFT (Outlook, Teams)
    "jbm":  "Profile 5",    # 个 (personal Gmail)
}


def _open_in_chrome(url, profile_key):
    """Open a URL in Chrome with the specified profile."""
    profile_dir = _CHROME_PROFILES.get(profile_key)
    if profile_dir:
        subprocess.run(
            ["open", "-na", "Google Chrome", "--args",
             f"--profile-directory={profile_dir}", url],
            capture_output=True, timeout=5,
        )
    else:
        subprocess.run(["open", url])


def do_open(item):
    """Open the current item in the browser with the correct Chrome profile."""
    t = item["type"]
    if t == "email":
        msg_id = item["_data"]["email"]["id"]
        acct = item["_data"]["email"].get("_account", "m5c7")
        u = ACCOUNT_GMAIL_INDEX.get(acct, 0)
        url = f"https://mail.google.com/mail/u/{u}/#inbox/{msg_id}"
        profile = "m5x2" if acct == "m5c7" else "jbm"
        _open_in_chrome(url, profile)
        return
    elif t == "outlook":
        url = item["_data"].get("link", "")
        if not url:
            # Fallback: open Outlook web search for this email's subject
            import urllib.parse
            subj = item["_data"].get("email", {}).get("subject", "")
            url = "https://outlook.office.com/mail/0/inbox" + (
                f"?search={urllib.parse.quote(subj)}" if subj else ""
            )
        _open_in_chrome(url, "msft")
        return
    elif t == "imsg":
        # No web URL for iMessage — open Messages app
        subprocess.run(["open", "-a", "Messages"])
        return
    elif t == "slack":
        d = item["_data"]
        ch = d["thread"]["channel_id"]
        url = f"https://app.slack.com/client/T/{ch}"
        # Slack workspace context: m5x2 slack → m5x2, github slack → msft
        ws = d.get("workspace", "")
        if "m5x2" in ws.lower() or "mckay" in ws.lower():
            profile = "m5x2"
        else:
            profile = "msft"
        _open_in_chrome(url, profile)
        return
    elif t == "teams":
        url = item["_data"].get("link", "")
        if url:
            _open_in_chrome(url, "msft")
        return
    else:
        return

# ── Claude ────────────────────────────────────────────────────────────────────

def ask_claude(item, user_input):
    t = item["type"]
    type_label = {"email": "email", "imsg": "iMessage", "slack": "Slack DM", "outlook": "Outlook email", "teams": "Teams DM"}[t]
    prompt = f"""You are a personal assistant for Jonathan McKay.
Given an inbound {type_label} and a user instruction, respond with JSON only.

Schema:
{{"action": "<action>", "message": "<short explanation>", "content": "<optional text>"}}

Actions: "reply" (put reply in content), "archive" (done, no reply), "delete", "task" (put task in content), "skip", "answer" (answer in message)

FROM: {item['from']}
SUBJECT/PREVIEW: {item['preview']}

CONTENT:
{item['body'][-2000:]}

User instruction: {user_input}"""

    msg = ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    try:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
        return json.loads(text)
    except Exception:
        return {"action": "answer", "message": text}

# ── Fetch all sources ─────────────────────────────────────────────────────────

# Gmail account → /u/ index for web URLs
ACCOUNT_GMAIL_INDEX = {
    "m5c7": 0,
    "gmail": 1,
}

# Display names for each account
ACCOUNT_DISPLAY = {
    "m5c7": "m5x2",
    "gmail": "jbm",
}

# User's own email addresses — skip messages sent by these
MY_EMAILS = {"mckay@m5c7.com", "mckay@m5x2.com", "jonathan.b.mckay@gmail.com"}

def fetch_emails(skip_triage=False):
    items = []
    per_account = {}  # display_name -> count
    _update_status("Gmail", "...")
    services = {}
    for acct in _ibx.ACCOUNTS:
        try:
            svc = _ibx.get_gmail_service(acct["tokens"], acct["creds"])
            services[acct["name"]] = svc
            _ibx._gmail_services[acct["name"]] = svc
        except Exception as e:
            console.print(f"  [yellow]Gmail ✗ {acct['name']}: {e}[/yellow]")

    if not services:
        return items, per_account

    # Pre-triage: intercept autosign emails before triage can move them out of INBOX
    _autosign_queued_ids: set = set()
    if _autosign_available:
        for name, svc in services.items():
            try:
                unread = _ibx.fetch_inbox(svc, unread_only=True)
                for m in unread:
                    item = normalize_email(m, svc, name)
                    if item and _signer.is_autosign_email(item, AUTOSIGN_SENDERS):
                        _autosign_queued_ids.add(item.get("_data", {}).get("email", {}).get("id", ""))
                        t = threading.Thread(target=_autosign_item, args=(item,))
                        t.start()
                        _autosign_threads.append(t)
            except Exception:
                pass

    # Triage (skip in background fetches — re-triaging removes INBOX labels
    # from queued emails, causing them to appear "resolved elsewhere")
    if not skip_triage:
        for name, svc in services.items():
            total, moved = _ibx.triage_inbox(svc, name)

    # Fetch remaining (cross-account dedup by RFC Message-ID)
    seen_message_ids = set()
    for name, svc in services.items():
        msgs = _ibx.fetch_inbox(svc, unread_only=False, dedup_threads=True)
        count = 0
        for m in msgs:
            item = normalize_email(m, svc, name)
            if not item:
                continue
            mid = item["_data"]["email"].get("message_id", "")
            if mid and mid in seen_message_ids:
                continue
            if mid:
                seen_message_ids.add(mid)
            if _autosign_available and _signer.is_autosign_email(item, AUTOSIGN_SENDERS):
                if item.get("_data", {}).get("email", {}).get("id", "") not in _autosign_queued_ids:
                    _autosign_queued_ids.add(item.get("_data", {}).get("email", {}).get("id", ""))
                    t = threading.Thread(target=_autosign_item, args=(item,))
                    t.start()
                    _autosign_threads.append(t)
                continue  # don't add to review queue
            items.append(item)
            count += 1
        display = ACCOUNT_DISPLAY.get(name, name)
        per_account[display] = count

    total_email = sum(per_account.values())
    _update_status("Gmail", f"✓ {total_email}")
    return items, per_account

def fetch_imsgs():
    items = []
    _update_status("iMsg", "...")
    if not _imsg.CHAT_DB.exists():
        _update_status("iMsg", "✗ no db")
        return items

    _imsg._contacts = _imsg.build_contact_cache()
    processed = _imsg.load_processed()

    try:
        conn = _imsg.snapshot_db()
    except Exception as e:
        _update_status("iMsg", "✗")
        return items

    raw_threads = _imsg.fetch_unread_threads(conn, processed)
    if not raw_threads:
        _update_status("iMsg", "✓ 0")
        conn.close()
        return items
    threads_to_review, auto_handled = _imsg.triage_threads(raw_threads)

    if auto_handled:
        for t in auto_handled:
            processed[t["chat_identifier"]] = t["latest_date"]
        _imsg.save_processed(processed)

    for t in threads_to_review:
        try:
            card = _imsg.build_thread_card(conn, t)
            items.append(normalize_imsg(card))
        except Exception as e:
            console.print(f"  [yellow]thread error: {e}[/yellow]")

    conn.close()
    _update_status("iMsg", f"✓ {len(items)}")
    return items

def fetch_slack():
    items = []
    _update_status("Slack", "...")
    if not _slack.CONFIG_FILE.exists():
        _update_status("Slack", "✓ 0")
        return items

    with open(_slack.CONFIG_FILE) as f:
        workspaces = json.load(f)

    for workspace, token in workspaces.items():
        try:
            self_id = _slack.get_self_id(token)
            channels = _slack.fetch_recent_channels(token)
            count = 0
            for ch in channels:
                try:
                    thread = _slack.build_thread(token, ch, self_id)
                    if thread:
                        items.append(normalize_slack(thread, token, workspace))
                        count += 1
                except Exception as e:
                    console.print(f"  [yellow]channel error: {e}[/yellow]")
            _update_status("Slack", f"✓ {len(items)}")
        except Exception as e:
            console.print(f"  [yellow]Slack ✗ {workspace}: {e}[/yellow]")

    if "Slack" not in _fetch_status or not _fetch_status["Slack"].startswith("✓"):
        _update_status("Slack", f"✓ {len(items)}")
    return items

def fetch_outlook():
    """Fetch Outlook emails via workiq natural language interface."""
    if not _outlook_available:
        return []
    _update_status("Outlook", "...")
    try:
        items = _outlook.fetch_outlook_items()
        for item in items:
            item["received_at"] = _parse_received_at(item)
        _update_status("Outlook", f"✓ {len(items)}")
        return items
    except Exception as e:
        _update_status("Outlook", "✗")
        return []

def fetch_teams():
    """Fetch Teams DMs via workiq natural language interface."""
    if not _teams_available:
        return []
    _update_status("Teams", "...")
    try:
        items = _teams.fetch_teams_items()
        for item in items:
            item["received_at"] = _parse_received_at(item)
        _update_status("Teams", f"✓ {len(items)}")
        return items
    except Exception as e:
        _update_status("Teams", "✗")
        return []

# ── Background poll ───────────────────────────────────────────────────────────

def _item_uid(item):
    """Stable unique ID for an item, used to track external resolution."""
    if item["type"] == "email":
        # Prefer RFC Message-ID for dedup: same email delivered to multiple
        # accounts or appearing as multiple Gmail messages shares this header.
        mid = item["_data"]["email"].get("message_id", "")
        return ("email", mid if mid else item["_data"]["email"]["id"])
    elif item["type"] == "outlook":
        return ("outlook", item["_data"]["item_id"])
    elif item["type"] == "imsg":
        return ("imsg", item["_data"]["thread"]["chat_identifier"])
    elif item["type"] == "slack":
        return ("slack", item["_data"]["thread"]["channel_id"])
    elif item["type"] == "teams":
        chat_id = item["_data"].get("chat_id", "")
        if chat_id:
            return ("teams_thread", chat_id)
        return ("teams", item["_data"]["item_id"])
    return None


def _poll_resolved(items_snapshot, resolved, stop_event, msg_queue, interval=60):
    """
    Background thread: every `interval` seconds, re-check each email item.
    If a message no longer has INBOX label it was handled elsewhere — mark resolved.
    Posts status messages to msg_queue for the main loop to display.
    """
    while not stop_event.wait(interval):
        for item in list(items_snapshot):
            uid = _item_uid(item)
            if uid is None or uid in resolved:
                continue

            try:
                if item["type"] == "email":
                    svc = item["_data"]["service"]
                    msg_id = item["_data"]["email"]["id"]
                    msg = svc.users().messages().get(
                        userId="me", id=msg_id, format="minimal",
                        fields="labelIds",
                    ).execute()
                    if "INBOX" not in msg.get("labelIds", []):
                        resolved.add(uid)

                elif item["type"] == "imsg":
                    # Resolved if the stored processed ts covers this message's latest_apple_ts
                    proc = _imsg.load_processed()
                    cid = item["_data"]["thread"]["chat_identifier"]
                    item_ts = item["_data"]["thread"].get("latest_apple_ts", 0)
                    if proc.get(cid, -1) >= item_ts:
                        resolved.add(uid)

                elif item["type"] == "slack":
                    d = item["_data"]
                    token = d["token"]
                    channel_id = d["thread"]["channel_id"]
                    latest_ts = d["thread"].get("latest_ts", "0")
                    info = _slack.slack_get(token, "conversations.info", channel=channel_id)
                    last_read = info.get("channel", {}).get("last_read", "0")
                    if last_read != "0" and float(latest_ts) <= float(last_read):
                        resolved.add(uid)

            except Exception:
                pass

        msg_queue.put(f"[dim]↻ checked — next in {interval}s (c to check now)[/dim]")


def check_resolved_now(items_snapshot, resolved):
    """Synchronously check all items and update resolved set. Returns count newly resolved."""
    newly = 0
    for item in items_snapshot:
        uid = _item_uid(item)
        if uid is None or uid in resolved:
            continue
        try:
            if item["type"] == "email":
                svc = item["_data"]["service"]
                msg_id = item["_data"]["email"]["id"]
                msg = svc.users().messages().get(
                    userId="me", id=msg_id, format="minimal",
                    fields="labelIds",
                ).execute()
                if "INBOX" not in msg.get("labelIds", []):
                    resolved.add(uid)
                    newly += 1
            elif item["type"] == "imsg":
                proc = _imsg.load_processed()
                cid = item["_data"]["thread"]["chat_identifier"]
                item_ts = item["_data"]["thread"].get("latest_apple_ts", 0)
                if proc.get(cid, -1) >= item_ts:
                    resolved.add(uid)
                    newly += 1
            elif item["type"] == "slack":
                d = item["_data"]
                token = d["token"]
                channel_id = d["thread"]["channel_id"]
                latest_ts = d["thread"].get("latest_ts", "0")
                info = _slack.slack_get(token, "conversations.info", channel=channel_id)
                last_read = info.get("channel", {}).get("last_read", "0")
                if last_read != "0" and float(latest_ts) <= float(last_read):
                    resolved.add(uid)
                    newly += 1
        except Exception:
            pass
    return newly


def _bg_continuous_fetch(resolved, bg_injected, bg_lock, stop_event, drainer_done_event, interval=120):
    """Background thread: periodically fetch new items from all sources.
    Items are staged in bg_injected for the main loop to pick up.
    Does NOT use seen_uids — the main loop filters against its live queue to allow
    new messages in already-seen Slack/iMessage threads to reappear.
    """
    import io
    drainer_done_event.wait()

    # Quiet console to suppress output from fetch functions in background
    quiet = Console(file=io.StringIO())

    while not stop_event.wait(interval):
        # Swap each source module's console to suppress background output.
        # Do NOT swap ibx0's own console — the main thread uses it for
        # interactive prompts (e.g. "Send? (y/n)") and swapping it races
        # with the main loop, causing hangs.
        modules = [_ibx, _imsg, _slack]
        if _outlook_available:
            modules.append(_outlook)
        if _teams_available:
            modules.append(_teams)
        saved = {mod: mod.console for mod in modules if hasattr(mod, "console")}
        for mod in saved:
            mod.console = quiet

        try:
            from concurrent.futures import ThreadPoolExecutor
            fetchers = [
                ("email", lambda: fetch_emails(skip_triage=True)),
                ("imsg", fetch_imsgs),
                ("slack", fetch_slack),
            ]
            if _outlook_available:
                fetchers.append(("outlook", fetch_outlook))
            if _teams_available:
                fetchers.append(("teams", fetch_teams))

            new_items = []
            with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
                futs = {pool.submit(fn): name for name, fn in fetchers}
                for fut in futs:
                    try:
                        r = fut.result(timeout=30)
                        items = r[0] if isinstance(r, tuple) else r
                        new_items.extend(items)
                    except Exception:
                        pass

            with bg_lock:
                for it in new_items:
                    uid = _item_uid(it)
                    if uid and uid not in resolved:
                        bg_injected.append(it)
        except Exception:
            pass
        finally:
            for mod, orig in saved.items():
                mod.console = orig
            quiet.file.truncate(0)
            quiet.file.seek(0)


# ── Main ──────────────────────────────────────────────────────────────────────

TERM_COLOR = Path(__file__).parent.parent.parent / "scripts" / "term-color.sh"

def set_term_color(color):
    if TERM_COLOR.exists():
        subprocess.run(["bash", str(TERM_COLOR), color], capture_output=True)

def main():
    global _live
    console.print(Rule("[bold]Inbox 0[/bold]", style="dim"))

    # ── Concurrent fetch: all sources in parallel ────────────────────────────
    # Thread-safe queue for items arriving from any source
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _incoming = queue.Queue()       # items land here as each source finishes
    _source_counts = {}             # source -> count (for status line)
    _per_account = {}               # email account breakdown
    _fetch_done = threading.Event() # set when ALL sources have reported

    # Initialize status line
    _fetch_status.clear()
    _live = Live(_status_line(), console=console, refresh_per_second=4, transient=False)
    _live.start()

    def _bg_fetch(name, fn, *args):
        """Run a fetch function and post results to _incoming queue."""
        try:
            result = fn(*args)
            if name == "email":
                items, acct_counts = result
                _per_account.update(acct_counts)
            else:
                items = result
            _source_counts[name] = len(items)
            for it in items:
                _incoming.put(it)
        except Exception as e:
            console.print(f"[dim]  {name} fetch error: {e}[/dim]")
            _source_counts[name] = 0

    # Launch all sources in parallel
    fetch_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ibx-fetch")
    futures = []
    futures.append(fetch_pool.submit(_bg_fetch, "email", fetch_emails))
    futures.append(fetch_pool.submit(_bg_fetch, "imsg", fetch_imsgs))
    futures.append(fetch_pool.submit(_bg_fetch, "slack", fetch_slack))
    if _outlook_available:
        futures.append(fetch_pool.submit(_bg_fetch, "outlook", fetch_outlook))
    else:
        _source_counts["outlook"] = 0
        _update_status("Outlook", "✓ 0")
    if _teams_available:
        futures.append(fetch_pool.submit(_bg_fetch, "teams", fetch_teams))
    else:
        _source_counts["teams"] = 0
        _update_status("Teams", "✓ 0")

    # Wait up to 8s for fast sources, but break immediately if any items arrive
    fast_deadline = time.time() + 8
    while time.time() < fast_deadline:
        if all(n in _source_counts for n in ("email", "imsg", "slack")):
            break
        if not _incoming.empty():
            break
        time.sleep(0.2)

    # Stop the live status display
    if _live is not None:
        _live.stop()
        _live = None
    # Print final status line (persists after Live stops)
    console.print(_status_line())

    # Drain whatever has arrived so far into all_items
    all_items = []
    seen_uids = set()
    while not _incoming.empty():
        it = _incoming.get_nowait()
        uid = _item_uid(it)
        if uid not in seen_uids:
            all_items.append(it)
            seen_uids.add(uid)

    # Sort: Slack by ts desc, then everything else in arrival order
    slack_items_init = [it for it in all_items if it.get("type") == "slack"]
    other_items_init = [it for it in all_items if it.get("type") != "slack"]
    all_items = sorted(slack_items_init, key=lambda x: -x.get("ts", 0)) + other_items_init

    # Background drainer: keeps pulling from _incoming as slow sources finish
    _bg_injected = []  # shared list for late items (read from main loop)
    _bg_lock = threading.Lock()
    _drainer_done = threading.Event()
    def _bg_drainer():
        """Continuously drain _incoming queue and stage late items for injection."""
        while not _fetch_done.is_set():
            try:
                it = _incoming.get(timeout=1)
                uid = _item_uid(it)
                with _bg_lock:
                    if uid not in seen_uids:
                        _bg_injected.append(it)
                        seen_uids.add(uid)
            except queue.Empty:
                pass
        # Final drain
        while not _incoming.empty():
            it = _incoming.get_nowait()
            uid = _item_uid(it)
            with _bg_lock:
                if uid not in seen_uids:
                    _bg_injected.append(it)
                    seen_uids.add(uid)
        _drainer_done.set()
    threading.Thread(target=_bg_drainer, daemon=True).start()

    # Mark fetch done when all futures complete (non-blocking)
    def _bg_wait_all():
        for f in futures:
            f.result(timeout=180)
        _fetch_done.set()
    threading.Thread(target=_bg_wait_all, daemon=True).start()

    def _build_status():
        email_parts = "  ".join(
            f"[green]{d}:{c}[/green]" for d, c in _per_account.items()
        ) or f"[green]0 email[/green]"
        ol = _source_counts.get("outlook", "...")
        tm = _source_counts.get("teams", "...")
        im = _source_counts.get("imsg", "...")
        sl = _source_counts.get("slack", "...")
        return (f"({email_parts}  "
                f"[cyan]{ol} outlook[/cyan]  "
                f"[yellow]{tm} teams[/yellow]  "
                f"[magenta]{im} iMsg[/magenta]  "
                f"[blue]{sl} slack[/blue])")

    status_line = _build_status()

    # Only block on slow sources if we have NOTHING to show yet
    if not all_items and not _fetch_done.is_set():
        slow_pending = [n for n in ("outlook", "teams") if n not in _source_counts]
        if slow_pending:
            console.print(f"[dim]  waiting for {', '.join(slow_pending)}...[/dim]")
        _fetch_done.wait(timeout=150)

    # Quick drain of whatever's arrived so far — don't block on slow sources
    _drainer_done.wait(timeout=0.2)
    with _bg_lock:
        for it in _bg_injected:
            uid = _item_uid(it)
            if uid not in seen_uids:
                all_items.append(it)
                seen_uids.add(uid)
        _bg_injected.clear()
    while not _incoming.empty():
        try:
            it = _incoming.get_nowait()
            uid = _item_uid(it)
            if uid not in seen_uids:
                all_items.append(it)
                seen_uids.add(uid)
        except queue.Empty:
            break
    status_line = _build_status()

    if not all_items:
        _wait_for_autosign()
        _mark_inbox_habits_done()
        set_term_color("blue")
        _render_block_status()
        console.print(f"\n[dim]Inbox zero — watching for new items... (q to quit)[/dim]  {status_line}")

        # Start continuous fetch even when starting at zero
        stop_poll = threading.Event()
        resolved = set()
        fetch_thread = threading.Thread(
            target=_bg_continuous_fetch,
            args=(resolved, _bg_injected, _bg_lock, stop_poll, _drainer_done),
            daemon=True,
        )
        fetch_thread.start()

        while True:
            with _bg_lock:
                if _bg_injected:
                    staged = list(_bg_injected)
                    _bg_injected.clear()
                else:
                    staged = []
            if staged:
                fresh = [it for it in staged if _item_uid(it) not in resolved]
                if fresh:
                    all_items = fresh
                    set_term_color("red")
                    console.print(f"[green]  + {len(fresh)} new item(s)[/green]")
                    break

            try:
                ready, _, _ = select.select([sys.stdin], [], [], 2.0)
                if ready:
                    line_raw = sys.stdin.readline().strip()
                    line = line_raw.lower()
                    if line == "q":
                        console.print("[dim]Bye.[/dim]")
                        stop_poll.set()
                        return
                    if line_raw == "R":
                        console.print("[dim]Reloading...[/dim]")
                        stop_poll.set()
                        sys.exit(0)
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Bye.[/dim]")
                stop_poll.set()
                return

    set_term_color("red")
    console.print(f"\n[bold]{len(all_items)} item(s)[/bold] to review {status_line}")

    # Background poll: detect items handled outside this TUI
    resolved = set()
    stop_poll = threading.Event()
    poll_msgs = queue.Queue()
    poll_thread = threading.Thread(
        target=_poll_resolved,
        args=(list(all_items), resolved, stop_poll, poll_msgs),
        daemon=True,
    )
    poll_thread.start()

    # Continuous background fetch: poll all sources for new items
    fetch_thread = threading.Thread(
        target=_bg_continuous_fetch,
        args=(resolved, _bg_injected, _bg_lock, stop_poll, _drainer_done),
        daemon=True,
    )
    fetch_thread.start()

    index = 0
    skipped = []

    def filter_resolved(lst):
        """Remove externally-resolved items; return (filtered_list, removed_count)."""
        kept, removed = [], 0
        for it in lst:
            if _item_uid(it) in resolved:
                removed += 1
            else:
                kept.append(it)
        return kept, removed

    while True:
        # Prune any items resolved elsewhere before advancing
        all_items, gone = filter_resolved(all_items)
        skipped, gone_skipped = filter_resolved(skipped)
        total_gone = gone + gone_skipped
        if total_gone:
            console.print(f"[dim]  ↩ {total_gone} item(s) resolved elsewhere — removed[/dim]")
            if index > len(all_items):
                index = len(all_items)

        if index >= len(all_items):
            if skipped:
                console.print(f"\n[dim]Cycling through {len(skipped)} skipped...[/dim]")
                all_items = skipped
                skipped = []
                index = 0
            else:
                # Inbox zero — go blue immediately, let background polling find new items
                _wait_for_autosign()
                _mark_inbox_habits_done()
                set_term_color("blue")
                _render_block_status()
                console.print("[dim]Inbox zero — watching for new items... (q to quit)[/dim]")

                while True:
                    # Check for items from continuous background fetch
                    with _bg_lock:
                        if _bg_injected:
                            staged = list(_bg_injected)
                            _bg_injected.clear()
                        else:
                            staged = []
                    if staged:
                        existing_uids_now = {_item_uid(it) for it in all_items + skipped}
                        existing_uids_now.update(resolved)
                        fresh = [it for it in staged if _item_uid(it) not in existing_uids_now]
                        if fresh:
                            all_items = fresh
                            index = 0
                            set_term_color("red")
                            console.print(f"[green]  + {len(fresh)} new item(s)[/green]")
                            break

                    # Wait up to 2s, check for user quit/reload
                    try:
                        ready, _, _ = select.select([sys.stdin], [], [], 2.0)
                        if ready:
                            line_raw = sys.stdin.readline().strip()
                            line = line_raw.lower()
                            if line == "q":
                                console.print("[dim]Bye.[/dim]")
                                stop_poll.set()
                                sys.exit(2)
                            if line_raw == "R":
                                console.print("[dim]Reloading...[/dim]")
                                stop_poll.set()
                                sys.exit(0)
                    except (KeyboardInterrupt, EOFError):
                        console.print("\n[dim]Bye.[/dim]")
                        stop_poll.set()
                        sys.exit(2)

                continue

        # Mark all items before current index as resolved so background
        # fetches don't re-inject them after they leave all_items
        for i in range(index):
            uid = _item_uid(all_items[i])
            if uid:
                resolved.add(uid)

        item = all_items[index]
        display_card(item, index + 1, len(all_items))
        print_help()

        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            stop_poll.set()
            _wait_for_autosign()
            set_term_color("blue")
            sys.exit(2)

        # After user input: inject background arrivals and print poll messages
        # (done here so they don't interrupt the card display above)
        while not poll_msgs.empty():
            console.print(poll_msgs.get_nowait())
        with _bg_lock:
            if _bg_injected:
                new_late = list(_bg_injected)
                _bg_injected.clear()
            else:
                new_late = []
        if new_late:
            existing_uids_now = {_item_uid(it) for it in all_items + skipped}
            existing_uids_now.update(resolved)
            fresh = [it for it in new_late if _item_uid(it) not in existing_uids_now]
            if fresh:
                all_items.extend(fresh)
                status_line = _build_status()
                console.print(f"[cyan]  + {len(fresh)} item(s) arrived in background[/cyan]  {status_line}")

        if not user_input:
            index += 1
            continue

        cmd = user_input.lower()

        if cmd == "q":
            console.print("[dim]Bye.[/dim]")
            stop_poll.set()
            _wait_for_autosign()
            set_term_color("blue")
            sys.exit(2)

        elif cmd == "r" and user_input == "R":  # capital R only (lowercase r = reply)
            console.print("[dim]Reloading...[/dim]")
            stop_poll.set()
            sys.exit(0)  # exit 0 → wrapper restarts immediately

        elif cmd == "o":
            try:
                do_open(item)
                console.print("[dim]Opened in browser.[/dim]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        elif cmd == "a":
            try:
                do_archive(item)
                console.print("[green]Done.[/green]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
            index += 1

        elif cmd == "d":
            try:
                do_delete(item)
                console.print("[red]Deleted.[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
            index += 1

        elif cmd == "e":
            full = item.get("_full_body", item.get("body", ""))
            console.print(Panel(full, box=box.SIMPLE, border_style="dim", padding=(0, 1)))

        elif cmd == "s":
            skipped.append(item)
            console.print("[dim]Skipped.[/dim]")
            index += 1

        elif cmd == "c":
            console.print("[dim]Checking...[/dim]")
            newly = check_resolved_now(all_items + skipped, resolved)
            all_items, gone = filter_resolved(all_items)
            skipped, gone_skipped = filter_resolved(skipped)
            total_gone = gone + gone_skipped
            if total_gone:
                console.print(f"[dim]  ↩ {total_gone} item(s) resolved elsewhere — removed[/dim]")
                if index > len(all_items):
                    index = len(all_items)
            else:
                console.print("[dim]  all items still pending[/dim]")

        elif cmd == "f":
            console.print("[dim]Fetching new items...[/dim]")
            existing_uids = {_item_uid(it) for it in all_items + skipped}
            new_emails, new_per = fetch_emails()
            new_imsgs = fetch_imsgs()
            new_slack = fetch_slack()
            new_all = new_emails + new_imsgs + new_slack
            added = [it for it in new_all if _item_uid(it) not in existing_uids and _item_uid(it) not in resolved]
            if added:
                all_items.extend(added)
                console.print(f"[green]  + {len(added)} new item(s) added to queue[/green]")
            else:
                console.print("[dim]  no new items[/dim]")

        elif cmd == "?":
            print_help()

        elif cmd.startswith("r "):
            reply_text = user_input[2:].strip()
            console.print(Panel(reply_text, title="Reply", border_style="cyan"))
            console.print("[bold cyan]Send? (y/n)[/bold cyan]")
            confirm = input("> ").strip().lower()
            if confirm == "y":
                try:
                    do_reply(item, reply_text)
                    update_last_contact(item)
                    _notify_sent()
                    _print_response_stats(item)
                    index += 1
                except Exception as e:
                    console.print(f"[red]Send failed: {e}[/red]")
            else:
                console.print("[dim]Cancelled.[/dim]")

        elif cmd.startswith("R "):
            # Reply-all (Outlook only for now; others fall back to reply)
            reply_text = user_input[2:].strip()
            console.print(Panel(reply_text, title="Reply All", border_style="magenta"))
            console.print("[bold magenta]Reply-all? (y/n)[/bold magenta]")
            confirm = input("> ").strip().lower()
            if confirm == "y":
                try:
                    t = item["type"]
                    if t == "outlook":
                        d = item["_data"]
                        _outlook.reply_all(d["item_id"], d["email"]["subject"], d["email"]["from"], reply_text)
                        do_archive(item)
                    else:
                        do_reply(item, reply_text)
                    update_last_contact(item)
                    _notify_sent()
                    _print_response_stats(item)
                    index += 1
                except Exception as e:
                    console.print(f"[red]Send failed: {e}[/red]")
            else:
                console.print("[dim]Cancelled.[/dim]")

        elif cmd.startswith("P "):
            # Pipe through reply-all: AI drafts reply from instruction
            instruction = user_input[2:].strip()
            if item["type"] == "outlook":
                console.print(f"[dim]Drafting reply-all: \"{instruction}\"...[/dim]")
                d = item["_data"]
                draft = _outlook.pipe_through(
                    d["item_id"], d["email"]["subject"], d["email"]["from"],
                    item.get("body", ""), instruction, reply_all_flag=True,
                )
                if draft:
                    console.print(Panel(draft, title="AI Draft — Reply All — Sent", border_style="green"))
                    _print_response_stats(item)
                    index += 1
                else:
                    console.print("[red]Draft failed — not sent[/red]")
            else:
                console.print("[yellow]Pipe-through only supported for Outlook.[/yellow]")

        elif cmd.startswith("p "):
            # Pipe through: AI drafts reply from instruction
            instruction = user_input[2:].strip()
            if item["type"] == "outlook":
                console.print(f"[dim]Drafting reply: \"{instruction}\"...[/dim]")
                d = item["_data"]
                draft = _outlook.pipe_through(
                    d["item_id"], d["email"]["subject"], d["email"]["from"],
                    item.get("body", ""), instruction,
                )
                if draft:
                    console.print(Panel(draft, title="AI Draft — Sent", border_style="green"))
                    _print_response_stats(item)
                    index += 1
                else:
                    console.print("[red]Draft failed — not sent[/red]")
            else:
                console.print("[yellow]Pipe-through only supported for Outlook. Use 'r <text>' for other types.[/yellow]")

        elif cmd.startswith("t "):
            task_text = user_input[2:].strip()
            subprocess.run(["pbcopy"], input=task_text.encode())
            console.print(f"[green]Todo:[/green] {task_text} [dim](copied)[/dim]")
            try:
                do_archive(item)
            except Exception:
                pass
            index += 1

        else:
            # Natural language → Claude
            console.print("[dim]...[/dim]")
            result = ask_claude(item, user_input)
            action = result.get("action", "answer")
            message = result.get("message", "")
            content = result.get("content", "")

            if action == "answer":
                console.print(f"\n{message}")

            elif action == "reply":
                console.print(Panel(content, title="Proposed Reply", border_style="cyan"))
                if message:
                    console.print(f"[dim]{message}[/dim]")
                console.print("[bold cyan](y)es / (e)dit / (n)o[/bold cyan]")
                confirm = input("> ").strip().lower()
                if confirm in ("y", "s"):
                    try:
                        do_reply(item, content)
                        _notify_sent()
                        _print_response_stats(item)
                        index += 1
                    except Exception as e:
                        console.print(f"[red]Send failed: {e}[/red]")
                elif confirm == "e":
                    new_text = input("Edit reply: ").strip()
                    if new_text:
                        try:
                            do_reply(item, new_text)
                            _notify_sent()
                            _print_response_stats(item)
                            index += 1
                        except Exception as e:
                            console.print(f"[red]Send failed: {e}[/red]")
                    else:
                        console.print("[dim]Cancelled.[/dim]")
                else:
                    console.print("[dim]Cancelled.[/dim]")

            elif action in ("archive", "done", "mark_read"):
                try:
                    do_archive(item)
                    console.print("[green]Done.[/green]")
                    index += 1
                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")

            elif action == "delete":
                try:
                    do_delete(item)
                    console.print("[red]Deleted.[/red]")
                    index += 1
                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")

            elif action == "task":
                task_text = content or message
                subprocess.run(["pbcopy"], input=task_text.encode())
                console.print(f"[green]Todo:[/green] {task_text} [dim](copied)[/dim]")
                try:
                    do_archive(item)
                except Exception:
                    pass
                index += 1

            elif action == "skip":
                skipped.append(item)
                console.print("[dim]Skipped.[/dim]")
                index += 1

            else:
                console.print(f"\n[bold]Proposed:[/bold] {action}")
                if message:
                    console.print(f"[dim]{message}[/dim]")

if __name__ == "__main__":
    main()
