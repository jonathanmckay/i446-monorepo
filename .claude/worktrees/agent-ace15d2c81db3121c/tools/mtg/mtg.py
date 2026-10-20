#!/usr/bin/env python3
"""
mtg.py — Zero-click meeting automation.

Phase 1: Calendar polling + pre-brief emails.
Cron: */5 * * * * python3 ~/i446-monorepo/tools/mtg/mtg.py poll 2>>~/.mtg.log

Usage:
    python3 mtg.py poll [--dry-run]     # Check calendar, advance state machine
    python3 mtg.py status               # Show current event states
    python3 mtg.py brief <event-id>     # Force-generate a pre-brief
"""

import json
import os
import sys
import re
import logging
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Config ──────────────────────────────────────────────────────────────────

VAULT = Path.home() / "vault"
STATE_FILE = VAULT / "h335/d358/.mtg-state.json"
CONFIG_FILE = VAULT / "h335/d358/.mtg-config.json"
D359_DIR = VAULT / "d359"
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"

MTG_CONFIG_DIR = Path.home() / ".config/mtg"
# OAuth client from workspace-mcp (scoped to m5c7.com)
# Credentials stored in ~/.config/mtg/oauth.json
_oauth_creds_path = MTG_CONFIG_DIR / "oauth.json"
if _oauth_creds_path.exists():
    _oauth_creds = json.loads(_oauth_creds_path.read_text())
    OAUTH_CLIENT_ID = _oauth_creds["client_id"]
    OAUTH_CLIENT_SECRET = _oauth_creds["client_secret"]
else:
    OAUTH_CLIENT_ID = os.environ.get("MTG_OAUTH_CLIENT_ID", "")
    OAUTH_CLIENT_SECRET = os.environ.get("MTG_OAUTH_CLIENT_SECRET", "")
MTG_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://mail.google.com/",
]

LOG = logging.getLogger("mtg")

DEFAULT_CONFIG = {
    "stacks": {
        "google": {
            "calendar_id": "mckay@m5c7.com",
            "send_via": "gmail",
            "confidential": False,
            "enabled": True,
        },
        "microsoft": {
            "calendar_id": "jomckay@microsoft.com",
            "send_via": "outlook",
            "confidential": True,
            "tenure_tag": "microsoft",
            "transcript_dir": "h335/i9/mtg-transcripts",
            "enabled": False,
        },
    },
    "hourly_rate": 150,
    "pre_brief_minutes": 30,
    "recording_grace_minutes": 5,
    "skip_patterns": ["Focus Time", "OOO", "Lunch", "Block", "Hold"],
    "reconcile_window_min": 15,
}

SKIP_PATTERNS_LOWER = None  # lazily computed


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            user = json.load(f)
        cfg = {**DEFAULT_CONFIG, **user}
    else:
        cfg = DEFAULT_CONFIG
    global SKIP_PATTERNS_LOWER
    SKIP_PATTERNS_LOWER = [p.lower() for p in cfg["skip_patterns"]]
    return cfg


# ── Google Auth (shared credentials for Calendar + Gmail) ───────────────────

_creds_cache = None

def get_creds():
    """Get or refresh OAuth credentials. One-time interactive auth on first run."""
    global _creds_cache
    if _creds_cache and _creds_cache.valid:
        return _creds_cache

    MTG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tokens_path = MTG_CONFIG_DIR / "tokens.json"
    creds = None

    if tokens_path.exists():
        with open(tokens_path) as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=OAUTH_CLIENT_ID,
            client_secret=OAUTH_CLIENT_SECRET,
            scopes=MTG_SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_config(
                {"installed": {
                    "client_id": OAUTH_CLIENT_ID,
                    "client_secret": OAUTH_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }},
                MTG_SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with open(tokens_path, "w") as f:
            json.dump({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
            }, f)

    _creds_cache = creds
    return creds


def get_gcal_service():
    return build("calendar", "v3", credentials=get_creds())


def list_upcoming_events(service, calendar_id, hours_ahead=2):
    """Fetch events starting within the next N hours."""
    now = datetime.now(timezone.utc)
    later = now + timedelta(hours=hours_ahead)
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=now.isoformat(),
        timeMax=later.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=20,
    ).execute()
    return result.get("items", [])


def get_gmail_service():
    return build("gmail", "v1", credentials=get_creds())


BRIEFS_FILE = VAULT / "z_ibx/mtg-briefs.json"


def send_self_email(gmail_svc, subject, body):
    """Send an email to self (mckay@m5c7.com)."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = "mckay@m5c7.com"
    msg["From"] = "mckay@m5c7.com"
    msg["Subject"] = subject
    raw = urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_svc.users().messages().send(userId="me", body={"raw": raw}).execute()


def stage_prebrief(event_id, subject, body, event_state):
    """Write pre-brief to staging file for -2n to pick up as a card."""
    briefs = []
    if BRIEFS_FILE.exists():
        try:
            briefs = json.loads(BRIEFS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            briefs = []

    # Remove stale briefs (>4 hours old)
    now = datetime.now(timezone.utc).isoformat()
    briefs = [b for b in briefs if b.get("start", "") > (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()]

    # Don't duplicate
    if any(b["event_id"] == event_id for b in briefs):
        return

    briefs.append({
        "event_id": event_id,
        "title": event_state["title"],
        "start": event_state["start"],
        "subject": subject,
        "body": body,
        "staged_at": now,
    })

    BRIEFS_FILE.write_text(json.dumps(briefs, indent=2, ensure_ascii=False))


# ── State Machine ───────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"events": {}, "last_poll": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_poll"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def should_skip(event):
    """Return True if event should not be tracked."""
    # All-day events
    if "dateTime" not in event.get("start", {}):
        return True
    # Declined
    for att in event.get("attendees", []):
        if att.get("self") and att.get("responseStatus") == "declined":
            return True
    # Skip patterns
    title = event.get("summary", "").lower()
    for pat in SKIP_PATTERNS_LOWER:
        if pat in title:
            return True
    return False


def detect_conference_type(event):
    """Detect if meeting is Teams, Zoom, Google Meet, etc."""
    location = (event.get("location") or "").lower()
    desc = (event.get("description") or "").lower()
    combined = location + " " + desc
    if "teams.microsoft.com" in combined:
        return "teams"
    if "zoom.us" in combined:
        return "zoom"
    if event.get("conferenceData"):
        return "google_meet"
    return "in_person"


def detect_domain(event):
    """Infer domain from event title/attendees."""
    title = (event.get("summary") or "").lower()
    attendees = [a.get("email", "") for a in event.get("attendees", [])]
    has_msft = any("microsoft.com" in e for e in attendees)
    if any(kw in title for kw in ["m5x2", "mckay capital", "property", "tenant", "rent"]):
        return "m5x2"
    if has_msft or any(kw in title for kw in ["standup", "sprint", "exp", "slt"]):
        return "i9"
    return "i9"  # default


def extract_attendee_names(event):
    """Get attendee display names, excluding self."""
    self_emails = {"mckay@m5c7.com", "mckay@m5x2.com", "jomckay@microsoft.com"}
    names = []
    for att in event.get("attendees", []):
        email = att.get("email", "")
        if email.lower() in self_emails:
            continue
        name = att.get("displayName") or email.split("@")[0]
        names.append(name)
    return names


def create_event_state(event, source="google"):
    """Create initial state entry for a discovered event."""
    return {
        "state": "DISCOVERED",
        "source": source,
        "title": event.get("summary", "(no title)"),
        "start": event["start"]["dateTime"],
        "end": event["end"]["dateTime"],
        "attendees": [a.get("email", "") for a in event.get("attendees", [])],
        "attendee_names": extract_attendee_names(event),
        "organizer": event.get("organizer", {}).get("email", ""),
        "calendar_id": event.get("organizer", {}).get("email", ""),
        "location": event.get("location", ""),
        "conference_type": detect_conference_type(event),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "briefed_at": None,
        "recording_pid": None,
        "recording_path": None,
        "transcript_path": None,
        "note_path": None,
        "scores": None,
        "cost_usd": None,
        "commitments": [],
        "domain": detect_domain(event),
        "confidential": False,
    }


# ── Pre-Brief Generation ───────────────────────────────────────────────────

def find_d359(name):
    """Find a d359 file matching a name. Returns (path, content) or None."""
    name_lower = name.lower().strip()
    best = None
    best_score = 0

    for p in D359_DIR.glob("*.md"):
        stem_lower = p.stem.lower().replace(" d359", "").strip()
        if name_lower == stem_lower:
            score = 3
        elif name_lower in stem_lower or stem_lower in name_lower:
            score = 2
        else:
            # Try first name match
            name_first = name_lower.split()[0] if " " in name_lower else name_lower
            stem_first = stem_lower.split()[0] if " " in stem_lower else stem_lower
            if name_first == stem_first and len(name_first) > 2:
                score = 1
            else:
                continue
        if score > best_score:
            best = p
            best_score = score

    if best:
        return best, best.read_text()
    return None


def extract_d359_profile(content):
    """Extract profile section (between frontmatter and first ## date header)."""
    lines = content.split("\n")
    in_frontmatter = False
    profile_lines = []
    past_frontmatter = False

    for line in lines:
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                past_frontmatter = True
                continue
        if not past_frontmatter:
            continue
        # Stop at first date header
        if re.match(r"^##\s+\d{4}", line):
            break
        profile_lines.append(line)

    return "\n".join(profile_lines).strip()


def extract_d359_recent(content, max_entries=3):
    """Extract the most recent N meeting entry headers + first few lines."""
    entries = []
    lines = content.split("\n")
    current_entry = None

    for line in lines:
        if re.match(r"^##\s+\d{4}", line):
            if current_entry:
                entries.append(current_entry)
            current_entry = {"header": line, "lines": []}
        elif current_entry is not None:
            if len(current_entry["lines"]) < 4:
                current_entry["lines"].append(line)

    if current_entry:
        entries.append(current_entry)

    result = []
    for e in entries[:max_entries]:
        body = "\n".join(e["lines"]).strip()
        if body:
            result.append(f"{e['header']}\n{body}")
        else:
            result.append(e["header"])
    return "\n\n".join(result)


def search_todoist_tasks(person_name):
    """Search Todoist for open tasks mentioning a person."""
    import urllib.request
    url = f"https://api.todoist.com/api/v1/tasks?limit=10"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TODOIST_TOKEN}",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
    except Exception:
        return []

    tasks = data if isinstance(data, list) else data.get("results", [])
    name_lower = person_name.lower()
    first_name = name_lower.split()[0] if " " in name_lower else name_lower
    matches = []
    for t in tasks:
        content = t.get("content", "").lower()
        if first_name in content or name_lower in content:
            matches.append(t["content"])
    return matches[:5]


def generate_prebrief(event_state):
    """Generate a pre-brief for a meeting. Returns (subject, body)."""
    title = event_state["title"]
    start = datetime.fromisoformat(event_state["start"])
    start_str = start.strftime("%-I:%M%p").lower()
    end = datetime.fromisoformat(event_state["end"])
    duration = int((end - start).total_seconds() / 60)
    names = event_state.get("attendee_names", [])

    sections = []
    sections.append(f"## {title}")
    sections.append(f"**Time:** {start_str} ({duration}min) | **Type:** {event_state['conference_type']}")

    if event_state.get("location"):
        sections.append(f"**Location:** {event_state['location']}")

    sections.append("")

    # Per-attendee context
    for name in names:
        result = find_d359(name)
        if result:
            path, content = result
            profile = extract_d359_profile(content)
            recent = extract_d359_recent(content)

            sections.append(f"### {name}")
            if profile:
                sections.append(profile)
            sections.append("")
            if recent:
                sections.append("**Recent:**")
                sections.append(recent)
            sections.append("")

            # Todoist tasks
            tasks = search_todoist_tasks(name)
            if tasks:
                sections.append("**Open tasks:**")
                for t in tasks:
                    sections.append(f"- {t}")
                sections.append("")
        else:
            sections.append(f"### {name}")
            sections.append("_(no d359 file)_")
            sections.append("")

    subject = f"[MTG PREP] {title} ({start_str})"
    body = "\n".join(sections)
    return subject, body


# ── Poll Command ────────────────────────────────────────────────────────────

def poll(dry_run=False):
    """Main polling loop. Called by cron every 5 minutes."""
    cfg = load_config()
    state = load_state()
    now = datetime.now(timezone.utc)
    pre_brief_min = cfg["pre_brief_minutes"]
    gmail_svc = None  # lazy init

    # Google stack
    google_cfg = cfg["stacks"]["google"]
    if google_cfg["enabled"]:
        try:
            gcal = get_gcal_service()
            events = list_upcoming_events(gcal, google_cfg["calendar_id"])
        except Exception as e:
            LOG.error(f"Google Calendar poll failed: {e}")
            events = []

        for event in events:
            eid = event["id"]

            if should_skip(event):
                continue

            # Discover
            if eid not in state["events"]:
                es = create_event_state(event, source="google")
                es["confidential"] = google_cfg.get("confidential", False)
                state["events"][eid] = es
                LOG.info(f"DISCOVERED: {es['title']} at {es['start']}")

            es = state["events"][eid]

            # Check for cancellation
            if event.get("status") == "cancelled":
                es["state"] = "CANCELLED"
                continue

            # Transition: DISCOVERED → BRIEFED
            if es["state"] == "DISCOVERED":
                start_time = datetime.fromisoformat(es["start"])
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                minutes_until = (start_time - now).total_seconds() / 60

                if minutes_until <= pre_brief_min:
                    subject, body = generate_prebrief(es)

                    if dry_run:
                        print(f"\n{'='*60}")
                        print(f"[DRY RUN] Would stage pre-brief:")
                        print(f"Subject: {subject}")
                        print(f"{'='*60}")
                        print(body)
                        print(f"{'='*60}\n")
                    else:
                        try:
                            stage_prebrief(eid, subject, body, es)
                            LOG.info(f"BRIEFED (staged): {es['title']}")
                        except Exception as e:
                            LOG.error(f"Failed to stage pre-brief for {es['title']}: {e}")
                            continue

                    es["state"] = "BRIEFED"
                    es["briefed_at"] = now.isoformat()

    # Prune events older than 7 days
    cutoff = (now - timedelta(days=7)).isoformat()
    to_remove = [
        eid for eid, es in state["events"].items()
        if es.get("start", "") < cutoff and es["state"] in ("DONE", "CANCELLED", "SKIPPED")
    ]
    for eid in to_remove:
        del state["events"][eid]

    save_state(state)

    if dry_run:
        print(f"\nPoll complete. {len(state['events'])} events tracked.")


# ── Status Command ──────────────────────────────────────────────────────────

def status():
    """Show current event states."""
    state = load_state()
    events = state.get("events", {})

    if not events:
        print("No events tracked.")
        return

    print(f"Last poll: {state.get('last_poll', 'never')}")
    print(f"Events: {len(events)}\n")

    for eid, es in sorted(events.items(), key=lambda x: x[1].get("start", "")):
        start = datetime.fromisoformat(es["start"]).strftime("%m/%d %H:%M") if es.get("start") else "?"
        state_str = es["state"]
        title = es["title"][:40]
        names = ", ".join(es.get("attendee_names", []))[:30]
        print(f"  [{state_str:12s}] {start} {title:<40s} {names}")


# ── Brief Command ───────────────────────────────────────────────────────────

def brief(event_id):
    """Force-generate and print a pre-brief for an event."""
    state = load_state()
    es = state["events"].get(event_id)
    if not es:
        print(f"Event {event_id} not found in state.")
        sys.exit(1)

    subject, body = generate_prebrief(es)
    print(f"Subject: {subject}\n")
    print(body)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [mtg] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "poll":
        dry_run = "--dry-run" in sys.argv
        poll(dry_run=dry_run)
    elif cmd == "status":
        status()
    elif cmd == "brief":
        if len(sys.argv) < 3:
            print("Usage: mtg.py brief <event-id>")
            sys.exit(1)
        brief(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
