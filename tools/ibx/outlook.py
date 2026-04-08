#!/usr/bin/env python3
"""
outlook — Outlook Email as Cards
Process Outlook inbox via Microsoft Graph API. Mirrors ibx.py interface.
"""

import json
import os
import re
import sys
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

import msal
import requests

from rich.console import Console

# ── Config ──────────────────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "Mail.Send"]
CONFIG_DIR = Path.home() / ".config" / "outlook"
TOKEN_CACHE_FILE = CONFIG_DIR / "token_cache.json"
APP_CONFIG_FILE = CONFIG_DIR / "app.json"
# app.json format: {"client_id": "...", "tenant_id": "..."}
# tenant_id: use "common" for multi-tenant, or your org's tenant ID

console = Console()

# ── Auth ─────────────────────────────────────────────────────────────────────

def _load_app_config():
    """Load Azure AD app registration config."""
    if not APP_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Outlook app config not found at {APP_CONFIG_FILE}\n"
            f"Create it with: {{\"client_id\": \"<your-app-id>\", \"tenant_id\": \"common\"}}\n"
            f"Register an app at https://portal.azure.com → App registrations → New registration\n"
            f"  • Redirect URI: select 'Mobile and desktop applications' → https://login.microsoftonline.com/common/oauth2/nativeclient\n"
            f"  • API permissions: Microsoft Graph → Delegated → Mail.ReadWrite, Mail.Send"
        )
    with open(APP_CONFIG_FILE) as f:
        return json.load(f)


def _build_msal_app(config):
    """Build MSAL PublicClientApplication with persistent token cache."""
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_FILE.exists():
        cache.deserialize(TOKEN_CACHE_FILE.read_text())

    app = msal.PublicClientApplication(
        config["client_id"],
        authority=f"https://login.microsoftonline.com/{config.get('tenant_id', 'common')}",
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache):
    """Persist MSAL token cache to disk."""
    if cache.has_state_changed:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE_FILE.write_text(cache.serialize())


def get_graph_token():
    """Acquire a valid access token, using cache or device code flow."""
    config = _load_app_config()
    app, cache = _build_msal_app(config)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # Device code flow — user opens browser
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow.get('error_description', flow)}")

    console.print(f"\n[bold yellow]Outlook sign-in required[/bold yellow]")
    console.print(f"  Open: [cyan]{flow['verification_uri']}[/cyan]")
    console.print(f"  Code: [bold]{flow['user_code']}[/bold]\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    _save_cache(cache)
    return result["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Email Fetching ───────────────────────────────────────────────────────────

def fetch_inbox(token, max_results=50, unread_only=False):
    """Fetch inbox messages. Returns list of message dicts."""
    url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
    params = {
        "$top": max_results,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,isRead,conversationId",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"

    resp = requests.get(url, headers=_headers(token), params=params)
    resp.raise_for_status()
    return resp.json().get("value", [])


def get_email(token, msg_id):
    """Fetch full message by ID."""
    url = f"{GRAPH_BASE}/me/messages/{msg_id}"
    params = {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,conversationId,internetMessageId",
    }
    resp = requests.get(url, headers=_headers(token), params=params)
    resp.raise_for_status()
    msg = resp.json()

    from_addr = msg.get("from", {}).get("emailAddress", {})
    from_str = f"{from_addr.get('name', '')} <{from_addr.get('address', '')}>"

    to_list = msg.get("toRecipients", [])
    to_str = ", ".join(
        f"{r['emailAddress'].get('name', '')} <{r['emailAddress']['address']}>"
        for r in to_list
    )
    cc_list = msg.get("ccRecipients", [])
    cc_str = ", ".join(
        f"{r['emailAddress'].get('name', '')} <{r['emailAddress']['address']}>"
        for r in cc_list
    )

    # Parse date
    try:
        dt = datetime.fromisoformat(msg["receivedDateTime"].replace("Z", "+00:00"))
        date = dt.strftime("%b %d, %I:%M%p").lower()
    except Exception:
        date = msg.get("receivedDateTime", "")

    body_content = msg.get("body", {}).get("content", "")
    body_type = msg.get("body", {}).get("contentType", "text")
    if body_type.lower() == "html":
        body_content = _html_to_text(body_content)

    # Trim quoted reply chains
    lines = body_content.splitlines()
    trimmed = []
    for line in lines:
        if re.match(r"^On .+ wrote:$", line.strip()) or line.strip().startswith(">"):
            break
        # Outlook-style separator
        if re.match(r"^_{10,}$", line.strip()) or re.match(r"^-{10,}$", line.strip()):
            break
        if "From:" in line and "Sent:" in line:
            break
        trimmed.append(line)
    body_content = "\n".join(trimmed).strip()

    return {
        "id": msg_id,
        "internet_message_id": msg.get("internetMessageId", ""),
        "subject": msg.get("subject", "(no subject)"),
        "from": from_str,
        "to": to_str,
        "cc": cc_str,
        "date": date,
        "body": body_content[:3000],
        "conversation_id": msg.get("conversationId", ""),
    }


def _html_to_text(html_str):
    """Simplified HTML → plain text (mirrors ibx.py)."""
    import html as html_mod
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_str, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|tr|li|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [l.strip() for l in text.splitlines()]
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

def archive(token, msg_id):
    """Move message out of inbox (move to Archive folder)."""
    # Graph API: move to archive. If Archive doesn't exist, move to deleteditems
    # and we try the "archive" well-known folder first.
    url = f"{GRAPH_BASE}/me/messages/{msg_id}/move"
    resp = requests.post(url, headers=_headers(token),
                         json={"destinationId": "archive"})
    if resp.status_code >= 400:
        # Fallback: just mark read
        mark_read(token, msg_id)


def delete(token, msg_id):
    """Move message to trash."""
    url = f"{GRAPH_BASE}/me/messages/{msg_id}/move"
    requests.post(url, headers=_headers(token),
                  json={"destinationId": "deleteditems"})


def mark_read(token, msg_id):
    """Mark message as read."""
    url = f"{GRAPH_BASE}/me/messages/{msg_id}"
    requests.patch(url, headers=_headers(token), json={"isRead": True})


def send_reply(token, msg_id, body_text):
    """Reply to a message."""
    url = f"{GRAPH_BASE}/me/messages/{msg_id}/reply"
    resp = requests.post(url, headers=_headers(token), json={
        "comment": body_text,
    })
    resp.raise_for_status()


def forward_email(token, msg_id, to_addr, note_text):
    """Forward a message."""
    url = f"{GRAPH_BASE}/me/messages/{msg_id}/forward"
    resp = requests.post(url, headers=_headers(token), json={
        "comment": note_text,
        "toRecipients": [{"emailAddress": {"address": to_addr}}],
    })
    resp.raise_for_status()


# ── Triage ───────────────────────────────────────────────────────────────────

TRIAGE_FOLDER = "ai-no-response-needed"

def _get_or_create_folder(token, folder_name):
    """Get a mail folder by name, creating under inbox if needed."""
    url = f"{GRAPH_BASE}/me/mailFolders"
    resp = requests.get(url, headers=_headers(token),
                        params={"$filter": f"displayName eq '{folder_name}'"})
    folders = resp.json().get("value", [])
    if folders:
        return folders[0]["id"]
    # Create it
    resp = requests.post(url, headers=_headers(token),
                         json={"displayName": folder_name})
    resp.raise_for_status()
    return resp.json()["id"]


def triage_inbox(token):
    """Pre-process: move info-only unread emails to triage folder. Returns (total, moved)."""
    import anthropic
    ai = anthropic.Anthropic()

    messages = fetch_inbox(token, max_results=200, unread_only=True)
    if not messages:
        return 0, 0

    folder_id = _get_or_create_folder(token, TRIAGE_FOLDER)
    moved = 0

    for msg_summary in messages:
        try:
            email = get_email(token, msg_summary["id"])
            # Classify
            prompt = f"""Classify this email: does it require a response or action from the recipient, or is it purely informational?

Reply with ONLY "info" or "response".

- "info" = newsletters, marketing, automated status updates with no action needed, shipping tracking, social media notifications, calendar invites already accepted
- "response" = someone asking a question, requesting action, scheduling, needs a decision, lease/legal notices, personal messages, anything that needs to be reviewed

When in doubt, prefer "response".

EMAIL:
From: {email['from']}
Subject: {email['subject']}
Date: {email['date']}

{email['body'][:1000]}"""

            resp = ai.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text.strip().lower()
            is_info = "info" in answer and "response" not in answer

            subj = email["subject"][:60]
            if is_info:
                move_url = f"{GRAPH_BASE}/me/messages/{msg_summary['id']}/move"
                requests.post(move_url, headers=_headers(token),
                              json={"destinationId": folder_id})
                moved += 1
                console.print(f"  [dim]→ nrn:[/dim] {subj}")
            else:
                console.print(f"  [dim]→ keep:[/dim] {subj}")
        except Exception as e:
            console.print(f"  [yellow]triage error: {e}[/yellow]")

    return len(messages), moved
