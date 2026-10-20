#!/usr/bin/env python3
"""Backfill / enrich the jbm (jonathan.b.mckay@gmail.com) calendar with the
Toggl time archive. Walks backward in 90-day windows from a given end date
until a window has zero entries (= before tracking began).

Each event is a transparent calendar entry, summary "{desc} @{project}", and
carries the FULL Toggl metadata in extendedProperties.private so the calendar
is a complete record independent of Toggl:
    src, toggl_id, project, project_id, tags, dur (seconds)

Idempotent + resumable:
  - new entries are created with full metadata;
  - entries already present are PATCHed to add metadata if they lack it
    (this is the one-time retrofit for events created before metadata capture);
  - fully-enriched entries are skipped.

Usage: backfill_jbm_archive.py [END_YYYY-MM-DD] [FLOOR_YYYY-MM-DD]
"""
import json, time, sys, base64, datetime as dt, urllib.request, urllib.parse, urllib.error

JBM = "jonathan.b.mckay@gmail.com"
SRC = "0r-backfill"
CLAUDE_JSON = "/Users/mckay/.claude.json"
GTOKENS = "/Users/mckay/.config/google-calendar-mcp/tokens.json"
GKEYS = "/Users/mckay/.config/google-calendar-mcp/gcp-oauth.keys.json"
WID = "2092616"

def log(m): print(m, flush=True)

# --- Toggl ---
TKEY = json.load(open(CLAUDE_JSON))["mcpServers"]["toggl_server"]["env"]["TOGGL_API_KEY"]
THDR = {"Authorization": "Basic " + base64.b64encode((TKEY+":api_token").encode()).decode()}

_proj = {}
def projects():
    if _proj: return _proj
    url = f"https://api.track.toggl.com/api/v9/workspaces/{WID}/projects?per_page=200"
    for p in json.load(urllib.request.urlopen(urllib.request.Request(url, headers=THDR), timeout=30)):
        _proj[p["id"]] = p["name"]
    return _proj

_tags = {}
_tags_loaded = [False]
def tags_map():
    if _tags_loaded[0]: return _tags
    url = f"https://api.track.toggl.com/api/v9/workspaces/{WID}/tags"
    try:
        for t in (json.load(urllib.request.urlopen(urllib.request.Request(url, headers=THDR), timeout=30)) or []):
            _tags[t["id"]] = t["name"]
    except Exception:
        pass
    _tags_loaded[0] = True
    return _tags

def toggl_day(day):
    s = day.isoformat(); e = (day+dt.timedelta(days=1)).isoformat()
    url = f"https://api.track.toggl.com/api/v9/me/time_entries?start_date={s}&end_date={e}"
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=THDR), timeout=30))
        except urllib.error.HTTPError as ex:
            if ex.code == 400:
                return toggl_day_reports(day)  # too old for v9 -> Reports API
            if ex.code in (429,500,502,503): time.sleep(2**attempt); continue
            raise
    return []

def toggl_day_reports(day):
    hdr = dict(THDR); hdr["Content-Type"] = "application/json"
    tm = tags_map()
    out = []; first_row = None; first_id = None
    while True:
        b = {"start_date": day.isoformat(), "end_date": day.isoformat(), "page_size": 1000}
        if first_row is not None: b["first_row_number"] = first_row
        if first_id is not None: b["first_id"] = first_id
        req = urllib.request.Request(
            f"https://api.track.toggl.com/reports/api/v3/workspace/{WID}/search/time_entries",
            data=json.dumps(b).encode(), headers=hdr, method="POST")
        resp = None
        for attempt in range(5):
            try:
                resp = urllib.request.urlopen(req, timeout=45); break
            except urllib.error.HTTPError as ex:
                if ex.code in (429,500,502,503): time.sleep(2**attempt); continue
                raise
        rows = json.load(resp)
        for r in rows:
            tnames = [tm.get(t, str(t)) for t in (r.get("tag_ids") or [])]
            for te in r.get("time_entries", []):
                out.append({"id": te.get("id"), "start": te.get("start"),
                            "stop": te.get("stop"), "duration": te.get("seconds", 0),
                            "project_id": r.get("project_id"),
                            "description": r.get("description"), "tags": tnames})
        nr = resp.headers.get("X-Next-Row-Number")
        if not nr: break
        first_row = int(nr); ni = resp.headers.get("X-Next-ID")
        first_id = int(ni) if ni else None
    return out

# --- Google ---
def _find(d, names):
    for n in names:
        if n in d: return d[n]
    for v in d.values():
        if isinstance(v, dict):
            r = _find(v, names)
            if r: return r
_gtok = {"at": None, "exp": 0}
def gtoken():
    if _gtok["at"] and time.time() < _gtok["exp"] - 120: return _gtok["at"]
    tok = json.load(open(GTOKENS))["jbm"]; keys = json.load(open(GKEYS)); k = keys.get("installed") or keys.get("web")
    data = urllib.parse.urlencode({"client_id": k["client_id"], "client_secret": k["client_secret"],
        "refresh_token": _find(tok, ["refresh_token"]), "grant_type": "refresh_token"}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=30))
    _gtok["at"] = r["access_token"]; _gtok["exp"] = time.time() + r.get("expires_in", 3600)
    return _gtok["at"]
def gget(path):
    return json.load(urllib.request.urlopen(urllib.request.Request("https://www.googleapis.com/calendar/v3/"+path,
        headers={"Authorization":"Bearer "+gtoken()}), timeout=30))
def _gwrite(method, path, body):
    for attempt in range(6):
        try:
            req = urllib.request.Request("https://www.googleapis.com/calendar/v3/"+path,
                data=json.dumps(body).encode(),
                headers={"Authorization":"Bearer "+gtoken(), "Content-Type":"application/json"}, method=method)
            urllib.request.urlopen(req, timeout=30); return True
        except urllib.error.HTTPError as ex:
            if ex.code in (403,429,500,503): time.sleep(min(2**attempt, 30)); continue
            raise
    return False
def gcreate(body):
    return _gwrite("POST", f"calendars/{urllib.parse.quote(JBM)}/events?sendUpdates=none", body)
def gpatch(event_id, body):
    return _gwrite("PATCH", f"calendars/{urllib.parse.quote(JBM)}/events/{event_id}", body)

def existing_events(day):
    # +/-1 day window: Toggl buckets by UTC day; boundary entries land outside
    # the local day. Returns {toggl_id: event} for our archive events.
    params = urllib.parse.urlencode({"timeMin": (day-dt.timedelta(days=1)).isoformat()+"T00:00:00-07:00",
        "timeMax": (day+dt.timedelta(days=2)).isoformat()+"T00:00:00-07:00",
        "maxResults":"250","singleEvents":"true","privateExtendedProperty":f"src={SRC}"})
    ev = gget(f"calendars/{urllib.parse.quote(JBM)}/events?"+params)
    out = {}
    for e in ev.get("items", []):
        tid = ((e.get("extendedProperties") or {}).get("private") or {}).get("toggl_id")
        if tid: out[str(tid)] = e
    return out

def entry_meta(e):
    pid = e.get("project_id")
    return {"src": SRC, "toggl_id": str(e.get("id")),
            "project": (projects().get(pid) or "") if pid else "",
            "project_id": str(pid) if pid else "",
            "tags": ",".join(e.get("tags") or []),
            "dur": str(e.get("duration", 0))}

def backfill_day(day):
    proj = projects(); tags_map()
    entries = toggl_day(day)
    todo = []
    for e in entries:
        if e.get("duration", 0) <= 0: continue
        name = proj.get(e.get("project_id"), "")
        if name == "睡觉": continue
        desc = (e.get("description") or "").strip()
        summary = f"{desc} @{name}" if name else (desc or "(untracked)")
        todo.append((e, summary))
    if not todo: return 0, 0, 0
    have = existing_events(day)
    created = patched = skipped = 0
    for e, summary in todo:
        tid = str(e["id"]); priv = entry_meta(e)
        if tid in have:
            cur = (have[tid].get("extendedProperties") or {}).get("private") or {}
            if "tags" in cur and "project_id" in cur:
                skipped += 1; continue
            gpatch(have[tid]["id"], {"extendedProperties": {"private": priv}})
            patched += 1; time.sleep(0.2); continue
        gcreate({"summary": summary, "start": {"dateTime": e["start"]},
                 "end": {"dateTime": e["stop"]}, "transparency": "transparent",
                 "extendedProperties": {"private": priv}})
        created += 1; time.sleep(0.25)
    return created, patched, skipped

def main():
    end = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()-dt.timedelta(days=1)
    floor = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
    window = 90; chunk_end = end; tc = tp = ts = 0; chunk_no = 0
    while True:
        chunk_no += 1
        chunk_start = chunk_end - dt.timedelta(days=window-1)
        if floor and chunk_start < floor: chunk_start = floor
        log(f"=== chunk {chunk_no}: {chunk_start} .. {chunk_end} ===")
        cc = cp = cs = days = 0; d = chunk_end
        while d >= chunk_start:
            try:
                c, pa, s = backfill_day(d)
            except Exception as ex:
                log(f"  {d}: ERROR {type(ex).__name__}: {ex} - skipped"); c = pa = s = 0
            if c or pa or s: days += 1
            if c or pa: log(f"  {d}: +{c} created, {pa} patched, {s} ok")
            cc += c; cp += pa; cs += s; d -= dt.timedelta(days=1)
        tc += cc; tp += cp; ts += cs
        log(f"  chunk {chunk_no} total: +{cc} created, {cp} patched, {cs} ok, {days}/{window} days with data")
        if days == 0:
            log("  empty window - reached start of history. done."); break
        if floor and chunk_start <= floor:
            log("  hit floor. done."); break
        chunk_end = chunk_start - dt.timedelta(days=1)
    log(f"=== DONE: +{tc} created, {tp} patched, {ts} already enriched ===")

if __name__ == "__main__":
    main()
