#!/usr/bin/env python3
"""
lease_signer.py — Playwright automation that countersigns AppFolio leases.

Flow:
  1. Follow the SendGrid-tracked AppFolio URL (redirects to actual page)
  2. Log in if the page requires it
  3. Click the countersign/sign link
  4. Scroll to bottom, accept signature
  5. Return result dict for logging

Usage:
  python3 lease_signer.py <appfolio_url>
"""
import re
import sys
import time
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("playwright not installed — run: pip install playwright && playwright install chromium")
    sys.exit(1)

from config import APPFOLIO_EMAIL, APPFOLIO_PASSWORD, APPFOLIO_SUBDOMAIN


def _wait(page, ms=1500):
    """Wait for network to settle and UI to render."""
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except PWTimeout:
        pass
    time.sleep(0.3)


def _login_if_needed(page) -> bool:
    """Detect AppFolio login page and fill credentials. Returns True if login was performed."""
    # Check for common login indicators
    if "login" not in page.url.lower() and "sign_in" not in page.url.lower():
        # Also check page content for login form
        try:
            email_field = page.locator('input[type="email"], input[name="email"], input[name="user[email]"], #user_email').first
            if not email_field.is_visible(timeout=2000):
                return False
        except Exception:
            return False

    # Find and fill email field
    email_selectors = [
        'input[name="user[email]"]',
        'input#user_email',
        'input[type="email"]',
        'input[name="email"]',
        'input[placeholder*="email" i]',
    ]
    for sel in email_selectors:
        try:
            field = page.locator(sel).first
            if field.is_visible(timeout=1000):
                field.fill(APPFOLIO_EMAIL)
                break
        except Exception:
            continue

    # Find and fill password field
    pw_selectors = [
        'input[name="user[password]"]',
        'input#user_password',
        'input[type="password"]',
        'input[name="password"]',
    ]
    for sel in pw_selectors:
        try:
            field = page.locator(sel).first
            if field.is_visible(timeout=1000):
                field.fill(APPFOLIO_PASSWORD)
                break
        except Exception:
            continue

    # Click submit
    submit_selectors = [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
        'input[value="Sign In"]',
        'input[value="Log In"]',
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                _wait(page, 5000)
                return True
        except Exception:
            continue

    return False


def _find_and_click_sign(page) -> bool:
    """Find the countersign/sign action button or link on the lease page."""
    sign_selectors = [
        'a:has-text("Countersign")',
        'button:has-text("Countersign")',
        'a:has-text("Sign")',
        'button:has-text("Sign")',
        'a:has-text("Review and Sign")',
        'a:has-text("Review & Sign")',
        'a[href*="countersign"]',
        'a[href*="sign"]',
    ]
    for sel in sign_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                _wait(page, 5000)
                return True
        except Exception:
            continue
    return False


def _accept_signature(page) -> bool:
    """Scroll to bottom and click the accept/sign button."""
    # Scroll to very bottom of the page
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    accept_selectors = [
        'button:has-text("Sign and Accept")',
        'button:has-text("Accept and Sign")',
        'button:has-text("I Agree")',
        'button:has-text("Accept")',
        'button:has-text("Sign")',
        'input[value*="Sign"]',
        'input[value*="Accept"]',
        'a:has-text("Sign and Accept")',
        'a:has-text("Accept and Sign")',
        # Checkbox that might need to be checked first
        'input[type="checkbox"]#agree',
        'input[type="checkbox"][name*="agree"]',
    ]

    # Check if there's an agreement checkbox first
    try:
        checkbox = page.locator('input[type="checkbox"]').first
        if checkbox.is_visible(timeout=1000) and not checkbox.is_checked():
            checkbox.check()
            time.sleep(0.5)
    except Exception:
        pass

    for sel in accept_selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                _wait(page, 5000)
                return True
        except Exception:
            continue
    return False


def _check_success(page) -> bool:
    """Check if the page shows a success/completion state."""
    indicators = ["signed", "complete", "thank you", "successfully", "confirmed"]
    text = page.text_content("body").lower()
    title = page.title().lower()
    return any(w in text or w in title for w in indicators)


def sign_lease(appfolio_url: str, headless: bool = True) -> dict:
    """
    Navigate to appfolio_url and countersign the lease via Playwright.
    Returns {"status": "success"|"failed"|"timeout"|"blocked", "url": final_url}.
    """
    if not APPFOLIO_PASSWORD:
        return {"status": "failed", "error": "AF_PASSWORD not set"}

    result = {"status": "failed", "url": appfolio_url}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1024, "height": 768})
        page = ctx.new_page()

        # Step 1: Navigate to the URL (follows SendGrid redirect)
        try:
            page.goto(appfolio_url, wait_until="domcontentloaded", timeout=30_000)
            _wait(page, 5000)
        except PWTimeout:
            browser.close()
            return {"status": "failed", "error": "page load timeout"}

        # Step 2: Login if needed
        _login_if_needed(page)

        # Step 3: Find and click countersign/sign
        if not _find_and_click_sign(page):
            # Maybe we're already on the signing page — try accept directly
            pass

        # Step 4: Accept signature at bottom
        if _accept_signature(page):
            _wait(page, 3000)
            if _check_success(page):
                result = {"status": "success", "url": page.url}
            else:
                # Clicked accept but didn't see confirmation — try once more
                time.sleep(3)
                if _check_success(page):
                    result = {"status": "success", "url": page.url}
                else:
                    result = {"status": "failed", "error": "clicked accept but no confirmation seen", "url": page.url}
        else:
            # Couldn't find accept button — save screenshot for debugging
            try:
                page.screenshot(path="/tmp/autosign_debug.png")
            except Exception:
                pass
            result = {"status": "failed", "error": "could not find accept/sign button", "url": page.url}

        browser.close()

    return result


# ── Helpers for ibx integration ───────────────────────────────────────────────

_APPFOLIO_URL_RE = re.compile(r'https?://(?:sg\.appfolio\.com/ls/click\?[^\s>\"]+|[a-z]+\.appfolio\.com/[^\s>\"]+)', re.I)
_COUNTERSIGN_SUBJECT_RE = re.compile(r'countersign', re.I)
_UNIT_RE = re.compile(r'for\s+([\w\d]+\s+[\w\d]+\s+-\s+[\w\d]+)', re.I)
_TENANT_RE = re.compile(r'Tenants?\s+([\w ,]+)', re.I)


def extract_appfolio_url(body: str) -> Optional[str]:
    """Pull the first AppFolio/SendGrid URL out of an email body."""
    m = _APPFOLIO_URL_RE.search(body)
    return m.group(0).rstrip(">") if m else None


def is_autosign_email(item: dict, autosign_senders: list[str]) -> bool:
    """Return True if this ibx item should be auto-signed."""
    if item.get("type") != "email":
        return False
    sender = item.get("from", "").lower()
    if not any(s.lower() in sender for s in autosign_senders):
        return False
    subject = item.get("preview", "")
    body    = item.get("body", "")
    if not _COUNTERSIGN_SUBJECT_RE.search(subject + body):
        return False
    if not extract_appfolio_url(body):
        return False
    return True


def parse_email_metadata(item: dict) -> dict:
    """Extract property/unit/tenants from the email for DB logging."""
    subject = item.get("preview", "")
    body    = item.get("body", "")
    text    = subject + " " + body

    unit    = (_UNIT_RE.search(text) or [None, ""])[1].strip()
    tenants = (_TENANT_RE.search(body) or [None, ""])[1].strip()
    property_ = unit.split()[0] if unit else ""
    lease_type = "renewal" if "renewal" in text.lower() else "new"

    return {
        "property":    property_,
        "unit":        unit,
        "tenants":     tenants,
        "lease_type":  lease_type,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 lease_signer.py <appfolio_url>")
        sys.exit(1)
    url = sys.argv[1]
    headless = "--headless" in sys.argv
    print(f"Signing: {url[:80]}...")
    r = sign_lease(url, headless=headless)
    print(f"Result: {r}")
