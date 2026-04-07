#!/usr/bin/env python3
"""
lease_signer.py — CUA agent that countersigns AppFolio leases.

Flow:
  1. Follow the SendGrid-tracked AppFolio URL (redirects to actual page)
  2. Log in if the page requires it
  3. Use Claude claude-sonnet-4-6 + Playwright screenshots to navigate
  4. Click countersign and confirm
  5. Return result dict for logging

Usage:
  python3 lease_signer.py <appfolio_url>
"""
import base64
import re
import sys
import time
from typing import Optional

import anthropic

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("playwright not installed — run: pip install playwright && playwright install chromium")
    sys.exit(1)

from config import APPFOLIO_EMAIL, APPFOLIO_PASSWORD, APPFOLIO_SUBDOMAIN


SYSTEM_PROMPT = f"""You are an automation agent signing a lease renewal in AppFolio Property Manager.
AppFolio login: {APPFOLIO_EMAIL}

Your job:
1. If you see a login page, fill in the credentials and submit.
2. Once inside, find and click the "Countersign" or "Sign" button.
3. If prompted for a signature field, click on it and type the owner's name or use the provided signature tool.
4. Complete all required steps until the document shows "Signed" or "Complete".
5. When done, respond with the word DONE on its own line.

Instructions:
- Use tool calls to interact with the browser.
- After each action wait briefly for the page to settle.
- If you see a CAPTCHA or unusual blocker, respond with BLOCKED.
- Keep responses concise — just state what you're doing and call the tool.
"""


def _screenshot_b64(page) -> str:
    return base64.standard_b64encode(page.screenshot()).decode()


def _execute_action(page, action: dict):
    """Execute a computer-use action on the Playwright page."""
    atype = action.get("action")
    if atype == "screenshot":
        return  # next loop iteration will take one

    elif atype == "left_click":
        x, y = action["coordinate"]
        page.mouse.click(x, y)

    elif atype == "double_click":
        x, y = action["coordinate"]
        page.mouse.dblclick(x, y)

    elif atype == "right_click":
        x, y = action["coordinate"]
        page.mouse.click(x, y, button="right")

    elif atype == "type":
        page.keyboard.type(action["text"])

    elif atype == "key":
        page.keyboard.press(action["key"])

    elif atype == "scroll":
        x, y = action["coordinate"]
        page.mouse.wheel(action.get("delta_x", 0), action.get("delta_y", 0))

    elif atype == "mouse_move":
        x, y = action["coordinate"]
        page.mouse.move(x, y)

    time.sleep(0.4)  # let UI settle


def sign_lease(appfolio_url: str, headless: bool = True) -> dict:
    """
    Navigate to appfolio_url and countersign the lease using CUA.
    Returns {"status": "success"|"failed"|"timeout"|"blocked", "url": final_url}.
    """
    if not APPFOLIO_PASSWORD:
        return {"status": "failed", "error": "AF_PASSWORD env var not set"}

    client = anthropic.Anthropic()
    result = {"status": "timeout", "url": appfolio_url}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        try:
            page.goto(appfolio_url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            return {"status": "failed", "error": "page load timeout"}

        messages = []
        max_steps = 25

        for step in range(max_steps):
            screenshot = _screenshot_b64(page)
            current_url = page.url

            # Build next user message
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot}},
                    {"type": "text", "text": f"Step {step+1}. Current URL: {current_url}\n\nWhat should I do next to complete the countersigning?"}
                ]
            })

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[{
                    "type": "computer_20241022",
                    "name": "computer",
                    "display_width_px": 1280,
                    "display_height_px": 800,
                }],
                messages=messages,
                betas=["computer-use-2024-10-22"],
            )

            # Collect assistant turn for message history
            messages.append({"role": "assistant", "content": response.content})

            # Check text blocks for terminal states
            for block in response.content:
                if hasattr(block, "text"):
                    if "DONE" in block.text.upper():
                        result = {"status": "success", "url": page.url}
                        browser.close()
                        return result
                    if "BLOCKED" in block.text.upper():
                        result = {"status": "blocked", "url": page.url}
                        browser.close()
                        return result

            # Execute any tool calls
            for block in response.content:
                if block.type == "tool_use" and block.name == "computer":
                    _execute_action(page, block.input)

            if response.stop_reason == "end_turn":
                # No DONE signal but model stopped — check if page looks complete
                title = page.title().lower()
                if any(w in title for w in ("signed", "complete", "thank")):
                    result = {"status": "success", "url": page.url}
                    break

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
