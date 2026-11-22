#!/usr/bin/env python3
"""daily_watch.py — daily checks on Ix that raise an alert instead of waiting
for the Saturday review (JM 2026-11-21: "more of a daily check or something
that runs automatically, then filters up to [the weekly review] if failing").

Appends to ~/vault/z_ibx/alerts.jsonl, which vault_health.py's `alerts` check
reads over 7 days. Each condition is re-emitted as ONE summary line per reason
on every daily run while it holds: check_alerts drops anything older than 7
days and a dismissal hides everything up to its date, so a one-shot alert
would let a still-broken thing fall out of the review.

Watches:
  inbox_counts / stale          this host's z_ibx/inbox-counts-<host>.json is
                                missing or over an hour old (the launchd
                                counter com.jm.ibx-counts is dead)
  inbox_counts / stuck_failing  a habit's dtd badge has been unavailable on two
                                consecutive daily runs: its display flag
                                (badge_ok, else ok) is false, or the habit has
                                dropped out of the snapshot (2026-11-21: the
                                ibx s897 badge was blank for nine days)
  agent_claims / stale          an open dtd claim with no activity for 7+ days.
                                Claims outlive their session on purpose (`cc
                                <title>` resumes the thread), so this never
                                releases anything; it asks JM to resume or
                                release.

Usage: daily_watch.py [--dry-run]
Schedule: launchd com.jm.daily-watch, 04:35 daily on Ix (scripts/launchd/). Not
cron: `crontab -` hung on Ix when this was added, and the weekly launchd check
then covers this job too. A crash leaves a non-zero exit for that check.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
MONO = HOME / "i446-monorepo"
ALERTS = HOME / "vault/z_ibx/alerts.jsonl"
STATE = HOME / ".local/state/jm/daily-watch.json"
CLAIMS = MONO / "tools/did/agent_claims.py"

SNAPSHOT_MAX_AGE = 3600
CLAIM_MAX_IDLE = 7 * 86400
DETAIL_MAX = 118          # check_alerts shows the first 120 chars of a detail


def _names(items: list[str]) -> str:
    return ", ".join(items)


def _fit(head: str, items: list[str]) -> str:
    """`head: a, b, c` cut to DETAIL_MAX with a `+N more` tail."""
    shown: list[str] = []
    for i, it in enumerate(items):
        rest = len(items) - i - 1
        cand = f"{head}: {_names(shown + [it])}" + (f" +{rest} more" if rest else "")
        if len(cand) > DETAIL_MAX and shown:
            return f"{head}: {_names(shown)} +{len(items) - len(shown)} more"
        shown.append(it)
    return f"{head}: {_names(shown)}"[:DETAIL_MAX]


def _display_ok(rec: dict) -> bool:
    return bool(rec.get("badge_ok")) if "badge_ok" in rec else bool(rec.get("ok"))


def decide(snapshot: dict | None, claims: dict | None, prev: dict, now: float) -> tuple[list[dict], dict]:
    """Pure: ([{tool, reason, detail}], new state). `snapshot` is this host's
    inbox-counts file (None when missing/unreadable); `claims` is
    `agent_claims.py list` (None when it could not be read: no claim alert,
    never a false all-clear written into state)."""
    alerts: list[dict] = []
    known = set(prev.get("known", []))
    was_failing = set(prev.get("failing", []))
    state = {"known": sorted(known), "failing": sorted(was_failing)}

    ts = (snapshot or {}).get("ts")
    if not isinstance(ts, (int, float)) or now - ts > SNAPSHOT_MAX_AGE:
        age = "missing" if not isinstance(ts, (int, float)) else f"{int((now - ts) // 3600)}h old"
        alerts.append({"tool": "inbox_counts", "reason": "stale",
                       "detail": f"inbox-counts snapshot {age}: com.jm.ibx-counts is not writing it; dtd badges are blank"})
        # keep known/failing as they were: a dead counter says nothing about habits
    else:
        habits = {h: r for h, r in (snapshot.get("habits") or {}).items() if isinstance(r, dict)}
        known |= {h for h, r in habits.items() if _display_ok(r)}
        failing = {h for h in set(habits) | known if h not in habits or not _display_ok(habits[h])}
        stuck = sorted(failing & was_failing)
        if stuck:
            alerts.append({"tool": "inbox_counts", "reason": "stuck_failing",
                           "detail": _fit(f"{len(stuck)} inbox badge(s) failing on 2+ daily runs", stuck)})
        state = {"known": sorted(known), "failing": sorted(failing)}

    if claims is not None:
        stale = []
        for rec in claims.values():
            if not isinstance(rec, dict) or rec.get("done") or rec.get("state") in ("released", "done"):
                continue
            try:
                last = max(float(rec.get("claimed_at") or 0), float(rec.get("state_ts") or 0))
            except (TypeError, ValueError):
                continue
            if last and now - last > CLAIM_MAX_IDLE:
                stale.append((now - last, str(rec.get("task", "?"))))
        if stale:
            stale.sort(reverse=True)
            items = [f"{name[:22].rstrip()} ({int(idle // 86400)}d)" for idle, name in stale]
            alerts.append({"tool": "agent_claims", "reason": "stale",
                           "detail": _fit(f"{len(stale)} dtd claim(s) idle 7+ days, resume or release", items)})
    return alerts, state


def _snapshot() -> dict | None:
    sys.path.insert(0, str(MONO / "tools/ibx"))
    try:
        import inbox_counts
        return json.loads(inbox_counts.snapshot_path().read_text())
    except Exception:  # noqa: BLE001 — missing, torn or unimportable all mean "no snapshot"
        return None


def _claims() -> dict | None:
    try:
        r = subprocess.run([sys.executable, str(CLAIMS), "list"], capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    now = time.time()
    try:
        prev = json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        prev = {}
    alerts, state = decide(_snapshot(), _claims(), prev, now)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    host = socket.gethostname().split(".")[0]
    lines = [json.dumps({"ts": stamp, "host": host, "tool": a["tool"], "severity": "warning",
                         "reason": a["reason"], "detail": a["detail"]}, ensure_ascii=False) for a in alerts]
    if dry:
        print("\n".join(lines) or "no alerts")
        print("state:", json.dumps(state, ensure_ascii=False))
        return 0
    if lines:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with ALERTS.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
