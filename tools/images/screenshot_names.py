#!/usr/bin/env python3
"""Date-first names for the ~/files/screenshots monofolder (2026-10-27; moved into ~/files 2026-10-28).

  Screenshot 2026-10-27 at 8.20.47 AM.png  →  2026-10-27_082047--ss.png

Sorting by name is then chronological, which macOS's own names are not
(AM/PM). Handles every pattern in the 2018-2026 Dropbox/Screenshots pile:
Dropbox's 24h "Screenshot 2018-10-02 13.45.10.png", macOS "at H.MM.SS"
with or without AM/PM (newer macOS puts a U+202F narrow no-break space
before AM/PM), "Screen Shot" (pre-Ventura), and " (2)" / "(2)" duplicate
suffixes. Anything else keeps its original name behind an mtime prefix:
  IMG_1234.jpg  →  2024-03-01_101500--dl--IMG_1234.jpg
Non-images (.htm, .pptx …) are skipped and listed, not copied.

Usage: screenshot_names.py migrate SRC DST [--dry-run] [--move]
       (copies, SRC untouched; --move renames instead, for a local staging copy)
"""
import datetime as dt
import os
import re
import shutil
import sys
import time
from pathlib import Path

DATED = re.compile(
    r"^Screen ?[Ss]hot (\d{4})-(\d{2})-(\d{2})(?: at)? "
    r"(\d{1,2})\.(\d{2})\.(\d{2})(?:[\s ]*([AP]M))?\s*(?:\((\d+)\))?$")
# Dropbox Camera Uploads names: "2017-03-02 10.11.12.png", burst "...-1.png".
CAMERA = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2})\.(\d{2})\.(\d{2})(?:-(\d+))?$")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".tif", ".tiff",
             ".bmp", ".mov", ".mp4"}
STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}--")


def new_name(name: str, mtime: float, source: str = "ss") -> str:
    if STAMPED.match(name):
        return name  # already converted
    stem, ext = os.path.splitext(name)
    m = DATED.match(stem)
    if m:
        y, mo, d, h, mi, s, ampm, dup = m.groups()
        h = int(h)
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        out = f"{y}-{mo}-{d}_{h:02d}{mi}{s}--{source}"
        if dup:
            out += f"-{dup}"
        return out + ext.lower()
    m = CAMERA.match(stem)
    if m:
        y, mo, d, h, mi, s, dup = m.groups()
        out = f"{y}-{mo}-{d}_{h}{mi}{s}--{source}"
        if dup:
            out += f"-{int(dup) + 1}"  # Dropbox's -1 is the 2nd shot that second
        return out + ext.lower()
    # Not a screenshot name: in practice a saved/downloaded image (Facebook
    # `_n.jpg`s, article art) that landed in the folder, so tagged dl.
    t = dt.datetime.fromtimestamp(mtime)
    return f"{t:%Y-%m-%d_%H%M%S}--dl--{name}"


def _free(dst: Path, name: str) -> Path:
    p = dst / name
    if not p.exists():
        return p
    stem, ext = os.path.splitext(name)
    i = 2
    while (dst / f"{stem}-{i}{ext}").exists():
        i += 1
    return dst / f"{stem}-{i}{ext}"


def _copy_online_only(f: Path, target: Path, tries: int = 8) -> None:
    """Plain read, not shutil.copy2: macOS's fcopyfile is refused (EPERM) on
    Dropbox online-only placeholders. A read of a not-yet-downloaded file
    also fails with EPERM, but it kicks off the download, so retry with
    backoff until the bytes land."""
    for i in range(tries):
        try:
            data = f.read_bytes()
            break
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(min(0.5 * 2 ** i, 15))
    target.write_bytes(data)
    shutil.copystat(f, target)


def migrate(src: Path, dst: Path, dry_run: bool = False, move: bool = False) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in IMAGE_EXT:
            print(f"skip (not an image): {f.name}")
            continue
        name = new_name(f.name, f.stat().st_mtime)
        done = dst / name
        if done.exists() and done.stat().st_size == f.stat().st_size:
            continue  # copied by an earlier (interrupted) run
        target = _free(dst, name)
        if dry_run:
            print(f"{f.name}  →  {target.name}")
        else:
            if move:
                f.rename(target)  # same disk: no second copy (Ix staging, 2026-10-27)
            else:
                _copy_online_only(f, target)
        n += 1
    return n


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "migrate":
        print(migrate(Path(sys.argv[2]).expanduser(), Path(sys.argv[3]).expanduser(),
                      "--dry-run" in sys.argv, "--move" in sys.argv), "files")
    else:
        print(__doc__)
        sys.exit(2)
