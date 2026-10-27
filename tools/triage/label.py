#!/usr/bin/env python3
"""label: file documents interactively, with JM's answers kept as the eval set.

The classifier is the Claude Code session itself (no API calls, no credits):

  batch <dir> [--n 12]     next N unlabeled top-level files in <dir>, each with a
                           text snippet, plus the ~/files destination catalog
                           (classify.catalog). Saved as a batch file; prints JSON.
  propose <batch> <json>   the session writes, per row, up to 3 destinations
                           {"1": {"a": "<dest key or path>", "b": ..., "suggest": "a"}}
  show <batch>             the decision table JM fills in: one letter per row
                           (a/b/c = an option, x = trash, s = skip, leave in place)
  answer <batch> "1a 2c"   moves each file (logged to the triage log, undoable),
                           appends one row per file to ~/files/_meta/labels.jsonl
  score                    agreement of the suggestion with JM's choice, overall,
                           by area and by batch

labels.jsonl is the eval sheet: it lives in ~/files (Syncthing to Ix, never the
GitHub-pushed vault) because it names tax and family documents. classify.py's
eval can score a model against it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("classify", HERE / "classify.py")
classify = importlib.util.module_from_spec(_spec); sys.modules["classify"] = classify
_spec.loader.exec_module(classify)

STATE = classify.STATE
FILES = Path.home() / "files"
LABELS = FILES / "_meta" / "labels.jsonl"
TRASH = Path.home() / ".Trash"
ANSWER_RE = re.compile(r"(\d+)\s*([a-z])", re.I)
DRIVE_DIR = STATE / "drive"                     # drives.tsv + idx-<id>.json (rclone lsjson --hash)
REMOTE = "m5x2drive"


# ── Google Drive destinations (2026-10-27: m5x2 documents live in the m5x2 Drive) ──

def drives() -> dict[str, str]:
    """shared-drive name -> id, from the cached `rclone backend drives` list."""
    try:
        return {n: i for i, n in (l.split("\t", 1) for l in (DRIVE_DIR / "drives.tsv").read_text().splitlines() if l)}
    except OSError:
        return {}


def drive_catalog(depth: int = 3, min_files: int = 3, only: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """`gdrive:<drive>/<dir>` -> sample filenames, folders holding >= min_files files."""
    out: dict[str, list[str]] = {}
    for name, did in drives().items():
        if only and name not in only:
            continue
        try:
            items = json.loads((DRIVE_DIR / f"idx-{did}.json").read_text())
        except (OSError, ValueError):
            continue
        for it in items:
            parts = it["Path"].split("/")[:-1][:depth]
            if parts:
                out.setdefault(f"gdrive:{name}/" + "/".join(parts), []).append(it["Name"])
    return {k: v[:4] for k, v in sorted(out.items()) if len(v) >= min_files}


def _remote(dest: str) -> str:
    """'gdrive:m5x2 Main/r204 /x' -> 'm5x2drive,team_drive=<id>:r204 /x'."""
    name, _, path = dest[len("gdrive:"):].partition("/")
    did = drives().get(name)
    if not did:
        raise ValueError(f"unknown shared drive {name!r}; refresh {DRIVE_DIR}/drives.tsv")
    return f"{REMOTE},team_drive={did}:{path}"


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(src: Path, dest: str) -> str:
    """Copy src (file or folder) into the Drive folder `dest`, never overwriting:
    identical bytes already there -> nothing uploaded; same name, different
    bytes -> uploaded as `name ~N`. Verified with rclone check. Returns the
    Drive path it now lives at."""
    import subprocess
    base = _remote(dest).rstrip("/")
    if src.is_dir():
        target = f"{base}/{src.name}"
        subprocess.run(["rclone", "copy", str(src), target, "--ignore-existing"], check=True, capture_output=True)
        r = subprocess.run(["rclone", "check", str(src), target, "--one-way"], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"verify failed for {src.name}: {r.stderr.strip()[-200:]}")
        return f"{dest}/{src.name}"
    name, k = src.name, 1
    while True:
        r = subprocess.run(["rclone", "lsjson", "--hash", f"{base}/{name}"], capture_output=True, text=True)
        found = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        if not found:
            break
        if found[0].get("Hashes", {}).get("md5") == _md5(src):
            return f"{dest}/{name}"                  # already filed, identical
        name = f"{src.stem} ~{k}{src.suffix}"
        k += 1
    subprocess.run(["rclone", "copyto", str(src), f"{base}/{name}"], check=True, capture_output=True)
    r = subprocess.run(["rclone", "lsjson", "--hash", f"{base}/{name}"], capture_output=True, text=True)
    got = json.loads(r.stdout or "[]")
    if not got or got[0].get("Hashes", {}).get("md5") != _md5(src):
        raise RuntimeError(f"verify failed for {src.name}")
    return f"{dest}/{name}"


def _batch_path(batch: str) -> Path:
    return STATE / f"label-{batch}.json"


def _labeled() -> set[str]:
    try:
        return {json.loads(l)["path"] for l in LABELS.read_text().splitlines() if l.strip()}
    except OSError:
        return set()


def _dir_snippet(d: Path) -> str:
    items = sorted(x for x in d.rglob("*") if x.is_file() and not x.name.startswith("."))
    size = sum(x.stat().st_size for x in items)
    names = ", ".join(str(x.relative_to(d)) for x in items[:8])
    return f"[folder: {len(items)} files, {size / 1e6:.1f} MB] {names}"


def cmd_batch(folder: Path, n: int, max_mb: float, match: str = "", exclude: str = "",
              dirs: bool = False, drive: tuple[str, ...] = ()) -> dict:
    done = _labeled()
    files = [p for p in sorted(folder.iterdir()) if (p.is_dir() if dirs else p.is_file())
             and not p.name.startswith(".")
             and str(p) not in done
             and (not match or re.search(match, p.name, re.I))
             and not (exclude and re.search(exclude, p.name, re.I))][:n]
    cfg = classify.load_config()
    cat = classify.catalog(cfg)
    rows = [{"n": i, "path": str(p), "name": p.name + ("/" if p.is_dir() else ""), "size": p.stat().st_size,
             "snippet": (_dir_snippet(p) if p.is_dir() else classify.snippet(p, max_mb))[:400]}
            for i, p in enumerate(files, 1)]
    batch = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    data = {"batch": batch, "folder": str(folder), "rows": rows,
            "catalog": {k: [f.name for f in d.files[:4]] for k, d in sorted(cat.items())}}
    if drive:
        data["catalog"].update(drive_catalog(only=drive))
    STATE.mkdir(parents=True, exist_ok=True)
    _batch_path(batch).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return data


def cmd_propose(batch: str, proposals: dict) -> None:
    p = _batch_path(batch)
    data = json.loads(p.read_text())
    for row in data["rows"]:
        prop = proposals.get(str(row["n"]))
        if prop:
            row["options"] = {k: v for k, v in prop.items() if k in "abc" and v}
            row["suggest"] = prop.get("suggest", "a")
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def cmd_show(batch: str) -> str:
    data = json.loads(_batch_path(batch).read_text())
    out = [f"Batch {batch}: one letter per row (a/b/c = option, x = trash, s = skip)", "",
           "| # | File | a | b | c | Suggest |", "|---|---|---|---|---|---|"]
    for r in data["rows"]:
        o = r.get("options", {})
        out.append(f"| {r['n']} | `{r['name']}` | {o.get('a', '')} | {o.get('b', '')} | {o.get('c', '')} "
                   f"| **{r.get('suggest', '')}** |")
    return "\n".join(out)


def upload_version(src: Path, drive_file: str, tag: str = "local") -> str:
    """Keep both (JM 2026-10-27): put `src` beside the Drive file it conflicts with as
    `<stem> (<tag> YYYY-MM-DD)<ext>` (src's modified date). Drive's own revisions
    expire after 30 days, so a sibling file is the durable version. `drive_file`
    is '<shared drive>/<path>' as in the drive index. Returns the new Drive path."""
    import subprocess
    name, _, path = drive_file.partition("/")
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    when = dt.datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
    new = f"{src.stem} ({tag} {when}){src.suffix}"
    base = _remote(f"gdrive:{name}/{folder}").rstrip("/")
    if folder == "":
        base = _remote(f"gdrive:{name}/").rstrip("/")
    target = f"{base}/{new}"
    r = subprocess.run(["rclone", "lsjson", "--hash", target], capture_output=True, text=True)
    have = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    if not have:
        subprocess.run(["rclone", "copyto", str(src), target], check=True, capture_output=True)
        r = subprocess.run(["rclone", "lsjson", "--hash", target], capture_output=True, text=True)
        have = json.loads(r.stdout or "[]")
    if not have or have[0].get("Hashes", {}).get("md5") != _md5(src):
        raise RuntimeError(f"verify failed for {src.name}")
    return f"{name}/{folder}/{new}"


def _area(dest: str) -> str:
    return dest.split("/")[0] if dest not in ("trash", "skip") else dest


def _target(dest: str, name: str) -> Path:
    """A catalog key (templated or not) or a path under ~/files."""
    if "{year}" in dest:
        m = classify.YEAR_RE.search(name)
        year = m.group(1) if m else str(dt.date.today().year)
        return classify.resolve(classify.load_config(), dest, year)
    p = Path(dest).expanduser()
    return p if p.is_absolute() else FILES / dest


def _free(d: Path, name: str) -> Path:
    t, k = d / name, 1
    while t.exists():
        t = d / f"{Path(name).stem} ~{k}{Path(name).suffix}"
        k += 1
    return t


def expand(answers: str, rows: list[int], default: str = "") -> list[tuple[int, str]]:
    """'1-5 x, 7b' -> pairs; ranges allowed; rows not mentioned get `default` (JM:
    'assume a unless I say otherwise', 2026-10-27)."""
    got: dict[int, str] = {}
    for lo, hi, letter in re.findall(r"(\d+)(?:\s*-\s*(\d+))?\s*([a-z])", answers.lower()):
        for n in range(int(lo), int(hi or lo) + 1):
            got[n] = letter
    if default:
        for n in rows:
            got.setdefault(n, default)
    return sorted(got.items())


def cmd_answer(batch: str, answers: str, dry_run: bool = False, default: str = "") -> list[dict]:
    data = json.loads(_batch_path(batch).read_text())
    rows = {r["n"]: r for r in data["rows"]}
    log = STATE / f"{dt.date.today()}.jsonl"
    out = []
    for num, letter in expand(answers, list(rows), default):
        r = rows.get(num)
        if not r:
            continue
        opts = r.get("options", {})
        chosen = {"x": "trash", "s": "skip"}.get(letter) or opts.get(letter)
        if chosen is None:
            print(f"  {num}: no option {letter}; skipped", file=sys.stderr)
            continue
        sl = r.get("suggest", "")
        sug = {"x": "trash", "s": "skip"}.get(sl) or opts.get(sl, sl)
        src = Path(r["path"])
        dst = None
        if not dry_run and chosen != "skip" and src.exists():
            if chosen.startswith("gdrive:"):
                try:
                    dst = upload(src, chosen)
                except Exception as e:                  # leave it local; JM sees the error
                    print(f"  {num}: upload failed, left in place: {e}", file=sys.stderr)
                    continue
                TRASH.mkdir(parents=True, exist_ok=True)
                gone = _free(TRASH, src.name)
                shutil.move(str(src), str(gone))
                STATE.mkdir(parents=True, exist_ok=True)
                with log.open("a") as f:
                    f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "rule": "label",
                                        "action": "upload+trash", "src": str(src), "dst": dst,
                                        "local_copy": str(gone), "batch": batch}) + "\n")
            to_dir = None if chosen.startswith("gdrive:") else (TRASH if chosen == "trash" else _target(chosen, src.name))
            if to_dir is None:
                pass
            elif to_dir.resolve() == src.parent.resolve():
                dst = src                      # already there: label it, don't rename it
            else:
                to_dir.mkdir(parents=True, exist_ok=True)
                dst = _free(to_dir, src.name)
                shutil.move(str(src), str(dst))
            if to_dir is not None:
                STATE.mkdir(parents=True, exist_ok=True)
                with log.open("a") as f:
                    f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "rule": "label",
                                        "action": "trash" if chosen == "trash" else "move",
                                        "src": str(src), "dst": str(dst), "batch": batch}) + "\n")
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "batch": batch, "path": r["path"],
               "name": r["name"], "snippet_sha": hashlib.sha256(r.get("snippet", "").encode()).hexdigest()[:12],
               "suggested": sug, "chosen": chosen, "agree": sug == chosen,
               "letter": letter, "suggest_letter": r.get("suggest"), "dst": str(dst) if dst else None}
        out.append(rec)
        if not dry_run:
            LABELS.parent.mkdir(parents=True, exist_ok=True)
            with LABELS.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out


def cmd_score() -> dict:
    try:
        recs = [json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()]
    except OSError:
        recs = []
    recs = [r for r in recs if r["chosen"] != "skip"]

    def rate(rs):
        return {"n": len(rs), "agree": sum(r["agree"] for r in rs),
                "pct": round(100 * sum(r["agree"] for r in rs) / len(rs)) if rs else None}
    by_area, by_batch = {}, {}
    for r in recs:
        by_area.setdefault(_area(r["chosen"]), []).append(r)
        by_batch.setdefault(r["batch"], []).append(r)
    return {"overall": rate(recs), "by_area": {k: rate(v) for k, v in sorted(by_area.items())},
            "by_batch": {k: rate(v) for k, v in sorted(by_batch.items())}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch"); b.add_argument("folder", type=Path); b.add_argument("--n", type=int, default=12)
    b.add_argument("--max-mb", type=float, default=10)
    b.add_argument("--match", default="", help="only names matching this regex")
    b.add_argument("--exclude", default="", help="skip names matching this regex")
    b.add_argument("--dirs", action="store_true", help="batch folders instead of files")
    b.add_argument("--drive", action="append", default=[], help="add this shared drive's folders to the menu")
    pr = sub.add_parser("propose"); pr.add_argument("batch"); pr.add_argument("json")
    sh = sub.add_parser("show"); sh.add_argument("batch")
    an = sub.add_parser("answer"); an.add_argument("batch"); an.add_argument("answers")
    an.add_argument("--dry-run", action="store_true")
    an.add_argument("--default", default="a", help="letter for rows not mentioned ('' to leave them)")
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        print(json.dumps(cmd_batch(a.folder.expanduser(), a.n, a.max_mb, a.match, a.exclude, a.dirs, tuple(a.drive)), indent=1, ensure_ascii=False))
    elif a.cmd == "propose":
        cmd_propose(a.batch, json.loads(a.json)); print(cmd_show(a.batch))
    elif a.cmd == "show":
        print(cmd_show(a.batch))
    elif a.cmd == "answer":
        for r in cmd_answer(a.batch, a.answers, a.dry_run, a.default):
            print(f"  {'✓' if r['agree'] else '✗'} {r['name']} -> {r['chosen']}")
        print(json.dumps(cmd_score()["overall"]))
    elif a.cmd == "score":
        print(json.dumps(cmd_score(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
