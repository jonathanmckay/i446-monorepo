#!/usr/bin/env python3
"""wifi-portal: get through the United / Alaska in-flight sign-in page
without touching it (user request 2026-10-27: 30 flights/yr, 2-10 min each).

Runs from launchd on every network change and every 60 s. Deliberately
narrow, so it can never make signing in anywhere else harder:

  * Online (captive.apple.com answers "Success") → exit, nothing touched.
  * Captive, but the sign-in page is not on an allowlisted airline host
    (hotel, airport, conference …) → exit; macOS's own sheet handles it.
  * Never switches or joins networks; network choice stays with macOS.
  * Never clicks anything that looks like a purchase.
  * One attempt per stuck session (state resets once online again), then
    a notification so JM takes over. 2FA prompts stop it immediately.
  * Kill switch: `portal.py off` (or unload the launchd agent).

Every step's screenshot + HTML lands in ~/.cache/wifi-portal/runs/<ts>/, so
the first real flight produces the DOM needed to harden the airline steps.

Credentials come from the login Keychain, never from this repo:
  security add-generic-password -s wifi-portal-united -a <MileagePlus #> -w
  security add-generic-password -s wifi-portal-alaska -a <Atmos #/email> -w

Usage: portal.py [run|now|status|off|on] [--dry-run]
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROBE_URL = "http://captive.apple.com/hotspot-detect.html"
CACHE = Path.home() / ".cache" / "wifi-portal"
STATE = CACHE / "state.json"
DISABLED = CACHE / "disabled"
RUNS = CACHE / "runs"

# Portal host suffix → Keychain service holding that airline's login.
AIRLINES = {
    "unitedwifi.com": "wifi-portal-united",
    "alaskawifi.com": "wifi-portal-alaska",
}

# Click candidates. BLOCK always wins over GO: a "Connect for $8" button is
# a purchase, not a connect.
GO = re.compile(r"\b(free|connect|get started|continue|accept|agree|"
                r"sign ?in|log ?in|join|start browsing|get online|access|"
                r"activate|redeem|i'?m a member|member)\b", re.I)
BLOCK = re.compile(r"(buy|purchase|\bpay\b|payment|checkout|subscribe|"
                   r"subscription|upgrade|credit card|card number|\$|€|£|"
                   r"\bprice\b|\busd\b|\bplans?\b|\bpass(es)?\b)", re.I)
TERMS = re.compile(r"(terms|agree|accept|conditions|policy)", re.I)
OTP = re.compile(r"(otp|one.?time|verification|passcode|\bcode\b|2fa|mfa)", re.I)

MAX_STEPS = 10
SESSION_TTL = 6 * 3600  # a "stuck session" older than a long flight is stale


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def notify(msg: str, sound: bool = False) -> None:
    script = f'display notification {json.dumps(msg)} with title "wifi-portal"'
    if sound:
        script += ' sound name "Submarine"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def probe(url: str = PROBE_URL, timeout: float = 5.0):
    """(online, portal_url). portal_url is where the captive network sends
    us — from a 30x Location, or a meta-refresh / JS location in the body."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(url, timeout=timeout)
        status, headers, body = resp.status, resp.headers, resp.read(65536)
    except urllib.error.HTTPError as e:
        status, headers, body = e.code, e.headers, e.read(65536) if e.fp else b""
    except Exception as e:  # no route / DNS / timeout: not a portal we can see
        log(f"probe failed: {e!r}")
        return False, None
    text = body.decode("utf-8", "replace")
    if status == 200 and "Success" in text:
        return True, None
    loc = headers.get("Location") if headers else None
    if not loc:
        m = (re.search(r'http-equiv=["\']?refresh["\']?[^>]*url=([^"\'>\s]+)', text, re.I)
             or re.search(r'location(?:\.href)?\s*=\s*["\']([^"\']+)', text, re.I))
        loc = m.group(1) if m else None
    return False, urllib.parse.urljoin(url, loc) if loc else None


def airline_for(portal_url):
    """Keychain service for an allowlisted portal host, else None."""
    if not portal_url:
        return None
    host = (urllib.parse.urlparse(portal_url).hostname or "").lower()
    for suffix, service in AIRLINES.items():
        if host == suffix or host.endswith("." + suffix):
            return service
    return None


def keychain_login(service: str):
    """(account, password) from the login Keychain, or None."""
    meta = subprocess.run(["security", "find-generic-password", "-s", service],
                          capture_output=True, text=True)
    pw = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                        capture_output=True, text=True)
    if meta.returncode or pw.returncode:
        return None
    m = re.search(r'"acct"<blob>="([^"]*)"', meta.stdout)
    return (m.group(1) if m else ""), pw.stdout.rstrip("\n")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def drive(portal_url, creds, run_dir: Path, is_online, dry_run=False) -> str:
    """Walk the portal in headless Chromium. Returns 'online', 'needs_2fa',
    'stuck', or 'dry_run'. `is_online` is a zero-arg callable (injected so
    tests can point it at a fake portal)."""
    from playwright.sync_api import sync_playwright

    run_dir.mkdir(parents=True, exist_ok=True)
    clicked = set()
    filled_login = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(portal_url, wait_until="domcontentloaded", timeout=20000)
            for step in range(MAX_STEPS):
                page.wait_for_timeout(1500)
                _snapshot(page, run_dir, step)
                if is_online():
                    return "online"
                if _has_otp(page):
                    return "needs_2fa"
                acted = False
                if creds and not filled_login and _fill_login(page, creds, dry_run):
                    filled_login = acted = True
                acted |= _tick_terms(page, dry_run)
                target = _pick_button(page, clicked)
                if target is not None:
                    el, label = target
                    clicked.add(label)
                    log(f"step {step}: click {label!r}")
                    if not dry_run:
                        try:
                            el.click(timeout=5000)
                        except Exception as e:
                            log(f"click failed: {e!r}")
                    acted = True
                if dry_run:
                    return "dry_run"
                if not acted:
                    page.wait_for_timeout(3000)
                    return "online" if is_online() else "stuck"
            return "online" if is_online() else "stuck"
        finally:
            browser.close()


def _snapshot(page, run_dir: Path, step: int) -> None:
    try:
        page.screenshot(path=str(run_dir / f"{step:02d}.png"), full_page=True)
        (run_dir / f"{step:02d}.html").write_text(page.content())
        (run_dir / f"{step:02d}.url").write_text(page.url)
    except Exception as e:
        log(f"snapshot failed: {e!r}")


def _label(el) -> str:
    try:
        txt = el.inner_text() or el.get_attribute("value") \
            or el.get_attribute("aria-label") or ""
    except Exception:
        return ""
    return " ".join(txt.split())[:80]


def _visible(el) -> bool:
    try:
        return el.is_visible() and el.is_enabled()
    except Exception:
        return False


def _has_otp(page) -> bool:
    for el in page.query_selector_all("input"):
        if not _visible(el):
            continue
        attrs = " ".join(filter(None, (el.get_attribute(a) for a in
                                       ("name", "id", "placeholder", "autocomplete", "aria-label"))))
        if el.get_attribute("autocomplete") == "one-time-code" or OTP.search(attrs):
            return True
    return False


def _fill_login(page, creds, dry_run) -> bool:
    pw = next((el for el in page.query_selector_all("input[type=password]") if _visible(el)), None)
    if pw is None:
        return False
    user = next((el for el in page.query_selector_all(
        "input[type=text], input[type=email], input[type=tel], input:not([type])")
        if _visible(el)), None)
    if user is None:
        return False
    log("filling login form")
    if not dry_run:
        user.fill(creds[0])
        pw.fill(creds[1])
    return True


def _tick_terms(page, dry_run) -> bool:
    acted = False
    for box in page.query_selector_all("input[type=checkbox]"):
        try:
            if box.is_checked():
                continue
            near = box.evaluate(
                "b => (b.closest('label') || b.parentElement || b).innerText || ''")
        except Exception:
            continue
        if TERMS.search(near or "") and not BLOCK.search(near or ""):
            log(f"tick terms: {' '.join(near.split())[:60]!r}")
            if not dry_run:
                try:
                    box.check(timeout=3000, force=True)
                except Exception as e:
                    log(f"tick failed: {e!r}")
            acted = True
    return acted


def _pick_button(page, clicked):
    """Best GO candidate not yet clicked and not a purchase; 'free' first."""
    cands = []
    for el in page.query_selector_all(
            "button, a, [role=button], input[type=submit], input[type=button]"):
        if not _visible(el):
            continue
        label = _label(el)
        if not label or label in clicked or BLOCK.search(label) or not GO.search(label):
            continue
        cands.append((0 if re.search(r"\bfree\b", label, re.I) else 1, el, label))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    return cands[0][1], cands[0][2]


def run(force: bool = False, dry_run: bool = False) -> int:
    if DISABLED.exists() and not force:
        return 0
    online, portal = probe()
    state = load_state()
    if online:
        if state:
            save_state({})  # session over: next captive network gets a fresh try
        return 0
    service = airline_for(portal)
    if service is None:
        if portal:
            log(f"captive but not allowlisted, leaving alone: {portal}")
        return 0
    if not force and state.get("portal_host") == service \
            and time.time() - state.get("at", 0) < SESSION_TTL:
        return 0  # already tried this session; JM has it
    save_state({"portal_host": service, "at": time.time()})
    creds = keychain_login(service)
    run_dir = RUNS / time.strftime("%Y%m%d-%H%M%S")
    log(f"portal {portal} ({service}), creds={'yes' if creds else 'no'}, run={run_dir}")
    try:
        result = drive(portal, creds, run_dir, lambda: probe()[0], dry_run=dry_run)
    except Exception as e:
        log(f"drive crashed: {e!r}")
        result = "stuck"
    log(f"result: {result}")
    if result == "online":
        notify("In-flight wifi connected.")
    elif result == "needs_2fa":
        notify("Wifi sign-in wants a 2FA code; finish it in the sign-in sheet.", sound=True)
    elif result == "stuck":
        notify("Couldn't finish the wifi sign-in; over to you. Steps saved for a fix.", sound=True)
    return 0


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "run"
    dry = "--dry-run" in argv
    if cmd == "off":
        CACHE.mkdir(parents=True, exist_ok=True)
        DISABLED.touch()
        print("wifi-portal disabled")
    elif cmd == "on":
        DISABLED.unlink(missing_ok=True)
        print("wifi-portal enabled")
    elif cmd == "status":
        online, portal = probe()
        print(json.dumps({"disabled": DISABLED.exists(), "online": online,
                          "portal": portal, "allowlisted": airline_for(portal),
                          "state": load_state()}, indent=2))
    elif cmd == "now":
        return run(force=True, dry_run=dry)
    else:
        return run(dry_run=dry)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
