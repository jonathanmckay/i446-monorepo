#!/usr/bin/env python3
"""toggl_db — JM's own store of Toggl time entries (SQLite), 2026-10-21.

Why: nothing local held the entries themselves. The dashboard cache
(.toggl-daily-cache.json) holds per-day totals and its backfill capped every
60-day Reports window at 10 pages of 50 rows, so ~25% of 2026's days were
silently absent; the 0r daily notes and the jbm calendar start in June. This
file is the independent record every consumer (dashboard, 1s897, 1n, 2n, 1i9)
can read without touching Toggl's API or its rate limits.

Store:  ~/vault/i156/toggl.sqlite  (override: $TOGGL_DB)
  - ONE writer: Ix (nightly cron). Straylight reads the Syncthing copy.
  - Every write goes to a temp copy that is renamed over the file, and the
    journal mode is DELETE (no -wal sidecar), so Syncthing only ever sees a
    complete database. Git ignores it (binary churn, same reason Excel lives
    in OneDrive).
  - entries: one row per Toggl entry, keyed by Toggl's id; `day` is the
    local calendar day in HOME_TZ (fixed, not the /travel zone, so a rebuild
    buckets the same entry into the same day every time).

Sync: Reports v3 search/time_entries, 7-day windows, paginated until the API
stops handing out x-next-id (no page cap), stopping to wait when the quota
header runs low, and FAILING LOUDLY on an HTTP error instead of keeping a
partial window. A window that completes cleanly also deletes rows for its
days that the API no longer returns (entries deleted in Toggl).

Usage:
  toggl_db.py sync   [--days N] [--window 7] [--export]   # last N days (default 14)
  toggl_db.py backfill --since YYYY-MM-DD [--export]       # history, same path
  toggl_db.py export-cache [--path FILE]                   # rewrite the dashboard json from the db
  toggl_db.py status                                       # coverage + gaps
  toggl_db.py verify [--days 7]                            # per-day seconds vs the live v9 API
  toggl_db.py query --start D --end D [--project CODE]     # daily totals
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import daytime  # noqa: E402

HOME_TZ = daytime.HOME_TZ
DB_PATH = Path(os.environ.get("TOGGL_DB") or (Path.home() / "vault" / "i156" / "toggl.sqlite"))
DASH_DIR = Path.home() / "i446-monorepo" / "tools" / "personal-dashboard"
CACHE_PATH = DASH_DIR / ".toggl-daily-cache.json"
TAGS_CACHE = Path.home() / ".cache" / "toggl-tags.json"
WORKSPACE_ID = int(os.environ.get("TOGGL_WORKSPACE_ID", "2092616"))
REPORTS_URL = f"https://api.track.toggl.com/reports/api/v3/workspace/{WORKSPACE_ID}/search/time_entries"
LIVE_URL = "https://api.track.toggl.com/api/v9/me/time_entries"
QUOTA_FLOOR = 20          # pause when x-toggl-quota-remaining drops under this
MAX_PAGES = 400           # runaway guard, not a cap: windows normally fit in ONE page (see PAGE_SIZE)
# Measured 2026-10-21: with the default page size (50) the cursor pagination
# of search/time_entries is unreliable here — page 2 came back EMPTY when
# the x-next-timestamp cursor was echoed and came back with the WRONG rows
# when it was not, so the old backfill lost a third of two days' entries.
# Asking for a large page returns the whole window in one response
# (99 entries for 2 days, no x-next-id), so that is the primary path; the
# cursor (all three fields) is only followed if the API still hands one out.
PAGE_SIZE = 1000
NO_PROJECT = "no project"


def _backfill_mod():
    """PROJECT_ID_TO_CODE and the .env / ~/.claude.json credential loader live in
    the dashboard's backfill script; load it by path (it has no Flask deps)."""
    spec = importlib.util.spec_from_file_location("backfill_toggl_cache", DASH_DIR / "backfill_toggl_cache.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backfill_toggl_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


def project_codes() -> dict[int, str]:
    return dict(_backfill_mod().PROJECT_ID_TO_CODE)


def creds() -> str:
    _backfill_mod()._load_env()
    key = os.environ.get("TOGGL_API_KEY", "")
    if not key:
        sys.exit("ERROR: TOGGL_API_KEY not set (tools/personal-dashboard/.env or ~/.claude.json toggl_server env)")
    return base64.b64encode(f"{key}:api_token".encode()).decode()


def tag_names() -> dict[str, str]:
    try:
        return {str(k): v for k, v in json.loads(TAGS_CACHE.read_text()).get("tags", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


# ---------------------------------------------------------------------------
# schema + atomic writer
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL,
  start TEXT NOT NULL,
  stop TEXT,
  seconds INTEGER NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  project_id INTEGER,
  project TEXT NOT NULL,
  tag_ids TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]',
  at TEXT,
  fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_day ON entries(day);
CREATE INDEX IF NOT EXISTS entries_project_day ON entries(project, day);
CREATE TABLE IF NOT EXISTS sync_log (
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  pages INTEGER NOT NULL,
  rows INTEGER NOT NULL,
  deleted INTEGER NOT NULL,
  complete INTEGER NOT NULL
);
"""


def connect_ro(path: Path = DB_PATH) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"no database at {path} (run sync/backfill on the writer host)")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def writer(path: Path = DB_PATH):
    """Modify a temp copy, then rename it over the real file: readers (and
    Syncthing) never see a half-written database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    if path.exists():
        shutil.copyfile(path, tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
        conn.close()
        os.replace(tmp, path)
    except BaseException:
        conn.close()
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
class FetchError(RuntimeError):
    pass


def _post(url: str, body: dict, auth: str, timeout: int = 30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {k.lower(): v for k, v in resp.getheaders()}, json.loads(resp.read())


def _get(url: str, auth: str, timeout: int = 30):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def local_day(start_iso: str) -> str:
    return datetime.fromisoformat(start_iso).astimezone(HOME_TZ).date().isoformat()


def fetch_window(auth: str, start: date, end: date, codes: dict[int, str], tags: dict[str, str],
                 post=_post, sleep=time.sleep, log=print) -> tuple[list[dict], int]:
    """All entries whose local day is in [start, end], as row dicts for the
    entries table, plus the page count. Raises FetchError on any HTTP/parse
    error: a partial window must never look like a complete one."""
    body = {"start_date": start.isoformat(), "end_date": end.isoformat(), "page_size": PAGE_SIZE}
    out: list[dict] = []
    now = int(time.time())
    for page in range(1, MAX_PAGES + 1):
        try:
            hdrs, rows = post(REPORTS_URL, body, auth)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
            raise FetchError(f"{start}..{end} page {page}: {e!r}") from e
        for row in rows:
            pid = row.get("project_id")
            code = codes.get(pid, NO_PROJECT) if pid else NO_PROJECT
            tids = [str(t) for t in (row.get("tag_ids") or [])]
            for te in row.get("time_entries", []):
                secs = te.get("seconds") or 0
                if secs <= 0 or not te.get("start"):
                    continue
                try:
                    day = local_day(te["start"])
                except (ValueError, TypeError):
                    continue
                if not (start.isoformat() <= day <= end.isoformat()):
                    continue
                out.append({
                    "id": int(te["id"]), "day": day, "start": te["start"], "stop": te.get("stop"),
                    "seconds": int(secs), "description": row.get("description") or "",
                    "project_id": pid, "project": code, "tag_ids": json.dumps(tids),
                    "tags": json.dumps([tags.get(t, t) for t in tids], ensure_ascii=False),
                    "at": te.get("at"), "fetched_at": now,
                })
        remaining = hdrs.get("x-toggl-quota-remaining")
        if remaining is not None and int(remaining) < QUOTA_FLOOR:
            wait = int(hdrs.get("x-toggl-quota-resets-in") or 60) + 2
            log(f"    quota {remaining} left: sleeping {wait}s for the reset")
            sleep(wait)
        nid, nrow, nts = hdrs.get("x-next-id"), hdrs.get("x-next-row-number"), hdrs.get("x-next-timestamp")
        if not nid or not nrow or not rows:
            return out, page
        body = {**body, "first_id": int(nid), "first_row_number": int(nrow)}
        if nts:
            body["first_timestamp"] = int(nts)
        sleep(0.2)
    raise FetchError(f"{start}..{end}: still paginating after {MAX_PAGES} pages")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
UPSERT = """INSERT INTO entries (id, day, start, stop, seconds, description, project_id, project, tag_ids, tags, at, fetched_at)
VALUES (:id, :day, :start, :stop, :seconds, :description, :project_id, :project, :tag_ids, :tags, :at, :fetched_at)
ON CONFLICT(id) DO UPDATE SET day=excluded.day, start=excluded.start, stop=excluded.stop, seconds=excluded.seconds,
  description=excluded.description, project_id=excluded.project_id, project=excluded.project, tag_ids=excluded.tag_ids,
  tags=excluded.tags, at=excluded.at, fetched_at=excluded.fetched_at"""


def windows(start: date, end: date, size: int):
    """Oldest first so an interrupted backfill leaves a contiguous history."""
    cur = start
    while cur <= end:
        yield cur, min(end, cur + timedelta(days=size - 1))
        cur = cur + timedelta(days=size)


def apply_window(conn: sqlite3.Connection, w_start: date, w_end: date, rows: list[dict], pages: int,
                 now: int | None = None) -> tuple[int, int]:
    """Upsert the fetched rows and delete this window's rows the API no longer
    returns (only called for windows that fetched completely)."""
    now = now or int(time.time())
    conn.executemany(UPSERT, rows)
    ids = [r["id"] for r in rows]
    cur = conn.execute("SELECT id FROM entries WHERE day BETWEEN ? AND ?", (w_start.isoformat(), w_end.isoformat()))
    gone = [r[0] for r in cur if r[0] not in set(ids)]
    if gone:
        conn.executemany("DELETE FROM entries WHERE id = ?", [(g,) for g in gone])
    conn.execute("INSERT INTO sync_log VALUES (?,?,?,?,?,?,1)",
                 (w_start.isoformat(), w_end.isoformat(), now, pages, len(rows), len(gone)))
    return len(rows), len(gone)


def sync_range(start: date, end: date, window: int = 7, path: Path = DB_PATH, fetch=None, log=print) -> int:
    """Fetch [start, end] window by window into the db. Each window is a
    separate atomic write, so a crash mid-run keeps every completed window."""
    fetch = fetch or (lambda s, e, _a=creds(), _c=project_codes(), _t=tag_names(): fetch_window(_a, s, e, _c, _t, log=log))
    total = 0
    for w_start, w_end in windows(start, end, window):
        t0 = time.time()
        rows, pages = fetch(w_start, w_end)
        with writer(path) as conn:
            n, gone = apply_window(conn, w_start, w_end, rows, pages)
        total += n
        log(f"{w_start}..{w_end}: {n} entries, {pages} page(s), -{gone} gone, {time.time() - t0:.1f}s")
    return total


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
def daily_totals(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict[str, dict]:
    """{day: {"minutes": {code: n}, "entries": {code: n}}} — the dashboard cache
    shape. Every day between the first and last synced day is present, so an
    empty day is an explicit {} rather than a missing key."""
    q = "SELECT day, project, seconds FROM entries"
    args: list = []
    if start and end:
        q += " WHERE day BETWEEN ? AND ?"
        args = [start, end]
    out: dict[str, dict] = {}
    days = []
    for day, code, secs in conn.execute(q, args):
        d = out.setdefault(day, {"minutes": defaultdict(int), "entries": defaultdict(int)})
        d["minutes"][code] += secs // 60
        d["entries"][code] += 1
        days.append(day)
    lo, hi = (start, end) if start and end else ((min(days), max(days)) if days else (None, None))
    if lo and hi:
        cur = date.fromisoformat(lo)
        while cur <= date.fromisoformat(hi):
            out.setdefault(cur.isoformat(), {"minutes": {}, "entries": {}})
            cur += timedelta(days=1)
    return {d: {"minutes": dict(v["minutes"]), "entries": dict(v["entries"])} for d, v in sorted(out.items())}


def export_cache(conn: sqlite3.Connection, path: Path = CACHE_PATH) -> int:
    data = daily_totals(conn)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)
    return len(data)


def status(conn: sqlite3.Connection) -> str:
    n, lo, hi = conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM entries").fetchone()
    if not n:
        return "empty"
    days = {r[0] for r in conn.execute("SELECT DISTINCT day FROM entries")}
    span = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
    gaps = [d for d in (date.fromisoformat(lo) + timedelta(i) for i in range(span)) if d.isoformat() not in days]
    last = conn.execute("SELECT MAX(fetched_at) FROM sync_log").fetchone()[0]
    when = datetime.fromtimestamp(last, HOME_TZ).strftime("%Y-%m-%d %H:%M") if last else "never"
    lines = [f"{n} entries, {lo}..{hi} ({span} days, {len(days)} with entries, {len(gaps)} empty), last sync {when}"]
    if gaps:
        lines.append("empty days: " + ", ".join(g.isoformat() for g in gaps[:20]) + (" …" if len(gaps) > 20 else ""))
    return "\n".join(lines)


def verify(conn: sqlite3.Connection, auth: str, days: int, get=_get, log=print) -> list[str]:
    """Per-day seconds in the db vs the live v9 endpoint for the last N days."""
    end = date.today()
    start = end - timedelta(days=days)
    # v9 end_date is exclusive and UTC-based: a local evening entry sits on the
    # next UTC day, so ask two days past `end` and bucket by local day below.
    live = get(f"{LIVE_URL}?start_date={start.isoformat()}&end_date={(end + timedelta(days=2)).isoformat()}", auth)
    live_secs: dict[str, int] = defaultdict(int)
    for e in live:
        if (e.get("duration") or 0) > 0 and e.get("start"):
            live_secs[local_day(e["start"])] += int(e["duration"])
    db_secs = {d: s for d, s in conn.execute("SELECT day, SUM(seconds) FROM entries WHERE day BETWEEN ? AND ? GROUP BY day",
                                               (start.isoformat(), end.isoformat()))}
    bad = []
    for i in range(days + 1):
        d = (start + timedelta(i)).isoformat()
        if abs(live_secs.get(d, 0) - db_secs.get(d, 0)) >= 60:   # the two endpoints round seconds differently
            bad.append(f"{d}: db {db_secs.get(d, 0) // 60}m vs live {live_secs.get(d, 0) // 60}m")
    return bad


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--days", type=int, default=14); s.add_argument("--window", type=int, default=7)
    s.add_argument("--export", action="store_true")
    b = sub.add_parser("backfill"); b.add_argument("--since", required=True); b.add_argument("--window", type=int, default=7)
    b.add_argument("--export", action="store_true")
    e = sub.add_parser("export-cache"); e.add_argument("--path", default=str(CACHE_PATH))
    sub.add_parser("status")
    v = sub.add_parser("verify"); v.add_argument("--days", type=int, default=7)
    q = sub.add_parser("query"); q.add_argument("--start", required=True); q.add_argument("--end", required=True)
    q.add_argument("--project")
    a = ap.parse_args(argv)
    today = date.today()
    if a.cmd in ("sync", "backfill"):
        start = today - timedelta(days=a.days) if a.cmd == "sync" else date.fromisoformat(a.since)
        n = sync_range(start, today, a.window)
        print(f"synced {n} entries {start}..{today} into {DB_PATH}")
        if a.export:
            with connect_ro() as conn:
                print(f"exported {export_cache(conn)} days to {CACHE_PATH}")
        return 0
    with connect_ro() as conn:
        if a.cmd == "export-cache":
            print(f"exported {export_cache(conn, Path(a.path))} days to {a.path}")
        elif a.cmd == "status":
            print(status(conn))
        elif a.cmd == "verify":
            bad = verify(conn, creds(), a.days)
            print("\n".join(bad) if bad else f"db matches live for the last {a.days} days")
            return 1 if bad else 0
        elif a.cmd == "query":
            for d, v in daily_totals(conn, a.start, a.end).items():
                mins = v["minutes"] if not a.project else {a.project: v["minutes"].get(a.project, 0)}
                print(d, json.dumps(mins, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
