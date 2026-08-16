#!/usr/bin/env python3
"""lease_signerd — Autonomous lease countersigning daemon.

Polls Gmail every POLL_INTERVAL seconds for AppFolio countersign emails
from approved senders, signs them via Playwright, archives, and logs.

Runs on Ix via launchd (com.jm.lease-signerd).

Usage:
    python3 lease_signerd.py           # run forever (daemon mode)
    python3 lease_signerd.py --once    # single poll then exit (testing)
"""
from __future__ import annotations

import base64
import datetime
import email.mime.text
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

# Add ibx tools to path for Gmail auth, lib/ for the shared Todoist client
_IBX_DIR = Path(__file__).parent.parent / "ibx"
_LIB_DIR = Path(__file__).parent.parent.parent / "lib"
sys.path.insert(0, str(_IBX_DIR))
sys.path.insert(0, str(_LIB_DIR))
sys.path.insert(0, str(Path(__file__).parent))

import ibx as _ibx
import lease_signer as _signer
import automations_db as _autodb
import todoist as _todoist
from config import AUTOSIGN_SENDERS, DB_PATH

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL = int(os.environ.get("LEASE_SIGNERD_POLL", "300"))  # 5 min default
NOTIFY_TO = "mckay@m5c7.com"

# File-based notification counter: write a number to this file to get notified
# for that many upcoming successful signings. Decremented after each notification.
_NOTIFY_REMAINING_PATH = Path.home() / ".config/m5x2/lease_notify_remaining"

# Throttle state: written when we've alerted the operator that the 2FA
# session expired; cleared on the next successful sign. Holds {"alerted_at":
# iso timestamp, "todoist_task_id": id} so a still-broken daemon re-alerts
# (email + a fresh Todoist task) once per _AUTH_ALERT_INTERVAL rather than
# going silent until the next success — a single throttled-forever email is
# what let this sit broken and unnoticed for a month (2026-07-07→2026-08-06).
_AUTH_ALERT_PATH = Path.home() / ".config/m5x2/lease_auth_alert_sent"
_AUTH_ALERT_INTERVAL = datetime.timedelta(hours=24)

# Gmail label applied to emails that failed for non-auth reasons (so they're
# flagged for manual review instead of archived into oblivion or retried forever).
_REVIEW_LABEL = "lease-signer/needs-review"


def _notify_remaining() -> int:
    """Read how many notifications are still requested."""
    try:
        return int(_NOTIFY_REMAINING_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _decrement_notify():
    """Decrement the remaining notification counter."""
    n = _notify_remaining()
    if n > 0:
        _NOTIFY_REMAINING_PATH.write_text(str(n - 1))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lease_signerd")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info(f"Received signal {signum}, shutting down...")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------


def get_service():
    """Get authenticated Gmail service (reuses ibx OAuth tokens)."""
    return _ibx.get_gmail_service()


def fetch_unread(service) -> list[dict]:
    """Fetch unread inbox messages from the autosign senders specifically.

    Deliberately NOT _ibx.fetch_inbox(unread_only=True): that pulls only the
    50 most-recent unread inbox messages *before* filtering by sender, so any
    autosign email buried under other unread mail (or older than the top 50)
    was permanently invisible to the daemon no matter how many poll cycles
    ran — the actual reason a backlog of countersign requests going back to
    at least 2026-07-24 sat unprocessed even before the 2026-08-06 crash-loop
    bug (found/fixed 2026-08-16). Querying Gmail directly, scoped to the
    autosign senders, reaches the full backlog regardless of general inbox
    clutter, and paginates so no cap silently drops old messages.
    """
    sender_query = " OR ".join(f"from:{s}" for s in AUTOSIGN_SENDERS)
    query = f"in:inbox is:unread ({sender_query})"
    messages: list[dict] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token,
        ).execute()
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return messages


def normalize(msg, service) -> dict | None:
    """Normalize a Gmail message into the item format lease_signer expects."""
    try:
        eml = _ibx.get_email(service, msg["id"])
    except Exception as e:
        log.warning(f"Failed to fetch email {msg.get('id')}: {e}")
        return None
    return {
        "type": "email",
        "from": eml.get("from", ""),
        "to": eml.get("to", ""),
        "preview": eml.get("subject", "(no subject)"),
        "body": eml.get("body", ""),
        "_data": {"email": eml, "service": service},
    }


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


def _flag_for_review(service, msg_id: str):
    """Label a content-failed email and drop it from the unread queue (but keep
    it in the inbox — never archive a failure). Stops infinite re-attempts
    without losing the lease."""
    label_id = _ibx.get_or_create_label(service, _REVIEW_LABEL)
    service.users().messages().modify(
        userId="me", id=msg_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
    ).execute()


def _read_auth_alert_state() -> dict | None:
    if not _AUTH_ALERT_PATH.exists():
        return None
    try:
        return json.loads(_AUTH_ALERT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None  # corrupt/legacy ("sent") flag — treat as no prior alert


def maybe_send_auth_alert(service):
    """Alert the operator when the 2FA session expires: email + a Todoist
    task, re-firing once per _AUTH_ALERT_INTERVAL while still broken (not
    just once-ever) so a multi-day outage can't go silent like 2026-07-07's
    did. The Todoist task is the durable nag — closed automatically on the
    next successful sign (see process_email)."""
    state = _read_auth_alert_state()
    if state:
        alerted_at = datetime.datetime.fromisoformat(state["alerted_at"])
        if datetime.datetime.now() - alerted_at < _AUTH_ALERT_INTERVAL:
            return

    try:
        body = (
            "The lease auto-signer's AppFolio 2FA session has expired.\n"
            "Countersign requests are NOT being signed; they're being held\n"
            "(left unread in the inbox) until you re-authenticate.\n\n"
            "Fix — on the Mac running the daemon:\n"
            "    cd ~/i446-monorepo/tools/m5x2-automations\n"
            "    python3 lease_signer.py --login\n"
            "Then complete 2FA in the browser window that opens.\n"
        )
        msg = email.mime.text.MIMEText(body)
        msg["To"] = NOTIFY_TO
        msg["From"] = NOTIFY_TO
        msg["Subject"] = "⚠ Lease auto-signer paused — 2FA re-login needed"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info("Sent 2FA-expired alert email to operator.")
    except Exception as e:
        log.warning(f"Failed to send auth alert email: {e}")

    task_id = (state or {}).get("todoist_task_id")
    try:
        task = _todoist.create_task(
            "\U0001F513 Lease auto-signer needs 2FA re-login — leases piling up unsigned "
            "(cd ~/i446-monorepo/tools/m5x2-automations && python3 lease_signer.py --login) [10]",
            labels=["m5x2"], due_string="today", priority=4,
        )
        task_id = task.get("id", task_id)
        log.info(f"Created/refreshed Todoist reminder {task_id} for 2FA re-login.")
    except Exception as e:
        log.warning(f"Failed to create Todoist auth alert task: {e}")

    try:
        _AUTH_ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AUTH_ALERT_PATH.write_text(json.dumps({
            "alerted_at": datetime.datetime.now().isoformat(),
            "todoist_task_id": task_id,
        }))
    except OSError as e:
        log.warning(f"Failed to persist auth alert state: {e}")


def _clear_auth_alert():
    """On a successful sign: close any open reminder task and clear state."""
    state = _read_auth_alert_state()
    if state and state.get("todoist_task_id"):
        try:
            _todoist.close_task(state["todoist_task_id"])
            log.info(f"Closed Todoist reminder {state['todoist_task_id']} — daemon healthy again.")
        except Exception as e:
            log.warning(f"Failed to close Todoist auth alert task: {e}")
    _AUTH_ALERT_PATH.unlink(missing_ok=True)


def send_notification(service, item: dict, meta: dict, result: dict, count: int):
    """Email mckay@m5c7.com about a successful signing."""
    try:
        unit = meta.get("unit", "unknown unit")
        tenants = meta.get("tenants", "")
        ltype = meta.get("lease_type", "renewal")
        status = result.get("status", "unknown")
        remaining = _notify_remaining()
        body = (
            f"Auto-sign #{count} completed (daemon).\n\n"
            f"Unit:    {unit}\n"
            f"Tenants: {tenants}\n"
            f"Type:    {ltype}\n"
            f"Status:  {status}\n"
            f"From:    {item.get('from', '')}\n"
            f"Subject: {item.get('preview', '')}\n\n"
            f"({remaining} notification(s) remaining.)"
        )
        msg = email.mime.text.MIMEText(body)
        msg["To"] = NOTIFY_TO
        msg["From"] = NOTIFY_TO
        msg["Subject"] = f"\u2713 Auto-signed lease: {unit}"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        log.warning(f"Notification email failed: {e}")


# ---------------------------------------------------------------------------
# Core signing logic
# ---------------------------------------------------------------------------


def process_email(service, item: dict) -> str:
    """Sign a single countersign email.

    Returns one of: "success", "auth_failed", "failed", "skipped".
    Archives ONLY on success. Auth failures are left fully untouched (unread,
    in inbox) so they retry after re-login; content failures are labeled for
    review and dropped from the unread queue. A failure is never archived —
    that was the bug that silently lost ~3 weeks of leases.
    """
    url = _signer.extract_appfolio_url(item.get("body", ""))
    if not url:
        html_body = item.get("_data", {}).get("email", {}).get("html_body", "")
        url = _signer.extract_appfolio_url(html_body)
    if not url:
        log.warning(f"No AppFolio URL in email from {item.get('from', '?')}")
        return "skipped"

    meta = _signer.parse_email_metadata(item)
    log.info(f"Signing: {meta.get('unit', url[:60])}")

    try:
        result = _signer.sign_lease(url, headless=True)
        status = result.get("status", "failed")
        error = result.get("error", "")
    except Exception as exc:
        result = {}
        status = "failed"
        error = str(exc)
        log.error(f"Exception during signing: {exc}")

    # Never let a Sheets-logging hiccup crash the daemon or block archiving —
    # this exact failure mode (missing ~/.config/m5x2/sheets_token.json on Ix)
    # silently broke the daemon for 10 days (2026-08-06→08-16): every poll
    # crashed on log_signing() before reaching the archive-on-success step
    # below, so successful (and failed) signings never got archived and the
    # backlog piled up unread in the inbox.
    try:
        _autodb.log_signing(
            DB_PATH,
            property=meta.get("property", ""),
            unit=meta.get("unit", ""),
            tenants=meta.get("tenants", ""),
            lease_type=meta.get("lease_type", "renewal"),
            source_sender=item.get("from", ""),
            source_subject=item.get("preview", ""),
            appfolio_url=url,
            status=status,
        )
    except Exception as e:
        log.error(f"Failed to log signing to Sheets (continuing anyway): {e}")

    email_id = item.get("_data", {}).get("email", {}).get("id", "")

    if status == "success":
        log.info(f"Signed successfully: {meta.get('unit', '')}")
        # We're healthy again — clear any prior auth-expired alert.
        _clear_auth_alert()
        remaining = _notify_remaining()
        if remaining > 0:
            count = _autodb.count_successful(DB_PATH)
            send_notification(service, item, meta, result, count)
            _decrement_notify()
            log.info(f"Notification sent ({remaining - 1} remaining)")
        # Archive ONLY on success.
        try:
            _ibx.archive(service, email_id)
            log.info(f"Archived email {email_id}")
        except Exception as e:
            log.warning(f"Failed to archive: {e}")
        return "success"

    # --- Failure: never archive. ---
    if "2FA" in error or "no browser session" in error.lower():
        # Transient auth/session failure. Leave the email fully untouched
        # (unread, in inbox) so it retries automatically after re-login.
        log.warning(f"Auth/session failure — holding {meta.get('unit', email_id)} for retry: {error}")
        return "auth_failed"

    # Content failure (no sign button, no confirmation, etc.): flag for manual
    # review and remove from the unread queue so we don't reattempt forever —
    # but keep it in the inbox (do NOT archive).
    log.warning(f"Signing failed ({status}) for {meta.get('unit', email_id)}: {error}")
    try:
        if email_id:
            _flag_for_review(service, email_id)
            log.info(f"Flagged email {email_id} for manual review ({_REVIEW_LABEL})")
    except Exception as e:
        log.warning(f"Failed to flag {email_id} for review: {e}")
    return "failed"


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------


def poll_once(service) -> int:
    """Run one poll cycle. Returns number of emails processed."""
    try:
        messages = fetch_unread(service)
    except Exception as e:
        log.error(f"Gmail fetch failed: {e}")
        return 0

    processed = 0
    for msg in messages:
        item = normalize(msg, service)
        if not item:
            continue
        if not _signer.is_autosign_email(item, AUTOSIGN_SENDERS):
            continue
        try:
            status = process_email(service, item)
        except Exception as e:
            # An unexpected exception here must never kill the whole poll
            # cycle — that's what turned one bad email into a 10-day, ~7000-
            # restart crash loop stuck on a single message while the rest of
            # the backlog piled up unread (2026-08-06→08-16). Log it and move
            # on to the next message; this one stays unread for manual review.
            log.error(f"Unexpected error processing email "
                      f"{item.get('_data', {}).get('email', {}).get('id', '?')}: {e}")
            continue
        processed += 1
        if status == "auth_failed":
            # Every remaining email would fail identically. Stop this cycle so
            # we don't launch a browser per email, alert the operator once, and
            # leave all pending leases unread for retry after re-login.
            maybe_send_auth_alert(service)
            log.warning("2FA session expired — pausing cycle until re-login "
                        "(python3 lease_signer.py --login). Pending leases held unread.")
            break

    return processed


def main():
    once = "--once" in sys.argv

    log.info(f"lease_signerd starting (poll_interval={POLL_INTERVAL}s, once={once})")

    try:
        service = get_service()
    except Exception as e:
        log.error(f"Gmail auth failed: {e}")
        sys.exit(1)

    log.info("Gmail authenticated. Entering poll loop.")

    while not _shutdown:
        n = poll_once(service)
        if n:
            log.info(f"Processed {n} email(s) this cycle")

        if once:
            break

        # Sleep in small increments for responsive shutdown
        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    log.info("lease_signerd stopped.")


if __name__ == "__main__":
    main()
