"""Cross-process, client-side rate limiter for the Toggl API.

Toggl's free tier is ~a 1 req/sec leaky bucket; bursts come back as 402 (Payment
Required) or 429. The tricky part: `tg-fast` spawns a fresh PROCESS per /tg
command, so an in-process limiter can't coordinate across invocations. This
paces EVERY caller (the MCP server, toggl_cli/tg-fast, janus, 0t-fast) through
one shared token bucket kept in a small fcntl-locked state file, plus a shared
cooldown that all processes honour after a 402/429.

Design notes:
- Proactive PACING (token bucket) is what actually prevents tripping the limit;
  the post-402 cooldown is the reactive backstop.
- acquire() never blocks longer than MAX_WAIT: for an interactive command,
  proceeding (and maybe eating one 429, which _request retries) beats freezing
  the UI for the full cooldown. Background callers (janus) already skip via
  their own non-blocking guard, so they never sit in here.
- All knobs are env-overridable so an interactive context can tighten MAX_WAIT.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import fcntl  # POSIX advisory locks (macOS/Linux); Toggl tooling is mac-only here
except ImportError:  # pragma: no cover - non-POSIX fallback: no throttling
    fcntl = None

STATE = Path(os.environ.get("TOGGL_THROTTLE_STATE")
             or (Path.home() / ".cache" / "toggl-throttle.json"))
RATE = float(os.environ.get("TOGGL_THROTTLE_RATE", "1.0"))        # tokens per second
BURST = float(os.environ.get("TOGGL_THROTTLE_BURST", "3.0"))      # bucket capacity
MAX_WAIT = float(os.environ.get("TOGGL_THROTTLE_MAX_WAIT", "8.0"))  # never block longer
COOLDOWN = float(os.environ.get("TOGGL_THROTTLE_COOLDOWN", "60.0"))  # after a 402/429


def _load(fh) -> dict:
    fh.seek(0)
    try:
        return json.loads(fh.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _store(fh, d: dict) -> None:
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(d))
    fh.flush()


def _refill(d: dict, now: float) -> tuple[float, float]:
    """(tokens, cooldown_until) after refilling the bucket up to `now`."""
    tokens = float(d.get("tokens", BURST))
    last = float(d.get("last", now))
    cd = float(d.get("cooldown_until", 0.0))
    tokens = min(BURST, tokens + max(0.0, now - last) * RATE)
    return tokens, cd


def acquire(max_wait: float | None = None) -> float:
    """Block until a token is free (paced ~RATE/sec, shared across processes),
    honouring any post-402 cooldown. Caps the wait at MAX_WAIT so a CLI command
    can't freeze. Returns seconds actually waited. Best-effort: on any locking/IO
    failure it returns immediately rather than blocking the caller."""
    if fcntl is None:
        return 0.0
    cap = MAX_WAIT if max_wait is None else max_wait
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0.0
    waited = 0.0
    deadline = time.monotonic() + cap
    while True:
        try:
            fh = open(STATE, "a+")
        except OSError:
            return waited
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            d = _load(fh)
            now = time.time()
            tokens, cd = _refill(d, now)
            need = max(0.0, cd - now)
            if tokens < 1.0:
                refill_wait = (1.0 - tokens) / RATE if RATE > 0 else float("inf")
                need = max(need, refill_wait)
            remaining = deadline - time.monotonic()
            if need <= 0.0 or remaining <= 0.0:
                # A token is ready (or we hit the wait cap): consume and go.
                d["tokens"] = max(0.0, tokens) - 1.0
                d["last"] = now
                d["cooldown_until"] = cd
                _store(fh, d)
                return waited
            # Must wait: persist the refill, then drop the lock so peers proceed.
            d["tokens"] = tokens
            d["last"] = now
            d["cooldown_until"] = cd
            _store(fh, d)
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()
        nap = min(need, deadline - time.monotonic(), 0.5)
        if nap <= 0:
            return waited
        time.sleep(nap)
        waited += nap


def cooling_down() -> bool:
    """True while a post-402 cooldown is active (read from the shared state file).
    Lets EVERY process go quiet after a 402 — not just the one that hit it — so
    background pollers (janus) stop adding load and the limit can recover, rather
    than dribbling calls through and re-tripping it."""
    if fcntl is None:
        return False
    try:
        d = json.loads(STATE.read_text())
        return time.time() < float(d.get("cooldown_until", 0.0))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return False


def note_rate_limit(seconds: float | None = None) -> None:
    """Record that Toggl rate-limited us (402/429) so every process backs off
    until the shared cooldown clears. Best-effort."""
    if fcntl is None:
        return
    s = COOLDOWN if seconds is None else seconds
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        fh = open(STATE, "a+")
    except OSError:
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        d = _load(fh)
        d["cooldown_until"] = time.time() + s
        _store(fh, d)
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
