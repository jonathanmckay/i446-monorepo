#!/usr/bin/env python3
"""Export Outlook for Mac (Legacy) data → ~/m5x2-cache/ for Dream nightly.

Reads the Legacy Outlook 15 SQLite catalog (no Graph API, no auth).
Writes JSON snapshots + manifest with generated_at for staleness checks.

Outputs:
  ~/m5x2-cache/outlook-mail.json      - last 7d inbox (subject, sender, time, preview, read)
  ~/m5x2-cache/outlook-flagged.json   - all currently-flagged mail
  ~/m5x2-cache/outlook-calendar.json  - next 14d events (subject, start, end, organizer)
  ~/m5x2-cache/manifest.json          - merged with prior keys; updates 'outlook' section

If the catalog is empty (New Outlook only, never flipped to Legacy), all
queries return [] and the script exits 0 with a 'stale_reason' in manifest.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DATA_DIR = HOME / "Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile/Data"
DB_PATH = DATA_DIR / "Outlook.sqlite"
OUT_DIR = HOME / "m5x2-cache"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Outlook stores timestamps as "ticks since 1601-01-01 UTC" in microseconds.
# Convert: unix_seconds = ticks/1_000_000 - 11_644_473_600
EPOCH_OFFSET = 11_644_473_600


def ticks_to_iso(ticks):
    if not ticks:
        return None
    try:
        unix = ticks / 1_000_000 - EPOCH_OFFSET
        return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return None


def open_db_readonly(path: Path) -> sqlite3.Connection:
    # URI mode + immutable=1 avoids SQLite-WAL contention with running Outlook.
    uri = f"file:{path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def fetch_mail(con: sqlite3.Connection, days: int = 7, limit: int = 200) -> list[dict]:
    cur = con.cursor()
    cutoff_ticks = (int(datetime.now(tz=timezone.utc).timestamp()) - days * 86400 + EPOCH_OFFSET) * 1_000_000
    cur.execute(
        """
        SELECT Message_TimeReceived, Message_NormalizedSubject,
               Message_SenderList, Message_DisplayTo, Message_Preview,
               Message_ReadFlag, Record_FlagStatus, Message_HasAttachment,
               Conversation_ConversationID
        FROM Mail
        WHERE Message_TimeReceived > ?
        ORDER BY Message_TimeReceived DESC
        LIMIT ?
        """,
        (cutoff_ticks, limit),
    )
    out = []
    for row in cur.fetchall():
        out.append({
            "received": ticks_to_iso(row[0]),
            "subject": row[1] or "",
            "sender": row[2] or "",
            "to": row[3] or "",
            "preview": (row[4] or "")[:300],
            "read": bool(row[5]),
            "flagged": bool(row[6]),
            "has_attachment": bool(row[7]),
            "conversation_id": row[8],
        })
    return out


def fetch_flagged(con: sqlite3.Connection, limit: int = 100) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        """
        SELECT Message_TimeReceived, Message_NormalizedSubject,
               Message_SenderList, Message_Preview, Record_DueDate, Record_FlagStatus
        FROM Mail
        WHERE Record_FlagStatus > 0
        ORDER BY Record_DueDate ASC, Message_TimeReceived DESC
        LIMIT ?
        """,
        (limit,),
    )
    out = []
    for row in cur.fetchall():
        out.append({
            "received": ticks_to_iso(row[0]),
            "subject": row[1] or "",
            "sender": row[2] or "",
            "preview": (row[3] or "")[:300],
            "due": ticks_to_iso(row[4]),
            "flag_status": row[5],
        })
    return out


def fetch_calendar(con: sqlite3.Connection, days: int = 14, limit: int = 200) -> list[dict]:
    cur = con.cursor()
    now = int(datetime.now(tz=timezone.utc).timestamp())
    start_ticks = (now + EPOCH_OFFSET) * 1_000_000
    end_ticks = (now + days * 86400 + EPOCH_OFFSET) * 1_000_000
    # CalendarEvents doesn't store subject/organizer inline; they live in Blocks.
    # We capture what's queryable; brief/organizer can be added later via Blocks join.
    cur.execute(
        """
        SELECT Calendar_StartDateUTC, Calendar_EndDateUTC,
               Calendar_AttendeeCount, Calendar_IsRecurring,
               Calendar_HasReminder, Calendar_UID
        FROM CalendarEvents
        WHERE Calendar_StartDateUTC BETWEEN ? AND ?
          AND Calendar_SyncBlocked = 0
        ORDER BY Calendar_StartDateUTC ASC
        LIMIT ?
        """,
        (start_ticks, end_ticks, limit),
    )
    out = []
    for row in cur.fetchall():
        out.append({
            "start": ticks_to_iso(row[0]),
            "end": ticks_to_iso(row[1]),
            "attendee_count": row[2],
            "recurring": bool(row[3]),
            "reminder": bool(row[4]),
            "uid": row[5],
        })
    return out


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def update_manifest(section: dict) -> None:
    manifest_path = OUT_DIR / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}
    manifest["outlook"] = section
    manifest["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    write_json(manifest_path, manifest)


def main() -> int:
    section = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "mail_count": 0,
        "flagged_count": 0,
        "calendar_count": 0,
        "stale_reason": None,
    }

    if not DB_PATH.exists():
        section["stale_reason"] = "Outlook.sqlite missing; Legacy Outlook never initialized"
        update_manifest(section)
        print(f"[WARN] {section['stale_reason']}", file=sys.stderr)
        return 0

    try:
        con = open_db_readonly(DB_PATH)
    except sqlite3.Error as e:
        section["stale_reason"] = f"sqlite open failed: {e}"
        update_manifest(section)
        print(f"[ERR] {section['stale_reason']}", file=sys.stderr)
        return 0

    try:
        mail = fetch_mail(con)
        flagged = fetch_flagged(con)
        cal = fetch_calendar(con)
    finally:
        con.close()

    write_json(OUT_DIR / "outlook-mail.json", mail)
    write_json(OUT_DIR / "outlook-flagged.json", flagged)
    write_json(OUT_DIR / "outlook-calendar.json", cal)

    section["mail_count"] = len(mail)
    section["flagged_count"] = len(flagged)
    section["calendar_count"] = len(cal)
    if not mail and not cal:
        section["stale_reason"] = "DB present but empty; flip to Legacy Outlook and let it sync"
    update_manifest(section)

    print(f"mail={len(mail)} flagged={len(flagged)} calendar={len(cal)}")
    if section["stale_reason"]:
        print(f"[WARN] {section['stale_reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
