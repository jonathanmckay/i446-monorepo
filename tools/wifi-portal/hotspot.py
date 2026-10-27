#!/usr/bin/env python3
"""imago → fuchikoma when imago's carrier throttles it (user request
2026-10-27). One direction only: once on fuchikoma we stay there, so the
two hotspots can never flap. Assumes both are in range (JM's call); a
failed join just leaves macOS to rejoin imago, and we back off.

Throttle test: a 500 KB download from Cloudflare's speed endpoint, at most
every 5 min while on imago (~6 MB/hr). Under 1 Mbps on two consecutive
checks = throttled (carrier hotspot caps land at 128-600 kbps; one slow
sample is usually just a dead spot).

Needs the SSID, which macOS redacts from background processes unless
`sudo ipconfig setverbose 1` has been run once; without it this is a no-op.
"""
import json
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

FROM, TO = "imago", "fuchikoma"
PROBE = "https://speed.cloudflare.com/__down?bytes=500000"
THRESHOLD_MBPS = 1.0
STRIKES = 2
CHECK_EVERY = 300
FAIL_BACKOFF = 1800
STATE = Path.home() / ".cache" / "wifi-portal" / "hotspot.json"


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hotspot: {msg}", flush=True)


def current_ssid():
    out = subprocess.run(["ipconfig", "getsummary", "en0"],
                         capture_output=True, text=True).stdout
    m = re.search(r"^\s+SSID : (.+)$", out, re.M)
    if not m or m.group(1).strip() == "<redacted>":
        return None
    return m.group(1).strip()


def measure_mbps(timeout: float = 8.0):
    """Throughput of one PROBE download. A timeout reads as 0.0 (a fetch
    that can't finish 500 KB in 8 s is under 0.5 Mbps anyway); an HTTP or
    DNS error is None, no reading — Cloudflare 403s urllib's default UA, and
    counting that as "slow" would switch away from a healthy imago."""
    req = urllib.request.Request(PROBE, headers={"User-Agent": "Mozilla/5.0 wifid"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = len(r.read())
    except (TimeoutError, socket.timeout) as e:
        log(f"probe timed out: {e!r}")
        return 0.0
    except Exception as e:
        log(f"probe failed: {e!r}")
        return None
    return n * 8 / 1e6 / max(time.monotonic() - t0, 1e-3)


def _keychain_pw(ssid: str):
    r = subprocess.run(["security", "find-generic-password", "-s",
                        f"wifi-hotspot-{ssid}", "-w"], capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def join(ssid: str) -> bool:
    cmd = ["networksetup", "-setairportnetwork", "en0", ssid]
    pw = _keychain_pw(ssid)
    if pw:
        cmd.append(pw)
    r = subprocess.run(cmd, capture_output=True, text=True)
    # networksetup exits 0 even on failure; it reports errors on stdout.
    ok = r.returncode == 0 and not (r.stdout.strip() or r.stderr.strip())
    if not ok:
        log(f"join {ssid} failed: {(r.stdout + r.stderr).strip()}")
    return ok


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s))


def run(now=None, ssid_fn=current_ssid, measure_fn=measure_mbps,
        join_fn=join, notify_fn=None) -> str:
    """One tick. Returns what it did (for tests and the log)."""
    now = time.time() if now is None else now
    ssid = ssid_fn()
    state = _load()
    if ssid != FROM:
        if state.get("strikes"):
            _save({**state, "strikes": 0})
        return "not_on_imago" if ssid else "ssid_unknown"
    if now < state.get("backoff_until", 0) or now - state.get("checked_at", 0) < CHECK_EVERY:
        return "waiting"
    mbps = measure_fn()
    if mbps is None:
        _save({**state, "checked_at": now})
        return "no_reading"
    strikes = state.get("strikes", 0) + 1 if mbps < THRESHOLD_MBPS else 0
    log(f"imago {mbps:.2f} Mbps (strikes {strikes}/{STRIKES})")
    state = {**state, "checked_at": now, "strikes": strikes}
    if strikes < STRIKES:
        _save(state)
        return "ok" if strikes == 0 else "slow"
    if join_fn(TO):
        _save({"strikes": 0, "checked_at": now})
        if notify_fn:
            notify_fn(f"imago looked throttled ({mbps:.1f} Mbps); switched to {TO}.")
        return "switched"
    _save({**state, "strikes": 0, "backoff_until": now + FAIL_BACKOFF})
    if notify_fn:
        notify_fn(f"imago looks throttled but joining {TO} failed.")
    return "join_failed"
