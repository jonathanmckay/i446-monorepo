#!/usr/bin/env python3
"""triage — rule-based cleanup for unorganized folders (Downloads, Desktop, Documents).

Each source in config/triage.json is a folder plus an ordered rule list
("rules_from": "<source>" reuses another source's list); the first matching
rule decides a top-level item's fate:
  trash   move to ~/.Trash (reversible until the Trash is emptied)
  move    move to `dest`; {year} = first 20xx in the name, else the year the
          file landed. If an identical file already sits at the destination,
          the incoming copy is trashed instead. "rename": "screenshot" gives
          the date-first ~/files/screenshots name (tools/images/screenshot_names.py).
  unzip   extract beside the zip (inheriting its age), trash the zip, then
          triage what came out in the same run
  queue   leave in place, list in duplicates.json for the weekly /1-i446 to
          review: same name as a file beside it, different bytes
          (tools/vault/vault_health.py). Byte-identical copies are trashed daily.
  review  leave in place, list it for JM
`dated_min_days` matches a `YYYY-MM-DD` folder at least that many days old by its
name (the ~/.to-delete batches).
Unmatched items are kept. A rule with "enabled": false is reported as review
("proposed: ...") until switched on. Ages count from Spotlight's
kMDItemDateAdded (when the file landed in the folder), falling back to birth
time; unzipped output keeps its zip's date via a sidecar in the state dir.

Dry run by default; --apply executes and logs every change to
~/.local/state/jm/triage/YYYY-MM-DD.jsonl (src, dst) so any run can be undone.
--card files one Todoist card listing the review pile on Sundays (once per
ISO week); --card-now files it regardless of the day.

Usage: triage.py [--source downloads] [--apply] [--card | --card-now] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "triage.json"
STATE = Path.home() / ".local" / "state" / "jm" / "triage"
ADDED = STATE / "added.json"          # path -> inherited date-added (unzip output)
DUPES = STATE / "duplicates.json"     # name clashes queued for the weekly /1-i446 review
TRASH = Path.home() / ".Trash"
PHOTOS_DB = Path.home() / "Pictures" / "Photos Library.photoslibrary" / "database" / "Photos.sqlite"
TOKEN = Path.home() / ".config" / "todoist" / "token"
COPY_RE = re.compile(r"^(?P<stem>.+) \(\d+\)(?P<ext>\.[^.]+)?$")
YEAR_RE = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
APPLE_EPOCH = 978307200
sys.path.insert(0, str(REPO / "tools" / "images"))
import screenshot_names  # noqa: E402


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
    action: str          # trash | move | unzip | queue | review | keep
    dest: Path | None = None
    note: str = ""
    rename: str = ""


# ── scanning ────────────────────────────────────────────────────────────────

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


def _load_added() -> dict[str, str]:
    try:
        return json.loads(ADDED.read_text())
    except (OSError, ValueError):
        return {}


def _remember_added(path: Path, when: dt.datetime) -> None:
    data = {k: v for k, v in _load_added().items() if Path(k).exists()}
    data[str(path)] = when.isoformat(timespec="seconds")
    ADDED.parent.mkdir(parents=True, exist_ok=True)
    ADDED.write_text(json.dumps(data, indent=1))


def scan(root: Path, skip: list[str]) -> list[Item]:
    paths = sorted(p for p in root.iterdir() if p.name not in skip and not p.name.startswith(".triage-"))
    added = _date_added(paths)
    inherited = _load_added()
    items = []
    for p in paths:
        st = p.lstat()
        when = (dt.datetime.fromisoformat(inherited[str(p)]) if str(p) in inherited
                else added.get(p) or dt.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime)))
        items.append(Item(p, when, _size(p)))
    return items


# ── matchers ────────────────────────────────────────────────────────────────

def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_bytes(a: Path, b: Path) -> bool:
    return (a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size
            and _sha(a) == _sha(b))


def is_duplicate_copy(p: Path) -> bool:
    """`name (1).ext` whose original `name.ext` sits beside it with identical bytes."""
    m = COPY_RE.match(p.name)
    return bool(m) and same_bytes(p, p.with_name(m["stem"] + (m["ext"] or "")))


def is_name_clash(p: Path) -> bool:
    """`name (1).ext` whose original `name.ext` sits beside it with different bytes."""
    m = COPY_RE.match(p.name)
    if not m or not p.is_file():
        return False
    orig = p.with_name(m["stem"] + (m["ext"] or ""))
    return orig.is_file() and not same_bytes(p, orig)


def is_extracted_zip(p: Path) -> bool:
    """`foo.zip` with its unpacked `foo/` folder beside it."""
    return p.suffix.lower() == ".zip" and p.with_suffix("").is_dir()


def is_app_zip(p: Path) -> bool:
    """A zip whose top level holds a macOS .app bundle, i.e. an installer."""
    if p.suffix.lower() != ".zip" or not p.is_file():
        return False
    try:
        with zipfile.ZipFile(p) as z:
            return any(n.split("/")[0].endswith(".app") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)


def has_twin(p: Path, exts: list[str]) -> bool:
    """Same stem with one of `exts` beside it (IMG_1.jpg next to IMG_1.HEIC)."""
    return p.is_file() and any(
        q.is_file() and q != p and q.stem == p.stem and q.suffix.lower().lstrip(".") in exts
        for q in p.parent.glob(glob_escape(p.stem) + ".*"))


_photos_cache: dict[str, list[float]] | None = None


def _photos_index() -> dict[str, list[float]]:
    """original filename (lower) -> capture times (unix) for photos not in Photos' trash.

    Read-only, immutable open of the library database; any failure (no Full Disk
    Access under launchd, schema change) yields {} so the photo stays in review."""
    global _photos_cache
    if _photos_cache is None:
        _photos_cache = {}
        try:
            con = sqlite3.connect(f"file:{PHOTOS_DB}?mode=ro&immutable=1", uri=True)
            for name, created in con.execute(
                    "select a.ZORIGINALFILENAME, z.ZDATECREATED from ZADDITIONALASSETATTRIBUTES a "
                    "join ZASSET z on a.ZASSET = z.Z_PK where z.ZTRASHEDSTATE = 0"):
                if name and created is not None:
                    _photos_cache.setdefault(name.lower(), []).append(created + APPLE_EPOCH)
            con.close()
        except sqlite3.Error:
            _photos_cache = {}
    return _photos_cache


def _capture_time(p: Path) -> float | None:
    try:
        raw = subprocess.run(["mdls", "-name", "kMDItemContentCreationDate", "-raw", str(p)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z").timestamp()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def in_photos_library(p: Path) -> bool:
    """Photos holds an original with this filename captured within 2 s of this file."""
    times = _photos_index().get(p.name.lower())
    if not times or not p.is_file():
        return False
    t = _capture_time(p)
    return t is not None and any(abs(t - x) <= 2 for x in times)


WORK_MARKERS = re.compile(rb"(?i)(xbox|microsoft\.com|#107C10|game ?pass|activision|blizzard|"
                          rb"MSIP_Label|xcore|gaming-microsoft)")
TEXT_EXTS = {".html", ".htm", ".md", ".csv", ".eml", ".py", ".json", ".txt", ".js", ".svg", ".ipynb"}


def is_work_content(p: Path) -> bool:
    """Microsoft material by content, not name (2026-10-27: ~/Archive held 78 work
    files, most with neutral names like mdau_review.html): an Office file with a
    Microsoft sensitivity label or company, or text containing Xbox/Microsoft markers."""
    if not p.is_file() or p.stat().st_size > 50_000_000:
        return False
    ext = p.suffix.lower()
    try:
        if ext in (".pptx", ".xlsx", ".docx"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n in ("docProps/custom.xml", "docProps/app.xml"):
                        x = z.read(n)
                        if b"MSIP_Label" in x or re.search(rb"<Company>[^<]*Microsoft", x):
                            return True
                    elif n.startswith(("ppt/slides/", "word/document", "xl/sharedStrings")) and WORK_MARKERS.search(z.read(n)):
                        return True
        elif ext in TEXT_EXTS:
            return bool(WORK_MARKERS.search(p.read_bytes()))
    except (zipfile.BadZipFile, OSError):
        return False
    return False


DATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def dated_older(p: Path, days: float) -> bool:
    """A `YYYY-MM-DD` folder (a ~/.to-delete batch) dated at least `days` ago. The
    date comes from the name: Spotlight doesn't index hidden folders, and the files
    inside keep their original, much older, birth times."""
    if not (p.is_dir() and DATED_RE.fullmatch(p.name)):
        return False
    try:
        when = dt.datetime.strptime(p.name, "%Y-%m-%d")
    except ValueError:
        return False
    return (dt.datetime.now() - when).days >= days


def matches(rule: dict, item: Item) -> bool:
    p = item.path
    if item.age_days < rule.get("min_age_days", 0):
        return False
    if "dated_min_days" in rule and not dated_older(p, rule["dated_min_days"]):
        return False
    if "glob" in rule and not fnmatch.fnmatch(p.name, rule["glob"]):
        return False
    if "ext" in rule and (p.is_dir() or p.suffix.lower().lstrip(".") not in rule["ext"]):
        return False
    if "regex" in rule and not re.search(rule["regex"], p.name):
        return False
    if "not_regex" in rule and re.search(rule["not_regex"], p.name):
        return False
    if rule.get("duplicate") and not is_duplicate_copy(p):
        return False
    if rule.get("name_clash") and not is_name_clash(p):
        return False
    if rule.get("extracted") and not is_extracted_zip(p):
        return False
    if rule.get("app_zip") and not is_app_zip(p):
        return False
    if "twin_exts" in rule and not has_twin(p, rule["twin_exts"]):
        return False
    if rule.get("in_photos") and not in_photos_library(p):
        return False
    if rule.get("work_content") and not is_work_content(p):
        return False
    if rule.get("git_repo") and not (p.is_dir() and (p / ".git").exists()):
        return False
    return True


def _dest(template: str, item: Item) -> Path:
    m = YEAR_RE.search(item.path.name)
    year = m.group(1) if m else str(item.added.year)
    return Path(template.replace("{year}", year)
                        .replace("{yyyy-mm}", item.added.strftime("%Y-%m"))).expanduser()


def decide(item: Item, rules: list[dict]) -> Decision:
    for rule in rules:
        if matches(rule, item):
            dest = _dest(rule["dest"], item) if rule["action"] == "move" else None
            if not rule.get("enabled", True):
                where = f" -> {dest}" if dest else ""
                note = f"proposed: {rule['action']}{where}" + (f"; {rule['note']}" if rule.get("note") else "")
                return Decision(item, rule["name"], "review", None, note)
            return Decision(item, rule["name"], rule["action"], dest, rule.get("note", ""),
                            rule.get("rename", ""))
    return Decision(item, "-", "keep")


# ── applying ────────────────────────────────────────────────────────────────

def _free_name(dest_dir: Path, name: str) -> Path:
    target, n = dest_dir / name, 1
    while target.exists():
        target = dest_dir / f"{Path(name).stem} ~{n}{Path(name).suffix}"
        n += 1
    return target


def _move(src: Path, dest_dir: Path, rename: str = "") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if rename == "screenshot":
        target = screenshot_names._free(dest_dir, screenshot_names.new_name(src.name, src.stat().st_mtime))
    else:
        target = _free_name(dest_dir, src.name)
    shutil.move(str(src), str(target))
    return target


def _unzip(item: Item) -> Path:
    """Extract beside the zip; a single top-level entry is lifted out so there is
    no folder-in-folder. Returns what came out."""
    zp = item.path
    tmp = zp.parent / f".triage-unzip-{zp.stem}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    with zipfile.ZipFile(zp) as z:
        for m in z.infolist():
            if m.filename.startswith("__MACOSX/") or m.filename.endswith(".DS_Store"):
                continue
            z.extract(m, tmp)           # zipfile strips absolute paths and '..'
    entries = list(tmp.iterdir())
    if len(entries) == 1:
        out = _free_name(zp.parent, entries[0].name)
        shutil.move(str(entries[0]), str(out))
        tmp.rmdir()
    else:
        out = _free_name(zp.parent, zp.stem)
        tmp.rename(out)
    _remember_added(out, item.added)
    return out


def apply(decisions: list[Decision], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with log.open("a") as f:
        for d in decisions:
            if d.action not in ("trash", "move", "unzip"):
                continue
            src, action = d.item.path, d.action
            try:
                if action == "unzip":
                    out = _unzip(d.item)
                    rec = {"action": "unzip", "src": str(src), "dst": str(out)}
                    f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                        "rule": d.rule, **rec}) + "\n")
                    action = "trash"
                elif action == "move" and same_bytes(src, d.dest / src.name):
                    action = "trash"        # identical copy already filed
                dst = _move(src, TRASH, "") if action == "trash" else _move(src, d.dest, d.rename)
            except (OSError, zipfile.BadZipFile, RuntimeError) as e:   # encrypted / corrupt zip
                print(f"  skip {src.name}: {e}", file=sys.stderr)
                continue
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                "rule": d.rule, "action": action,
                                "src": str(src), "dst": str(dst)}) + "\n")
            done += 1
    return done


# ── duplicate queue (trashed weekly by /1-i446) ─────────────────────────────

def original_of(p: Path) -> Path | None:
    """The file `p` duplicates: `name.ext` for `name (1).ext`, else a same-stem HEIC."""
    m = COPY_RE.match(p.name)
    if m:
        orig = p.with_name(m["stem"] + (m["ext"] or ""))
        if orig.is_file():
            return orig
    for q in p.parent.glob(glob_escape(p.stem) + ".*"):
        if q != p and q.suffix.lower() == ".heic":
            return q
    return None


def write_queue(decisions: list[Decision]) -> int:
    rows = []
    for d in decisions:
        if d.action == "queue" and (orig := original_of(d.item.path)):
            rows.append({"path": str(d.item.path), "original": str(orig), "rule": d.rule,
                         "size": d.item.size})
    STATE.mkdir(parents=True, exist_ok=True)
    DUPES.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return len(rows)


def review_queue(do_apply: bool = True) -> tuple[list[dict], list[dict]]:
    """Re-check the queued name clashes. Returns (still clashing, resolved).
    A copy whose bytes now match its original is trashed (when do_apply); one
    whose file or original is gone is dropped. Nothing that differs is touched."""
    try:
        rows = json.loads(DUPES.read_text())
    except (OSError, ValueError):
        return [], []
    pending, resolved = [], []
    log = STATE / f"{dt.date.today()}.jsonl"
    for r in rows:
        p, orig = Path(r["path"]), Path(r["original"])
        if not (p.is_file() and orig.is_file()):
            resolved.append({**r, "outcome": "gone"})
        elif same_bytes(p, orig):
            if do_apply:
                dst = _move(p, TRASH)
                with log.open("a") as f:
                    f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "rule": r["rule"],
                                        "action": "trash", "src": str(p), "dst": str(dst), "by": "1-i446"}) + "\n")
            resolved.append({**r, "outcome": "identical, trashed"})
        else:
            pending.append(r)
    return pending, resolved


# ── reporting ───────────────────────────────────────────────────────────────

def _human(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


ORDER = {"trash": 0, "move": 1, "unzip": 2, "queue": 3, "review": 4, "keep": 5}


def _groups(decisions: list[Decision]) -> list[tuple[tuple[str, str], list[Decision]]]:
    groups: dict[tuple[str, str], list[Decision]] = {}
    for d in decisions:
        groups.setdefault((d.action, d.rule), []).append(d)
    return sorted(groups.items(), key=lambda kv: (ORDER[kv[0][0]], kv[0][1]))


def report(name: str, decisions: list[Decision]) -> str:
    lines = [f"{name}: {len(decisions)} items, {_human(sum(d.item.size for d in decisions))}", ""]
    for (action, rule), ds in _groups(decisions):
        lines.append(f"  {action:<6} {rule:<18} {len(ds):>4}  {_human(sum(d.item.size for d in ds)):>7}")
    for (action, rule), ds in _groups(decisions):
        if action == "review":
            lines += ["", f"  review / {rule}" + (f" ({ds[0].note})" if ds[0].note else "")]
            lines += [f"    {d.item.added:%Y-%m-%d}  {d.item.path.name}"
                      for d in sorted(ds, key=lambda d: d.item.added)]
    return "\n".join(lines)


def card(name: str, decisions: list[Decision]) -> str:
    """One Todoist card per ISO week listing the review pile; returns a status line."""
    review = [d for d in decisions if d.action == "review"]
    if not review:
        return "card: nothing to review"
    year, week, _ = dt.date.today().isocalendar()
    mark = STATE / f"card-{name}-{year}-W{week:02d}"
    if mark.exists():
        return f"card: already filed for {year}-W{week:02d}"
    desc = []
    for (_, rule), ds in _groups(review):
        desc.append(f"**{rule}** ({len(ds)})" + (f": {ds[0].note}" if ds[0].note else ""))
        desc += [f"- {d.item.path.name}" for d in sorted(ds, key=lambda d: d.item.added)]
        desc.append("")
    body = json.dumps({"content": f"😈 {name} triage ({platform.node().split('.')[0]}): {len(review)} to review (15) [5]",
                       "description": "\n".join(desc)[:15000], "labels": ["i447"],
                       "due_string": "today", "priority": 4}).encode()
    req = urllib.request.Request("https://api.todoist.com/api/v1/tasks", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + TOKEN.read_text().strip(),
                                          "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()
    STATE.mkdir(parents=True, exist_ok=True)
    mark.touch()
    return f"card: filed ({len(review)} items)"


# ── main ────────────────────────────────────────────────────────────────────

def run_source(name: str, src: dict, do_apply: bool) -> list[Decision]:
    root = Path(src["path"]).expanduser()
    if not root.is_dir():                   # e.g. the Ix-only queue when running on Straylight
        print(f"{name}: {root} not on this machine; skipped")
        return []
    decisions = [decide(i, src["rules"]) for i in scan(root, src.get("skip", []))]
    if not do_apply:
        return decisions
    log = STATE / f"{dt.date.today()}.jsonl"
    total = 0
    for _ in range(3):                      # unzip output is triaged in the same run
        total += apply(decisions, log)
        unzipped = any(d.action == "unzip" for d in decisions)
        decisions = [decide(i, src["rules"]) for i in scan(root, src.get("skip", []))]
        if not unzipped:
            break
    queued = write_queue(decisions)
    print(f"{name}: applied {total} changes, queued {queued} duplicate(s) for /1-i446; log: {log}")
    return decisions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", action="append", help="source name(s) from config; default all")
    ap.add_argument("--apply", action="store_true", help="execute trash/move/unzip (default: dry run)")
    ap.add_argument("--card", action="store_true", help="file the review card if today is Sunday")
    ap.add_argument("--card-now", action="store_true", help="file the review card today")
    ap.add_argument("--json", action="store_true", help="machine-readable decisions")
    ap.add_argument("--config", type=Path, default=CONFIG)
    args = ap.parse_args(argv)

    sources = json.loads(args.config.read_text())["sources"]
    out = []
    for name in args.source or list(sources):
        src = sources[name]
        if "rules_from" in src:             # share another source's rule list
            src = {**src, "rules": sources[src["rules_from"]]["rules"]}
        decisions = run_source(name, src, args.apply)
        if args.json:
            out += [{"source": name, "path": str(d.item.path), "rule": d.rule, "action": d.action,
                     "dest": str(d.dest) if d.dest else None, "size": d.item.size,
                     "added": d.item.added.isoformat(timespec="seconds")} for d in decisions]
            continue
        print(report(name, decisions))
        if not args.apply:
            print("\n  dry run; pass --apply to execute")
        if args.card_now or (args.card and dt.date.today().weekday() == 6):
            try:
                print(card(name, decisions))
            except Exception as e:      # the report above still stands
                print(f"card: not filed: {e}", file=sys.stderr)
    if args.json:
        json.dump(out, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
