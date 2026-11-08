#!/usr/bin/env python3
"""Sync Claude Code permission allowlists between ix and straylight.

Union-merge of permissions.allow in ~/.claude/settings.local.json across
machines, minus tombstones. No ssh: each machine runs this script from its
own launchd agent and exchanges state through a Syncthing-synced folder.

  scripts/.perm-sync/<host>.json   (gitignored; the vault Syncthing folder)
      {"host", "synced_at", "allow", "tombstones", "merged": {peer: synced_at}}

Each run: read the local allowlist + tombstones, read every peer's state file,
  merged_allow = (local_allow | peer_allows) - (local_tombstones | peer_tombstones)
write that into the local settings.local.json (only permissions.allow), then
publish this host's state file. Each host writes only its own file, so
Syncthing never produces conflict copies. Two runs (one per host) converge.

Why not ssh: until 2026-10-28 this ran on Ix only and sshed into Straylight;
inbound ssh to Straylight is blocked since then (MDM), so every run skipped
and the weekly vault-health check went stale. Straylight now runs its own
half: scripts/install-perm-sync.sh installs the agent there.

To permanently revoke a permission, add its exact string to
~/.claude/perm-sync-removals.json (a JSON list) on EITHER machine; tombstones
propagate through the state files and the entry leaves both allowlists.

Freshness signal (vault_health check "perm-sync"): synced_at in each host's
state file. A host's synced_at only advances when that host itself ran.
Real errors (local JSON parse failure, write failure) exit non-zero; an
unreadable peer file is logged and skipped.
"""

import argparse
import fcntl
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

BACKUPS_KEPT = 30
# In the vault-synced repo, gitignored. Not ~/files: on Ix that is an external
# volume launchd-run python3 may not write to (TCC, seen 2026-11-08).
STATE_DIR_DEFAULT = Path(__file__).resolve().parent / ".perm-sync"


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def default_host():
    """Exact names only: a loose match could make a third Mac overwrite ix.json."""
    h = socket.gethostname().split(".")[0].lower()
    if h == "ix":
        return "ix"
    if h.startswith("straylight"):
        return "straylight"
    raise SystemExit(f"unknown host {h!r}: pass --host explicitly")


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())  # parse error = real error, propagate


def atomic_write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".perm-sync-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def allow_of(settings):
    return set(settings.get("permissions", {}).get("allow", []))


def read_peers(state_dir, host):
    """Peer state files, skipping our own, Syncthing temp/conflict files and
    anything unparseable (a half-synced file is retried next hour)."""
    peers = {}
    if not state_dir.is_dir():
        return peers
    for p in sorted(state_dir.glob("*.json")):
        name = p.stem
        if name == host or name.startswith(".") or ".sync-conflict" in name:
            continue
        try:
            data = json.loads(p.read_text())
            peers[name] = {
                "allow": set(data["allow"]),
                "tombstones": set(data.get("tombstones", [])),
                "synced_at": data.get("synced_at"),
            }
        except (OSError, ValueError, KeyError, TypeError) as e:
            log(f"skipping unreadable peer state {p.name}: {e}")
    return peers


def write_settings(settings_path, settings, merged, backup_dir):
    if settings_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, backup_dir / f"settings.local.{stamp}.json")
        for old in sorted(backup_dir.glob("settings.local.*.json"))[:-BACKUPS_KEPT]:
            old.unlink()
        mode = settings_path.stat().st_mode & 0o777
    else:
        mode = 0o600
    settings.setdefault("permissions", {})["allow"] = merged
    atomic_write(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n", mode)


def sync(host, claude_dir, state_dir, now=None):
    settings_path = claude_dir / "settings.local.json"
    tomb_path = claude_dir / "perm-sync-removals.json"
    empty = {"permissions": {"allow": []}}

    settings = read_json(settings_path, empty)
    local_allow = allow_of(settings)
    peers = read_peers(state_dir, host)

    tombstones = set(read_json(tomb_path, []))
    incoming = set()
    for peer in peers.values():
        tombstones |= peer["tombstones"]
        incoming |= peer["allow"]
    merged_set = (local_allow | incoming) - tombstones

    for entry in merged_set & set(settings.get("permissions", {}).get("deny", [])):
        log(f"warning: allow entry also in local deny (deny wins): {entry}")

    current_list = settings.get("permissions", {}).get("allow", [])
    merged = sorted(merged_set, key=str.lower)
    # Compare sets: Claude Code may reorder its own file; only rewrite on content.
    if merged_set != set(current_list):
        # Re-read right before writing so an approval landing mid-run survives.
        settings = read_json(settings_path, empty)
        merged = sorted((allow_of(settings) | merged_set) - tombstones, key=str.lower)
        write_settings(settings_path, settings, merged, claude_dir / "perm-sync-backups")
        added = len(set(merged) - local_allow)
        removed = len(local_allow - set(merged))
        log(f"local updated: {len(merged)} entries (+{added} -{removed})")
    else:
        log(f"local unchanged ({len(merged)} entries)")

    tomb_text = json.dumps(sorted(tombstones, key=str.lower), indent=2) + "\n"
    if not tomb_path.exists() or tomb_path.read_text() != tomb_text:
        atomic_write(tomb_path, tomb_text)

    state = {
        "host": host,
        "synced_at": int(now if now is not None else time.time()),
        "allow": merged,
        "tombstones": sorted(tombstones, key=str.lower),
        "merged": {name: p["synced_at"] for name, p in sorted(peers.items())},
    }
    atomic_write(state_dir / f"{host}.json", json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    log(f"published {host}.json; peers merged: {', '.join(peers) or 'none'}")
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=None, help="this machine's name (default: from hostname)")
    ap.add_argument("--state-dir", type=Path, default=STATE_DIR_DEFAULT)
    ap.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    args = ap.parse_args(argv)
    host = args.host or default_host()

    args.claude_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.claude_dir / ".perm-sync.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another sync is running, skipping")
        return 0
    sync(host, args.claude_dir, args.state_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
