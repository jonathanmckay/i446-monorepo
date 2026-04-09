#!/usr/bin/env python3
"""
slack — Slack DMs as Cards CLI
Process unread Slack DMs one at a time, across multiple workspaces.
"""

import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

import anthropic
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# ── Config ─────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".config" / "slack" / "tokens.json"

console = Console()
ai = anthropic.Anthropic()

# ── Slack API ─────────────────────────────────────────────────────────────────

def slack_get(token, method, **params):
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('error', 'unknown')}")
    return data

def slack_post(token, method, **payload):
    url = f"https://slack.com/api/{method}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('error', 'unknown')}")
    return data

# ── Users ─────────────────────────────────────────────────────────────────────

_user_cache: dict[str, str] = {}

def get_username(token, user_id):
    if not user_id:
        return "unknown"
    if user_id in _user_cache:
        return _user_cache[user_id]
    try:
        info = slack_get(token, "users.info", user=user_id)
        u = info["user"]
        name = (u.get("real_name") or
                u.get("profile", {}).get("display_name") or
                u["name"])
        _user_cache[user_id] = name
    except Exception:
        _user_cache[user_id] = user_id
    return _user_cache[user_id]

def get_self_id(token):
    return slack_get(token, "auth.test").get("user_id", "")

# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch_recent_channels(token, days=7):
    """Return DM/MPIM channels with activity in the last N days."""
    import time
    cutoff = time.time() - days * 86400
    result = []
    cursor = None
    while True:
        kwargs = {"types": "im,mpim", "exclude_archived": "true", "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        data = slack_get(token, "conversations.list", **kwargs)
        for ch in data.get("channels", []):
            if ch.get("is_archived"):
                continue
            if ch.get("is_user_deleted"):
                continue
            updated = ch.get("updated", 0) / 1000
            if updated > cutoff:
                result.append(ch)
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return result

def build_thread(token, channel, self_id):
    """Build a display thread from a Slack channel. Returns None if no unread messages."""
    # Check read state — skip channels where all messages have been read
    try:
        info = slack_get(token, "conversations.info", channel=channel["id"])
        ch_info = info.get("channel", {})
        last_read = ch_info.get("last_read", "0")
        if int(ch_info.get("unread_count", 0)) == 0 and last_read != "0":
            return None
    except Exception:
        pass  # If info fails, fall through and show the thread

    msgs_data = slack_get(token, "conversations.history", channel=channel["id"], limit=15)
    msgs = msgs_data.get("messages", [])
    if not msgs:
        return None

    # Resolve display name
    if channel.get("is_im"):
        display = get_username(token, channel.get("user", ""))
    else:
        # MPIM: list members excluding self
        try:
            members_data = slack_get(token, "conversations.members", channel=channel["id"])
            members = [get_username(token, uid) for uid in members_data.get("members", [])
                       if uid != self_id]
            display = ", ".join(members[:3]) or channel.get("name", channel["id"])
        except Exception:
            display = channel.get("name", channel["id"])

    # Format messages oldest→newest (API returns newest first)
    formatted = []
    for m in reversed(msgs[:10]):
        if m.get("type") != "message":
            continue
        subtype = m.get("subtype", "")
        if subtype in ("bot_message", "channel_join", "channel_leave"):
            continue
        sender = "me" if m.get("user") == self_id else get_username(token, m.get("user", ""))
        ts = float(m.get("ts", 0))
        time_str = datetime.fromtimestamp(ts).strftime("%b %d %I:%M%p").lower().lstrip("0")
        text = m.get("text", "").strip() or "[no text]"
        # Expand user mentions like <@U123> → @name
        def expand_mention(match):
            uid = match.group(1)
            return "@" + get_username(token, uid)
        import re
        text = re.sub(r"<@(U[A-Z0-9]+)>", expand_mention, text)
        formatted.append({"sender": sender, "time": time_str, "text": text, "ts": m.get("ts", "0")})

    if not formatted:
        return None

    return {
        "channel_id": channel["id"],
        "display_name": display,
        "is_im": channel.get("is_im", False),
        "messages": formatted,
        "latest_ts": formatted[-1]["ts"],
        "unread_count": int(channel.get("unread_count", 1)),
    }

# ── Actions ───────────────────────────────────────────────────────────────────

def mark_read(token, channel_id, ts):
    try:
        slack_post(token, "conversations.mark", channel=channel_id, ts=ts)
    except Exception as e:
        console.print(f"[yellow]mark_read failed: {e}[/yellow]")

def send_reply(token, channel_id, text):
    slack_post(token, "chat.postMessage", channel=channel_id, text=text)

# ── Display ───────────────────────────────────────────────────────────────────

def display_card(thread, idx, total, workspace):
    console.print()
    title = (f"[bold cyan]{thread['display_name']}[/bold cyan]  "
             f"[dim]{workspace}  [{idx}/{total}][/dim]")
    body = Text()
    for m in thread["messages"][-6:]:
        sender_style = "bold green" if m["sender"] == "me" else "bold cyan"
        body.append(f"{m['sender']}", style=sender_style)
        body.append(f"  {m['time']}\n", style="dim")
        body.append(f"{m['text']}\n\n")
    console.print(Panel(body, title=title, border_style="blue", box=box.ROUNDED))

def print_help():
    console.print(
        "\n[dim]Commands:[/dim]  "
        "[bold]a[/bold] mark read  "
        "[bold]r <text>[/bold] reply  "
        "[bold]s[/bold] skip  "
        "[bold]t <text>[/bold] todo  "
        "[bold]q[/bold] quit  "
        "[dim]or type anything → Claude[/dim]\n"
    )

# ── Claude ────────────────────────────────────────────────────────────────────

def ask_claude(thread, user_input):
    msgs_text = "\n".join(
        f"{m['sender']} ({m['time']}): {m['text']}"
        for m in thread["messages"]
    )
    prompt = f"""You are a personal messaging assistant for Jonathan McKay.
Given a Slack thread and a user instruction, respond with JSON only — no prose outside JSON.

Schema:
{{"action": "<action>", "message": "<short explanation>", "content": "<optional reply text>"}}

Actions:
- "reply" — send a reply; put text in "content"
- "mark_read" — mark as read, no reply needed
- "task" — create a todo; put task text in "content"
- "skip" — leave for later
- "answer" — answer the user's question; put answer in "message"

SLACK THREAD with {thread['display_name']}:
{msgs_text}

User instruction: {user_input}"""

    msg = ai.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return {"action": "answer", "message": text}

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CONFIG_FILE.exists():
        console.print(f"[yellow]No Slack tokens configured.[/yellow]\n")
        console.print(f"Create [dim]{CONFIG_FILE}[/dim]:")
        console.print('[dim]  {{"m5x2": "xoxp-...", "github": "xoxp-..."}}[/dim]\n')
        console.print("To get a User OAuth Token:")
        console.print("  1. api.slack.com/apps → Create New App → From Scratch")
        console.print("  2. OAuth & Permissions → User Token Scopes:")
        console.print("     [dim]channels:history, channels:read, channels:write,[/dim]")
        console.print("     [dim]groups:history, groups:read, groups:write,[/dim]")
        console.print("     [dim]im:history, im:read, im:write,[/dim]")
        console.print("     [dim]mpim:history, mpim:read, mpim:write,[/dim]")
        console.print("     [dim]chat:write, users:read[/dim]")
        console.print("  3. Install to Workspace → copy User OAuth Token (xoxp-...)")
        sys.exit(0)

    with open(CONFIG_FILE) as f:
        workspaces = json.load(f)

    if not workspaces:
        console.print("[dim]No workspaces configured in tokens.json.[/dim]")
        sys.exit(0)

    console.print("[bold]slack[/bold] — connecting...", style="dim")

    all_threads = []
    for workspace, token in workspaces.items():
        try:
            self_id = get_self_id(token)
            channels = fetch_recent_channels(token)
            count = 0
            for ch in channels:
                try:
                    thread = build_thread(token, ch, self_id)
                    if thread:
                        all_threads.append({
                            **thread,
                            "token": token,
                            "workspace": workspace,
                            "self_id": self_id,
                        })
                        count += 1
                except Exception as e:
                    console.print(f"  [dim yellow]skipped channel: {e}[/dim yellow]")
            console.print(f"  [dim]✓ {workspace}: {count} recent DMs[/dim]")
        except Exception as e:
            console.print(f"  [yellow]✗ {workspace}: {e}[/yellow]")

    if not all_threads:
        console.print("[dim]Slack inbox zero.[/dim]")
        return

    print_help()
    index = 0
    skipped = []

    while True:
        if index >= len(all_threads):
            if skipped:
                console.print(f"\n[dim]Cycling through {len(skipped)} skipped...[/dim]")
                all_threads = skipped
                skipped = []
                index = 0
            else:
                console.print("[dim]All done.[/dim]")
                break

        thread = all_threads[index]
        display_card(thread, index + 1, len(all_threads), thread["workspace"])
        console.print(f"[dim][{index + 1}/{len(all_threads)}][/dim] ", end="")

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

        elif cmd in ("a", "n"):
            mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
            console.print("[green]Marked read.[/green]")
            index += 1

        elif cmd == "s":
            skipped.append(thread)
            console.print("[dim]Skipped.[/dim]")
            index += 1

        elif cmd == "?":
            print_help()

        elif cmd.startswith("r "):
            reply_text = user_input[2:].strip()
            console.print(Panel(reply_text, title="Reply", border_style="cyan"))
            console.print("[bold cyan]Send? (y/n)[/bold cyan]")
            confirm = input("> ").strip().lower()
            if confirm == "y":
                send_reply(thread["token"], thread["channel_id"], reply_text)
                mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
                console.print("[green]Sent + marked read.[/green]")
                index += 1
            else:
                console.print("[dim]Cancelled.[/dim]")

        elif cmd.startswith("t "):
            task_text = user_input[2:].strip()
            subprocess.run(["pbcopy"], input=task_text.encode())
            console.print(f"[green]Todo:[/green] {task_text} [dim](copied)[/dim]")
            mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
            index += 1

        else:
            # Natural language → Claude
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
                if confirm in ("y", "s"):
                    send_reply(thread["token"], thread["channel_id"], content)
                    mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
                    console.print("[green]Sent + marked read.[/green]")
                    index += 1
                elif confirm == "e":
                    new_text = input("Edit reply: ").strip()
                    if new_text:
                        send_reply(thread["token"], thread["channel_id"], new_text)
                        mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
                        console.print("[green]Sent + marked read.[/green]")
                        index += 1
                    else:
                        console.print("[dim]Cancelled.[/dim]")
                else:
                    console.print("[dim]Cancelled.[/dim]")

            elif action in ("mark_read", "archive"):
                mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
                console.print("[green]Marked read.[/green]")
                index += 1

            elif action == "task":
                task_text = content or message
                subprocess.run(["pbcopy"], input=task_text.encode())
                console.print(f"[green]Todo:[/green] {task_text} [dim](copied)[/dim]")
                mark_read(thread["token"], thread["channel_id"], thread["latest_ts"])
                index += 1

            elif action == "skip":
                skipped.append(thread)
                console.print("[dim]Skipped.[/dim]")
                index += 1

            else:
                console.print(f"\n[bold]Proposed:[/bold] {action}")
                if message:
                    console.print(f"[dim]{message}[/dim]")

if __name__ == "__main__":
    main()
