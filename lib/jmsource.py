"""Record which surface a write came through, for JM Dash's source dimension.

SOURCE is the surface JM interacted through, not the data's origin system:

  cli     first-party terminal tools (did-fast, dtd, janus TUI, /inbound, Claude skills)
  1p-app  first-party apps with a UI (janus phone view, /卯 wakeup web app, future own app)
  3p-app  third-party apps (Excel, toggl.com, Todoist apps, native mail apps)
  watch   watch complications

`via` names the specific tool or app (did-fast, excel, quick-close, ...).

Neither Toggl nor Todoist reports which client wrote an entry, so first-party
tools log their own writes here and the dashboard treats anything absent from
the log as 3p-app (or `unknown` before the per-metric cutover, see CUTOVER).

One append-only JSONL file per host per month under the vault, so Syncthing
never has two machines appending to the same file. Recording is best-effort:
it must never break the write it describes, so every failure is swallowed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ("cli", "1p-app", "3p-app", "watch")

_DEFAULT_LOG_DIR = Path.home() / "vault" / "i447" / "i446" / "source-log"
LOG_DIR = _DEFAULT_LOG_DIR
_HOST_FILE = Path.home() / ".claude" / ".host-name"

# First moment every first-party writer of a metric is instrumented. Earlier
# events are `unknown`, never 3p-app: absence from the log says nothing about
# where they came from. None = not cut over yet. Still missing (2026-10-05):
# task closes in tools/did/did-fast.py (two raw /close calls) and Claude's
# hosted Todoist MCP; time needs janus and the toggl MCP server restarted on
# the new toggl_api.
CUTOVER = {
    "task": None,
    "time": None,
}


def _host() -> str:
    try:
        name = _HOST_FILE.read_text().strip()
    except OSError:
        name = ""
    return (name or socket.gethostname().split(".")[0] or "unknown").lower()


def default_via() -> str:
    """Tool name for the `via` field: $JMSOURCE_VIA, else the entry script."""
    if os.environ.get("JMSOURCE_VIA"):
        return os.environ["JMSOURCE_VIA"]
    stem = Path(sys.argv[0] or "").stem
    # `python3 -`, `python3 -c`, and the REPL have no script name.
    return "inline" if stem in ("", "-", "-c", "__main__") else stem


def default_source() -> str:
    s = os.environ.get("JMSOURCE", "cli")
    return s if s in SOURCES else "cli"


def _local_iso(now: datetime) -> str:
    """`now` in the active zone (lib/daytime, honors /travel), with offset, so
    the dashboard buckets each event by the local day it happened in even
    after the zone changes."""
    try:
        lib = str(Path(__file__).parent)
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import daytime
        return now.astimezone(daytime.active_zone()).isoformat(timespec="seconds")
    except Exception:
        return now.astimezone().isoformat(timespec="seconds")


def record(metric: str, ext_id, *, source: str | None = None,
           via: str | None = None, ok: bool = True, **extra) -> None:
    """Append one event. `metric` is "task" or "time"; `ext_id` is the
    Todoist task id or Toggl entry id. Call after the API write returns."""
    if LOG_DIR == _DEFAULT_LOG_DIR and "PYTEST_CURRENT_TEST" in os.environ:
        return  # tests exercising the hooked write paths must not touch the real log
    try:
        now = datetime.now(timezone.utc)
        row = {
            "ts": now.isoformat(timespec="seconds"),
            "local": _local_iso(now),
            "metric": metric,
            "ext_id": str(ext_id),
            "source": source or default_source(),
            "via": via or default_via(),
            "host": _host(),
        }
        if not ok:
            row["ok"] = False
        row.update(extra)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"{row['host']}-{now:%Y-%m}.jsonl"
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)  # one write() per line: no torn interleaving
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def load(months: list[str] | None = None) -> list[dict]:
    """All recorded rows across hosts, optionally limited to YYYY-MM months."""
    rows = []
    if not LOG_DIR.exists():
        return rows
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        if months and not any(path.stem.endswith(m) for m in months):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # torn tail from a live append
        except OSError:
            continue
    return rows
