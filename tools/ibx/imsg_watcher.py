#!/usr/bin/env python3
"""
imsg_watcher — instant iMessage notifications via FSEvents (fswatch).

Watches chat.db for changes. On each change, queries for new unread threads
not yet notified. Sends a macOS notification via terminal-notifier.
Clicking the notification opens imsg in a new cmux tab.
"""

import json
import requests
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CHAT_DB = Path.home() / "Library/Messages/chat.db"
DB_SNAPSHOT = Path("/tmp/imsg_watcher.db")
STATE_FILE = Path.home() / ".config/imsg/watcher_notified.json"
TASK_STATE_FILE = Path.home() / ".config/imsg/todoist_task.json"
OPEN_SCRIPT = Path(__file__).parent / "ibx-open-imsg.sh"

APPLE_EPOCH_OFFSET = 978307200
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
TODOIST_API = "https://api.todoist.com/api/v1"


# ── State ─────────────────────────────────────────────────────────────────────

def load_notified() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_notified(ids: set):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(list(ids)))


# ── DB ────────────────────────────────────────────────────────────────────────

def get_unread_threads() -> dict[str, int]:
    """Return {chat_identifier: unread_count} for all unread threads."""
    try:
        shutil.copy2(CHAT_DB, DB_SNAPSHOT)
        conn = sqlite3.connect(str(DB_SNAPSHOT))
        rows = conn.execute("""
            SELECT c.chat_identifier, COUNT(DISTINCT m.ROWID) as cnt
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            WHERE m.is_read = 0 AND m.is_from_me = 0
            GROUP BY c.ROWID
        """).fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# ── Todoist ───────────────────────────────────────────────────────────────────

def load_task_id() -> str | None:
    if TASK_STATE_FILE.exists():
        try:
            return json.loads(TASK_STATE_FILE.read_text()).get("task_id")
        except Exception:
            pass
    return None

def save_task_id(task_id: str | None):
    TASK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TASK_STATE_FILE.write_text(json.dumps({"task_id": task_id}))

def ensure_todoist_task(new_threads: dict):
    """Create a Todoist inbox task for pending iMessages, if one isn't already open."""
    existing = load_task_id()
    if existing:
        try:
            resp = requests.get(
                f"{TODOIST_API}/tasks/{existing}",
                headers={"Authorization": f"Bearer {TODOIST_TOKEN}"},
                timeout=5,
            )
            if resp.status_code == 200:
                return  # task still open
        except Exception:
            pass

    count = sum(new_threads.values())
    n = len(new_threads)
    label = f"imsg: {count} new message{'s' if count != 1 else ''} in {n} thread{'s' if n != 1 else ''}"
    try:
        resp = requests.post(
            f"{TODOIST_API}/tasks",
            headers={"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"},
            json={"content": label, "due_string": "today", "labels": ["imsg"]},
            timeout=5,
        )
        if resp.status_code == 200:
            task_id = resp.json().get("id")
            if task_id:
                save_task_id(task_id)
    except Exception:
        pass

# ── Notify ────────────────────────────────────────────────────────────────────

def notify(title: str, message: str):
    try:
        subprocess.run([
            "terminal-notifier",
            "-title", title,
            "-message", message,
            "-sound", "default",
            "-execute", f'bash "{OPEN_SCRIPT}"',
            "-group", "ibx-imsg",
        ], capture_output=True)
    except Exception:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"'
        ])


# ── Main ──────────────────────────────────────────────────────────────────────

def check_and_notify(notified: set) -> set:
    current = get_unread_threads()
    new_threads = {k: v for k, v in current.items() if k not in notified}
    if new_threads:
        count = sum(new_threads.values())
        n = len(new_threads)
        label = f"{count} message{'s' if count != 1 else ''} in {n} thread{'s' if n != 1 else ''}"
        notify("imsg", label)
        ensure_todoist_task(new_threads)
        notified = notified | set(new_threads.keys())
        save_notified(notified)
    # Prune notified set: remove threads that are no longer unread
    notified = notified & set(current.keys())
    save_notified(notified)
    return notified


def main():
    if not CHAT_DB.exists():
        print("chat.db not found — grant Full Disk Access to Terminal", file=sys.stderr)
        sys.exit(1)

    notified = load_notified()

    try:
        proc = subprocess.Popen(
            ["fswatch", "--one-per-batch", "--event=Updated", str(CHAT_DB)],
            stdout=subprocess.PIPE,
        )
        print(f"imsg_watcher: watching {CHAT_DB} via fswatch", flush=True)
        for _ in proc.stdout:
            time.sleep(0.5)  # debounce
            notified = check_and_notify(notified)
    except FileNotFoundError:
        print("fswatch not found, falling back to 30s polling", file=sys.stderr)
        while True:
            notified = check_and_notify(notified)
            time.sleep(30)


if __name__ == "__main__":
    main()
