"""where — which timezone JM is in, from the device nearest him.

Device priority (JM, 2026-10-08): imago (iPhone) → fuchikoma (Android) →
straylight (laptop) → ix (home server). Each device keeps one report in the
Syncthing-synced vault, ~/vault/z_ibx/where/<device>.json:

    {"tz": "Pacific/Honolulu", "since": <epoch tz first seen>, "seen": <epoch>}

Macs report their own OS zone (report_self, from cron/launchd); phones are
reported by the Ix servers they browse (dtd :5560, janus-mobile :5561),
which map the Tailscale peer IP to the device (record_peer).

Resolution (resolve):
  1. An explicit /travel override (daytime.TRAVEL_FILE) always wins.
  2. Otherwise the highest-priority device with a report wins, UNLESS a
     lower-priority device's zone changed after that device was last seen:
     the lower one has observed a move the higher one hasn't reported yet
     (flew home before opening a phone page; phone left behind). Reports
     never expire: a fixed freshness window would fall through to Ix, which
     is always fresh and never moves (critique 2026-10-08).
  3. No reports at all → None (caller falls back to the OS zone).

Never-backwards rule (apply_hold): a switch whose local date is EARLIER than
the last resolved date is held until the new zone's date catches up, so a
westward flip can't rewind "today" and trip every `date != today` reset in
janus/dtd. The held result is persisted per host in ACTIVE_FILE, which also
lets dtd.sh read the zone with a zsh builtin instead of spawning python.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from zoneinfo import ZoneInfo

PRIORITY = ["imago", "fuchikoma", "straylight", "ix"]
WHERE_DIR = Path.home() / "vault" / "z_ibx" / "where"
ACTIVE_FILE = Path.home() / ".local" / "state" / "jm" / "active_tz.json"
SEEN_EVERY = 1800  # rewrite an unchanged report at most this often (git/Syncthing churn)


def _valid_tz(name) -> bool:
    try:
        ZoneInfo(str(name))
        return True
    except Exception:
        return False


def this_device() -> str:
    h = socket.gethostname().lower()
    for d in PRIORITY:
        if h.startswith(d):
            return d
    return h.split(".")[0]


# ── Reports ───────────────────────────────────────────────────────────────────

def _read(device: str) -> dict | None:
    try:
        r = json.loads((WHERE_DIR / f"{device}.json").read_text())
    except Exception:
        return None
    if isinstance(r, dict) and _valid_tz(r.get("tz")) and r.get("seen"):
        return r
    return None


def record(device: str, tz: str, now: float | None = None) -> bool:
    """Record that `device` is in `tz` now. Returns True if the file was written."""
    if device not in PRIORITY or not _valid_tz(tz):
        return False
    now = now or time.time()
    cur = _read(device)
    if cur and cur["tz"] == tz:
        if now - cur["seen"] < SEEN_EVERY:
            return False
        rep = {"tz": tz, "since": cur.get("since", cur["seen"]), "seen": now}
    else:
        rep = {"tz": tz, "since": now, "seen": now}
    WHERE_DIR.mkdir(parents=True, exist_ok=True)
    path = WHERE_DIR / f"{device}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep))
    os.replace(tmp, path)
    return True


def os_zone() -> str | None:
    """This Mac's configured zone name (follows location when macOS's
    automatic time zone is on)."""
    try:
        target = os.readlink("/etc/localtime")
        return target.split("zoneinfo/", 1)[1]
    except Exception:
        return None


def report_self(now: float | None = None) -> bool:
    tz = os_zone()
    return bool(tz) and record(this_device(), tz, now)


_TS_BINS = ["tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale"]
_peer_cache: dict[str, tuple[float, str | None]] = {}


def peer_device(ip: str) -> str | None:
    """Tailscale device name for a peer IP, or None. Only 100.64/10 CGNAT
    addresses qualify: behind a proxy remote_addr is 127.0.0.1 and must
    never be attributed to a device."""
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except Exception:
        return None
    if not (a == 100 and 64 <= b <= 127):
        return None
    hit = _peer_cache.get(ip)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    name = None
    for b_ in _TS_BINS:
        try:
            out = subprocess.run([b_, "whois", ip], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        for line in out.splitlines():
            if line.strip().startswith("Name:"):
                name = line.split(":", 1)[1].strip().split(".")[0].lower()
                break
        break
    _peer_cache[ip] = (time.time(), name)
    return name


def record_peer(ip: str, tz: str) -> str | None:
    """Server side of a phone beacon. Returns the device recorded, or None."""
    dev = peer_device(ip)
    if dev in PRIORITY and record(dev, tz):
        return dev
    return dev if dev in PRIORITY else None


# ── Resolution ────────────────────────────────────────────────────────────────

def pick(reports: dict[str, dict]) -> tuple[str, str] | None:
    """(device, tz) per the priority + observed-move rule. Pure; tested."""
    present = [d for d in PRIORITY if d in reports]
    if not present:
        return None
    best = present[0]
    for d in present[1:]:
        lo, hi = reports[d], reports[best]
        if lo["tz"] != hi["tz"] and lo.get("since", 0) > hi["seen"]:
            best = d
    return best, reports[best]["tz"]


def resolve() -> tuple[str, str] | None:
    reports = {d: r for d in PRIORITY if (r := _read(d))}
    return pick(reports)


def apply_hold(candidate: str, last: dict | None, now: dt.datetime | None = None) -> str:
    """Return the zone to use: `candidate`, unless switching to it would put
    today earlier than the last resolved date (then keep the old zone)."""
    if not last or last.get("tz") == candidate or not _valid_tz(last.get("tz")):
        return candidate
    now = now or dt.datetime.now(dt.timezone.utc)
    new_date = now.astimezone(ZoneInfo(candidate)).date().isoformat()
    return candidate if new_date >= last.get("date", "") else last["tz"]


_cache: dict = {"key": None, "tz": None}


def _mtimes() -> tuple:
    out = []
    for d in PRIORITY:
        try:
            out.append((WHERE_DIR / f"{d}.json").stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def active_tz() -> str | None:
    """The resolved, never-backwards zone name for this host, or None if no
    device has reported. Cheap when nothing changed (5 stats + a date check)."""
    if this_device() in ("straylight", "ix"):
        try:
            report_self()  # readlink + throttled write: a Mac's own zone is never stale
        except Exception:
            pass
    r = resolve() if _cache["key"] != (k := _mtimes()) else _cache["res"]
    _cache["key"], _cache["res"] = k, r
    if not r:
        return None
    try:
        last = json.loads(ACTIVE_FILE.read_text())
    except Exception:
        last = None
    tz = apply_hold(r[1], last)
    today = dt.datetime.now(ZoneInfo(tz)).date().isoformat()
    if not last or last.get("tz") != tz or last.get("date") != today:
        try:
            ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = ACTIVE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tz": tz, "date": today, "device": r[0]}))
            os.replace(tmp, ACTIVE_FILE)
        except Exception:
            pass
    return tz


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "report":
        report_self()
        print(active_tz() or "")
    elif a and a[0] == "status":
        for d in PRIORITY:
            r = _read(d)
            print(f"{d:11} {r['tz'] if r else '-':24} "
                  f"{'since ' + time.strftime('%m-%d %H:%M', time.localtime(r['since'])) if r else ''}"
                  f"{'  seen ' + time.strftime('%m-%d %H:%M', time.localtime(r['seen'])) if r else ''}")
        print("pick:", resolve(), " active:", active_tz())
    else:
        print(active_tz() or "")
