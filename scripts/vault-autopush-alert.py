#!/usr/bin/env python3
"""vault-autopush-alert — operator alerting for a stuck vault-autopush.sh.

Mirrors the m5x2-automations lease_signerd.py auth-alert pattern: a stuck
rebase used to fail silently forever (every 10 minutes, no notification —
confirmed stuck 2026-08-23 through 2026-09-07 before anyone noticed). This
gives it the same "email + Todoist nag, re-fire every 24h while still
broken, auto-closed on the next success" treatment.

Usage:
    python3 vault-autopush-alert.py --fail "<reason>"   # rebase/push failed
    python3 vault-autopush-alert.py --clear             # healthy again
"""
from __future__ import annotations

import argparse
import base64
import datetime
import email.mime.text
import json
import sys
from pathlib import Path

_IBX_DIR = Path.home() / "i446-monorepo/tools/ibx"
_LIB_DIR = Path.home() / "i446-monorepo/lib"
sys.path.insert(0, str(_IBX_DIR))
sys.path.insert(0, str(_LIB_DIR))

import ibx as _ibx          # noqa: E402
import todoist as _todoist  # noqa: E402

NOTIFY_TO = "mckay@m5c7.com"
STATE_PATH = Path.home() / ".config/m5x2/vault_autopush_fail_state"
ALERT_INTERVAL = datetime.timedelta(hours=24)


def _read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(task_id):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "alerted_at": datetime.datetime.now().isoformat(),
        "todoist_task_id": task_id,
    }))


def fail(reason: str):
    state = _read_state()
    if state:
        alerted_at = datetime.datetime.fromisoformat(state["alerted_at"])
        if datetime.datetime.now() - alerted_at < ALERT_INTERVAL:
            return  # already alerted recently; still broken, don't re-nag yet

    try:
        service = _ibx.get_gmail_service()
        body = (
            "vault-autopush.sh (on Ix) could not sync ~/vault.\n\n"
            f"Reason:\n{reason}\n\n"
            "Local commits are piling up unpushed. This does NOT corrupt files\n"
            "by itself (the rebase now runs in an isolated worktree, never in\n"
            "the live ~/vault Syncthing folder), but it does mean Ix and\n"
            "Straylight are drifting apart in git history until resolved.\n\n"
            "Fix: cd ~/vault && git status, resolve the underlying conflict\n"
            "(check /tmp/vault-autopush-rebase.err for details), then let the\n"
            "next 10-min cycle push normally.\n"
        )
        msg = email.mime.text.MIMEText(body)
        msg["To"] = NOTIFY_TO
        msg["From"] = NOTIFY_TO
        msg["Subject"] = "⚠ vault-autopush stuck — ~/vault not syncing"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        print(f"WARN: alert email failed: {e}", file=sys.stderr)

    task_id = (state or {}).get("todoist_task_id")
    try:
        task = _todoist.create_task(
            "\U0001F513 vault-autopush stuck — ~/vault not syncing to GitHub "
            "(cd ~/vault && git status; see /tmp/vault-autopush-rebase.err) [10]",
            labels=["i447"], due_string="today", priority=4,
        )
        task_id = task.get("id", task_id)
    except Exception as e:
        print(f"WARN: Todoist alert task failed: {e}", file=sys.stderr)

    _write_state(task_id)


def clear():
    state = _read_state()
    if state and state.get("todoist_task_id"):
        try:
            _todoist.close_task(state["todoist_task_id"])
        except Exception as e:
            print(f"WARN: failed to close Todoist alert task: {e}", file=sys.stderr)
    STATE_PATH.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fail", metavar="REASON")
    g.add_argument("--clear", action="store_true")
    args = ap.parse_args()

    if args.fail is not None:
        fail(args.fail)
    else:
        clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
