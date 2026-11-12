"""inbox_counts — per-habit pending counts for the daily inbox cards.

ibx0 writes one snapshot per fetch cycle to the Syncthing-synced vault
(~/vault/z_ibx/inbox-counts-<host>.json); dtd reads every host's file and
badges each inbox habit row with its pending count (``ibx i9 (4) [20] ✉3``);
ibx0 auto-closes a habit whose every channel was pinged successfully and
came back empty (2026-11-12, JM: "put the number of messages that are pending
on the daily tasks and close if there is a successful ping and the number is
zero").

A *channel* is one fetch target (a Gmail account, a Slack workspace, iMessage,
Outlook, Teams). A *habit* is a 0n daily card; most map to one channel,
``ibx s897`` spans two (personal Gmail + iMessage). A habit appears in a
snapshot only when every one of its channels reported this cycle, and is
``ok`` only when every one of them succeeded — a host that cannot reach a
channel (ix has no Outlook) simply never writes that habit, so it can neither
badge nor close it. The old behaviour closed all six habits on the first
inbox zero of the day regardless of which sources had actually answered.

Pure: no network, no console. Everything that touches a fetcher lives in
ibx0.py.
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import time
from pathlib import Path

HABIT_CHANNELS: dict[str, tuple[str, ...]] = {
    "ibx m5x2": ("gmail:m5c7",),
    "ibx s897": ("gmail:gmail", "imsg"),
    "ibx i9": ("outlook",),
    "slack i9": ("slack:github",),
    "slack m5x2": ("slack:m5x2",),
    "teams": ("teams",),
}

ALL_CHANNELS: frozenset[str] = frozenset(c for cs in HABIT_CHANNELS.values() for c in cs)

Z_IBX = Path.home() / "vault" / "z_ibx"
FILE_PREFIX = "inbox-counts-"
# A badge older than this is not shown and never closes anything: a dead
# poller must not keep reporting yesterday's zero.
MAX_AGE_S = 15 * 60


def norm(name: str) -> str:
    """Bridge the sheet spelling ("ibx - s897") and Todoist ("ibx s897")."""
    return re.sub(r"\s+", " ", name.lower().replace(" - ", " ")).strip()


def host_slug(hostname: str | None = None) -> str:
    """Same rule as dtd.sh's DTD_HOSTSLUG so the per-host files line up."""
    h = (hostname if hostname is not None else socket.gethostname()).lower()
    return re.sub(r"[^a-z0-9]+", "-", h).strip("-")[:24] or "host"


def channel_of(item: dict) -> str | None:
    """Fetch target an ibx0 queue item came from, or None if unmapped."""
    t = item.get("type")
    d = item.get("_data") or {}
    if t == "email":
        acct = (d.get("email") or {}).get("_account")
        return f"gmail:{acct}" if acct else None
    if t == "imsg":
        return "imsg"
    if t == "slack":
        ws = d.get("workspace")
        return f"slack:{ws}" if ws else None
    if t in ("outlook", "teams"):
        return t
    return None


def build_snapshot(items, channel_ok: dict, host: str | None = None, now: float | None = None) -> dict:
    """Count pending items per habit.

    ``channel_ok`` maps channel → True (fetched fine), False (fetch failed) or
    None (started, never finished). A habit is written only when all of its
    channels have an entry; ``ok`` requires every one to be True. Counts are
    reported even for a failed channel (the items that did arrive are still
    pending), but ``ok`` False stops dtd from badging and ibx0 from closing.
    """
    per_channel: dict[str, int] = {}
    for it in items:
        ch = channel_of(it)
        if ch is not None:
            per_channel[ch] = per_channel.get(ch, 0) + 1
    habits: dict[str, dict] = {}
    for habit, chans in HABIT_CHANNELS.items():
        if any(c not in channel_ok for c in chans):
            continue
        habits[habit] = {
            "count": sum(per_channel.get(c, 0) for c in chans),
            "ok": all(channel_ok.get(c) is True for c in chans),
        }
    return {
        "ts": now if now is not None else time.time(),
        "host": host or host_slug(),
        "habits": habits,
    }


def snapshot_path(host: str | None = None, z_ibx: Path | None = None) -> Path:
    return (z_ibx or Z_IBX) / f"{FILE_PREFIX}{host or host_slug()}.json"


def write_snapshot(snap: dict, z_ibx: Path | None = None) -> Path:
    """Atomic write (rename) so a reader never sees a torn file."""
    p = snapshot_path(snap.get("host"), z_ibx)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, p)
    return p


def read_badges(z_ibx: Path | None = None, now: float | None = None,
                max_age: float = MAX_AGE_S) -> dict[str, dict]:
    """Newest fresh entry per habit across every host's file.

    Returns {habit: {"count", "ok", "ts", "host"}}. Stale files (older than
    ``max_age``) and unparsable ones are ignored. Two hosts may both report a
    habit (Straylight and ix both read Gmail); the newer timestamp wins.
    """
    now = time.time() if now is None else now
    out: dict[str, dict] = {}
    pattern = str((z_ibx or Z_IBX) / f"{FILE_PREFIX}*.json")
    for fp in glob.glob(pattern):
        try:
            with open(fp, encoding="utf-8") as f:
                snap = json.load(f)
        except Exception:
            continue
        if not isinstance(snap, dict):
            continue
        ts = snap.get("ts")
        if not isinstance(ts, (int, float)) or now - ts > max_age or ts > now + 300:
            continue
        for habit, rec in (snap.get("habits") or {}).items():
            if not isinstance(rec, dict) or "count" not in rec:
                continue
            key = norm(habit)
            cur = out.get(key)
            if cur is None or ts > cur["ts"]:
                out[key] = {"count": int(rec["count"]), "ok": bool(rec.get("ok")),
                            "ts": ts, "host": snap.get("host", "")}
    return out


def badge_text(rec: dict | None) -> str:
    """Row suffix for dtd: '✉3' when the ping succeeded, '' otherwise.

    Returned as the escaped envelope so dtd's embedded generator stays ASCII.
    """
    if not rec or not rec.get("ok"):
        return ""
    return "✉" + str(rec["count"])


def zero_habits(snap: dict) -> list[str]:
    """Habits this snapshot proves empty: pinged ok, nothing pending."""
    return [h for h, r in (snap.get("habits") or {}).items()
            if r.get("ok") and int(r.get("count", 0)) == 0]
