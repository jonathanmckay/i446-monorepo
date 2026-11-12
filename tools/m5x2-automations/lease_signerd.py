#!/usr/bin/env python3
"""lease_signerd — Autonomous lease countersigning daemon.

Polls Gmail every POLL_INTERVAL seconds for AppFolio countersign emails
from approved senders, signs them via Playwright, archives, and logs.

Self-healing (2026-11-12): when a sign hits AppFolio's 2FA wall, or the daily
session probe finds the device trust lapsed, the daemon re-logs-in itself —
relogin.relogin() with the SMS code read out of Ix's Messages over ssh — at
most once an hour (each attempt texts JM a code), and only then falls back to
the email + "do 2fa with Ix" Todoist card. State files in ~/.config/m5x2:
  appfolio_session_ok       {"ts", "source": sign|relogin|probe} — last proof the
                            session works; /1-i446's lease-signer check reads it
  lease_auto_relogin_last   throttle stamp, written BEFORE the SMS is requested
  lease_auth_alert_sent     operator alert state (pre-existing)

Runs on Ix via launchd (com.jm.lease-signerd).

Usage:
    python3 lease_signerd.py           # run forever (daemon mode)
    python3 lease_signerd.py --once    # single poll then exit (testing)
"""
from __future__ import annotations

import base64
import datetime
import email.mime.text
import email.utils
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
import relogin as _relogin
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
# The Todoist reminder spawned on a 2FA/session failure. Named the way the
# task is actually done (2026-09-25 user request): Screen Share into Ix, run
# `python3 lease_signer.py --login` there, complete AppFolio's 2FA in the
# Chromium it opens, press Enter. The [10] is the task's 分 value.
AUTH_ALERT_TASK = "do 2fa with Ix to refresh appfolio cookie [10]"

# Self-healing relogin (2026-11-12). The throttle stamp is written before the
# SMS is requested so a crash mid-relogin + KeepAlive restart can't text JM a
# code every minute. The session_ok file is the health signal /1-i446 reads.
_AUTO_RELOGIN_LAST_PATH = Path.home() / ".config/m5x2/lease_auto_relogin_last"
_AUTO_RELOGIN_INTERVAL = datetime.timedelta(hours=1)
SESSION_OK_PATH = Path.home() / ".config/m5x2/appfolio_session_ok"
_PROBE_INTERVAL = datetime.timedelta(hours=24)

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


def maybe_send_auth_alert(service, auto_detail: str = ""):
    """Alert the operator when the 2FA session expires: email + a Todoist
    task, re-firing once per _AUTH_ALERT_INTERVAL while still broken (not
    just once-ever) so a multi-day outage can't go silent like 2026-07-07's
    did. The Todoist task is the durable nag — closed automatically on the
    next successful sign or auto-relogin (see _clear_auth_alert callers).
    Since 2026-11-12 this only fires after try_auto_relogin() has failed or
    is throttled; auto_detail says why."""
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
            f"Automatic re-login: {auto_detail or 'not attempted'}\n\n"
            "Fix — from anywhere (reads the SMS code off Ix itself):\n"
            "    ssh ix zsh ~/i446-monorepo/tools/m5x2-automations/relogin.sh\n"
            "or on Ix: python3 lease_signer.py --login and complete 2FA in the\n"
            "browser window that opens.\n"
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
            AUTH_ALERT_TASK,
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


def _mark_session_ok(source: str):
    """Record proof that the AppFolio session works (a sign, a relogin, or the
    daily probe). /1-i446 warns when this is over 2 days old."""
    try:
        SESSION_OK_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_OK_PATH.write_text(json.dumps({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"), "source": source}))
    except OSError as e:
        log.warning(f"Failed to write {SESSION_OK_PATH}: {e}")


def _session_ok_age() -> datetime.timedelta | None:
    try:
        ts = json.loads(SESSION_OK_PATH.read_text())["ts"]
        return datetime.datetime.now() - datetime.datetime.fromisoformat(ts)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _relogin_throttled() -> bool:
    try:
        last = datetime.datetime.fromisoformat(_AUTO_RELOGIN_LAST_PATH.read_text().strip())
    except (OSError, ValueError):
        return False
    return datetime.datetime.now() - last < _AUTO_RELOGIN_INTERVAL


def try_auto_relogin(service, reason: str = "") -> bool:
    """Re-authenticate the AppFolio session without JM: the single owner of the
    expired→healthy transition. Stamps the 1h throttle BEFORE requesting the
    SMS (a crash + KeepAlive restart must not text a code a minute), runs
    relogin.relogin() with the code read from Ix's Messages, and on success
    records session_ok and clears any open operator alert. On throttle,
    failure or exception it raises the operator alert instead. Never throws."""
    if _relogin_throttled():
        log.warning("Auto-relogin throttled (last attempt < 1h ago); alerting operator.")
        maybe_send_auth_alert(service, auto_detail="throttled — an attempt within the last hour failed")
        return False
    try:
        _AUTO_RELOGIN_LAST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AUTO_RELOGIN_LAST_PATH.write_text(datetime.datetime.now().isoformat(timespec="seconds"))
    except OSError as e:
        log.warning(f"Failed to write relogin throttle stamp: {e}")
    log.info(f"Auto-relogin: AppFolio session expired ({reason or 'unknown'}); requesting SMS code...")
    try:
        res = _relogin.relogin(get_code=_relogin.sms_code_provider(log=log.warning), headless=True)
    except Exception as e:  # noqa: BLE001 — Playwright/ssh failures must not kill the daemon
        res = {"ok": False, "detail": f"exception: {e}"}
    if res.get("ok"):
        how = "code submitted, device trusted 30d" if res.get("code_submitted") else "password only"
        log.info(f"Auto-relogin OK ({how}) → {res.get('url', '')}")
        _mark_session_ok("relogin")
        _clear_auth_alert()
        return True
    log.warning(f"Auto-relogin FAILED: {res.get('detail', '')[:300]}")
    maybe_send_auth_alert(service, auto_detail=f"FAILED — {res.get('detail', '')[:200]}")
    return False


def maybe_probe_session(service):
    """Once a day (or when there's no record at all) open AppFolio to prove the
    session still works, so a lapsed device trust is caught within 24h rather
    than when the next lease arrives — and so /1-i446 has a fresh signal in
    weeks with no leases. Runs between polls, in-process, so it never shares
    the Chromium profile with a sign. Never throws."""
    age = _session_ok_age()
    if age is not None and age < _PROBE_INTERVAL:
        return
    log.info("Probing AppFolio session (no proof in the last 24h)...")
    try:
        res = _signer.check_session(headless=True)
    except Exception as e:  # noqa: BLE001
        log.warning(f"Session probe crashed (will retry next cycle): {e}")
        return
    if res.get("ok"):
        log.info(f"Session probe OK → {res.get('url', '')}")
        _mark_session_ok("probe")
        return
    log.warning(f"Session probe: {res.get('error', 'failed')}")
    try_auto_relogin(service, reason=f"probe: {res.get('error', '')}")


def send_review_alert(service, item: dict, meta: dict, url: str, error: str):
    """Email JM AND the original forwarder when the autosigner fails on a
    lease for content reasons (no sign button, no confirmation, etc.) — the
    2026-09 gap this closes: _flag_for_review used to be completely silent,
    so failed leases sat unsigned under a label nobody watches until someone
    happened to notice in AppFolio. The sender needs to know too since
    they're the one who'll otherwise assume it's handled."""
    unit = meta.get("unit", "unknown unit")
    _, sender_addr = email.utils.parseaddr(item.get("from", ""))
    recipients = [NOTIFY_TO]
    if sender_addr and sender_addr.lower() != NOTIFY_TO.lower():
        recipients.append(sender_addr)
    try:
        body = (
            f"The lease auto-signer could NOT sign this lease automatically:\n\n"
            f"Unit:    {unit}\n"
            f"Tenants: {meta.get('tenants', '')}\n"
            f"Type:    {meta.get('lease_type', 'renewal')}\n"
            f"Error:   {error}\n"
            f"Link:    {url}\n\n"
            f"JM needs to sign this one personally in AppFolio.\n"
        )
        msg = email.mime.text.MIMEText(body)
        msg["To"] = ", ".join(recipients)
        msg["From"] = NOTIFY_TO
        msg["Subject"] = f"⚠ Auto-sign failed — {unit} needs JM to sign manually"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info(f"Sent manual-sign-needed alert to {', '.join(recipients)} for {unit}")
    except Exception as e:
        log.warning(f"Failed to send review-needed alert for {unit}: {e}")


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
        _mark_session_ok("sign")
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
    send_review_alert(service, item, meta, url, error)
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
    relogin_done = False   # at most one auto-relogin per cycle
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
            # First auth failure of the cycle: fix the session ourselves and
            # retry this very email (sign_lease has closed its Chromium, so
            # relogin owns the profile). A second auth failure in the same
            # cycle — the retry, or a later email — never relogins again:
            # alert and hold everything unread for the next cycle.
            if not relogin_done:
                relogin_done = True
                if try_auto_relogin(service, reason="sign hit the login page"):
                    try:
                        status = process_email(service, item)
                    except Exception as e:
                        log.error(f"Unexpected error re-processing email after relogin: {e}")
                        continue
                    if status != "auth_failed":
                        continue
                    maybe_send_auth_alert(service, auto_detail="succeeded, but signing still landed on the login page")
            else:
                maybe_send_auth_alert(service, auto_detail="already attempted this cycle")
            # Every remaining email would fail identically. Stop this cycle so
            # we don't launch a browser per email, and leave all pending leases
            # unread for retry after re-login.
            log.warning("2FA session expired — pausing cycle until re-login. Pending leases held unread.")
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
        if not _shutdown:
            maybe_probe_session(service)

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
