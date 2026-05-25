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
import email.mime.text
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path

# Add ibx tools to path for Gmail auth
_IBX_DIR = Path(__file__).parent.parent / "ibx"
sys.path.insert(0, str(_IBX_DIR))
sys.path.insert(0, str(Path(__file__).parent))

import ibx as _ibx
import lease_signer as _signer
import automations_db as _autodb
from config import AUTOSIGN_SENDERS, DB_PATH

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL = int(os.environ.get("LEASE_SIGNERD_POLL", "300"))  # 5 min default
NOTIFY_TO = "mckay@m5c7.com"

# File-based notification counter: write a number to this file to get notified
# for that many upcoming successful signings. Decremented after each notification.
_NOTIFY_REMAINING_PATH = Path.home() / ".config/m5x2/lease_notify_remaining"


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
    """Fetch unread inbox messages."""
    return _ibx.fetch_inbox(service, unread_only=True)


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


def process_email(service, item: dict) -> bool:
    """Sign a single countersign email. Returns True on success."""
    url = _signer.extract_appfolio_url(item.get("body", ""))
    if not url:
        html_body = item.get("_data", {}).get("email", {}).get("html_body", "")
        url = _signer.extract_appfolio_url(html_body)
    if not url:
        log.warning(f"No AppFolio URL in email from {item.get('from', '?')}")
        return False

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

    if status == "success":
        log.info(f"Signed successfully: {meta.get('unit', '')}")
        remaining = _notify_remaining()
        if remaining > 0:
            count = _autodb.count_successful(DB_PATH)
            send_notification(service, item, meta, result, count)
            _decrement_notify()
            log.info(f"Notification sent ({remaining - 1} remaining)")
    else:
        log.warning(f"Signing failed ({status}): {error}")

    # Archive only on successful signing — failed/timed-out emails stay in inbox
    if status == "success":
        try:
            email_id = item["_data"]["email"]["id"]
            _ibx.archive(service, email_id)
            log.info(f"Archived email {email_id}")
        except Exception as e:
            log.warning(f"Failed to archive: {e}")
    else:
        log.info(f"Keeping email in inbox (status={status})")

    return status == "success"


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
        process_email(service, item)
        processed += 1

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
