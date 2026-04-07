#!/usr/bin/env python3
"""
ibx-all — Unified inbox: Gmail + iMessages + Slack in one queue.
Fetches from all sources, sorts by timestamp, presents as a single card stream.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

# ── Import source modules ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import ibx as _ibx
import imsg as _imsg
import slack as _slack

# ── Auto-sign integration (optional — skipped if module not found) ────────────
_AUTOSIGN_DIR = Path(__file__).parent.parent / "m5x2-automations"
sys.path.insert(0, str(_AUTOSIGN_DIR))
try:
    import lease_signer as _signer
    import automations_db as _autodb
    from config import AUTOSIGN_SENDERS, DB_PATH as _AUTODB_PATH
    _autosign_available = True
except ImportError:
    _autosign_available = False

import anthropic
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()
ai = anthropic.Anthropic()

# ── Type badge colors ─────────────────────────────────────────────────────────
TYPE_STYLE = {
    "email": "bold green",
    "imsg":  "bold magenta",
    "slack": "bold blue",
}
TYPE_LABEL = {
    "email": "EMAIL",
    "imsg":  "IMSG",
    "slack": "SLACK",
}

# ── Normalize items ───────────────────────────────────────────────────────────

def normalize_email(msg_ref, service, account):
    """Fetch and normalize a Gmail message into a unified item."""
    try:
        email = _ibx.get_email(service, msg_ref["id"])
        email["_account"] = account
    except Exception as e:
        return None
    return {
        "type": "email",
        "source": account,
        "from": email.get("from", ""),
        "to": email.get("to", ""),
        "cc": email.get("cc", ""),
        "preview": email.get("subject", "(no subject)"),
        "body": email.get("body", ""),
        "ts": 0.0,  # Gmail doesn't give easy sort ts; use fetch order
        "_data": {"email": email, "service": service},
    }

def normalize_imsg(thread):
    """Normalize an iMessage thread card into a unified item."""
    try:
        # Parse latest_date like "apr 05, 01:30pm"
        ts = 0.0
    except Exception:
        ts = 0.0
    return {
        "type": "imsg",
        "source": "iMessage",
        "from": thread.get("display_name", ""),
        "preview": thread.get("last_message", "")[:80],
        "body": thread.get("conversation", ""),
        "ts": ts,
        "_data": {"thread": thread},
    }

def normalize_slack(thread, token, workspace):
    """Normalize a Slack thread into a unified item."""
    try:
        ts = float(thread.get("latest_ts", 0))
    except Exception:
        ts = 0.0
    msgs = thread.get("messages", [])
    body = "\n".join(f"{m['sender']} ({m['time']}): {m['text']}" for m in msgs)
    return {
        "type": "slack",
        "source": workspace,
        "from": thread.get("display_name", ""),
        "preview": msgs[-1]["text"][:80] if msgs else "",
        "body": body,
        "ts": ts,
        "_data": {"thread": thread, "token": token, "workspace": workspace},
    }

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

    title = f"{badge}  {source}  {counter}"
    console.print(Panel(header, title=title, border_style="dim", box=box.SIMPLE_HEAD, padding=(0, 1)))

    # Body — show last ~2000 chars
    body = item["body"][-2000:] if item["body"] else "(no content)"
    console.print(Panel(body, box=box.SIMPLE, border_style="dim", padding=(0, 1)))

def print_help():
    console.print(
        "\n[dim]Commands:[/dim]  "
        "[bold]a[/bold] archive/done  "
        "[bold]d[/bold] delete  "
        "[bold]r <text>[/bold] reply  "
        "[bold]s[/bold] skip  "
        "[bold]t <text>[/bold] todo  "
        "[bold]c[/bold] check now  "
        "[bold]q[/bold] quit  "
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


def _autosign_item(item):
    """Fire-and-forget: sign the lease, archive the email, log to DB."""
    url = _signer.extract_appfolio_url(item.get("body", ""))
    if not url:
        console.print("[yellow]  ⚠ autosign: no AppFolio URL found in email[/yellow]")
        return
    meta = _signer.parse_email_metadata(item)
    console.print(f"  [dim cyan]⚙ auto-signing lease: {meta.get('unit', url[:60])}...[/dim cyan]")
    result = _signer.sign_lease(url, headless=True)
    status = result.get("status", "failed")
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
    console.print(f"  [{color}]{icon} lease {status}: {meta.get('unit', '')}[/{color}]")
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
    elif t == "imsg":
        thread = item["_data"]["thread"]
        proc = _imsg.load_processed()
        proc[thread["chat_identifier"]] = thread.get("latest_apple_ts", 0)
        _imsg.save_processed(proc)
        _imsg.mark_thread_read(thread["chat_identifier"])
    elif t == "slack":
        d = item["_data"]
        _slack.mark_read(d["token"], d["thread"]["channel_id"], d["thread"]["latest_ts"])

def do_delete(item):
    t = item["type"]
    if t == "email":
        _ibx.delete(item["_data"]["service"], item["_data"]["email"]["id"])
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

def do_reply(item, reply_text):
    t = item["type"]
    if t == "email":
        _ibx.send_reply(item["_data"]["service"], item["_data"]["email"], reply_text)
    elif t == "imsg":
        _imsg.reply_imessage(item["_data"]["thread"], reply_text)
    elif t == "slack":
        d = item["_data"]
        _slack.send_reply(d["token"], d["thread"]["channel_id"], reply_text)
    do_archive(item)

# ── Claude ────────────────────────────────────────────────────────────────────

def ask_claude(item, user_input):
    t = item["type"]
    type_label = {"email": "email", "imsg": "iMessage", "slack": "Slack DM"}[t]
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

# Display names for each account
ACCOUNT_DISPLAY = {
    "m5c7": "m5x2",
    "gmail": "jbm",
}

def fetch_emails():
    items = []
    per_account = {}  # display_name -> count
    console.print("\n[bold]Gmail[/bold] — connecting...", style="dim")
    services = {}
    for acct in _ibx.ACCOUNTS:
        try:
            svc = _ibx.get_gmail_service(acct["tokens"], acct["creds"])
            services[acct["name"]] = svc
            _ibx._gmail_services[acct["name"]] = svc
            console.print(f"  [dim]✓ {acct['name']}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]✗ {acct['name']}: {e}[/yellow]")

    if not services:
        return items, per_account

    # Triage
    console.print("[dim]  triaging...[/dim]")
    for name, svc in services.items():
        total, moved = _ibx.triage_inbox(svc, name)
        if total:
            console.print(f"  [dim]{name}: {moved}/{total} → no-response-needed[/dim]")

    # Fetch remaining
    for name, svc in services.items():
        msgs = _ibx.fetch_inbox(svc, unread_only=False)
        count = 0
        for m in msgs:
            item = normalize_email(m, svc, name)
            if not item:
                continue
            if _autosign_available and _signer.is_autosign_email(item, AUTOSIGN_SENDERS):
                # Run in background thread — don't block fetch
                t = threading.Thread(target=_autosign_item, args=(item,), daemon=True)
                t.start()
                continue  # don't add to review queue
            items.append(item)
            count += 1
        display = ACCOUNT_DISPLAY.get(name, name)
        per_account[display] = count
        if msgs:
            console.print(f"  [dim]{display}: {count} to review[/dim]")

    return items, per_account

def fetch_imsgs():
    items = []
    console.print("\n[bold]iMessages[/bold] — reading...", style="dim")
    if not _imsg.CHAT_DB.exists():
        console.print("[yellow]  chat.db not found — grant Terminal Full Disk Access[/yellow]")
        return items

    _imsg._contacts = _imsg.build_contact_cache()
    processed = _imsg.load_processed()

    try:
        conn = _imsg.snapshot_db()
    except Exception as e:
        console.print(f"[yellow]  iMessage read failed: {e}[/yellow]")
        return items

    raw_threads = _imsg.fetch_unread_threads(conn, processed)
    if not raw_threads:
        console.print("  [dim]no unread threads[/dim]")
        conn.close()
        return items

    console.print(f"  [dim]triaging {len(raw_threads)} thread(s)...[/dim]")
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
    console.print(f"  [dim]{len(items)} thread(s) to review[/dim]")
    return items

def fetch_slack():
    items = []
    console.print("\n[bold]Slack[/bold] — connecting...", style="dim")
    if not _slack.CONFIG_FILE.exists():
        console.print("  [dim]no tokens configured — skipping[/dim]")
        return items

    with open(_slack.CONFIG_FILE) as f:
        workspaces = json.load(f)

    for workspace, token in workspaces.items():
        try:
            self_id = _slack.get_self_id(token)
            channels = _slack.fetch_unread_channels(token)
            count = 0
            for ch in channels:
                try:
                    thread = _slack.build_thread(token, ch, self_id)
                    if thread:
                        items.append(normalize_slack(thread, token, workspace))
                        count += 1
                except Exception as e:
                    console.print(f"  [yellow]channel error: {e}[/yellow]")
            console.print(f"  [dim]✓ {workspace}: {count} unread[/dim]")
        except Exception as e:
            console.print(f"  [yellow]✗ {workspace}: {e}[/yellow]")

    return items

# ── Background poll ───────────────────────────────────────────────────────────

def _item_uid(item):
    """Stable unique ID for an item, used to track external resolution."""
    if item["type"] == "email":
        return ("email", item["_data"]["email"]["id"])
    elif item["type"] == "imsg":
        return ("imsg", item["_data"]["thread"]["chat_identifier"])
    elif item["type"] == "slack":
        return ("slack", item["_data"]["thread"]["channel_id"])
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
        except Exception:
            pass
    return newly


# ── Main ──────────────────────────────────────────────────────────────────────

TERM_COLOR = Path(__file__).parent.parent.parent / "scripts" / "term-color.sh"

def set_term_color(color):
    if TERM_COLOR.exists():
        subprocess.run(["bash", str(TERM_COLOR), color], capture_output=True)

def main():
    console.print(Rule("[bold]Inbox 0[/bold]", style="dim"))

    email_items, per_account = fetch_emails()
    imsg_items = fetch_imsgs()
    slack_items = fetch_slack()

    # Merge: Slack sorted by ts (most recent first), emails/imsgs in fetch order
    all_items = (
        sorted(slack_items, key=lambda x: -x["ts"]) +
        email_items +
        imsg_items
    )

    # Build per-account email breakdown: "jbm:2  m5x2:1"
    email_parts = "  ".join(
        f"[green]{display}:{count}[/green]"
        for display, count in per_account.items()
    ) or f"[green]0 email[/green]"

    status_line = (f"({email_parts}  "
                   f"[magenta]{len(imsg_items)} iMsg[/magenta]  "
                   f"[blue]{len(slack_items)} slack[/blue])")

    if not all_items:
        console.print(f"\n[dim]Inbox zero.[/dim]  {status_line}")
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
        # Print any status messages from the background poll thread
        while not poll_msgs.empty():
            console.print(poll_msgs.get_nowait())

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
                console.print("[dim]Inbox zero.[/dim]")
                stop_poll.set()
                set_term_color("blue")
                break

        item = all_items[index]
        display_card(item, index + 1, len(all_items))
        print_help()

        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            stop_poll.set()
            set_term_color("blue")
            sys.exit(2)

        if not user_input:
            index += 1
            continue

        cmd = user_input.lower()

        if cmd == "q":
            console.print("[dim]Bye.[/dim]")
            stop_poll.set()
            set_term_color("blue")
            sys.exit(2)

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
                    console.print("[green]Sent + done.[/green]")
                    index += 1
                except Exception as e:
                    console.print(f"[red]Send failed: {e}[/red]")
            else:
                console.print("[dim]Cancelled.[/dim]")

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
                        console.print("[green]Sent + done.[/green]")
                        index += 1
                    except Exception as e:
                        console.print(f"[red]Send failed: {e}[/red]")
                elif confirm == "e":
                    new_text = input("Edit reply: ").strip()
                    if new_text:
                        try:
                            do_reply(item, new_text)
                            console.print("[green]Sent + done.[/green]")
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
