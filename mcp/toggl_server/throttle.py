"""Cross-process, client-side rate limiter for the Toggl API.

Toggl enforces two different limits (engineering.toggl.com/docs/track, 2026-10):

1. A ~1 req/sec leaky bucket per API token -> HTTP 429. Short; the shared
   `cooldown_until` (COOLDOWN, 60s) covers it.
2. An HOURLY sliding-window quota per BUCKET -> HTTP 402 with "Your quota will
   reset in N seconds". User-scoped requests (`/api/v9/me/*`) get 30/hr on
   EVERY plan (Toggl offers no higher quota for them, whatever the 402 body's
   "upgrade" text says). Workspace-scoped requests (`/workspaces/{id}/*`, the
   Reports API) get the plan's quota: 600/hr on Premium. A 402 on one bucket
   says nothing about the other, so the quota cooldown is tracked PER BUCKET
   (`quota_until_user`, `quota_until_ws`) -- before 2026-10-20 one exhausted
   /me/current bucket parked every write too, with 580 workspace requests
   still available.

The tricky part: `tg-fast` spawns a fresh PROCESS per /tg command, so an
in-process limiter can't coordinate across invocations. This paces EVERY
caller (the MCP server, toggl_cli/tg-fast, janus, 0t-fast) through one shared
token bucket kept in a small fcntl-locked state file, plus the shared
cooldowns that all processes honour after a 402/429.

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
import re
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
# Toggl's free-tier HOURLY cap says exactly how long it lasts ("Your quota will
# reset in 653 seconds"). Honouring that beats the 60s default, which had every
# poller re-trip the cap once a minute for the rest of the hour (2026-10-16).
MAX_COOLDOWN = float(os.environ.get("TOGGL_THROTTLE_MAX_COOLDOWN", "3600.0"))
RESET_MARGIN = 5.0  # s past the server's reset estimate before the first retry
_RESET_RE = re.compile(r"reset in (\d+) seconds", re.IGNORECASE)

# Toggl's hourly quota buckets. "user" = /api/v9/me/* (30/hr on every plan);
# "ws" = everything workspace-scoped, including the Reports API.
BUCKETS = ("user", "ws")


def bucket_for(path: str) -> str:
    """Which hourly-quota bucket an API path is charged to."""
    return "user" if (path or "").startswith("/me") else "ws"


def _quota_key(bucket: str) -> str:
    return f"quota_until_{bucket}"


def parse_reset_seconds(body: str | bytes | None) -> float | None:
    """Seconds until the quota resets, from a 402 body; None when absent."""
    if not body:
        return None
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    m = _RESET_RE.search(body)
    return float(m.group(1)) if m else None


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


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return {}


def _until_values(d: dict, bucket: str | None) -> list:
    """The cooldown deadlines that apply: the global 429 one always, plus the
    named bucket's quota deadline (or EVERY bucket's when bucket is None, the
    conservative legacy reading used by callers that don't say which bucket
    they're about to hit)."""
    vals = [float(d.get("cooldown_until", 0.0) or 0.0)]
    for b in (BUCKETS if bucket is None else (bucket,)):
        vals.append(float(d.get(_quota_key(b), 0.0) or 0.0))
    return vals


def cooling_down(bucket: str | None = None) -> bool:
    """True while a cooldown that applies to `bucket` is active (read from the
    shared state file): the global 429 cooldown, or that bucket's hourly-quota
    cooldown. bucket=None = any. Lets EVERY process go quiet after a 402/429,
    not just the one that hit it, so background pollers (janus) stop adding
    load and the limit can recover rather than dribbling calls through and
    re-tripping it."""
    if fcntl is None:
        return False
    return time.time() < max(_until_values(_read_state(), bucket))


def cooldown_remaining(bucket: str | None = None) -> float:
    """Seconds left on the cooldown that applies to `bucket` (0.0 when clear).
    bucket=None = the longest across all buckets."""
    if fcntl is None:
        return 0.0
    return max(0.0, max(_until_values(_read_state(), bucket)) - time.time())


def quota_remaining(bucket: str) -> float:
    """Seconds left on `bucket`'s hourly-quota (402) cooldown alone, ignoring
    the global 429 cooldown. 0.0 when that bucket is open. A caller about to
    hit an exhausted bucket should not send: the request is a guaranteed 402
    (and, before 2026-10-20, acquire()'s 8s wait-cap let exactly those doomed
    requests through)."""
    if fcntl is None:
        return 0.0
    d = _read_state()
    return max(0.0, float(d.get(_quota_key(bucket), 0.0) or 0.0) - time.time())


def note_quota_reset(estimate: float | None, bucket: str | None = None) -> None:
    """Back off for the server's own reset estimate (the hourly-cap 402 body,
    see parse_reset_seconds), padded by RESET_MARGIN, never below the default
    COOLDOWN and never above MAX_COOLDOWN. None = no estimate: the default.
    With a bucket, only that bucket's quota cooldown is armed (a /me 402 must
    not park workspace writes); without one, the legacy global cooldown."""
    if estimate is None:
        secs = None
    else:
        secs = min(MAX_COOLDOWN, max(COOLDOWN, estimate + RESET_MARGIN))
    note_rate_limit(secs, bucket=bucket)


def note_rate_limit(seconds: float | None = None, bucket: str | None = None) -> None:
    """Record that Toggl rate-limited us (402/429) so every process backs off
    until the shared cooldown clears. bucket=None arms the global (429)
    cooldown; a bucket name arms that bucket's quota cooldown. Best-effort."""
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
        d["cooldown_until" if bucket is None else _quota_key(bucket)] = time.time() + s
        _store(fh, d)
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
