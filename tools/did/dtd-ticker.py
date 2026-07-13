#!/usr/bin/env python3
"""dtd footer ticker — pushes a live elapsed timer into fzf's footer (~10 Hz).

dtd's task list is a static fzf buffer: a list *row* cannot tick sub-second
without reloading the whole list, which is heavy (spawns python+jq each time)
and fights j/k navigation. Instead we own the footer. fzf 0.65+ accepts actions
over an HTTP server (`--listen`); we POST `change-footer(...)` with a locally
computed elapsed time so nothing in the list is touched.

dtd's own start/complete bindings write the running entry to a local timer file
(`desc<TAB>start_epoch`, emptied on stop) the instant they fire. The ticker
watches that file every tick (a cheap stat, no network) so a dtd-initiated
start/switch/stop shows in the footer within ~0.1s. The Toggl API poll is kept
only to RECONCILE externally-started timers (e.g. /tg, /do, janus) when the
local file is idle; on its own it lagged dtd-initiated changes by up to POLL.

Best-effort: every error is swallowed — the timer is non-critical eye-candy and
must never wedge the picker.

Usage: dtd-ticker.py <port_file> [timer_file]
  <port_file> is written by fzf's `start` binding (echo $FZF_PORT > file).
  [timer_file] is dtd's $DTD_TIMER (desc<TAB>start_epoch); optional for back-compat.
Exits when the port file disappears (picker gone) or POSTs fail persistently.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path("~/i446-monorepo").expanduser()))
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

# config.py reads TOGGL_API_KEY from the env at import time, so seed it from
# ~/.claude.json (same source as janus) BEFORE toggl_api is imported.
if not os.environ.get("TOGGL_API_KEY"):
    try:
        cj = json.loads(Path("~/.claude.json").expanduser().read_text())
        os.environ["TOGGL_API_KEY"] = (
            cj.get("mcpServers", {}).get("toggl_server", {})
              .get("env", {}).get("TOGGL_API_KEY", ""))
    except Exception:
        pass

TICK = 0.1   # seconds between footer repaints
# The footer clock is extrapolated locally every TICK, so a Toggl read is only
# needed to NOTICE an externally-changed timer. 3s meant 20 req/min per open
# picker — the single biggest contributor to Toggl 429s. 12s detection lag is
# imperceptible for a footer and cuts this source ~75%.
POLL = 12.0  # seconds between Toggl API reads (to catch a changed timer)


def _toggl_api():
    try:
        from mcp.toggl_server import toggl_api
        return toggl_api
    except Exception:
        return None


def fmt(elapsed: float) -> str:
    """Match janus's running clock: 12m05.3s."""
    m, s = divmod(max(0, int(elapsed)), 60)
    frac = int((elapsed % 1) * 10)
    return f"{m}m{s:02d}.{frac}s"


def _read_port(port_file: Path):
    try:
        return int(port_file.read_text().strip())
    except Exception:
        return None


def _read_timer_file(timer_file: Path):
    """Read dtd's $DTD_TIMER. Returns (start_epoch|None, desc, mtime|None).

    Format is `desc<TAB>start_epoch`; an empty file means dtd is idle (stopped).
    mtime lets the caller detect a change cheaply without re-parsing every tick.
    """
    if timer_file is None:
        return None, "", None
    try:
        mtime = timer_file.stat().st_mtime
    except Exception:
        return None, "", None
    try:
        raw = timer_file.read_text().strip()
    except Exception:
        return None, "", mtime
    if not raw:
        return None, "", mtime
    parts = raw.split("\t")
    desc = parts[0].replace("(", "").replace(")", "")
    try:
        start = float(parts[1]) if len(parts) > 1 else None
    except Exception:
        start = None
    return start, desc, mtime


def _post(port: int, action: str) -> bool:
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}", data=action.encode("utf-8"), method="POST")
        key = os.environ.get("FZF_API_KEY")
        if key:
            req.add_header("X-API-Key", key)
        urllib.request.urlopen(req, timeout=0.5).read()
        return True
    except Exception:
        return False


def main() -> None:
    if len(sys.argv) < 2:
        return
    port_file = Path(sys.argv[1])
    timer_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    api = _toggl_api()

    # Wait up to 5s for fzf to publish its port.
    for _ in range(50):
        if _read_port(port_file) is not None:
            break
        time.sleep(0.1)

    start = None      # epoch seconds of the running entry, or None when idle
    desc = ""
    last_poll = 0.0
    last_timer_mtime = None
    fails = 0

    while True:
        if not port_file.exists():   # picker exited and cleaned up
            return
        port = _read_port(port_file)
        now = time.time()

        # Fast local signal: dtd's start/complete bindings write $DTD_TIMER the
        # instant they fire, so a dtd-initiated change shows within one TICK
        # instead of waiting on the Toggl poll below.
        fstart, fdesc, fmtime = _read_timer_file(timer_file)
        if fmtime is not None and fmtime != last_timer_mtime:
            last_timer_mtime = fmtime
            start, desc = fstart, fdesc   # fstart None => dtd is idle

        if api and now - last_poll >= POLL:
            last_poll = now
            try:
                # Shared cache: ride janus's (or any other poller's) fetch
                # instead of hitting Toggl every POLL. The elapsed clock is
                # extrapolated locally, so a cache up to TTL old still renders an
                # exact footer; a started/stopped timer invalidates it at once.
                cur = api.get_current_cached()
            except Exception:
                cur = None
            if cur and cur.get("start"):
                try:
                    st = datetime.fromisoformat(str(cur["start"]).replace("Z", "+00:00"))
                    start = st.timestamp()
                except Exception:
                    start = None
                # Strip parens so they can't terminate the change-footer() action.
                desc = (cur.get("description") or "").replace("(", "").replace(")", "")
            elif start is None or now - start > POLL:
                # Toggl says idle. Only clear if there's no FRESH local timer:
                # right after a dtd start the shared cache can still be stale
                # (up to its TTL), and we must not clobber the just-applied
                # local start. A timer older than POLL is safe to reconcile.
                start, desc = None, ""

        if start is not None:
            body = f"▶ {fmt(time.time() - start)}"
            if desc:
                body += f" · {desc}"
        else:
            body = "▶ (idle)"

        if port is not None:
            ok = _post(port, f"change-footer({body})")
            fails = 0 if ok else fails + 1
            if fails > 30:   # ~3s of failed POSTs → picker is gone
                return
        time.sleep(TICK)


if __name__ == "__main__":
    main()
