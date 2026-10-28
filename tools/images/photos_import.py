#!/usr/bin/env python3
"""Import a folder tree of old photos into Apple Photos (2026-10-27: the
Dropbox Archive/Pictures merge, vault i446-photos-and-screenshots).

Each top-level folder becomes an album inside a Photos folder ("Archive" by
default), keeping the old trip/era organization. Photos' own duplicate check
stays on, so anything already in the library is skipped. Screenshot-named
images go to ~/files/screenshots under the date-first name instead (the image
rules: screenshots never live in Photos). Files Photos can't take (mp3 …)
are listed, not touched.

Progress is recorded per batch in STATE, so a rerun resumes.

Usage: photos_import.py ROOT [--folder Archive] [--only NAME] [--album NAME]
                         [--min-free-gb N] [--dry-run]
  --album puts every file in one album (default: one per top-level folder).
"""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
import sys
from pathlib import Path

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("screenshot_names", HERE / "screenshot_names.py")
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)

MEDIA = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".tif", ".tiff", ".bmp", ".psd",
         ".arw", ".cr2", ".nef", ".dng", ".mov", ".mp4", ".m4v", ".avi", ".mts"}
BATCH = 200
SCREENSHOTS = Path.home() / "files" / "screenshots"
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
        if "-1712" in r.stderr:  # AppleEvent timed out: Photos wedged, same as a silent 0
            print(f"Photos timed out: {r.stderr.strip()[:120]}", flush=True)
            return 0
        raise RuntimeError(r.stderr.strip())
    return int(r.stdout.strip() or 0)


def photos_responsive(timeout_s: int = 20) -> bool:
    r = subprocess.run(["osascript", "-e", f"with timeout of {timeout_s} seconds",
                        "-e", 'tell application "Photos" to count of albums',
                        "-e", "end timeout"], capture_output=True, text=True)
    return r.returncode == 0


def restart_photos() -> None:
    """Photos' scripted import wedges silently after a few thousand files
    (2026-10-27: 2,440 in, every later batch returned 0 with no error and
    nothing logged); a restart clears it. Relaunched hidden (-g -j) so it
    doesn't steal focus."""
    subprocess.run(["osascript", "-e", "with timeout of 20 seconds",
                    "-e", 'tell application "Photos" to quit', "-e", "end timeout"],
                   capture_output=True)
    for _ in range(30):
        if subprocess.run(["pgrep", "-x", "Photos"], capture_output=True).returncode:
            break
        time.sleep(1)
    else:
        # A hung Photos never processes the quit event; without this the same
        # frozen instance survived every "restart" (Ix, 2026-10-27, 43 min).
        subprocess.run(["killall", "-9", "Photos"], capture_output=True)
        time.sleep(3)
    # On Ix the wedge was in photolibraryd itself (Photos hung at 0% CPU
    # waiting on it); launchd restarts it on demand.
    subprocess.run(["killall", "photolibraryd"], capture_output=True)
    time.sleep(3)
    subprocess.run(["open", "-g", "-j", "-a", "Photos"])
    # Wait until the library actually answers (it can take a minute+ after
    # photolibraryd restarts), not a fixed sleep.
    for _ in range(20):
        time.sleep(15)
        if photos_responsive():
            return
    print("Photos still unresponsive 5 min after restart", flush=True)


def wait_for_space(min_free_gb: float, max_wait_s: int = 4 * 3600) -> None:
    """Block until the home volume has min_free_gb free. With "Optimize Mac
    Storage", Photos frees uploaded originals only under disk pressure, so a
    long import is paced by iCloud upload; better to wait than fill a server."""
    waited = 0
    while shutil.disk_usage(Path.home()).free / 1e9 < min_free_gb:
        if waited >= max_wait_s:
            raise SystemExit(f"free space stayed under {min_free_gb} GB for {waited // 60} min; stopping")
        if waited == 0:
            print(f"free space under {min_free_gb} GB; waiting for iCloud upload to free some", flush=True)
        time.sleep(300)
        waited += 300


def run(root: Path, folder: str = "Archive", only=None, dry_run=False,
        album_name=None, min_free_gb: float = 0) -> None:
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
            name = sn.new_name(p.name, p.stat().st_mtime)
            done = SCREENSHOTS / name
            if done.exists() and done.stat().st_size == p.stat().st_size:
                continue  # copied by an earlier run
            shutil.copy2(p, sn._free(SCREENSHOTS, name))
    state = _load()
    failed_streak = 0
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
            if min_free_gb:
                wait_for_space(min_free_gb)
            if not photos_responsive():
                print(f"{key}: Photos unresponsive before batch; restarting", flush=True)
                restart_photos()
            n = import_batch(folder, album_name or album, chunk)
            if n == 0:
                print(f"{key}: 0 imported, restarting Photos and retrying", flush=True)
                restart_photos()
                n = import_batch(folder, album_name or album, chunk)
                if n == 0:
                    # A batch Photos can't take at all (e.g. two old-camera
                    # SCHD*.MOV videos) is recorded and skipped; three in a
                    # row means Photos itself is stuck, so stop.
                    failed_streak += 1
                    print(f"{key}: still 0 after a Photos restart; skipped", flush=True)
                    if failed_streak >= 3:
                        _save(state)
                        raise SystemExit("3 batches in a row imported nothing; stopping")
            if n:
                failed_streak = 0
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
        only=opt("--only"), dry_run="--dry-run" in args,
        album_name=opt("--album"), min_free_gb=float(opt("--min-free-gb") or 0))
