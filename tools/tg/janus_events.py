"""janus usage events, in dtd's format so the two logs interoperate.

Same schema as dtd's $DTD_EVENTS (tools/did/dtd.sh, 2026-10-10), one TSV line
per event:
    epoch \t session \t event \t key \t ids(comma) \t extra
events: session (start/end), action (a key or API call; ids = Toggl entry ids
or task ids; extra = typed text / target), outcome (result of an action; extra
= seconds and/or reason), view (day navigation; extra = viewed date).

Written to ~/.local/state/jm/janus-events.tsv beside dtd-events.tsv
(machine-local; host is on the session line). A reader can union both files
and tell the apps apart by filename. Never raises: logging must not cost the
UI anything.
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

PATH = Path(os.environ.get("JANUS_EVENTS")
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "jm" / "janus-events.tsv")
# dtd's session id shape: <pid>-<epoch>-<random>
SESSION = f"{os.getpid()}-{int(time.time())}-{random.randint(0, 32767)}"


def _clean(v) -> str:
    return str(v if v is not None else "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def emit(event: str, key: str = "", ids=(), extra="", session: str | None = None) -> None:
    if isinstance(ids, (str, int)):
        ids = [ids]
    line = "\t".join([f"{time.time():.6f}", _clean(session or SESSION), _clean(event), _clean(key),
                      ",".join(_clean(i) for i in ids if i not in (None, "")), _clean(extra)[:300]])
    path = PATH
    if ("pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get("JANUS_EVENTS"):
        return  # a test run must never write the real usage log
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
