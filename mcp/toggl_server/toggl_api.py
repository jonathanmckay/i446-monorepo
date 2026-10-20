import base64
import json
import sys
import os
import signal
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from . import throttle
from .config import TOGGL_API_KEY, TOGGL_WORKSPACE_ID

BASE_URL = "https://api.track.toggl.com/api/v9"
# Reports API v3: workspace-scoped, so it is charged to the plan's hourly
# quota (600/hr on Premium) instead of the fixed 30/hr that every /me/* read
# shares on every plan (Toggl docs, 2026-10-20). All entry READS go through
# it; see get_entries. It never returns the running entry (verified live).
REPORTS_URL = "https://api.track.toggl.com/reports/api/v3"
REPORTS_PAGE_SIZE = 1000
# Reports rows carry tag_ids, not names; the workspace tag list (one
# workspace-bucket call) is cached here and refetched only when an unknown
# id shows up (a tag created since).
TAGS_CACHE = Path.home() / ".cache" / "toggl-tags.json"

# janus polls the running timer only every 30s; signalling it after a
# timer-state change makes it refresh immediately. This lives in toggl_api
# (the shared HTTP layer) so EVERY caller benefits — the MCP server and
# /d357, not just the toggl_cli path that previously had its own nudge.
JANUS_PID = Path.home() / ".cache" / "janus.pid"

# Shared running-timer cache. /me/time_entries/current is the ONE read that
# has no workspace-scoped equivalent (every /workspaces/... variant is a 405),
# so it is charged to the 30/hr user bucket on every plan. Several processes
# poll it (janus every 30s, every open dtd picker via dtd-ticker, …), so:
#  - every live get_current() write-throughs here and pollers read this file
#    within CURRENT_CACHE_TTL instead of each hitting the API (N pollers ->
#    ~one network read per window: 12/hr at the 5-minute TTL);
#  - every MUTATION write-throughs the running state it already knows from
#    its own response (start -> the new entry, stop/delete -> None) instead of
#    invalidating and making every poller re-read. Before 2026-10-20 a morning
#    of 15 habit switches cost ~45 user-bucket reads and 402'd by 09:00.
# The TTL only bounds how late a timer changed from ANOTHER client (the phone
# app) is noticed; the elapsed clock is computed from the entry's start.
CURRENT_CACHE = Path.home() / ".cache" / "toggl-current.json"
CURRENT_CACHE_TTL = 300.0  # seconds
# A reading older than this is not merged into get_entries as "running".
CURRENT_CACHE_MAX_MERGE_AGE = 600.0


def _notify_tui():
    """SIGUSR1 the running janus so it refreshes now instead of on its poll.
    Best-effort: a missing/stale pid file or dead process is ignored."""
    try:
        os.kill(int(JANUS_PID.read_text().strip()), signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


def _write_current_cache(entry):
    """Persist the last-known running entry (or None when idle) so concurrent
    pollers can share one fetch. Atomic tmp+replace so a reader never sees a
    torn file. Best-effort: a write failure just means no sharing this round."""
    try:
        CURRENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CURRENT_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "entry": entry}))
        tmp.replace(CURRENT_CACHE)
    except OSError:
        pass


def _invalidate_current_cache():
    """Drop the cache so the next read fetches fresh. Only for callers that
    changed the running state through a path whose response they did not keep
    (janus's bulk edits); the mutation helpers below write-through instead."""
    try:
        CURRENT_CACHE.unlink(missing_ok=True)
    except OSError:
        pass


def _read_current_cache(max_age=None):
    """(found, entry) from the shared cache with NO network fallback. found is
    False when the file is missing, torn, or older than max_age (None = any
    age). Lets mutations and get_entries consult the last observation without
    spending a user-bucket read."""
    try:
        raw = json.loads(CURRENT_CACHE.read_text())
        if max_age is not None and time.time() - float(raw["ts"]) > max_age:
            return False, None
        return True, raw["entry"]
    except (OSError, ValueError, KeyError, TypeError):
        return False, None


def _cached_is(entry_id):
    """True when the shared cache's last observation says `entry_id` is the
    running entry."""
    found, cur = _read_current_cache()
    return bool(found and isinstance(cur, dict) and cur.get("id") == entry_id)


def _auth_header():
    creds = base64.b64encode(f"{TOGGL_API_KEY}:api_token".encode()).decode()
    return f"Basic {creds}"


# Toggl is a ~1 req/sec leaky bucket; bursts get 429. Defaults ride the backoff
# out (good for background/MCP callers). Interactive callers that must never
# freeze the UI (dtd's execute-silent action scripts) lower these via env so a
# 429 fails fast instead of sleeping up to ~60s mid-keystroke.
_MAX_429_RETRIES = int(os.environ.get("TOGGL_MAX_429_RETRIES", "3"))
_MAX_429_DELAY = float(os.environ.get("TOGGL_MAX_429_DELAY", "30"))


def _request(method, path, body=None, base=None, want_headers=False):
    """One Toggl HTTP call. `path` is relative to BASE_URL (v9) or `base`.

    Quota handling (2026-10-20): the path's hourly-quota bucket is checked
    FIRST; while that bucket is in a 402 cooldown the call raises at once with
    no network request (a send would be a guaranteed 402, and acquire()'s wait
    cap used to let exactly those through). A 402 arms only ITS bucket's
    cooldown for the server's own reset estimate; a 429 arms the short global
    one and is retried. When a successful response says the bucket just hit
    zero (X-Toggl-Quota-Remaining: 0) the cooldown is armed proactively, so
    the next caller doesn't have to eat the 402 to learn it.
    want_headers=True returns (result, headers) for paginated endpoints."""
    url = f"{base or BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    bucket = throttle.bucket_for(path)
    left = throttle.quota_remaining(bucket)
    if left > 0:
        raise RuntimeError(
            f"Toggl API {method} {path} -> 402 (hourly quota for the {bucket} bucket is "
            f"exhausted; resets in {int(left)}s; request not sent)")
    # On 429, honour Retry-After (Toggl doesn't always send it, so fall back to
    # capped exponential backoff). Without this a tripped limit raises straight
    # to the UI and the next poll re-trips it — the same pattern ibx/slack.py
    # and ibx/sync_external_replies.py already use for their APIs.
    for attempt in range(_MAX_429_RETRIES):
        throttle.acquire()  # client-side pacing, shared across all processes
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", _auth_header())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    result = json.loads(raw) if raw else None
                    headers = getattr(resp, "headers", None)
                    _note_quota_headers(headers, bucket)
                    if method != "GET":  # a mutation succeeded → wake janus now
                        _notify_tui()
                    return (result, headers) if want_headers else result
                return (None, None) if want_headers else None
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            if e.code == 402:
                # Hourly quota: the body says how long ("reset in N seconds"),
                # the header too. Arm THIS bucket only.
                est = throttle.parse_reset_seconds(error_body)
                if est is None:
                    est = _header_float(e.headers, "X-Toggl-Quota-Resets-In")
                throttle.note_quota_reset(est, bucket=bucket)
            elif e.code == 429:
                throttle.note_rate_limit()  # short global leaky-bucket cooldown
                if attempt < _MAX_429_RETRIES - 1:
                    retry_after = e.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                    time.sleep(min(delay, _MAX_429_DELAY))
                    continue
            raise RuntimeError(f"Toggl API {method} {path} -> {e.code}: {error_body}")


def _header_float(headers, name):
    try:
        v = headers.get(name) if headers is not None else None
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _note_quota_headers(headers, bucket):
    """Arm the bucket's cooldown when a SUCCESSFUL response reports the quota
    just ran out, so the next request doesn't have to 402 to find out."""
    rem = _header_float(headers, "X-Toggl-Quota-Remaining")
    if rem is not None and rem <= 0:
        throttle.note_quota_reset(_header_float(headers, "X-Toggl-Quota-Resets-In"), bucket=bucket)


def _record_create(entry):
    """Log a first-party create for JM Dash's source dimension (lib/jmsource).
    Toggl doesn't return which client made an entry, so the creator logs it.
    Creator wins: a later stop or edit through another surface doesn't
    change an entry's source."""
    try:
        if not isinstance(entry, dict) or not entry.get("id"):
            return
        lib = str(Path.home() / "i446-monorepo" / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import jmsource
        jmsource.record("time", entry["id"])
    except Exception:
        pass


def create_entry(description, start_iso, stop_iso, duration_sec, project_id=None, tags=None):
    body = {
        "description": description,
        "start": start_iso,
        "stop": stop_iso,
        "duration": duration_sec,
        "workspace_id": TOGGL_WORKSPACE_ID,
        "created_with": "mcp-toggl-custom",
    }
    if project_id:
        body["project_id"] = project_id
    if tags:
        body["tags"] = tags
    entry = _request("POST", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries", body)
    _record_create(entry)
    return entry


def start_timer(description, project_id=None, tags=None, start_time=None):
    import datetime
    if start_time:
        start = start_time  # ISO format string
    else:
        start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "description": description,
        "start": start,
        "duration": -1,
        "workspace_id": TOGGL_WORKSPACE_ID,
        "created_with": "mcp-toggl-custom",
    }
    if project_id:
        body["project_id"] = project_id
    if tags:
        body["tags"] = tags
    entry = _request("POST", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries", body)
    _record_create(entry)
    if isinstance(entry, dict):
        _write_current_cache(entry)  # we KNOW what's running now: no re-read
    return entry


def stop_timer(entry_id):
    entry = _request("PATCH", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}/stop")
    # Only one entry can run per user, so a successful stop means idle.
    _write_current_cache(None)
    # Value-tag 媒分 credit (2026-09-16): every stop path funnels through
    # here, so an explicitly #-1/#-2/#-3-tagged entry is credited exactly
    # once regardless of who stopped it. Best-effort; see tag_credits.py.
    if isinstance(entry, dict):
        _credit_value_tags(entry)
    return entry


def get_current():
    """Live fetch of the running entry (None when idle). Write-throughs to the
    shared cache so concurrent pollers can ride this fetch via get_current_cached.
    USER-BUCKET read (30/hr on every plan): prefer get_current_cached unless a
    just-changed-elsewhere timer must be caught right now."""
    entry = _request("GET", "/me/time_entries/current")
    _write_current_cache(entry)
    return entry


def get_current_cached(max_age=CURRENT_CACHE_TTL):
    """Running entry from the shared cache when it's younger than max_age, else a
    live get_current() (which refreshes the cache). Lets many pollers share ~one
    network read per max_age window. The footer/elapsed clock is computed from the
    entry's start time, so a slightly stale entry still renders an exact clock;
    only detection of a timer changed from ANOTHER client lags by up to max_age.
    Every mutation made through this module write-throughs the cache at once.
    Falls back to live on any cache problem (missing, torn, malformed)."""
    found, entry = _read_current_cache(max_age)
    if found:
        return entry
    return get_current()


def _date_part(value, default):
    """A date from a 'YYYY-MM-DD' or RFC3339 string (tz-aware ones converted
    to the active local zone first), a date/datetime, or `default` when None."""
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        d = value
    elif hasattr(value, "year") and not isinstance(value, str):
        return value  # a date
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(text)
        except ValueError:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        if len(text) <= 10:
            return d.date()
    if d.tzinfo is not None:
        d = d.astimezone(_local_tz())
    return d.date()


def _local_tz():
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import daytime
        return daytime.active_zone()
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo


def _tag_names(tag_ids):
    """tag id -> name for the given ids, via the disk-cached workspace tag
    list; refetched (one workspace-bucket call) when an id is unknown. Ids
    still unknown after that (fetch failed) fall back to their str()."""
    ids = {int(t) for t in tag_ids if t is not None}
    try:
        cache = {int(k): v for k, v in json.loads(TAGS_CACHE.read_text()).items()}
    except (OSError, ValueError, TypeError):
        cache = {}
    if ids - set(cache):
        try:
            fresh = _request("GET", f"/workspaces/{TOGGL_WORKSPACE_ID}/tags") or []
            cache = {int(t["id"]): t["name"] for t in fresh if "id" in t}
            TAGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = TAGS_CACHE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({str(k): v for k, v in cache.items()}))
            tmp.replace(TAGS_CACHE)
        except (RuntimeError, OSError, KeyError, TypeError, ValueError):
            pass
    return {i: cache.get(i, str(i)) for i in ids}


def _reports_search(start, end):
    """Every completed entry whose start falls on [start, end] (inclusive,
    workspace-local dates) from Reports v3, flattened to v9-shaped dicts.
    Paginates via X-Next-Row-Number."""
    out = []
    first_row = 1
    for _ in range(200):  # hard stop; a day has far fewer than 200k rows
        body = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                "page_size": REPORTS_PAGE_SIZE, "first_row_number": first_row}
        groups, headers = _request("POST", f"/workspace/{TOGGL_WORKSPACE_ID}/search/time_entries",
                                   body, base=REPORTS_URL, want_headers=True)
        if not groups:
            break
        for g in groups:
            for te in g.get("time_entries") or []:
                secs = te.get("seconds")
                out.append({
                    "id": te.get("id"),
                    "workspace_id": TOGGL_WORKSPACE_ID,
                    "user_id": g.get("user_id"),
                    "project_id": g.get("project_id"),
                    "task_id": g.get("task_id"),
                    "billable": g.get("billable"),
                    "description": g.get("description") or "",
                    "start": te.get("start"),
                    "stop": te.get("stop"),
                    "duration": int(secs) if secs is not None else 0,
                    "tag_ids": list(g.get("tag_ids") or []),
                    "at": te.get("at"),
                })
        nxt = _header_float(headers, "X-Next-Row-Number")
        if nxt is None or int(nxt) <= first_row:
            break
        first_row = int(nxt)
    all_ids = {t for e in out for t in e["tag_ids"]}
    names = _tag_names(all_ids) if all_ids else {}
    for e in out:
        e["tags"] = [names.get(int(t), str(t)) for t in e["tag_ids"]]
    return out


def get_entries(start_date=None, end_date=None, include_running=True):
    """Time entries starting on the local days [start_date, end_date), v9
    shaped (id, description, start, stop, duration -- negative while running
    --, project_id, tags as NAMES, ...). Same signature and shape as the old
    GET /me/time_entries wrapper so every caller moved at once, but sourced
    from the Reports API (workspace bucket, 600/hr) instead of /me (30/hr).

    Differences from v9 to know about: v9 filtered on UTC instants and
    end-exclusive; Reports filters on workspace-local DATES, inclusive, so
    end_date here is mapped to end_date - 1 day. Every existing caller widens
    its window by a day each side and clips locally, so this is a superset
    for them. RFC3339 bounds are reduced to their local date.

    Reports never returns the RUNNING entry; it is merged in from the shared
    current cache when that observation is recent (no network: a cold cache
    on a machine without janus simply yields no running row). Pass
    include_running=False when only completed time matters."""
    today = datetime.now(_local_tz()).date()
    start = _date_part(start_date, today)
    end_excl = _date_part(end_date, start + timedelta(days=1))
    end = max(start, end_excl - timedelta(days=1))
    entries = _reports_search(start, end)
    if include_running:
        found, cur = _read_current_cache(CURRENT_CACHE_MAX_MERGE_AGE)
        if found and isinstance(cur, dict) and cur.get("id") is not None:
            if all(e["id"] != cur.get("id") for e in entries):
                try:
                    cur_day = datetime.fromisoformat(
                        str(cur.get("start")).replace("Z", "+00:00")).astimezone(_local_tz()).date()
                except (ValueError, TypeError):
                    cur_day = None
                if cur_day is not None and start <= cur_day <= end:
                    entries.append(dict(cur))
    entries.sort(key=lambda e: e.get("start") or "")
    return entries


def get_projects():
    """List all workspace projects (id, name, active, ...)."""
    return _request("GET", f"/workspaces/{TOGGL_WORKSPACE_ID}/projects")


def _credit_value_tags(entry):
    """Value-tag 媒分 credit (2026-09-16): append an explicitly #-1/#-2/#-3
    tagged entry's minutes to its 0n column, once (journaled). Best-effort;
    see tag_credits.py. Attaches the human lines as entry["_tag_credits"]."""
    try:
        from . import tag_credits
    except ImportError:  # loaded as a plain script, not a package
        import tag_credits  # type: ignore[no-redef]
    try:
        entry["_tag_credits"] = tag_credits.credit_entry(entry)
    except Exception as e:  # noqa: BLE001
        print(f"WARN tag credit failed: {e}", file=sys.stderr)
        entry["_tag_credits"] = []
    return entry


def update_entry(entry_id, **fields):
    """Update a time entry. Supported fields: description, start, stop, duration, project_id, tags.

    Setting `stop` on the RUNNING entry is a stop in all but name (janus
    retime, janus-mobile), so it gets the same value-tag credit as
    stop_timer; a retime of an already-closed entry does not (2026-09-17)."""
    found, cur = _read_current_cache()
    if not found:
        # Cold cache (no janus on this machine): one live read is worth it so
        # a value-tagged running entry still gets its 媒分 credit on stop.
        try:
            cur = get_current_cached()
        except Exception:  # noqa: BLE001
            cur = None
    was_running = isinstance(cur, dict) and cur.get("id") == entry_id
    body = {"workspace_id": TOGGL_WORKSPACE_ID}
    body.update(fields)
    entry = _request("PUT", f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}", body)
    if was_running:
        if fields.get("stop"):
            _write_current_cache(None)  # the running entry was closed
            if isinstance(entry, dict):
                _credit_value_tags(entry)
        elif isinstance(entry, dict) and (entry.get("duration") or 0) < 0:
            _write_current_cache(entry)  # still running, fields changed
    return entry


def delete_entry(entry_id):
    path = f"/workspaces/{TOGGL_WORKSPACE_ID}/time_entries/{entry_id}"
    left = throttle.quota_remaining(throttle.bucket_for(path))
    if left > 0:
        raise RuntimeError(f"Toggl API DELETE {path} -> 402 (hourly quota exhausted; "
                           f"resets in {int(left)}s; request not sent)")
    was_running = _cached_is(entry_id)
    throttle.acquire()  # same client-side pacing as _request()
    req = urllib.request.Request(f"{BASE_URL}{path}", method="DELETE")
    req.add_header("Authorization", _auth_header())
    try:
        with urllib.request.urlopen(req) as resp:
            ok = resp.status in (200, 204)
            if ok:
                if was_running:  # deleting the running entry → idle
                    _write_current_cache(None)
                _notify_tui()
            return ok
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 402:
            throttle.note_quota_reset(throttle.parse_reset_seconds(body),
                                      bucket=throttle.bucket_for(path))
        elif e.code == 429:
            throttle.note_rate_limit()
        raise RuntimeError(f"Toggl API DELETE -> {e.code}: {body}")


def trim_range(start_dt, end_dt, exclude_ids=None):
    """Ensure no existing Toggl entry -- completed or the currently-running
    one -- keeps covering [start_dt, end_dt) once a new or retimed entry
    claims it. Split/trim/delete whatever overlaps, except any id in
    exclude_ids (the entry itself, when this is a RETIME rather than a plain
    create -- it shouldn't try to trim itself out from under its own edit).

    Shared by every caller that creates or moves a definite time range:
    tg-fast.py's "<desc> <start>-<end>" range creation, did-fast.py's
    time-range /did items, and janus.py's entry-edit-to-a-new-time path
    (2026-07-19: "if I edit an event with a new time... MECE -- shorten [an
    overlapping entry] to make room, or delete the old one if full overlap").
    Originally did-fast-only (as `_trim_toggl_range`, fixing the 2026-07-16
    "asha"/"asha prep" double-count); promoted here once a third caller
    needed the identical logic rather than a third copy of it.

    The running entry is special-cased: it has no fixed end, so its portion
    AFTER the range isn't trimmed to a stop -- it's RESUMED as a new running
    entry starting right after, so live tracking keeps going instead of
    silently vanishing. Returns human-readable log lines, one per entry
    trimmed/split/resumed."""
    exclude_ids = exclude_ids or set()
    tz = start_dt.tzinfo
    results = []
    day = start_dt.date()
    # Toggl's start_date/end_date filter is UTC-based: a [day, day+1) local
    # window drops every evening-PT entry AND the running entry (both land on
    # the next UTC day), which made the running special-case below dead code
    # after ~17:00 (bug 2026-07-27: "2101-2115 snack" didn't split the
    # running run timer). Fetch a ±1-day window — the datetime overlap check
    # below filters precisely, so the extra entries are harmless.
    entries = get_entries(
        start_date=(day - timedelta(days=1)).isoformat(),
        end_date=(day + timedelta(days=2)).isoformat(),
    ) or []
    for e in entries:
        if e.get("id") in exclude_ids:
            continue
        try:
            e_start = datetime.fromisoformat(e["start"]).astimezone(tz)
        except (KeyError, ValueError, TypeError):
            continue
        is_running = (e.get("duration") or 0) < 0
        if is_running:
            e_end = datetime.now(tz)
        else:
            stop = e.get("stop")
            if not stop:
                continue
            try:
                e_end = datetime.fromisoformat(stop).astimezone(tz)
            except (ValueError, TypeError):
                continue
        if e_end <= start_dt or e_start >= end_dt:
            continue  # no overlap
        desc = e.get("description") or ""
        proj_id = e.get("project_id")
        tags = e.get("tags") or None
        if e_start < start_dt:
            pre_end = start_dt - timedelta(minutes=1)
            if pre_end > e_start:
                create_entry(desc, e_start.isoformat(), pre_end.isoformat(),
                              int((pre_end - e_start).total_seconds()), proj_id, tags)
                results.append(f"Trimmed: {desc} {e_start:%H:%M}-{pre_end:%H:%M}")
        if is_running:
            post_start = end_dt + timedelta(minutes=1)
            start_timer(desc, proj_id, tags, start_time=post_start.isoformat())
            results.append(f"Resumed: {desc} from {post_start:%H:%M}")
        elif e_end > end_dt:
            post_start = end_dt + timedelta(minutes=1)
            if e_end > post_start:
                create_entry(desc, post_start.isoformat(), e_end.isoformat(),
                              int((e_end - post_start).total_seconds()), proj_id, tags)
                results.append(f"Trimmed: {desc} {post_start:%H:%M}-{e_end:%H:%M}")
        delete_entry(e["id"])
    return results
