#!/usr/bin/env python3
"""Sync Claude Code permission allowlists between ix and straylight.

Union-merge of permissions.allow in ~/.claude/settings.local.json on both
machines, minus tombstones. No symlinks, no shared files: each machine keeps
its own real file; this script (run from ix cron) converges them.

Semantics:
  merged_allow = (ix_allow | straylight_allow) - (ix_tombstones | straylight_tombstones)

To permanently revoke a permission, add its exact string to
~/.claude/perm-sync-removals.json (a JSON list) on EITHER machine; the next
sync removes it from both allowlists and propagates the tombstone.

Only permissions.allow is touched; every other key stays per-machine.
An unreachable straylight (asleep, offline) is a normal skip, exit 0.
Real errors (JSON parse failure, write failure) exit non-zero.
On success writes ~/.claude/perm-sync-last-success (epoch seconds) so the
weekly vault-health audit can assert freshness.
"""

import base64
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
LOCAL_SETTINGS = HOME / ".claude" / "settings.local.json"
LOCAL_TOMBSTONES = HOME / ".claude" / "perm-sync-removals.json"
LOCK_FILE = HOME / ".claude" / ".perm-sync.lock"
LAST_SUCCESS = HOME / ".claude" / "perm-sync-last-success"
BACKUP_DIR = HOME / ".claude" / "perm-sync-backups"
BACKUPS_KEPT = 30
REMOTE = "straylight"
REMOTE_PY = "/usr/bin/python3"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]

# Runs on straylight via python3 -c exec(b64). stdin: JSON payload
# {"allow": [...], "tombstones": [...]}. Re-reads the remote file fresh and
# re-unions so an approval landing mid-sync survives. Prints result JSON.
REMOTE_HELPER = r'''
import json, os, shutil, sys, tempfile, time
from pathlib import Path

payload = json.load(sys.stdin)
incoming = set(payload["allow"])
tombstones = set(payload["tombstones"])

settings_path = Path.home() / ".claude" / "settings.local.json"
tomb_path = Path.home() / ".claude" / "perm-sync-removals.json"

settings = {"permissions": {"allow": []}}
if settings_path.exists():
    settings = json.loads(settings_path.read_text())
current = set(settings.get("permissions", {}).get("allow", []))

merged = sorted((current | incoming) - tombstones, key=str.lower)
changed = merged != sorted(current, key=str.lower) or set(merged) != current

if changed:
    backup_dir = Path.home() / ".claude" / "perm-sync-backups"
    backup_dir.mkdir(exist_ok=True)
    if settings_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, backup_dir / f"settings.local.{stamp}.json")
        backups = sorted(backup_dir.glob("settings.local.*.json"))
        for old in backups[:-30]:
            old.unlink()
    settings.setdefault("permissions", {})["allow"] = merged
    mode = settings_path.stat().st_mode & 0o777 if settings_path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(settings_path.parent), prefix=".perm-sync-")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, settings_path)

tomb_path.write_text(json.dumps(sorted(tombstones, key=str.lower), indent=2) + "\n")
print(json.dumps({"changed": changed, "count": len(merged)}))
'''


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())  # parse error = real error, propagate


def write_local(settings, merged):
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LOCAL_SETTINGS, BACKUP_DIR / f"settings.local.{stamp}.json")
    for old in sorted(BACKUP_DIR.glob("settings.local.*.json"))[:-BACKUPS_KEPT]:
        old.unlink()
    settings.setdefault("permissions", {})["allow"] = merged
    mode = LOCAL_SETTINGS.stat().st_mode & 0o777
    tmp = LOCAL_SETTINGS.with_name(".perm-sync-tmp.json")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, LOCAL_SETTINGS)


def main():
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another sync is running, skipping")
        return 0

    # Fetch remote allowlist + tombstones in one hop; sentinel distinguishes
    # missing files (fresh machine, fine) from unreachable host.
    fetch = subprocess.run(
        ["ssh", *SSH_OPTS, REMOTE, REMOTE_PY, "-c",
         "'import json,sys;from pathlib import Path;"
         "h=Path.home()/\".claude\";"
         "r=lambda p,d: json.loads(p.read_text()) if p.exists() else d;"
         "print(json.dumps({"
         "\"allow\": r(h/\"settings.local.json\",{}).get(\"permissions\",{}).get(\"allow\",[]),"
         "\"tombstones\": r(h/\"perm-sync-removals.json\",[])}))'"],
        capture_output=True, text=True, timeout=30,
    )
    if fetch.returncode != 0:
        log(f"straylight unreachable or fetch failed, skipping: {fetch.stderr.strip()[:200]}")
        return 0  # expected when the laptop is asleep; not an error

    remote = json.loads(fetch.stdout)
    local_settings = read_json(LOCAL_SETTINGS, {"permissions": {"allow": []}})
    local_allow = set(local_settings.get("permissions", {}).get("allow", []))
    tombstones = set(read_json(LOCAL_TOMBSTONES, [])) | set(remote["tombstones"])

    merged = sorted((local_allow | set(remote["allow"])) - tombstones, key=str.lower)

    # Flag policy divergence: a synced-in entry the local deny list rejects.
    local_deny = set(local_settings.get("permissions", {}).get("deny", []))
    for entry in set(merged) & local_deny:
        log(f"warning: allow entry also in local deny (deny wins): {entry}")

    if set(merged) != local_allow or merged != sorted(local_allow, key=str.lower):
        # Re-read fresh right before writing to shrink the lost-update window.
        local_settings = read_json(LOCAL_SETTINGS, {"permissions": {"allow": []}})
        fresh = set(local_settings.get("permissions", {}).get("allow", []))
        merged = sorted((fresh | set(merged)) - tombstones, key=str.lower)
        write_local(local_settings, merged)
        log(f"local updated: {len(merged)} entries (+{len(set(merged) - local_allow)})")
    else:
        log(f"local unchanged ({len(merged)} entries)")
    LOCAL_TOMBSTONES.write_text(json.dumps(sorted(tombstones, key=str.lower), indent=2) + "\n")

    payload = json.dumps({"allow": merged, "tombstones": sorted(tombstones)})
    helper_b64 = base64.b64encode(REMOTE_HELPER.encode()).decode()
    push = subprocess.run(
        ["ssh", *SSH_OPTS, REMOTE, REMOTE_PY, "-c",
         f"'import base64;exec(base64.b64decode(\"{helper_b64}\"))'"],
        input=payload, capture_output=True, text=True, timeout=30,
    )
    if push.returncode != 0:
        # Remote may have applied the write before the connection dropped;
        # idempotent either way, but report it accurately.
        log(f"remote write unconfirmed: {push.stderr.strip()[:200]}")
        return 1
    result = json.loads(push.stdout)
    log(f"remote {'updated' if result['changed'] else 'unchanged'} ({result['count']} entries)")

    LAST_SUCCESS.write_text(str(int(time.time())) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
