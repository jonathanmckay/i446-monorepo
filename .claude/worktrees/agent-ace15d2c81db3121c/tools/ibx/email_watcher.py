#!/usr/bin/env python3
"""
email_watcher — Gmail new-mail notifications via historyId polling.

Uses Gmail API history.list to detect new messages since the last check.
Only hits the API when the historyId changes, so it's lightweight.
Sends a macOS notification via terminal-notifier; clicking opens ibx.

Poll interval: 30s (well within Gmail's rate limits).
"""

import json
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CONFIG_DIR = Path.home() / ".config" / "eml"
STATE_FILE = Path.home() / ".config" / "eml" / "watcher_state.json"
OPEN_SCRIPT = Path(__file__).parent / "ibx-open-email.sh"

ACCOUNTS = [
    {"name": "m5c7", "tokens": "tokens.json", "creds": "gcp-oauth.keys.json"},
    {"name": "gmail", "tokens": "tokens-gmail.json", "creds": "gcp-oauth-gmail.keys.json"},
]

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
POLL_INTERVAL = 30  # seconds


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_service(tokens_file: str, creds_file: str):
    tokens_path = CONFIG_DIR / tokens_file
    creds_path = CONFIG_DIR / creds_file
    if not tokens_path.exists():
        return None
    with open(tokens_path) as f:
        data = json.load(f)
    creds = Credentials(
        token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(tokens_path, "w") as f:
                json.dump({
                    "access_token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                }, f)
        except Exception as e:
            print(f"Token refresh failed for {tokens_file}: {e}", file=sys.stderr)
            return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


# ── Notify ────────────────────────────────────────────────────────────────────

def notify(message: str):
    import subprocess
    try:
        subprocess.run([
            "terminal-notifier",
            "-title", "ibx",
            "-message", message,
            "-sound", "default",
            "-execute", f'bash "{OPEN_SCRIPT}"',
            "-group", "ibx-email",
        ], capture_output=True)
    except Exception:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "ibx"'
        ])


# ── Poll ──────────────────────────────────────────────────────────────────────

def get_history_id(svc):
    try:
        profile = svc.users().getProfile(userId="me").execute()
        return str(profile.get("historyId", ""))
    except Exception:
        return None

def count_new_since(svc, since_history_id: str) -> int:
    """Return count of new INBOX messages since the given historyId."""
    try:
        resp = svc.users().history().list(
            userId="me",
            startHistoryId=since_history_id,
            historyTypes=["messageAdded"],
            labelId="INBOX",
        ).execute()
        history = resp.get("history", [])
        count = sum(
            1 for h in history
            for m in h.get("messagesAdded", [])
            if "SENT" not in m.get("message", {}).get("labelIds", [])
        )
        return count
    except Exception:
        return 0

def poll_account(svc, name: str, state: dict) -> dict:
    current_id = get_history_id(svc)
    if not current_id:
        return state

    last_id = state.get(name)
    if last_id and current_id != last_id:
        count = count_new_since(svc, last_id)
        if count > 0:
            notify(f"{count} new email{'s' if count != 1 else ''} ({name})")

    state[name] = current_id
    return state


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    state = load_state()

    # Initialize historyIds without notifying (don't notify on startup)
    services = {}
    for acct in ACCOUNTS:
        svc = get_service(acct["tokens"], acct["creds"])
        if svc:
            services[acct["name"]] = svc
            hid = get_history_id(svc)
            if hid:
                state[acct["name"]] = hid
                print(f"email_watcher: {acct['name']} historyId={hid}", flush=True)
        else:
            print(f"email_watcher: skipping {acct['name']} (no credentials)", flush=True)

    if not services:
        print("No Gmail accounts authenticated. Run ibx first.", file=sys.stderr)
        sys.exit(1)

    save_state(state)
    print(f"email_watcher: polling every {POLL_INTERVAL}s", flush=True)

    while True:
        time.sleep(POLL_INTERVAL)
        for name, svc in services.items():
            try:
                state = poll_account(svc, name, state)
            except Exception as e:
                print(f"email_watcher: error polling {name}: {e}", file=sys.stderr)
        save_state(state)


if __name__ == "__main__":
    main()
