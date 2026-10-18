"""janus usage events, in dtd's format so the two logs interoperate.

Same schema as dtd's $DTD_EVENTS (tools/did/dtd.sh, 2026-10-10), one TSV line
per event:
    epoch \t session \t event \t key \t ids(comma) \t extra
events: session (start/end), action (a key or API call; ids = Toggl entry ids
or task ids; extra = typed text / target), outcome (result of an action; extra
= seconds and/or reason), view (day navigation; extra = viewed date).

Where it lives (2026-10-17): one file PER HOST in the Syncthing-synced vault,
~/vault/z_meta/events/janus-events.<host>.tsv, so every host ends up holding
every host's log and a reader can union them with read_all(). (The first
week's log sat machine-local in ~/.local/state/jm and an analysis that read
one machine missed the days spent on the other.) Per-host files mean the two
hosts never append to the same synced file, so Syncthing has nothing to
conflict on. z_meta/events/ is git-ignored in the vault: Syncthing carries
it, git does not. A legacy local file is copied into the per-host file once
and renamed *.migrated.

Env: JANUS_EVENTS (exact file; tests) beats JANUS_EVENTS_DIR (directory)
beats the vault default. Never raises: logging must not cost the UI anything.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import time
from pathlib import Path

HOST = os.uname().nodename.split(".")[0].lower()
SYNC_DIR = Path(os.environ.get("JANUS_EVENTS_DIR") or Path.home() / "vault" / "z_meta" / "events")
PATH = Path(os.environ.get("JANUS_EVENTS") or SYNC_DIR / f"janus-events.{HOST}.tsv")
LEGACY = (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
          / "jm" / "janus-events.tsv")
# dtd's session id shape: <pid>-<epoch>-<random>
SESSION = f"{os.getpid()}-{int(time.time())}-{random.randint(0, 32767)}"
FIELDS = ("ts", "session", "event", "key", "ids", "extra")


def _under_pytest() -> bool:
    return bool(("pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"))
                and not os.environ.get("JANUS_EVENTS"))


def _migrate_legacy() -> None:
    """Copy the pre-2026-10-17 machine-local log into the per-host synced
    file once; the old file is kept as *.migrated so nothing is lost and
    nothing is counted twice."""
    if os.environ.get("JANUS_EVENTS") or _under_pytest():
        return
    try:
        if LEGACY.exists() and not PATH.exists():
            PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(LEGACY, PATH)
            LEGACY.rename(LEGACY.with_suffix(".tsv.migrated"))
    except OSError:
        pass


_migrate_legacy()


def _clean(v) -> str:
    return str(v if v is not None else "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def emit(event: str, key: str = "", ids=(), extra="", session: str | None = None) -> None:
    if isinstance(ids, (str, int)):
        ids = [ids]
    line = "\t".join([f"{time.time():.6f}", _clean(session or SESSION), _clean(event), _clean(key),
                      ",".join(_clean(i) for i in ids if i not in (None, "")), _clean(extra)[:300]])
    if _under_pytest():
        return  # a test run must never write the real usage log
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_all(sync_dir: Path | None = None, pattern: str = "janus-events.*.tsv") -> list[dict]:
    """Every host's events, oldest first. Each row: ts (float), host (from the
    filename), session, event, key, ids (list[str]), extra. Malformed lines
    are skipped. Reads nothing but the synced per-host files."""
    rows: list[dict] = []
    d = Path(sync_dir) if sync_dir is not None else SYNC_DIR
    for f in sorted(d.glob(pattern)) if d.is_dir() else []:
        host = f.name[len("janus-events."):-len(".tsv")] if f.name.startswith("janus-events.") else f.stem
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            cols = line.split("\t")
            if len(cols) < 6:
                continue
            try:
                ts = float(cols[0])
            except ValueError:
                continue
            rows.append({"ts": ts, "host": host, "session": cols[1], "event": cols[2],
                         "key": cols[3], "ids": [i for i in cols[4].split(",") if i],
                         "extra": "\t".join(cols[5:])})
    rows.sort(key=lambda r: r["ts"])
    return rows
