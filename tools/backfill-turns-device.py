#!/usr/bin/env python3
"""Backfill `device` field in ~/.claude/timing/turns.jsonl.

Adds `"device":"<hostname>"` to every line missing the field. Runs on the
local machine — hostname defaults to resolve-device.py's output. Preserves
lines with malformed JSON numbers (`:.155983` style) via byte-level
string injection rather than parse/reserialize.

Usage:
    python3 backfill-turns-device.py            # uses resolve-device.py output
    python3 backfill-turns-device.py straylight # force name
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMING = Path.home() / ".claude" / "timing" / "turns.jsonl"
RESOLVER = Path.home() / ".claude" / "resolve-device.py"


def resolve_device() -> str:
    if len(sys.argv) >= 2:
        return sys.argv[1]
    if RESOLVER.exists():
        try:
            out = subprocess.run(
                ["python3", str(RESOLVER)],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return os.uname().nodename.split(".")[0].lower()


def main() -> None:
    if not TIMING.exists():
        print(f"not found: {TIMING}")
        sys.exit(1)
    device = resolve_device()
    backup = TIMING.with_suffix(TIMING.suffix + f".bak-pre-device-backfill-20260422")
    if not backup.exists():
        shutil.copy2(TIMING, backup)
    with open(TIMING) as f:
        lines = f.readlines()
    changed = skipped = 0
    out = []
    for line in lines:
        raw = line.rstrip("\n")
        if not raw.strip():
            out.append(line)
            continue
        if '"device"' in raw:
            out.append(line)
            skipped += 1
            continue
        if raw.endswith("}"):
            out.append(raw[:-1] + f',"device":"{device}"}}\n')
            changed += 1
        else:
            out.append(line)
    tmp = TIMING.with_suffix(TIMING.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.writelines(out)
    os.replace(tmp, TIMING)
    total = len(lines)
    print(f"device={device} backup={backup.name}")
    print(f"total={total} backfilled={changed} already-tagged={skipped}")


if __name__ == "__main__":
    main()
