#!/usr/bin/env python3
"""outlook-cache-daemon — keep Janus-on-Ix's Outlook calendar fresh.

Straylight launchd agent (com.mckay.outlook-cache-daemon, KeepAlive).
Every 5 min: fetch today + tomorrow via outlook_client, rsync the cache
files to ix:~/.cache/janus/, where Janus runs with JANUS_OUTLOOK_CACHE_ONLY=1.

Why a daemon and not the old */5 cron (outlook-cache-push.sh, disabled
2026-10-04): every cron run started a fresh Agency calendar server and
killed it at exit, and each fresh server could re-run Microsoft sign-in, so
a lapsed token meant a sign-in prompt every 5 min. This process owns ONE
server for its lifetime, the way local Janus used to. When a sign-in tab is
already pending in Safari it skips the fetch rather than opening another
(agency_mcp.entra_login_pending). 2026-10-05.
"""

import datetime as dt
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import outlook_client as oc  # noqa: E402

mcp = oc.mcp
INTERVAL = 300
CACHE_DIR = Path.home() / ".cache" / "janus"


def log(msg):
    print(f"[{dt.datetime.now():%F %T}] {msg}", flush=True)


_drained = set()


def drain_server_pipes():
    """agency_mcp starts the server with stdout/stderr PIPEs nobody reads; in
    a long-lived owner a full pipe buffer blocks the server. Drain them."""
    for srv in list(getattr(mcp, "_servers", {}).values()):
        proc = srv.get("proc")
        if proc is None or proc.pid in _drained:
            continue
        _drained.add(proc.pid)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                threading.Thread(target=lambda p=pipe: [None for _ in p],
                                 daemon=True).start()


def fetch():
    tz = oc._tz()
    d0 = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    ok = 0
    for off in (0, 1):  # today + tomorrow (Janus's late-evening lookahead)
        s = d0 + dt.timedelta(days=off)
        try:
            n = len(oc.list_events(s, s + dt.timedelta(days=1), force=True))
            ok += 1
            log(f"{s:%F}: {n} events")
        except Exception as e:
            log(f"{s:%F}: {e}")
    drain_server_pipes()
    return ok


def push():
    files = sorted(str(p) for p in CACHE_DIR.glob("outlook-*.json"))
    if not files:
        return
    cmd = ["rsync", "-q", "-t", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10",
           *files, "ix:.cache/janus/"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        subprocess.run(["ssh", "-o", "BatchMode=yes", "ix", "mkdir -p ~/.cache/janus"],
                       capture_output=True, timeout=30)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log(f"rsync failed rc={r.returncode}: {r.stderr.strip()[:200]}")


def main():
    if mcp is None:
        log("agency_mcp unavailable; exiting")
        return 1
    log("start")
    while True:
        try:
            pending = mcp.entra_login_pending(force=True)
            if pending:
                log("Microsoft sign-in pending in Safari; skipping fetch")
            else:
                fetch()
            push()
        except Exception as e:
            log(f"cycle error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
