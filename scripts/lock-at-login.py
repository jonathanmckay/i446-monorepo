#!/usr/bin/python3
"""Lock the console right after auto-login (Ix, 2026-11-19).

Ix runs with FileVault off and auto-login on so that Tailscale and the
com.jm.* agents come back after any reboot. This agent closes the gap that
leaves: it locks the screen as soon as the session exists, so whoever powers
the machine on meets a password prompt instead of a desktop.

Run by ~/Library/LaunchAgents/com.jm.lock-at-login.plist (RunAtLoad). It only
acts within MAX_UPTIME of boot, so reloading the agent later does not lock a
session in use. `--force` skips that guard (for testing).

Each run appends one line to ~/Library/Logs/lock-at-login.log.
"""
import ctypes
import re
import subprocess
import sys
import time

MAX_UPTIME = 600  # seconds after boot during which a load counts as "login"
ATTEMPTS = 60
LOGIN_FRAMEWORK = "/System/Library/PrivateFrameworks/login.framework/Versions/Current/login"


def uptime():
    out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                         capture_output=True, text=True).stdout
    m = re.search(r"sec = (\d+)", out)
    return time.time() - int(m.group(1)) if m else 0.0


def is_locked():
    out = subprocess.run(["/usr/sbin/ioreg", "-n", "Root", "-d1"],
                         capture_output=True, text=True).stdout
    return '"CGSSessionScreenIsLocked"=Yes' in out


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} uptime={uptime():.0f}s {msg}", flush=True)


def main():
    force = "--force" in sys.argv[1:]
    if not force and uptime() > MAX_UPTIME:
        log("skipped: not a boot-time login")
        return 0
    if is_locked():
        log("already locked")
        return 0
    lock = ctypes.CDLL(LOGIN_FRAMEWORK).SACLockScreenImmediate
    for attempt in range(1, ATTEMPTS + 1):
        lock()
        time.sleep(1)
        if is_locked():
            log(f"LOCKED after {attempt} attempt(s)")
            return 0
    log(f"FAILED: screen still unlocked after {ATTEMPTS} attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
