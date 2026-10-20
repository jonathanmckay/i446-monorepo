#!/usr/bin/env python3
"""relogin.py — Re-authenticate the AppFolio browser session (SMS 2FA relay).

Runs the full login flow (email/password autofill, request SMS 2-step
verification, submit the code) in ONE continuous Playwright session so the
Keycloak server-side "awaiting 2FA" state is never lost between steps — a
fresh process/page load after requesting the code is not guaranteed to
resume the same pending verification.

Protocol (stdout is line-buffered so a caller can drive this interactively
or via a FIFO):
    1. Prints "CODE_SENT" once the SMS has been requested.
    2. Reads one line from stdin: the 6-digit code.
    3. Submits it with "remember this device for 30 days" checked, so this
       exact profile (~/.config/m5x2/appfolio_browser_state) won't need
       2FA again for ~30 days.
    4. Prints "LOGIN_OK" + the landed URL on success, or "LOGIN_FAILED" +
       diagnostic detail (final URL, a page-text snippet, and a screenshot
       path) on failure. Exit code mirrors success/failure.

Caller is responsible for stopping tools/m5x2-automations' lease-signerd
daemon before running this and restarting it after — both processes share
the same persistent browser profile, and running them concurrently trips a
Chrome ProcessSingleton lock error (hit live 2026-09-09). See relogin.sh,
which wraps exactly that sequence.

The 2FA code itself has to come from wherever the SMS lands — Claude Code
has iMessage read access on machines with SMS-forwarding enabled and can
poll for the AppFolio text itself (sender was +14695475524, message
contains a 6-digit code) rather than asking the user to relay it by hand.

Usage:
    python3 relogin.py                    # interactive: paste the code when prompted
    echo 123456 | python3 relogin.py      # non-interactive / FIFO-fed
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright

from config import APPFOLIO_SUBDOMAIN
import lease_signer as ls


def relogin() -> int:
    with sync_playwright() as p:
        ctx = ls._launch_context(p, headless=True)
        page = ctx.new_page()
        page.goto(f"https://{APPFOLIO_SUBDOMAIN}.appfolio.com",
                  wait_until="domcontentloaded", timeout=30_000)
        ls._wait(page, 3000)
        ls._login_if_needed(page)
        ls._wait(page, 3000)

        # 2-step verification method picker (SMS vs phone call) -- only
        # appears when the saved session doesn't already satisfy AppFolio.
        # Absent entirely if the previous "remember this device" grant is
        # still within its ~30-day window, in which case we're just
        # re-confirming an already-valid session.
        radios = page.locator('input[name="twoFactorMethod"]')
        if radios.count() > 0:
            radios.nth(0).check()  # index 0 = SMS (id="method-sms")
            page.locator('button[type="submit"], input[type="submit"]').first.click()
            ls._wait(page, 4000)
            print("CODE_SENT", flush=True)
            code = sys.stdin.readline().strip()
            if not code:
                print("LOGIN_FAILED no code received on stdin", flush=True)
                ctx.close()
                return 1
            page.locator('input[name="code"]').fill(code)
            remember = page.locator('input[name="rememberMe"]')
            if remember.count() > 0:
                remember.check()
            page.locator('button[type="submit"], input[type="submit"]').first.click()
            ls._wait(page, 4000)
        else:
            print("CODE_SENT", flush=True)  # nothing to wait on; keep the protocol uniform
            sys.stdin.readline()  # drain in case a caller still sends one

        ok = "login" not in page.url.lower() and "authenticate" not in page.url.lower()
        if ok:
            print(f"LOGIN_OK {page.url}", flush=True)
            ctx.close()
            return 0
        shot = "/tmp/relogin_failed.png"
        try:
            page.screenshot(path=shot, full_page=True)
        except Exception:
            shot = "(screenshot failed)"
        snippet = (page.text_content("body") or "")[:300].replace("\n", " ")
        print(f"LOGIN_FAILED url={page.url} screenshot={shot} text={snippet!r}", flush=True)
        ctx.close()
        return 1


if __name__ == "__main__":
    sys.exit(relogin())
