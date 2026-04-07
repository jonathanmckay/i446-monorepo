#!/usr/bin/env python3
"""
ibx — Email as Cards CLI
Process Gmail inbox one email at a time.
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")
import base64
import json
import re
import html
import subprocess
from pathlib import Path
import anthropic
from email import message_from_bytes
from email.utils import parsedate_to_datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich import box

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Config ──────────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]
CONFIG_DIR = Path.home() / ".config" / "eml"
SEND_FROM = "mckay@m5x2.com"  # Override From header for outgoing replies

ACCOUNTS = [
    {"name": "m5c7", "tokens": "tokens.json", "creds": "gcp-oauth.keys.json"},
    {"name": "gmail", "tokens": "tokens-gmail.json", "creds": "gcp-oauth-gmail.keys.json"},
]

console = Console()
ai = anthropic.Anthropic()

# Gmail services populated during auth — used for contact lookup via sent mail search
_gmail_services = {}  # account_name -> service

# ── Auth ─────────────────────────────────────────────────────────────────────

def get_gmail_service(tokens_file="tokens.json", creds_file="gcp-oauth.keys.json"):
    tokens_path = CONFIG_DIR / tokens_file
    creds_path = CONFIG_DIR / creds_file
    creds = None
    tokens_path.parent.mkdir(parents=True, exist_ok=True)

    if tokens_path.exists():
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

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(tokens_path, "w") as f:
            json.dump({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
            }, f)

    return build("gmail", "v1", credentials=creds)

# ── Email Fetching ───────────────────────────────────────────────────────────

def fetch_inbox(service, max_results=50, unread_only=False):
    q = "in:inbox is:unread" if unread_only else "in:inbox"
    result = service.users().messages().list(
        userId="me",
        q=q,
        maxResults=max_results,
    ).execute()
    return result.get("messages", [])

def get_email(service, msg_id):
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="raw"
    ).execute()
    raw = base64.urlsafe_b64decode(msg["raw"])
    parsed = message_from_bytes(raw)

    subject = parsed.get("Subject", "(no subject)")
    from_ = parsed.get("From", "")
    to_ = parsed.get("To", "")
    cc_ = parsed.get("Cc", "")
    date_str = parsed.get("Date", "")
    try:
        date = parsedate_to_datetime(date_str).strftime("%b %d, %I:%M%p").lower()
    except Exception:
        date = date_str

    body = extract_body(parsed)

    return {
        "id": msg_id,
        "subject": subject,
        "from": from_,
        "to": to_,
        "cc": cc_,
        "date": date,
        "body": body,
        "thread_id": msg.get("threadId"),
    }

def extract_body(parsed):
    body = ""
    if parsed.is_multipart():
        for part in parsed.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
        # If text/plain is very short it's likely a disclaimer-only fallback (common in
        # Outlook/Exchange emails). Fall through to the HTML part for the real content.
        if len(body.strip()) < 150:
            body = ""
        if not body:
            for part in parsed.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = html_to_text(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
                        break
    else:
        payload = parsed.get_payload(decode=True)
        if payload:
            body = payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
        if parsed.get_content_type() == "text/html":
            body = html_to_text(body)

    # Trim quoted reply chains
    lines = body.splitlines()
    trimmed = []
    for line in lines:
        if re.match(r"^On .+ wrote:$", line.strip()) or line.strip().startswith(">"):
            break
        trimmed.append(line)
    body = "\n".join(trimmed).strip()

    return body[:3000]  # cap for display + Claude context

def html_to_text(html_str):
    # Strip style and script blocks entirely
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_str, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    # Block elements → newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|tr|li|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    # Remove lines that are just whitespace
    lines = [l.strip() for l in text.splitlines()]
    # Collapse multiple blank lines into one
    result = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()

# ── Actions ──────────────────────────────────────────────────────────────────

# ── Triage ──────────────────────────────────────────────────────────────────

TRIAGE_LABEL = "ai-no-response-needed"

def get_or_create_label(service, label_name):
    """Get label ID by name, creating it if it doesn't exist."""
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    label = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return label["id"]

def classify_email(email):
    """Ask Claude whether this email needs a response. Returns True if info-only."""
    prompt = f"""Classify this email: does it require a response or action from the recipient, or is it purely informational?

Reply with ONLY "info" or "response".

- "info" = newsletters, notifications, confirmations, FYI forwards, automated alerts, receipts, shipping updates, no-reply senders
- "response" = someone asking a question, requesting action, scheduling, needs a decision, lease signing, personal message

EMAIL:
From: {email['from']}
Subject: {email['subject']}
Date: {email['date']}

{email['body'][:1000]}"""

    msg = ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = msg.content[0].text.strip().lower()
    return "info" in answer and "response" not in answer

def triage_inbox(service, account_name):
    """Pre-process inbox: move info-only emails to ai-no-response-needed label."""
    messages = fetch_inbox(service, max_results=200, unread_only=True)
    if not messages:
        return 0, 0

    label_id = get_or_create_label(service, TRIAGE_LABEL)
    moved = 0

    for msg_ref in messages:
        try:
            email = get_email(service, msg_ref["id"])
            is_info = classify_email(email)
            subj = email['subject'][:60]
            if is_info:
                service.users().messages().modify(
                    userId="me", id=msg_ref["id"],
                    body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
                ).execute()
                moved += 1
                console.print(f"  [dim]→ nrn:[/dim] {subj}")
            else:
                console.print(f"  [dim]→ keep:[/dim] {subj}")
        except Exception as e:
            console.print(f"  [yellow]triage error: {e}[/yellow]")

    return len(messages), moved

# ── Contacts ─────────────────────────────────────────────────────────────────

def lookup_contact_email(name: str):
    """Search Gmail sent/inbox for a name, return the best matching email address.
    Uses only the gmail.modify scope — no People API needed."""
    if not _gmail_services:
        return None
    name_lower = name.strip().lower()
    tokens = [t for t in name_lower.split() if len(t) > 1]
    if not tokens:
        return None
    query = " ".join(tokens)
    candidates = {}  # email -> count
    for svc in _gmail_services.values():
        try:
            # Search sent mail first (most reliable signal), then inbox
            for q in [f"in:sent {query}", f"in:inbox {query}"]:
                res = svc.users().messages().list(userId="me", q=q, maxResults=20).execute()
                for m in res.get("messages", []):
                    hdrs = svc.users().messages().get(
                        userId="me", id=m["id"], format="metadata",
                        metadataHeaders=["To", "From", "Cc"],
                    ).execute().get("payload", {}).get("headers", [])
                    for h in hdrs:
                        for addr in re.findall(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', h.get("value", "")):
                            label = h.get("name", "")
                            # Score: sent To is strongest signal
                            weight = 3 if label == "To" and "sent" in q else 1
                            candidates[addr] = candidates.get(addr, 0) + weight
        except Exception:
            pass
    if not candidates:
        return None
    # Filter out self addresses, pick highest-scored
    self_addrs = {svc.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
                  for svc in _gmail_services.values()}
    ranked = sorted(
        [(addr, score) for addr, score in candidates.items() if addr.lower() not in self_addrs],
        key=lambda x: -x[1],
    )
    return ranked[0][0] if ranked else None

# ── Actions ─────────────────────────────────────────────────────────────────

def archive(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["INBOX", "UNREAD"]}
    ).execute()

def delete(service, msg_id):
    service.users().messages().trash(userId="me", id=msg_id).execute()

def mark_read(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()

def forward_email(service, eml, to_addr, note_text):
    import email.mime.text

    body = (
        f"{note_text}\n\n"
        f"---------- Forwarded message ---------\n"
        f"From: {eml['from']}\n"
        f"Subject: {eml['subject']}\n"
        f"Date: {eml['date']}\n\n"
        f"{eml['body']}"
    )

    import email.utils
    _, clean_to = email.utils.parseaddr(to_addr)
    if not clean_to or "@" not in clean_to:
        raise ValueError(f"Could not parse a valid email address from: {to_addr!r}")

    msg = email.mime.text.MIMEText(body)
    msg["To"] = clean_to
    msg["From"] = SEND_FROM
    msg["Subject"] = "Fwd: " + eml["subject"]

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()


def _extract_addresses(header_str):
    """Return list of email addresses from a header like 'Name <a@b.com>, c@d.com'."""
    return re.findall(r'[\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,}', header_str or "")


def send_reply(service, eml, body_text):
    import email.mime.text

    profile = service.users().getProfile(userId="me").execute()
    from_addr = profile["emailAddress"].lower()

    # Reply-all: To = original From, Cc = original To + Cc minus self
    original_from = eml["from"]
    match = re.search(r"<(.+?)>", original_from)
    to_addr = match.group(1) if match else original_from

    all_cc = _extract_addresses(eml.get("to", "")) + _extract_addresses(eml.get("cc", ""))
    cc_addrs = [a for a in all_cc if a.lower() != from_addr and a.lower() != to_addr.lower()
                and a.lower() != SEND_FROM.lower()]

    msg = email.mime.text.MIMEText(body_text)
    msg["To"] = to_addr
    msg["From"] = SEND_FROM
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = "Re: " + eml["subject"]
    msg["In-Reply-To"] = eml["id"]
    msg["References"] = eml["id"]

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": eml["thread_id"]}
    ).execute()

# ── Claude ───────────────────────────────────────────────────────────────────

def spawn_claude_tui(email, instruction=""):
    """Spawn full interactive Claude CLI TUI with email context."""
    prompt = (
        f"Email from {email['from']} | Subject: {email['subject']} | Date: {email['date']}\n\n"
        f"{email['body']}\n\n---\n"
        f"{instruction or 'Help me with this email.'}"
    )
    subprocess.run(["claude", prompt])

def ask_claude(email, user_input):
    prompt = f"""You are a personal email assistant for Jonathan McKay (Microsoft, GitHub/CoreAI Growth).
Given an email and a user instruction, respond with JSON only — no prose outside JSON.

Schema:
{{"action": "<action>", "message": "<short explanation>", "content": "<optional content>", "to": "<recipient address for forward>"}}

Actions:
- "archive" — mark read and remove from inbox
- "delete" — trash the email
- "reply" — send a reply; put the reply text in "content"
- "forward" — forward this email to someone else; put their email/name in "to", put the forwarding note in "content"
- "task" — create a todo; put task text in "content"
- "skip" — leave in inbox for later
- "answer" — just answer the question, no email action; put answer in "message"

Pick the action that best matches the user's intent. For reply/task, write the content concisely. For answer, be brief.

EMAIL:
From: {email['from']}
Subject: {email['subject']}
Date: {email['date']}

{email['body']}

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

# ── Display ──────────────────────────────────────────────────────────────────

def display_card(email, index, total):
    console.print()
    header = Text()
    header.append(f"FROM:  ", style="bold dim")
    header.append(email["from"], style="bold cyan")
    acct = email.get("_account", "")
    if acct:
        header.append(f"  @{acct}", style="dim magenta")
    header.append(f"\nSUBJ:  ", style="bold dim")
    header.append(email["subject"], style="bold white")
    header.append(f"\nDATE:  ", style="bold dim")
    header.append(email["date"], style="dim")

    body_text = email["body"] if email["body"] else "(no body)"

    console.print(Panel(
        header,
        box=box.SIMPLE_HEAD,
        border_style="dim",
        padding=(0, 1),
    ))
    console.print(Panel(
        body_text,
        box=box.SIMPLE,
        border_style="dim",
        padding=(0, 1),
    ))

def print_help():
    console.print(
        "\n[dim]Commands:[/dim]  "
        "[bold]a[/bold] archive  "
        "[bold]d[/bold] delete  "
        "[bold]s[/bold] skip  "
        "[bold]r <text>[/bold] reply  "
        "[bold]t <text>[/bold] todo  "
        "[bold]c <text>[/bold] claude TUI  "
        "[bold]q[/bold] quit  "
        "[dim]or type anything → Claude[/dim]\n"
    )

# ── Main Loop ────────────────────────────────────────────────────────────────

def main():
    console.print("[bold]ibx[/bold] — connecting to Gmail...", style="dim")

    # Connect to all accounts
    services = {}
    for acct in ACCOUNTS:
        try:
            svc = get_gmail_service(acct["tokens"], acct["creds"])
            services[acct["name"]] = svc
            _gmail_services[acct["name"]] = svc
            console.print(f"  [dim]✓ {acct['name']}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]✗ {acct['name']}: {e}[/yellow]")

    if not services:
        console.print("[red]No accounts connected.[/red]")
        sys.exit(1)

    import time

    POLL_INTERVAL = 60  # seconds

    while True:
        # Triage: classify inbox emails and move info-only ones
        console.print("\n[bold]Triaging inbox...[/bold]")
        for name, svc in services.items():
            total, moved = triage_inbox(svc, name)
            if total:
                console.print(f"  [dim]{name}: {moved}/{total} → {TRIAGE_LABEL}[/dim]")

        # Fetch remaining inbox messages (post-triage)
        all_messages = []
        for name, svc in services.items():
            msgs = fetch_inbox(svc, unread_only=False)
            console.print(f"  [dim]{name}: {len(msgs)} remaining in inbox[/dim]")
            for m in msgs:
                all_messages.append({"id": m["id"], "account": name, "service": svc})

        if not all_messages:
            console.print(f"[dim]Inbox zero. Checking again in {POLL_INTERVAL}s... (q to quit)[/dim]")
            import select
            # Wait for POLL_INTERVAL but allow 'q' to quit
            try:
                ready, _, _ = select.select([sys.stdin], [], [], POLL_INTERVAL)
                if ready:
                    line = sys.stdin.readline().strip().lower()
                    if line == "q":
                        console.print("[dim]Bye.[/dim]")
                        return
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Bye.[/dim]")
                return
            continue

        console.print(f"[dim]{len(all_messages)} emails need review[/dim]")
        print_help()

        messages = all_messages
        total = len(messages)
        index = 0
        skipped = []

        while True:
            if index >= len(messages):
                if skipped:
                    console.print(f"\n[dim]Cycling back through {len(skipped)} skipped...[/dim]")
                    messages = skipped
                    skipped = []
                    index = 0
                else:
                    break

            msg_ref = messages[index]
            service = msg_ref["service"]
            try:
                email = get_email(service, msg_ref["id"])
                email["_account"] = msg_ref["account"]
            except Exception as e:
                console.print(f"[red]Failed to fetch email:[/red] {e}")
                index += 1
                continue

            display_card(email, index + 1, total)
            console.print(f"[dim][{index + 1}/{total}][/dim] ", end="")

            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Bye.[/dim]")
                return

            if not user_input:
                index += 1
                continue

            cmd = user_input.lower()

            if cmd == "q":
                console.print("[dim]Bye.[/dim]")
                return

            elif cmd == "a":
                archive(service, email["id"])
                console.print("[green]Archived.[/green]")
                index += 1

            elif cmd == "d":
                delete(service, email["id"])
                console.print("[red]Deleted.[/red]")
                index += 1

            elif cmd == "s":
                skipped.append(msg_ref)
                console.print("[dim]Skipped.[/dim]")
                index += 1

            elif cmd == "n":
                mark_read(service, email["id"])
                console.print("[dim]Marked read.[/dim]")
                index += 1

            elif cmd.startswith("r "):
                instruction = user_input[2:].strip()
                console.print("[dim]Drafting reply...[/dim]")
                result = ask_claude(email, f"Write a reply: {instruction}")
                draft = result.get("content") or result.get("message", "")
                console.print(Panel(draft, title="Reply Draft", border_style="cyan"))
                console.print("[bold cyan]Send? (y/n)[/bold cyan]")
                confirm = input("> ").strip().lower()
                if confirm == "y":
                    send_reply(service, email, draft)
                    archive(service, email["id"])
                    console.print("[green]Sent + archived.[/green]")
                    index += 1
                else:
                    console.print("[dim]Cancelled.[/dim]")

            elif cmd.startswith("t "):
                task_hint = user_input[2:].strip()
                console.print(f"[green]Todo:[/green] {task_hint}")
                # TODO: wire to Todoist MCP or todoist API
                console.print("[dim](Todoist integration coming — copied to clipboard for now)[/dim]")
                subprocess.run(["pbcopy"], input=task_hint.encode())
                mark_read(service, email["id"])
                index += 1

            elif cmd == "?":
                print_help()

            elif cmd.startswith("c ") or cmd == "c":
                instruction = user_input[2:].strip() if len(user_input) > 1 else ""
                console.print("[dim]Opening Claude TUI...[/dim]")
                spawn_claude_tui(email, instruction)
                console.print()
                display_card(email, index + 1, len(messages))
                post = input("[a]rchive / [s]kip / [d]elete? ").strip().lower()
                if post == "a":
                    archive(service, email["id"])
                    console.print("[green]Archived.[/green]")
                    index += 1
                elif post == "d":
                    delete(service, email["id"])
                    console.print("[red]Deleted.[/red]")
                    index += 1
                else:
                    index += 1

            else:
                # Natural language → Claude → propose action → confirm
                console.print("[dim]...[/dim]")
                result = ask_claude(email, user_input)
                action = result.get("action", "answer")
                message = result.get("message", "")
                content = result.get("content", "")

                if action == "answer":
                    console.print(f"\n{message}")

                elif action == "reply":
                    console.print(Panel(content, title="Proposed Reply", border_style="cyan"))
                    console.print(f"[dim]{message}[/dim]")
                    console.print("[bold cyan](y)es / (e)dit / (n)o[/bold cyan]")
                    confirm = input("> ").strip().lower()
                    if confirm.startswith("s") or confirm.startswith("y"):
                        send_reply(service, email, content)
                        archive(service, email["id"])
                        console.print("[green]Sent + archived.[/green]")
                        index += 1
                    elif confirm.startswith("e"):
                        new_text = input("Edit reply: ").strip()
                        if new_text:
                            console.print(Panel(new_text, title="Edited Reply", border_style="cyan"))
                            console.print("[bold cyan]Send? (y/n)[/bold cyan]")
                            confirm2 = input("> ").strip().lower()
                            if confirm2 == "y":
                                send_reply(service, email, new_text)
                                archive(service, email["id"])
                                console.print("[green]Sent + archived.[/green]")
                                index += 1
                            else:
                                console.print("[dim]Cancelled.[/dim]")
                    else:
                        console.print("[dim]Cancelled.[/dim]")

                elif action == "forward":
                    to = result.get("to", "")
                    # Claude may return a name without an email — resolve via Contacts first
                    if "@" not in to:
                        looked_up = lookup_contact_email(to)
                        if looked_up:
                            console.print(f"[dim]Resolved '{to}' → {looked_up}[/dim]")
                            to = looked_up
                        else:
                            console.print(f"[yellow]Could not find '{to}' in Contacts.[/yellow]")
                            to = input("Enter email address to forward to: ").strip()
                            if not to or "@" not in to:
                                console.print("[dim]Cancelled.[/dim]")
                                continue
                    console.print(Panel(content, title=f"Forward to {to}", border_style="yellow"))
                    console.print(f"[dim]{message}[/dim]")
                    console.print("[bold cyan](y)es / (e)dit / (n)o[/bold cyan]")
                    confirm = input("> ").strip().lower()
                    if confirm.startswith("s") or confirm.startswith("y"):
                        forward_email(service, email, to, content)
                        archive(service, email["id"])
                        console.print(f"[green]Forwarded to {to} + archived.[/green]")
                        index += 1
                    elif confirm.startswith("e"):
                        new_text = input("Edit forwarding note: ").strip()
                        if new_text:
                            forward_email(service, email, to, new_text)
                            archive(service, email["id"])
                            console.print(f"[green]Forwarded to {to} + archived.[/green]")
                            index += 1
                        else:
                            console.print("[dim]Cancelled.[/dim]")
                    else:
                        console.print("[dim]Cancelled.[/dim]")

                else:
                    # archive, delete, skip, task — show proposal and confirm
                    console.print(f"\n[bold]Proposed:[/bold] {action}")
                    if message:
                        console.print(f"[dim]{message}[/dim]")
                    if content:
                        console.print(f"  {content}")
                    console.print("[bold cyan]OK? (y/n)[/bold cyan]")
                    confirm = input("> ").strip().lower()
                    if confirm == "y":
                        if action == "archive":
                            archive(service, email["id"])
                            console.print("[green]Archived.[/green]")
                            index += 1
                        elif action == "delete":
                            delete(service, email["id"])
                            console.print("[red]Deleted.[/red]")
                            index += 1
                        elif action == "skip":
                            skipped.append(msg_ref)
                            console.print("[dim]Skipped.[/dim]")
                            index += 1
                        elif action == "task":
                            task_text = content or message
                            console.print(f"[green]Todo:[/green] {task_text}")
                            subprocess.run(["pbcopy"], input=task_text.encode())
                            console.print("[dim](copied to clipboard)[/dim]")
                            mark_read(service, email["id"])
                            index += 1
                    else:
                        console.print("[dim]Cancelled.[/dim]")

        # Batch done — loop back to check for new emails
        console.print(f"\n[dim]Batch done. Checking for new emails...[/dim]")

if __name__ == "__main__":
    main()
