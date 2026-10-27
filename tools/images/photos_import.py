#!/usr/bin/env python3
"""Import a folder tree of old photos into Apple Photos (2026-10-27: the
Dropbox Archive/Pictures merge, vault i446-photos-and-screenshots).

Each top-level folder becomes an album inside a Photos folder ("Archive" by
default), keeping the old trip/era organization. Photos' own duplicate check
stays on, so anything already in the library is skipped. Screenshot-named
images go to ~/screenshots under the date-first name instead (the image
rules: screenshots never live in Photos). Files Photos can't take (mp3 …)
are listed, not touched.

Progress is recorded per batch in STATE, so a rerun resumes.

Usage: photos_import.py ROOT [--folder Archive] [--only NAME] [--dry-run]
"""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import tempfile
import sys
from pathlib import Path

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("screenshot_names", HERE / "screenshot_names.py")
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)

MEDIA = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".tif", ".tiff", ".bmp", ".psd",
         ".arw", ".cr2", ".nef", ".dng", ".mov", ".mp4", ".m4v", ".avi"}
BATCH = 200
SCREENSHOTS = Path.home() / "screenshots"
STATE = Path.home() / ".local" / "state" / "jm" / "photos-import.json"
LOOSE_ALBUM = "Archive (loose files)"

APPLESCRIPT = r'''
on run argv
    set folderName to item 1 of argv
    set albName to item 2 of argv
    set fileList to {}
    repeat with i from 3 to count of argv
        set end of fileList to (POSIX file (item i of argv)) as alias
    end repeat
    tell application "Photos"
        if not (exists folder folderName) then make new folder named folderName
        set fld to folder folderName
        if not (exists album albName of fld) then make new album named albName at fld
        set alb to album albName of fld
        with timeout of 3600 seconds
            set imported to import fileList into alb skip check duplicates true
        end timeout
        if imported is missing value then return 0
        return count of imported
    end tell
end run
'''


def is_screenshot(p: Path) -> bool:
    return p.name.lower().startswith(("screenshot", "screen shot"))


def plan(root: Path):
    """{album: [media files]}, [screenshots], [skipped non-media]."""
    albums, shots, skipped = {}, [], []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith(".") or p.name == "Thumbs.db":
            continue
        rel = p.relative_to(root)
        album = rel.parts[0] if len(rel.parts) > 1 else LOOSE_ALBUM
        if p.suffix.lower() not in MEDIA:
            skipped.append(p)
        elif is_screenshot(p):
            shots.append(p)
        else:
            albums.setdefault(album, []).append(p)
    return albums, shots, skipped


LIBRARY_DB = Path.home() / "Pictures" / "Photos Library.photoslibrary" / "database" / "Photos.sqlite"


def library_keys(db: Path = LIBRARY_DB) -> set:
    """(lowercased original filename, original byte size) of every asset in
    the Photos library, read from a temp copy (Photos holds the live DB)."""
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / ("Photos.sqlite" + suffix))
        con = sqlite3.connect(f"file:{tmp}/Photos.sqlite?mode=ro", uri=True)
        rows = con.execute("select lower(ZORIGINALFILENAME), ZORIGINALFILESIZE "
                           "from ZADDITIONALASSETATTRIBUTES").fetchall()
        con.close()
    return {(n, sz) for n, sz in rows if n}


def dedupe(albums: dict, existing: set):
    """Drop files already in the library or earlier in this run.
    Returns (albums, n_in_library, n_repeat_in_archive)."""
    seen, out, lib, rep = set(), {}, 0, 0
    for album, files in albums.items():
        keep = []
        for p in files:
            key = (p.name.lower(), p.stat().st_size)
            if key in existing:
                lib += 1
            elif key in seen:
                rep += 1
            else:
                seen.add(key)
                keep.append(p)
        if keep:
            out[album] = keep
    return out, lib, rep


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def import_batch(folder: str, album: str, files) -> int:
    r = subprocess.run(["osascript", "-e", APPLESCRIPT, folder, album, *map(str, files)],
                       capture_output=True, text=True, timeout=4000)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return int(r.stdout.strip() or 0)


def run(root: Path, folder: str = "Archive", only=None, dry_run=False) -> None:
    albums, shots, skipped = plan(root)
    albums, n_lib, n_rep = dedupe(albums, library_keys())
    print(f"skipping {n_lib} already in Photos, {n_rep} repeated within the archive")
    print(f"{sum(map(len, albums.values()))} media files in {len(albums)} albums, "
          f"{len(shots)} screenshots, {len(skipped)} skipped non-media")
    for p in skipped:
        print(f"  skip: {p.relative_to(root)}")
    if not dry_run:
        SCREENSHOTS.mkdir(exist_ok=True)
        for p in shots:
            target = sn._free(SCREENSHOTS, sn.new_name(p.name, p.stat().st_mtime))
            shutil.copy2(p, target)
    state = _load()
    for album, files in albums.items():
        if only and album != only:
            continue
        for i in range(0, len(files), BATCH):
            key = f"{folder}/{album}#{i // BATCH}"
            if key in state:
                continue
            chunk = files[i:i + BATCH]
            if dry_run:
                print(f"{key}: {len(chunk)} files")
                continue
            n = import_batch(folder, album, chunk)
            state[key] = {"files": len(chunk), "imported": n}
            _save(state)
            print(f"{key}: imported {n} of {len(chunk)}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__)
        sys.exit(2)
    opt = lambda k: args[args.index(k) + 1] if k in args else None
    run(Path(args[0]).expanduser(), folder=opt("--folder") or "Archive",
        only=opt("--only"), dry_run="--dry-run" in args)
