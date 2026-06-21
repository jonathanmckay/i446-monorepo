#!/usr/bin/env python3
"""dtd footer ticker — pushes a live elapsed timer into fzf's footer (~10 Hz).

dtd's task list is a static fzf buffer: a list *row* cannot tick sub-second
without reloading the whole list, which is heavy (spawns python+jq each time)
and fights j/k navigation. Instead we own the footer. fzf 0.65+ accepts actions
over an HTTP server (`--listen`); we POST `change-footer(...)` with a locally
computed elapsed time so nothing in the list is touched.

The Toggl API is polled only every few seconds to notice a timer change
(ctrl-s starts a new entry); the 10 Hz loop just re-renders from the cached
start timestamp. Best-effort: every error is swallowed — the timer is
non-critical eye-candy and must never wedge the picker.

Usage: dtd-ticker.py <port_file>
  <port_file> is written by fzf's `start` binding (echo $FZF_PORT > file).
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
# ~/.claude.json (same source as tg-tui) BEFORE toggl_api is imported.
if not os.environ.get("TOGGL_API_KEY"):
    try:
        cj = json.loads(Path("~/.claude.json").expanduser().read_text())
        os.environ["TOGGL_API_KEY"] = (
            cj.get("mcpServers", {}).get("toggl_server", {})
              .get("env", {}).get("TOGGL_API_KEY", ""))
    except Exception:
        pass

TICK = 0.1   # seconds between footer repaints
POLL = 3.0   # seconds between Toggl API reads (to catch a changed timer)


def _toggl_api():
    try:
        from mcp.toggl_server import toggl_api
        return toggl_api
    except Exception:
        return None


def fmt(elapsed: float) -> str:
    """Match tg-tui's running clock: 12m05.3s."""
    m, s = divmod(max(0, int(elapsed)), 60)
    frac = int((elapsed % 1) * 10)
    return f"{m}m{s:02d}.{frac}s"


def _read_port(port_file: Path):
    try:
        return int(port_file.read_text().strip())
    except Exception:
        return None


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
    api = _toggl_api()

    # Wait up to 5s for fzf to publish its port.
    for _ in range(50):
        if _read_port(port_file) is not None:
            break
        time.sleep(0.1)

    start = None      # epoch seconds of the running entry, or None when idle
    desc = ""
    last_poll = 0.0
    fails = 0

    while True:
        if not port_file.exists():   # picker exited and cleaned up
            return
        port = _read_port(port_file)
        now = time.time()

        if api and now - last_poll >= POLL:
            last_poll = now
            try:
                cur = api.get_current()
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
            else:
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
