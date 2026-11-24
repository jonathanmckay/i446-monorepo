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
    "slack i9": ("slack:XBOX",),
    "slack m5x2": ("slack:m5x2",),
    "teams": ("teams",),
}

# Channels the dtd badge counts, where that is narrower than the habit's full
# set (JM 2026-11-21: "count by ibx s897 based on the number of emails in my
# inbox"). ix's launchd counter cannot read chat.db (no Full Disk Access), so
# requiring iMessage blanked the s897 badge there for good. Auto-close is NOT
# narrowed: it still needs every channel in HABIT_CHANNELS pinged and empty.
BADGE_CHANNELS: dict[str, tuple[str, ...]] = {
    "ibx s897": ("gmail:gmail",),
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

    A habit listed in BADGE_CHANNELS is written as soon as its badge channels
    reported, and also carries ``badge_count`` / ``badge_ok`` over those
    channels alone; ``count`` / ``ok`` keep covering every channel (a channel
    that never reported counts as not ok), so zero_habits is unchanged.
    """
    per_channel: dict[str, int] = {}
    for it in items:
        ch = channel_of(it)
        if ch is not None:
            per_channel[ch] = per_channel.get(ch, 0) + 1
    habits: dict[str, dict] = {}
    for habit, chans in HABIT_CHANNELS.items():
        badge_chans = BADGE_CHANNELS.get(habit, chans)
        if any(c not in channel_ok for c in badge_chans):
            continue
        rec = {
            "count": sum(per_channel.get(c, 0) for c in chans),
            "ok": all(channel_ok.get(c) is True for c in chans),
        }
        if badge_chans != chans:
            rec["badge_count"] = sum(per_channel.get(c, 0) for c in badge_chans)
            rec["badge_ok"] = all(channel_ok.get(c) is True for c in badge_chans)
        habits[habit] = rec
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
                max_age: float = MAX_AGE_S, prefer_host: str | None = None) -> dict[str, dict]:
    """One fresh entry per habit across every host's file.

    Returns {habit: {"count", "ok", "ts", "host"}}. Stale files (older than
    ``max_age``) and unparsable ones are ignored. Two hosts may both report a
    habit (Straylight and ix both read Gmail) and they legitimately disagree:
    day-skips, the iMessage processed map and Gmail triage are per host. A
    fresh entry from ``prefer_host`` (dtd passes its own host) therefore wins
    outright, so the badge matches what opening ibx on this machine would
    show; otherwise the newest timestamp wins.

    Display only (auto-close reads the raw snapshot): a record carrying
    ``badge_count`` / ``badge_ok`` is returned with those AS ``count`` / ``ok``,
    so every reader (card_badge here, terminal dtd's inline check) badges from
    the narrowed channels without knowing about them.
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
            host = snap.get("host", "")
            cur = out.get(key)
            mine, cur_mine = host == prefer_host, bool(cur) and cur["host"] == prefer_host
            if cur is None or (mine and not cur_mine) or (mine == cur_mine and ts > cur["ts"]):
                if "badge_ok" in rec and "badge_count" in rec:
                    count, ok = rec["badge_count"], rec["badge_ok"]
                else:
                    count, ok = rec["count"], rec.get("ok")
                out[key] = {"count": int(count), "ok": bool(ok),
                            "ts": ts, "host": host}
    return out


def badge_text(rec: dict | None) -> str:
    """Row suffix for dtd: '✉3' when the ping succeeded, '' otherwise.

    Returned as the escaped envelope so dtd's embedded generator stays ASCII.
    """
    if not rec or not rec.get("ok"):
        return ""
    return "✉" + str(rec["count"])


# Habits the 😈 -1ibx block-ritual card totals: Slack and ibx for s897 and
# m5x2, i.e. the sum of the ✉N shown on those three cards (JM, 2026-11-22;
# was Slack-only incl. i9 from 2026-11-15). s897 has no Slack. i9 is out.
RITUAL_HABITS: tuple[str, ...] = ("ibx s897", "ibx m5x2", "slack m5x2")


def card_badge(name: str, badges: dict[str, dict]) -> str:
    """Badge for any dtd card name (annotations already stripped).

    An inbox habit gets its own count. The -1ibx ritual card gets the sums over
    RITUAL_HABITS, split by kind ("e13 s2": 13 email, 2 Slack): habits no host reports are left out, but one that reported
    a failed ping blanks the badge, because a partial sum would read as a
    full count.
    """
    if not badges:
        return ""
    key = norm(name.replace("\U0001F608", " "))
    if "-1ibx" in key.split():
        recs = [badges[h] for h in RITUAL_HABITS if h in badges]
        if not recs or not all(r.get("ok") for r in recs):
            return ""
        # e = the ibx habits, s = the Slack ones (JM 2026-11-24: "show it as
        # the number that is the sum, use s for slack e for email"). A kind no
        # host reports is left out, so the parts always add up to what the
        # habit cards show.
        parts = []
        for letter, kind in (("e", "ibx "), ("s", "slack ")):
            got = [int(badges[h]["count"]) for h in RITUAL_HABITS
                   if h.startswith(kind) and h in badges]
            if got:
                parts.append(letter + str(sum(got)))
        return " ".join(parts)
    return badge_text(badges.get(key))


def zero_habits(snap: dict) -> list[str]:
    """Habits this snapshot proves empty: pinged ok, nothing pending."""
    return [h for h, r in (snap.get("habits") or {}).items()
            if r.get("ok") and int(r.get("count", 0)) == 0]
