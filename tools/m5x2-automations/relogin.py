#!/usr/bin/env python3
"""relogin.py — Re-authenticate the AppFolio browser session (SMS 2FA relay).

Runs the full login flow (email/password autofill, request SMS 2-step
verification, submit the code) in ONE continuous Playwright session so the
Keycloak server-side "awaiting 2FA" state is never lost between steps — a
fresh process/page load after requesting the code is not guaranteed to
resume the same pending verification.

Two callers:

* CLI (relogin.sh, a human, or Claude over ssh). stdout protocol, line-buffered
  so a FIFO can drive it:
    1. Prints "CODE_SENT" once the SMS has been requested (or "NO_2FA_NEEDED"
       when the saved device trust still satisfies AppFolio; nothing is read
       from stdin then).
    2. Reads one line from stdin: the 6-digit code.
    3. Submits it with "remember this device for 30 days" checked, so this
       exact profile (~/.config/m5x2/appfolio_browser_state) won't need
       2FA again for ~30 days.
    4. Prints "LOGIN_OK" + the landed URL on success, or "LOGIN_FAILED" +
       diagnostic detail (final URL, a page-text snippet, and a screenshot
       path) on failure. Exit code mirrors success/failure.

* lease_signerd (2026-11-12, self-healing): calls relogin(get_code=
  sms_code_provider()) in-process when a sign hits the 2FA wall. The provider
  reads the AppFolio SMS (sender +14695475524, "123456 Use this code to
  verify your AppFolio account...") straight out of Ix's Messages database.
  launchd-spawned python has no Full Disk Access to chat.db, but sshd does,
  so the read goes through `ssh ix sqlite3` exactly like
  tools/ibx/thread_translator.py. Only messages that arrived AFTER the code
  was requested count, so a stale code is never replayed.

Whoever calls relogin() must not have another Chromium open on the same
persistent profile (ProcessSingleton lock, hit live 2026-09-09): relogin.sh
stops the daemon around a CLI run; the daemon itself only calls this after
sign_lease() has closed its own context.

Usage:
    python3 relogin.py                    # interactive: paste the code when prompted
    echo 123456 | python3 relogin.py      # non-interactive / FIFO-fed
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))

from config import APPFOLIO_SUBDOMAIN  # noqa: E402

SMS_SENDER = "4695475524"          # AppFolio's 2FA short-code-ish long number
SMS_TIMEOUT_S = 150                # AppFolio codes expire after 10 min; SMS lands in seconds
SMS_POLL_S = 5
SMS_SLACK_S = 10                   # chat.db date vs our clock: allow a little skew
_APPLE_EPOCH = 978307200           # 2001-01-01 — chat.db `date` is ns since then
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# Same ssh route (and ControlMaster socket reuse) as thread_translator.py.
_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/lease-relogin-ssh",
        "-o", "ControlPersist=600", "ix"]

# get_code(requested_at_epoch) -> code or None. requested_at is taken just
# BEFORE the "send me a code" click so a provider can ignore older messages.
CodeProvider = Callable[[float], Optional[str]]


# --- code extraction -------------------------------------------------------
def extract_code(text: str) -> Optional[str]:
    """First standalone 6-digit group in an AppFolio verification SMS."""
    m = _CODE_RE.search(text or "")
    return m.group(1) if m else None


def code_from_row(text: Optional[str], body_hex: Optional[str]) -> Optional[str]:
    """chat.db keeps the text in `text`, or (newer macOS) only inside the
    attributedBody TypedStream blob; the digits survive a latin-1 decode of
    the raw bytes, which is all we need here."""
    code = extract_code(text or "")
    if code:
        return code
    if body_hex:
        try:
            return extract_code(bytes.fromhex(body_hex.strip()).decode("latin-1"))
        except ValueError:
            return None
    return None


def _default_query(sql: str) -> list[list[str]]:
    remote = ("sqlite3 -readonly -separator \"$(printf '\\t')\" "
              "~/Library/Messages/chat.db " + shlex.quote(sql))
    r = subprocess.run(_SSH + [remote], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"sqlite over ssh failed: {r.stderr.strip()[:200]}")
    return [line.split("\t") for line in r.stdout.splitlines() if line]


def fetch_sms_code(since_epoch: float, sender: str = SMS_SENDER,
                   query=_default_query) -> Optional[str]:
    """Newest AppFolio code that arrived after since_epoch (unix seconds), or None."""
    since_ns = int((since_epoch - SMS_SLACK_S - _APPLE_EPOCH) * 1_000_000_000)
    sql = ("select coalesce(m.text, ''), hex(m.attributedBody) from message m "
           "join handle h on m.handle_id = h.ROWID "
           f"where h.id like '%{sender}%' and m.is_from_me = 0 and m.date > {since_ns} "
           "order by m.date desc limit 5;")
    for row in query(sql):
        text = row[0] if row else ""
        body_hex = row[1] if len(row) > 1 else ""
        code = code_from_row(text, body_hex)
        if code:
            return code
    return None


def sms_code_provider(timeout_s: float = SMS_TIMEOUT_S, poll_s: float = SMS_POLL_S,
                      query=_default_query, log=None) -> CodeProvider:
    """A get_code that polls Ix's Messages for the AppFolio SMS."""
    def get_code(requested_at: float) -> Optional[str]:
        deadline = time.time() + timeout_s
        last_err = None
        while time.time() < deadline:
            try:
                code = fetch_sms_code(requested_at, query=query)
            except Exception as e:  # noqa: BLE001 — ssh hiccup: keep polling
                last_err = e
                code = None
            if code:
                return code
            time.sleep(poll_s)
        if log and last_err:
            log(f"sms_code_provider: last chat.db error: {last_err}")
        return None
    return get_code


def stdin_code_provider(requested_at: float) -> Optional[str]:
    print("CODE_SENT", flush=True)
    return sys.stdin.readline().strip() or None


# --- the login flow --------------------------------------------------------
def relogin(get_code: CodeProvider = stdin_code_provider, headless: bool = True,
            report: Callable[[str], None] = lambda s: None) -> dict:
    """Returns {"ok": bool, "url": str, "code_submitted": bool, "detail": str}.
    `report` gets the CLI protocol lines (NO_2FA_NEEDED) for the main() printer."""
    from playwright.sync_api import sync_playwright
    import lease_signer as ls

    with sync_playwright() as p:
        ctx = ls._launch_context(p, headless=headless)
        page = ctx.new_page()
        try:
            page.goto(f"https://{APPFOLIO_SUBDOMAIN}.appfolio.com",
                      wait_until="domcontentloaded", timeout=30_000)
            ls._wait(page, 3000)
            ls._login_if_needed(page)
            ls._wait(page, 3000)

            code_submitted = False
            # 2-step verification method picker (SMS vs phone call) -- only
            # appears when the saved session doesn't already satisfy AppFolio.
            # Absent entirely if the previous "remember this device" grant is
            # still within its ~30-day window, in which case we're just
            # re-confirming an already-valid session.
            radios = page.locator('input[name="twoFactorMethod"]')
            if radios.count() > 0:
                radios.nth(0).check()  # index 0 = SMS (id="method-sms")
                requested_at = time.time()
                page.locator('button[type="submit"], input[type="submit"]').first.click()
                ls._wait(page, 4000)
                code = get_code(requested_at)
                if not code:
                    return {"ok": False, "url": page.url, "code_submitted": False,
                            "detail": "no 2FA code received"}
                page.locator('input[name="code"]').fill(code)
                remember = page.locator('input[name="rememberMe"]')
                if remember.count() > 0:
                    remember.check()
                page.locator('button[type="submit"], input[type="submit"]').first.click()
                ls._wait(page, 4000)
                code_submitted = True
            else:
                report("NO_2FA_NEEDED")

            ls._skip_passkey_prompt(page)  # "add a Passkey" nag parks a finished login
            ok = ("login" not in page.url.lower() and "authenticate" not in page.url.lower()
                  and page.locator('input[name="code"], input[name="twoFactorMethod"]').count() == 0)
            if ok:
                return {"ok": True, "url": page.url, "code_submitted": code_submitted, "detail": ""}
            shot = "/tmp/relogin_failed.png"
            try:
                page.screenshot(path=shot, full_page=True)
            except Exception:
                shot = "(screenshot failed)"
            snippet = (page.text_content("body") or "")[:300].replace("\n", " ")
            return {"ok": False, "url": page.url, "code_submitted": code_submitted,
                    "detail": f"url={page.url} screenshot={shot} text={snippet!r}"}
        finally:
            ctx.close()


def main() -> int:
    res = relogin(report=lambda s: print(s, flush=True))
    if res["ok"]:
        print(f"LOGIN_OK {res['url']}", flush=True)
        return 0
    print(f"LOGIN_FAILED {res['detail']}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
