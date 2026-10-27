#!/usr/bin/env python3
"""drive_gap: how much of a local tree is already in the m5x2 Google Drive.

Inputs: an `md5 -r` listing of the local tree ("<md5> <relative path>" per line)
and the Drive index label.py uses (~/.local/state/jm/triage/drive: drives.tsv +
idx-<id>.json from `rclone lsjson -R --files-only --hash`). Google-native docs
have no md5, so they only ever match by name.

Each local file lands in one bucket:
  in_drive    identical bytes somewhere in Drive (path given)
  name_match  same filename in Drive, different bytes (often an edited version)
  missing     nowhere in Drive

Usage: drive_gap.py <md5 listing> [--out gap.json]   prints totals by top folder
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

DRIVE_DIR = Path.home() / ".local" / "state" / "jm" / "triage" / "drive"
SKIP = {".DS_Store", "Icon\r", "desktop.ini", "Thumbs.db"}


def drive_index(drive_dir: Path = DRIVE_DIR) -> tuple[dict, dict]:
    by_md5, by_name = {}, defaultdict(list)
    for line in (drive_dir / "drives.tsv").read_text().splitlines():
        did, name = line.split("\t", 1)
        try:
            items = json.loads((drive_dir / f"idx-{did}.json").read_text())
        except (OSError, ValueError):
            continue
        for it in items:
            where = f"{name}/{it['Path']}"
            md5 = (it.get("Hashes") or {}).get("md5")
            if md5:
                by_md5.setdefault(md5, where)
            by_name[it["Name"].lower()].append(where)
    return by_md5, by_name


def classify(listing: list[tuple[str, str]], by_md5: dict, by_name: dict) -> list[dict]:
    out = []
    for md5, rel in listing:
        name = rel.rsplit("/", 1)[-1]
        if name in SKIP:
            continue
        if md5 in by_md5:
            out.append({"path": rel, "bucket": "in_drive", "drive": by_md5[md5]})
        elif name.lower() in by_name:
            out.append({"path": rel, "bucket": "name_match", "drive": by_name[name.lower()][0]})
        else:
            out.append({"path": rel, "bucket": "missing"})
    return out


def read_listing(p: Path) -> list[tuple[str, str]]:
    rows = []
    for line in p.read_text(errors="replace").splitlines():
        md5, _, rel = line.partition(" ")
        if len(md5) == 32 and rel:
            rows.append((md5, rel[2:] if rel.startswith("./") else rel))
    return rows


def summary(rows: list[dict]) -> dict:
    tot = defaultdict(lambda: defaultdict(int))
    for r in rows:
        top = r["path"].split("/")[0]
        tot[top][r["bucket"]] += 1
        tot["TOTAL"][r["bucket"]] += 1
    return {k: dict(v) for k, v in sorted(tot.items(), key=lambda kv: (kv[0] == "TOTAL", kv[0]))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("listing", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    by_md5, by_name = drive_index()
    rows = classify(read_listing(a.listing), by_md5, by_name)
    if a.out:
        a.out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    for top, b in summary(rows).items():
        print(f"{top:45} in_drive {b.get('in_drive', 0):5}  name_match {b.get('name_match', 0):5}  missing {b.get('missing', 0):5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
