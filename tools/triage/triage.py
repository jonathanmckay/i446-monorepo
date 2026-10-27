#!/usr/bin/env python3
"""triage — rule-based cleanup for unorganized folders (Downloads first).

Each source in config/triage.json is a folder plus an ordered rule list; the
first matching rule decides a top-level item's fate:
  trash   move to ~/.Trash (reversible until the Trash is emptied)
  move    move to `dest` ({yyyy-mm} = the month the file landed)
  review  leave in place, list it for JM
Unmatched items are kept. Ages count from Spotlight's kMDItemDateAdded (when
the file landed in the folder), falling back to birth time.

Dry run by default; --apply executes and logs every move to
~/.local/state/jm/triage/YYYY-MM-DD.jsonl (src, dst) so any run can be undone.

Usage: triage.py [--source downloads] [--apply] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "triage.json"
LOG_DIR = Path.home() / ".local" / "state" / "jm" / "triage"
TRASH = Path.home() / ".Trash"
COPY_RE = re.compile(r"^(?P<stem>.+) \(\d+\)(?P<ext>\.[^.]+)?$")


@dataclass
class Item:
    path: Path
    added: dt.datetime
    size: int

    @property
    def age_days(self) -> float:
        return (dt.datetime.now() - self.added).total_seconds() / 86400


@dataclass
class Decision:
    item: Item
    rule: str
    action: str          # trash | move | review | keep
    dest: Path | None = None
    note: str = ""


def _size(p: Path) -> int:
    if p.is_dir() and not p.is_symlink():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    return p.lstat().st_size


def _date_added(paths: list[Path]) -> dict[Path, dt.datetime]:
    """Batch-read kMDItemDateAdded; anything Spotlight doesn't know is omitted."""
    out: dict[Path, dt.datetime] = {}
    if not paths:
        return out
    try:
        raw = subprocess.run(["mdls", "-name", "kMDItemDateAdded", "-raw", *map(str, paths)],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return out
    for p, val in zip(paths, raw.split("\0")):
        try:
            out[p] = dt.datetime.strptime(val.strip(), "%Y-%m-%d %H:%M:%S %z").astimezone().replace(tzinfo=None)
        except ValueError:
            pass
    return out


def scan(root: Path, skip: list[str]) -> list[Item]:
    paths = sorted(p for p in root.iterdir() if p.name not in skip)
    added = _date_added(paths)
    items = []
    for p in paths:
        st = p.lstat()
        when = added.get(p) or dt.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))
        items.append(Item(p, when, _size(p)))
    return items


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_duplicate_copy(p: Path) -> bool:
    """`name (1).ext` whose original `name.ext` sits beside it with identical bytes."""
    m = COPY_RE.match(p.name)
    if not m or not p.is_file():
        return False
    orig = p.with_name(m["stem"] + (m["ext"] or ""))
    return (orig.is_file() and orig.stat().st_size == p.stat().st_size
            and _sha(orig) == _sha(p))


def matches(rule: dict, item: Item) -> bool:
    p = item.path
    if item.age_days < rule.get("min_age_days", 0):
        return False
    if "glob" in rule and not fnmatch.fnmatch(p.name, rule["glob"]):
        return False
    if "ext" in rule and (p.is_dir() or p.suffix.lower().lstrip(".") not in rule["ext"]):
        return False
    if "regex" in rule and not re.search(rule["regex"], p.name):
        return False
    if rule.get("duplicate") and not is_duplicate_copy(p):
        return False
    return True


def decide(item: Item, rules: list[dict]) -> Decision:
    for rule in rules:
        if matches(rule, item):
            dest = None
            if rule["action"] == "move":
                dest = Path(rule["dest"].replace("{yyyy-mm}", item.added.strftime("%Y-%m"))).expanduser()
            return Decision(item, rule["name"], rule["action"], dest, rule.get("note", ""))
    return Decision(item, "-", "keep")


def _free_name(dest_dir: Path, name: str) -> Path:
    target, n = dest_dir / name, 1
    while target.exists():
        target = dest_dir / f"{Path(name).stem} ~{n}{Path(name).suffix}"
        n += 1
    return target


def apply(decisions: list[Decision], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with log.open("a") as f:
        for d in decisions:
            if d.action not in ("trash", "move"):
                continue
            dest_dir = TRASH if d.action == "trash" else d.dest
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = _free_name(dest_dir, d.item.path.name)
            shutil.move(str(d.item.path), str(target))
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                "rule": d.rule, "action": d.action,
                                "src": str(d.item.path), "dst": str(target)}) + "\n")
            done += 1
    return done


def _human(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def report(name: str, decisions: list[Decision]) -> str:
    groups: dict[tuple[str, str], list[Decision]] = {}
    for d in decisions:
        groups.setdefault((d.action, d.rule), []).append(d)
    order = {"trash": 0, "move": 1, "review": 2, "keep": 3}
    lines = [f"{name}: {len(decisions)} items, {_human(sum(d.item.size for d in decisions))}", ""]
    for (action, rule), ds in sorted(groups.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        lines.append(f"  {action:<6} {rule:<18} {len(ds):>4}  {_human(sum(d.item.size for d in ds)):>7}")
    for (action, rule), ds in sorted(groups.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        if action != "review":
            continue
        lines += ["", f"  review / {rule}" + (f" ({ds[0].note})" if ds[0].note else "")]
        lines += [f"    {d.item.added:%Y-%m-%d}  {d.item.path.name}" for d in sorted(ds, key=lambda d: d.item.added)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", action="append", help="source name(s) from config; default all")
    ap.add_argument("--apply", action="store_true", help="execute trash/move (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable decisions")
    ap.add_argument("--config", type=Path, default=CONFIG)
    args = ap.parse_args(argv)

    sources = json.loads(args.config.read_text())["sources"]
    out = []
    for name in args.source or list(sources):
        src = sources[name]
        root = Path(src["path"]).expanduser()
        decisions = [decide(i, src["rules"]) for i in scan(root, src.get("skip", []))]
        if args.json:
            out += [{"source": name, "path": str(d.item.path), "rule": d.rule, "action": d.action,
                     "dest": str(d.dest) if d.dest else None, "size": d.item.size,
                     "added": d.item.added.isoformat(timespec="seconds")} for d in decisions]
            continue
        print(report(name, decisions))
        if args.apply:
            n = apply(decisions, LOG_DIR / f"{dt.date.today()}.jsonl")
            print(f"\n  applied {n} trash/move actions; log: {LOG_DIR}/{dt.date.today()}.jsonl")
        else:
            print("\n  dry run; pass --apply to execute")
    if args.json:
        json.dump(out, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
