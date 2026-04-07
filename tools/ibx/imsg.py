#!/usr/bin/env python3
"""
imsg — iMessage as Cards CLI
Process unread iMessage threads one at a time.
"""

import glob
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

# ── Config ────────────────────────────────────────────────────────────────────

CHAT_DB = Path.home() / "Library/Messages/chat.db"
DB_SNAPSHOT = Path("/tmp/imsg_snapshot.db")
STATE_DIR = Path.home() / ".config/imsg"
STATE_FILE = STATE_DIR / "processed.json"
TASK_STATE_FILE = STATE_DIR / "todoist_task.json"
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
# Apple Core Data epoch offset: seconds between 2001-01-01 and Unix epoch
APPLE_EPOCH_OFFSET = 978307200

console = Console()
ai = anthropic.Anthropic()
_contacts: dict[str, str] = {}

# ── State ─────────────────────────────────────────────────────────────────────

def load_processed() -> dict:
    """Return {chat_identifier: latest_apple_ts_processed}.
    Migrates legacy list format (all-time block) to dict using 0 as the cutoff so
    all unread messages for previously-seen threads still surface after migration."""
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, list):
            return {cid: 0 for cid in data}
        return data
    return {}

def save_processed(processed: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(processed, f)

def mark_thread_read(chat_identifier: str):
    """Mark all incoming messages in a chat as read in the live chat.db."""
    try:
        conn = sqlite3.connect(str(CHAT_DB), timeout=5)
        conn.execute("""
            UPDATE message SET is_read = 1
            WHERE rowid IN (
                SELECT cmj.message_id
                FROM chat_message_join cmj
                JOIN chat c ON c.rowid = cmj.chat_id
                WHERE c.chat_identifier = ?
            ) AND is_from_me = 0 AND is_read = 0
        """, (chat_identifier,))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Non-fatal — processed.json is the source of truth

# ── Contacts ─────────────────────────────────────────────────────────────────

_KNOWN_AB_CLASSES = {
    "streamtyped", "NSMutableAttributedString", "NSAttributedString",
    "NSObject", "NSMutableString", "NSString", "NSDictionary",
    "NSMutableDictionary", "NSColor", "NSFont", "NSParagraphStyle",
}

def extract_attributed_text(data: bytes) -> str:
    """Extract plain text from a TypedStream-encoded NSAttributedString.

    Strategy 1: scan raw bytes for TypedStream '+' string encoding:
      0x2B ('+') + 1-byte length (0x01–0x7F) + UTF-8 content
      0x2B ('+') + 0x80 + 2-byte big-endian length + UTF-8 content
    This avoids the bug where the length byte (e.g. 0x41 = 'A' for a 65-char
    message) is printable and gets confused with the message text by the regex.

    Strategy 2: regex fallback over UTF-8-decoded bytes for non-standard payloads.
    """
    # ── Raw-bytes pass ─────────────────────────────────────────────────────
    i = 0
    while i < len(data) - 2:
        if data[i] == 0x2B:  # '+' TypedStream string marker
            lb = data[i + 1]
            if 0 < lb < 0x80:
                start, length = i + 2, lb
            elif lb == 0x80 and i + 4 <= len(data):
                length = (data[i + 2] << 8) | data[i + 3]
                start = i + 4
            else:
                i += 1
                continue
            end = start + length
            if end > len(data):
                i += 1
                continue
            try:
                candidate = data[start:end].decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                i += 1
                continue
            if candidate and candidate not in _KNOWN_AB_CLASSES and not candidate.startswith("NS"):
                return candidate
        i += 1

    # ── Regex fallback (handles simplified / non-standard payloads) ─────────
    text = data.decode("utf-8", errors="replace")
    chunks = re.findall(r'[^\x00-\x1f\x7f-\x9f\ufffd]{4,}', text)
    result = [c for c in chunks if c not in _KNOWN_AB_CLASSES and not c.startswith("NS")]
    if not result:
        return ""
    msg = result[0]
    # Strip TypedStream '+' marker plus the following noise byte (length/type prefix)
    # e.g. "+pCheck the license..." → "Check the license..."
    msg = re.sub(r'^\+.', '', msg)
    return msg

def _normalize_phone(number: str) -> str:
    """Strip non-digits; for US numbers return last 10 digits."""
    digits = re.sub(r'\D', '', number)
    return digits[-10:] if len(digits) >= 10 else digits

def build_contact_cache() -> dict[str, str]:
    """Return phone→name mapping from AddressBook databases."""
    cache: dict[str, str] = {}
    dbs = glob.glob(
        str(Path.home() / "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")
    )
    dbs.append(str(Path.home() / "Library/Application Support/AddressBook/AddressBook-v22.abcddb"))
    for db_path in dbs:
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZORGANIZATION, p.ZFULLNUMBER
                FROM ZABCDPHONENUMBER p
                JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK
                WHERE p.ZFULLNUMBER IS NOT NULL
            """).fetchall()
            for first, last, nick, org, phone in rows:
                name = " ".join(filter(None, [first, last])).strip() or nick or org or ""
                if name and phone:
                    key = _normalize_phone(phone)
                    if key:
                        cache[key] = name
            conn.close()
        except Exception:
            pass
    return cache

# ── Database ──────────────────────────────────────────────────────────────────

def snapshot_db() -> sqlite3.Connection:
    """Copy chat.db to /tmp to avoid lock conflicts with Messages.app."""
    shutil.copy2(CHAT_DB, DB_SNAPSHOT)
    conn = sqlite3.connect(str(DB_SNAPSHOT))
    conn.row_factory = sqlite3.Row
    return conn

def apple_ts_to_datetime(ts) -> datetime:
    """Convert Apple Core Data timestamp (nanoseconds since 2001-01-01) to datetime."""
    if ts is None:
        return datetime.now()
    unix_ts = ts / 1e9 + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts)

def fetch_unread_threads(conn: sqlite3.Connection, processed: set, days: int = 7) -> list[dict]:
    """Fetch threads with inbound messages in the last `days` days, excluding processed ones.

    Uses a time window rather than is_read so messages read elsewhere (phone, Mac) still appear.
    processed.json is the sole source of truth for what ibx has already handled.
    """
    import time
    cutoff_apple_ts = int((time.time() - APPLE_EPOCH_OFFSET - days * 86400) * 1e9)

    rows = conn.execute("""
        SELECT
            c.ROWID as chat_id,
            c.guid,
            c.chat_identifier,
            c.display_name,
            COUNT(DISTINCT m.ROWID) as unread_count,
            MAX(m.date) as latest_date
        FROM chat c
        JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE m.is_from_me = 0
          AND m.date >= ?
        GROUP BY c.ROWID
        ORDER BY latest_date DESC
    """, (cutoff_apple_ts,)).fetchall()

    threads = []
    for row in rows:
        last_ts = processed.get(row["chat_identifier"], -1)
        if row["latest_date"] <= last_ts:
            continue  # no new messages since last processed
        threads.append(dict(row))
    return threads

def fetch_thread_messages(conn: sqlite3.Connection, chat_id: int, limit: int = 15) -> list[dict]:
    """Fetch recent messages in a thread, oldest first."""
    rows = conn.execute("""
        SELECT
            m.ROWID,
            m.text,
            m.attributedBody,
            m.date,
            m.is_from_me,
            h.id as handle_id,
            h.uncanonicalized_id as display_handle
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.ROWID IN (
            SELECT message_id FROM chat_message_join WHERE chat_id = ?
        )
        ORDER BY m.date DESC
        LIMIT ?
    """, (chat_id, limit)).fetchall()

    messages = []
    for row in rows:
        text = row["text"] or ""
        if not text and row["attributedBody"]:
            text = extract_attributed_text(bytes(row["attributedBody"]))
        if not text:
            continue
        dt = apple_ts_to_datetime(row["date"])
        messages.append({
            "id": row["ROWID"],
            "text": text,
            "date": dt,
            "is_from_me": bool(row["is_from_me"]),
            "handle_id": row["handle_id"] or "",
            "display_handle": row["display_handle"] or row["handle_id"] or "",
        })

    return list(reversed(messages))  # chronological order

def resolve_display_name(identifier: str) -> str:
    """Look up a phone number or email in the contacts cache."""
    key = _normalize_phone(identifier)
    if key in _contacts:
        return _contacts[key]
    return identifier

def build_thread_card(conn: sqlite3.Connection, thread_row: dict) -> dict:
    """Build a normalized thread dict for display and Claude."""
    chat_id = thread_row["chat_id"]
    messages = fetch_thread_messages(conn, chat_id)

    is_group = bool(thread_row["display_name"])
    display_name = thread_row["display_name"] or resolve_display_name(thread_row["chat_identifier"])

    convo_lines = []
    for m in messages[-10:]:
        sender = "me" if m["is_from_me"] else resolve_display_name(m["display_handle"] or display_name)
        ts = m["date"].strftime("%I:%M%p").lower().lstrip("0")
        convo_lines.append(f"[{ts}] {sender}: {m['text']}")

    unread_msgs = [m for m in messages if not m["is_from_me"]]
    latest_handle = unread_msgs[-1]["handle_id"] if unread_msgs else thread_row["chat_identifier"]
    latest_date = messages[-1]["date"].strftime("%b %d, %I:%M%p").lower() if messages else ""

    return {
        "chat_id": chat_id,
        "chat_identifier": thread_row["chat_identifier"],
        "chat_guid": thread_row["guid"],
        "display_name": display_name,
        "is_group": is_group,
        "handle_id": latest_handle,
        "unread_count": thread_row["unread_count"],
        "latest_date": latest_date,
        "latest_apple_ts": thread_row["latest_date"],  # raw Apple ns ts for processed tracking
        "conversation": "\n".join(convo_lines),
        "last_message": unread_msgs[-1]["text"] if unread_msgs else "",
    }

# ── Todoist integration ───────────────────────────────────────────────────────

def close_todoist_task():
    """Close the pending-iMessages Todoist task created by imsg_watcher, if any."""
    try:
        import requests as _req
        if not TASK_STATE_FILE.exists():
            return
        task_id = json.loads(TASK_STATE_FILE.read_text()).get("task_id")
        if not task_id:
            return
        _req.post(
            f"https://api.todoist.com/api/v1/tasks/{task_id}/close",
            headers={"Authorization": f"Bearer {TODOIST_TOKEN}"},
            timeout=5,
        )
        TASK_STATE_FILE.write_text(json.dumps({"task_id": None}))
    except Exception:
        pass

# ── Actions ───────────────────────────────────────────────────────────────────

def reply_imessage(thread: dict, text: str):
    """Send a reply via AppleScript (works for both 1:1 and group chats)."""
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_id = thread["chat_guid"].replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
tell application "Messages"
    set theChat to (first chat whose id is "{safe_id}")
    send "{safe_text}" to theChat
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

def delete_conversation(thread: dict):
    """Delete the conversation via AppleScript."""
    safe_id = thread["chat_guid"].replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
tell application "Messages"
    set theChat to (first chat whose id is "{safe_id}")
    delete theChat
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

# ── Triage ─────────────────────────────────────────────────────────────────────

def classify_thread(thread: dict) -> bool:
    """Ask Claude whether this thread needs a response. Returns True if info-only."""
    prompt = f"""Classify this iMessage thread: does it require a response or action, or is it purely informational/automated?

Reply with ONLY "info" or "response".

- "info" = automated alerts, OTP codes, shipping notifications, spam, one-way notifications, appointment reminders
- "response" = someone asking a question, making plans, personal conversation, needs a reply

THREAD:
From: {thread['display_name']}
Recent messages:
{thread['conversation'][-500:]}"""

    msg = ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = msg.content[0].text.strip().lower()
    return "info" in answer and "response" not in answer

def triage_threads(threads: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pre-classify threads. Returns (response-needed threads, auto-handled threads)."""
    needs_response = []
    auto_handled = []

    for t in threads:
        try:
            is_info = classify_thread(t)
        except Exception:
            is_info = False  # on error, keep for review

        name = (t["display_name"] or resolve_display_name(t["chat_identifier"]))[:50]
        if is_info:
            auto_handled.append(t)
            console.print(f"  [dim]→ auto:[/dim] {name}")
        else:
            needs_response.append(t)
            console.print(f"  [dim]→ keep:[/dim] {name}")

    return needs_response, auto_handled

# ── Claude ─────────────────────────────────────────────────────────────────────

def ask_claude(thread: dict, user_input: str) -> dict:
    prompt = f"""You are a personal messaging assistant for Jonathan McKay.
Given an iMessage thread and a user instruction, respond with JSON only — no prose outside JSON.

Schema:
{{"action": "<action>", "message": "<short explanation>", "content": "<optional content>"}}

Actions:
- "reply" — send a reply; put the reply text in "content"
- "task" — create a todo; put task text in "content"
- "done" — mark as handled (no reply needed)
- "delete" — delete the conversation
- "skip" — leave for later
- "answer" — just answer the question, no action; put answer in "message"

THREAD:
From: {thread['display_name']}
Last message: {thread['last_message']}

Conversation:
{thread['conversation']}

---
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
    except json.JSONDecodeError:
        return {"action": "answer", "message": text}

def spawn_claude_tui(thread: dict, instruction: str = ""):
    prompt = (
        f"iMessage thread with {thread['display_name']} | {thread['latest_date']}\n\n"
        f"{thread['conversation']}\n\n---\n"
        f"{instruction or 'Help me with this message thread.'}"
    )
    subprocess.run(["claude", prompt])

# ── Display ───────────────────────────────────────────────────────────────────

def display_card(thread: dict, index: int, total: int):
    console.print()
    header = Text()
    header.append("FROM:    ", style="bold dim")
    header.append(thread["display_name"], style="bold cyan")
    if thread["is_group"]:
        header.append("  [group]", style="dim magenta")
    header.append(f"\nDATE:    ", style="bold dim")
    header.append(thread["latest_date"], style="dim")
    header.append(f"\nUNREAD:  ", style="bold dim")
    header.append(str(thread["unread_count"]), style="bold yellow")

    console.print(Panel(
        header,
        box=box.SIMPLE_HEAD,
        border_style="dim",
        padding=(0, 1),
    ))
    console.print(Panel(
        thread["conversation"] or "(no messages)",
        box=box.SIMPLE,
        border_style="dim",
        padding=(0, 1),
    ))

def print_help():
    console.print(
        "\n[dim]Commands:[/dim]  "
        "[bold]d[/bold] done  "
        "[bold]x[/bold] delete  "
        "[bold]s[/bold] skip  "
        "[bold]r <text>[/bold] reply  "
        "[bold]t <text>[/bold] todo  "
        "[bold]c <text>[/bold] claude TUI  "
        "[bold]q[/bold] quit  "
        "[dim]or type anything → Claude[/dim]\n"
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _contacts
    console.print("[bold]imsg[/bold] — reading iMessages...", style="dim")
    _contacts = build_contact_cache()

    if not CHAT_DB.exists():
        console.print("[red]chat.db not found. Grant Terminal Full Disk Access in System Settings → Privacy.[/red]")
        sys.exit(1)

    processed = load_processed()

    try:
        conn = snapshot_db()
    except Exception as e:
        console.print(f"[red]Failed to read chat.db: {e}[/red]")
        sys.exit(1)

    raw_threads = fetch_unread_threads(conn, processed)

    if not raw_threads:
        console.print("[dim]No unread iMessages.[/dim]")
        conn.close()
        return

    console.print(f"\n[bold]Triaging {len(raw_threads)} thread(s)...[/bold]")
    threads_to_review, auto_handled = triage_threads(raw_threads)

    if auto_handled:
        for t in auto_handled:
            processed[t["chat_identifier"]] = t["latest_date"]
        save_processed(processed)
        console.print(f"  [dim]{len(auto_handled)} auto-handled (info-only)[/dim]")

    if not threads_to_review:
        console.print("[dim]No threads need your attention.[/dim]")
        conn.close()
        return

    # Build full thread cards
    threads = []
    for t in threads_to_review:
        try:
            card = build_thread_card(conn, t)
            threads.append(card)
        except Exception as e:
            console.print(f"[yellow]Error loading thread '{t.get('display_name', '?')}': {e}[/yellow]")

    conn.close()

    total = len(threads)
    console.print(f"[dim]{total} thread(s) need review[/dim]")
    print_help()

    index = 0
    skipped = []

    while True:
        if index >= len(threads):
            if skipped:
                console.print(f"\n[dim]Cycling back through {len(skipped)} skipped...[/dim]")
                threads = skipped
                skipped = []
                index = 0
            else:
                break

        thread = threads[index]
        display_card(thread, index + 1, total)
        console.print(f"[dim][{index + 1}/{total}][/dim] ", end="")

        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            break

        if not user_input:
            index += 1
            continue

        cmd = user_input.lower()

        if cmd == "q":
            console.print("[dim]Bye.[/dim]")
            break

        elif cmd == "d":
            processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
            save_processed(processed)
            console.print("[green]Done.[/green]")
            index += 1

        elif cmd == "x":
            processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
            save_processed(processed)
            console.print("[red]Done (manually delete in Messages if needed).[/red]")
            index += 1

        elif cmd == "s":
            skipped.append(thread)
            console.print("[dim]Skipped.[/dim]")
            index += 1

        elif cmd.startswith("r "):
            instruction = user_input[2:].strip()
            console.print("[dim]Drafting reply...[/dim]")
            result = ask_claude(thread, f"Write a reply: {instruction}")
            draft = result.get("content") or result.get("message", "")
            console.print(Panel(draft, title="Reply Draft", border_style="cyan"))
            console.print("[bold cyan]Send?[/bold cyan] [dim][y/n][/dim]")
            confirm = input("> ").strip().lower()
            if confirm == "y":
                try:
                    reply_imessage(thread, draft)
                    processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                    save_processed(processed)
                    console.print("[green]Sent + done.[/green]")
                    index += 1
                except Exception as e:
                    console.print(f"[red]Send failed: {e}[/red]")
            else:
                console.print("[dim]Cancelled.[/dim]")

        elif cmd.startswith("t "):
            task_hint = user_input[2:].strip()
            console.print(f"[green]Todo:[/green] {task_hint}")
            subprocess.run(["pbcopy"], input=task_hint.encode())
            console.print("[dim](copied to clipboard)[/dim]")
            processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
            save_processed(processed)
            index += 1

        elif cmd == "?":
            print_help()

        elif cmd.startswith("c ") or cmd == "c":
            instruction = user_input[2:].strip() if len(user_input) > 1 else ""
            console.print("[dim]Opening Claude TUI...[/dim]")
            spawn_claude_tui(thread, instruction)
            console.print()
            display_card(thread, index + 1, len(threads))
            post = input("[d]one / [r]eply / [x]delete / [s]kip? ").strip().lower()
            if post == "d":
                processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                save_processed(processed)
                console.print("[green]Done.[/green]")
                index += 1
            elif post.startswith("r"):
                reply_text = post[1:].strip() or input("Reply: ").strip()
                if reply_text:
                    try:
                        reply_imessage(thread, reply_text)
                        processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                        save_processed(processed)
                        console.print("[green]Sent + done.[/green]")
                    except Exception as e:
                        console.print(f"[red]Send failed: {e}[/red]")
                index += 1
            elif post == "x":
                processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                save_processed(processed)
                console.print("[red]Done (manually delete in Messages if needed).[/red]")
                index += 1
            else:
                index += 1

        else:
            # Natural language → Claude → propose action → confirm
            console.print("[dim]...[/dim]")
            result = ask_claude(thread, user_input)
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
                if confirm.startswith("y"):
                    try:
                        reply_imessage(thread, content)
                        processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                        save_processed(processed)
                        console.print("[green]Sent + done.[/green]")
                        index += 1
                    except Exception as e:
                        console.print(f"[red]Send failed: {e}[/red]")
                elif confirm.startswith("e"):
                    new_text = input("Edit reply: ").strip()
                    if new_text:
                        console.print(Panel(new_text, title="Edited Reply", border_style="cyan"))
                        console.print("[bold cyan]Send?[/bold cyan] [dim][y/n][/dim]")
                        confirm2 = input("> ").strip().lower()
                        if confirm2 == "y":
                            try:
                                reply_imessage(thread, new_text)
                                processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                                save_processed(processed)
                                console.print("[green]Sent + done.[/green]")
                                index += 1
                            except Exception as e:
                                console.print(f"[red]Send failed: {e}[/red]")
                        else:
                            console.print("[dim]Cancelled.[/dim]")
                else:
                    console.print("[dim]Cancelled.[/dim]")

            else:
                # done, delete, skip, task
                console.print(f"\n[bold]Proposed:[/bold] {action}")
                if message:
                    console.print(f"[dim]{message}[/dim]")
                if content:
                    console.print(f"  {content}")
                console.print("[bold cyan]OK?[/bold cyan] [dim][y/n][/dim]")
                confirm = input("> ").strip().lower()
                if confirm == "y":
                    if action == "done":
                        processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                        save_processed(processed)
                        console.print("[green]Done.[/green]")
                        index += 1
                    elif action == "delete":
                        processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                        save_processed(processed)
                        console.print("[red]Done (manually delete in Messages if needed).[/red]")
                        index += 1
                    elif action == "skip":
                        skipped.append(thread)
                        console.print("[dim]Skipped.[/dim]")
                        index += 1
                    elif action == "task":
                        task_text = content or message
                        console.print(f"[green]Todo:[/green] {task_text}")
                        subprocess.run(["pbcopy"], input=task_text.encode())
                        console.print("[dim](copied to clipboard)[/dim]")
                        processed[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
                        save_processed(processed)
                        index += 1
                else:
                    console.print("[dim]Cancelled.[/dim]")

    save_processed(processed)
    close_todoist_task()
    console.print("\n[dim]imsg done.[/dim]")

if __name__ == "__main__":
    main()
