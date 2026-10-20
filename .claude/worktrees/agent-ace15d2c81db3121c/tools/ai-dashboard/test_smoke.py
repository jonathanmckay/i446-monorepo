#!/usr/bin/env python3
"""Smoke test for the three local dashboards.

Hits each dashboard URL (60s timeout), asserts HTTP 200, body > 1000 bytes,
and looks for structural anchor strings that should always be present.

Also exercises two JSON endpoints on the AI-stats dashboard.

Usage:
    python3 test_smoke.py

Exits 0 if all pass, non-zero otherwise.

Not wired into cron (too noisy). Run manually after restarting dashboards
or as a post-deploy gate. See periodic-sync.sh for the companion comment.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

TIMEOUT = 60  # seconds
MIN_BODY_BYTES = 1000


@dataclass
class Check:
    url: str
    label: str
    anchors: tuple[str, ...] = ()
    expect_json: bool = False
    # Optional extra validator run against decoded JSON or text body.
    validator: Optional[Callable[[object], Optional[str]]] = None


CHECKS: list[Check] = [
    # AI Stats dashboard (dashboard.py on 5555)
    Check(
        url="http://localhost:5555/",
        label="AI Stats (5555)",
        anchors=(
            "AI Tools Usage Dashboard",
            "Claude Code",
            "Turns / Device",
        ),
    ),
    Check(
        url="http://localhost:5555/api/stats",
        label="AI Stats /api/stats (5555)",
        expect_json=True,
        validator=lambda data: (
            None
            if isinstance(data, dict) and "claude" in data
            else "expected top-level JSON object with 'claude' key"
        ),
    ),
    Check(
        url="http://localhost:5555/api/turns",
        label="AI Stats /api/turns (5555)",
        expect_json=True,
        validator=lambda data: (
            None
            if isinstance(data, list)
            else "expected top-level JSON list"
        ),
    ),
    # m5x2 dashboard (m5x2-dashboard.py on 5556)
    Check(
        url="http://localhost:5556/",
        label="m5x2 (5556)",
        anchors=(
            "m5x2 AI Dashboard",
            "Claude Code",
            "Overview",
        ),
    ),
    # Personal dashboard (tools/personal-dashboard/dashboard.py on 5558)
    Check(
        url="http://localhost:5558/",
        label="Personal (5558)",
        anchors=(
            "PERSONAL DASHBOARD",
            "Project Bocking",
            "Points / Day",
        ),
    ),
]


def _fetch(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-smoke/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, body, ctype


def run_check(check: Check) -> tuple[bool, str]:
    """Return (passed, message)."""
    try:
        status, body, ctype = _fetch(check.url)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:  # noqa: BLE001 — catchall for smoke test robustness
        return False, f"{type(e).__name__}: {e}"

    if status != 200:
        return False, f"status {status} (expected 200)"

    if len(body) <= MIN_BODY_BYTES:
        return False, f"body {len(body)}B <= {MIN_BODY_BYTES}B"

    if check.expect_json:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return False, f"invalid JSON: {e}"
        if check.validator is not None:
            err = check.validator(data)
            if err:
                return False, err
        return True, f"200 OK, {len(body)}B, valid JSON"

    text = body.decode("utf-8", errors="replace")
    missing = [a for a in check.anchors if a not in text]
    if missing:
        return False, f"missing anchor(s): {missing}"

    return True, f"200 OK, {len(body)}B, anchors ok"


def main() -> int:
    results: list[tuple[Check, bool, str]] = []
    for check in CHECKS:
        ok, msg = run_check(check)
        tag = "PASS" if ok else "FAIL"
        print(f"{tag} {check.url}  [{check.label}]  {msg}")
        results.append((check, ok, msg))

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\nSummary: {passed}/{total} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
