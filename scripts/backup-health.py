#!/usr/bin/env python3
"""backup-health — daily backup/sync health check, runs ON ix.

Verifies every leg of the backup story and writes a machine-readable verdict
to ~/vault/i447/backup-health.json (Syncthing carries it to Straylight, where
a Claude UserPromptSubmit hook surfaces failures in every session — silence
is also an alarm there: a stale verdict file means THIS check died).

Checks:
  vault_git     — origin/main tip < 6h old (Straylight autopush every 10 min)
  ix_branch     — ix's vault checkout on main, < 30 commits ahead
                  (regression 2026-07-28: stranded 6 weeks on a dream branch)
  monorepo_git  — origin/wip tip < 26h old (hourly auto-push)
  onedrive      — newest vault-backup-*.tar.zst < 8 days old and > 1 GB;
                  no .partial older than 36h  (needs FDA: the launchd job
                  must stay wrapped in `ssh localhost`, same as the backup
                  itself — TCC blocked plain launchd for 6 weeks unnoticed)
  syncthing     — process alive; z_ibx/task-queue.json (written on Straylight,
                  synced here) < 36h old proves the pipe actually flows

Launchd: com.jm.backup-health (daily 08:40, ssh-localhost wrapper).
On failure: macOS notification on ix + failures listed in the JSON.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import subprocess
import time

VAULT = os.path.expanduser("~/vault")
MONOREPO = os.path.expanduser("~/i446-monorepo")
ONEDRIVE_BACKUPS = os.path.expanduser("~/OneDrive/vault-backups")
HEALTH_JSON = os.path.join(VAULT, "i447", "backup-health.json")

HOURS = 3600.0


def run(cmd, timeout=60, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def commit_age_hours(repo, ref):
    rc, out, _ = run(["git", "-C", repo, "log", "-1", "--format=%ct", ref])
    if rc != 0 or not out:
        return None
    return (time.time() - int(out)) / HOURS


def check_vault_git():
    run(["git", "-C", VAULT, "fetch", "origin", "-q"], timeout=120)
    age = commit_age_hours(VAULT, "origin/main")
    if age is None:
        return "vault_git: cannot read origin/main"
    if age > 6:
        return f"vault_git: origin/main tip is {age:.1f}h old (Straylight autopush stale?)"
    return None


def check_ix_branch():
    rc, branch, _ = run(["git", "-C", VAULT, "branch", "--show-current"])
    if rc != 0:
        return "ix_branch: git failed"
    if branch != "main":
        return f"ix_branch: vault checkout on '{branch}', not main (autopush stranded)"
    rc, ahead, _ = run(["git", "-C", VAULT, "rev-list", "--count", "origin/main..HEAD"])
    if rc == 0 and ahead and int(ahead) > 30:
        return f"ix_branch: {ahead} unpushed commits piling up (autopush not pushing)"
    return None


def check_monorepo_git():
    run(["git", "-C", MONOREPO, "fetch", "origin", "-q"], timeout=120)
    age = commit_age_hours(MONOREPO, "origin/wip")
    if age is None:
        return "monorepo_git: cannot read origin/wip"
    if age > 26:
        return f"monorepo_git: origin/wip tip is {age:.1f}h old (hourly auto-push stale)"
    return None


def check_onedrive():
    snaps = sorted(glob.glob(os.path.join(ONEDRIVE_BACKUPS, "vault-backup-*.tar.zst")))
    if not snaps:
        return f"onedrive: no snapshots in {ONEDRIVE_BACKUPS} (TCC? dir missing?)"
    newest = max(snaps, key=os.path.getmtime)
    age_days = (time.time() - os.path.getmtime(newest)) / 86400
    size_gb = os.path.getsize(newest) / 1e9
    if age_days > 8:
        return f"onedrive: newest snapshot {os.path.basename(newest)} is {age_days:.1f} days old"
    if size_gb < 1:
        return f"onedrive: newest snapshot only {size_gb:.2f} GB — looks truncated"
    for p in glob.glob(os.path.join(ONEDRIVE_BACKUPS, "*.partial")):
        if (time.time() - os.path.getmtime(p)) / HOURS > 36:
            return f"onedrive: stale partial {os.path.basename(p)} (backup died mid-write)"
    return None


def check_syncthing():
    rc, _, _ = run(["pgrep", "-x", "syncthing"])
    if rc != 0:
        return "syncthing: process not running on ix"
    probe = os.path.join(VAULT, "z_ibx", "task-queue.json")
    if os.path.exists(probe):
        age = (time.time() - os.path.getmtime(probe)) / HOURS
        if age > 36:
            return f"syncthing: task-queue.json last synced {age:.0f}h ago — pipe from Straylight looks dead"
    return None


CHECKS = [
    ("vault_git", check_vault_git),
    ("ix_branch", check_ix_branch),
    ("monorepo_git", check_monorepo_git),
    ("onedrive", check_onedrive),
    ("syncthing", check_syncthing),
]


def main():
    failures = []
    results = {}
    for name, fn in CHECKS:
        try:
            err = fn()
        except Exception as e:
            err = f"{name}: check crashed ({type(e).__name__}: {e})"
        results[name] = err or "ok"
        if err:
            failures.append(err)

    verdict = {
        "ok": not failures,
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "host": "ix",
        "failures": failures,
        "checks": results,
    }
    os.makedirs(os.path.dirname(HEALTH_JSON), exist_ok=True)
    with open(HEALTH_JSON, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=1)

    if failures:
        msg = "; ".join(failures)[:180].replace('"', "'")
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "⚠️ backup-health: {len(failures)} failing"'],
                       capture_output=True, timeout=10)
        print("FAIL:", *failures, sep="\n  ")
        return 1
    print("ok: all %d checks passed" % len(CHECKS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
