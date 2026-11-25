#!/usr/bin/env python3
"""/1cal: find duplicate events on JM's two Google calendars, remove on request.

Read-only unless --apply is given. Two kinds of duplicate:

  invite  the same invite (one iCalUID) sitting on both 个人 and m5x2 Cal,
          because both of JM's addresses are on it.
  record  two archive events on 个人 for one Toggl entry (same
          extendedProperties.private.toggl_id): the nightly /0r copy plus a
          backfill copy.

    1cal.py                          report, numbered; saves the plan
    1cal.py --from 2026-10-01 --to 2026-12-31
    1cal.py --apply 1,3              do the suggested fix for items 1 and 3
    1cal.py --apply 2=jbm            item 2, but keep the 个人 copy
    1cal.py --apply records          every record duplicate
    1cal.py --apply all              everything marked auto

--apply works from the plan the last report saved, so the numbers are the ones
JM saw. Every change first appends the event as it was to REMOVED_LOG.

Both tokens in the MCP's tokens.json sign in as mckay@m5c7.com, which owns
m5x2 Cal and has owner access to 个人; nothing here acts as the gmail account.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

JBM = "jonathan.b.mckay@gmail.com"
M5X2 = "mckay@m5c7.com"
CALS = {"jbm": JBM, "m5x2": M5X2}
LABEL = {"jbm": "个人", "m5x2": "m5x2"}
# Every address of JM's that puts an event on one of the two calendars.
# mckay@m5x2.com is an alias of the m5c7 account (see tools/ibx/ibx0.py).
JM_ADDRS = {JBM: "jbm", M5X2: "m5x2", "mckay@m5x2.com": "m5x2"}
BUSINESS_DOMAINS = ("m5c7.com", "m5x2.com")
RECORD_SRCS = ("0r", "0r-backfill")

GKEYS = os.path.expanduser("~/.config/google-calendar-mcp/gcp-oauth.keys.json")
GTOKENS = os.path.expanduser("~/.config/google-calendar-mcp/tokens.json")
STATE = os.path.expanduser("~/.local/state/jm/1cal.json")
REMOVED_LOG = os.path.expanduser("~/.config/1cal/removed.jsonl")
API = "https://www.googleapis.com/calendar/v3/"
DAYS_BACK, DAYS_AHEAD = 60, 90


# ── pure: finding duplicates ────────────────────────────────────────────────

def _start(e):
    s = e.get("originalStartTime") or e.get("start") or {}
    return s.get("dateTime") or s.get("date") or ""


def _shown_start(e):
    s = e.get("start") or {}
    return s.get("dateTime") or s.get("date") or ""


def _private(e):
    return (e.get("extendedProperties") or {}).get("private") or {}


def _side_of(email):
    return JM_ADDRS.get((email or "").lower())


def find_record_dupes(events):
    """Archive events on 个人 that share a toggl_id. One item per group:
    keep the nightly `0r` copy when there is one, else the earliest created."""
    groups = {}
    for e in events:
        p = _private(e)
        if p.get("toggl_id") and p.get("src") in RECORD_SRCS:
            groups.setdefault(str(p["toggl_id"]), []).append(e)
    items = []
    for tid, evs in groups.items():
        if len(evs) < 2:
            continue
        evs.sort(key=lambda e: (_private(e).get("src") != "0r", e.get("created") or ""))
        keep, extra = evs[0], evs[1:]
        items.append({
            "kind": "record", "auto": True, "action": "delete_record",
            "toggl_id": tid, "start": _shown_start(keep), "summary": keep.get("summary") or "",
            "keep_id": keep["id"],
            "remove": [{"cal": "jbm", "id": x["id"], "src": _private(x).get("src")} for x in extra],
        })
    items.sort(key=lambda i: i["start"])
    return items


def suggest_keep(organizer_email):
    """Which calendar keeps an invite that is on both."""
    side = _side_of(organizer_email)
    if side:
        return side                      # JM organizes: the organizer's copy is the event
    domain = (organizer_email or "").rsplit("@", 1)[-1].lower()
    return "m5x2" if domain in BUSINESS_DOMAINS else "jbm"


def plan_invite(item, keep):
    """Fill in action/auto/why for one invite duplicate, given the side to keep."""
    lose = "m5x2" if keep == "jbm" else "jbm"
    org_side = _side_of(item["organizer"])
    item.update(keep=keep, lose=lose)
    if item["recurring"] and not item["complete"]:
        item.update(action="manual", auto=False,
                    why="recurring, and only some instances are on both calendars")
    elif org_side == keep:
        if item["loser_addrs"].get(lose):
            item.update(action="drop_attendee", auto=True,
                        why=f"you organize it on {LABEL[keep]}; take your {LABEL[lose]} address off the guest list")
        else:
            item.update(action="manual", auto=False,
                        why=f"your {LABEL[lose]} address is not a guest, so the copy got there another way")
    elif org_side == lose:
        item.update(action="manual", auto=False,
                    why=f"you organize it on {LABEL[lose]}; keeping the {LABEL[keep]} copy means moving the event (not automated)")
    else:
        item.update(action="delete_copy", auto=True,
                    why=f"{item['organizer']} organizes it; delete your {LABEL[lose]} copy")
    return item


def find_invite_dupes(jbm_events, m5x2_events):
    """Invites present on both calendars, one item per iCalUID."""
    def index(evs):
        out = {}
        for e in evs:
            if e.get("iCalUID") and not _private(e).get("toggl_id"):
                out.setdefault(e["iCalUID"], {})[_start(e)] = e
        return out
    a, b = index(jbm_events), index(m5x2_events)
    items = []
    for uid in sorted(set(a) & set(b)):
        shared = sorted(set(a[uid]) & set(b[uid]))
        if not shared:
            continue
        ja, mb = a[uid][shared[0]], b[uid][shared[0]]
        organizer = ((ja.get("organizer") or {}).get("email") or "").lower()
        guests = [(x.get("email") or "").lower() for x in (ja.get("attendees") or mb.get("attendees") or [])]
        recurring = bool(ja.get("recurringEventId") or mb.get("recurringEventId"))
        item = {
            "kind": "invite", "ical_uid": uid, "summary": ja.get("summary") or mb.get("summary") or "",
            "start": _shown_start(ja), "organizer": organizer,
            "recurring": recurring, "instances": len(shared),
            "complete": len(shared) == len(a[uid]) == len(b[uid]),
            "ids": {"jbm": ja.get("recurringEventId") or ja["id"],
                    "m5x2": mb.get("recurringEventId") or mb["id"]},
            "loser_addrs": {side: [g for g in guests if _side_of(g) == side] for side in CALS},
        }
        items.append(plan_invite(item, suggest_keep(organizer)))
    items.sort(key=lambda i: i["start"])
    return items


def parse_apply(spec, items):
    """'1,3=jbm,records' -> [(index, keep override or None)], validated."""
    picks, seen = [], set()
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        if tok in ("all", "records"):
            idxs = [(n, None) for n, i in enumerate(items, 1)
                    if i["auto"] and (tok == "all" or i["kind"] == "record")]
        else:
            num, _, side = tok.partition("=")
            if not num.isdigit() or not 1 <= int(num) <= len(items):
                raise ValueError(f"no item {num!r} in the saved plan (1..{len(items)})")
            if side and side not in CALS:
                raise ValueError(f"{tok!r}: keep side must be jbm or m5x2")
            if side and items[int(num) - 1]["kind"] != "invite":
                raise ValueError(f"{tok!r}: a keep side only applies to an invite")
            idxs = [(int(num), side or None)]
        for n, side in idxs:
            if n not in seen:
                seen.add(n)
                picks.append((n, side))
    return picks


# ── Google Calendar ─────────────────────────────────────────────────────────

_tok = {}


def _token():
    if not _tok:
        keys = json.load(open(GKEYS))
        k = keys.get("installed") or keys.get("web")
        t = json.load(open(GTOKENS))["m5c7"]
        data = urllib.parse.urlencode({
            "client_id": k["client_id"], "client_secret": k["client_secret"],
            "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
        _tok["at"] = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data, timeout=30))["access_token"]
    return _tok["at"]


def _call(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _ev_path(side, event_id=""):
    return f"calendars/{urllib.parse.quote(CALS[side])}/events" + (f"/{event_id}" if event_id else "")


def list_events(side, start, end):
    out, page = [], None
    while True:
        q = {"timeMin": f"{start}T00:00:00Z", "timeMax": f"{end}T23:59:59Z",
             "singleEvents": "true", "maxResults": "2500"}
        if page:
            q["pageToken"] = page
        d = _call("GET", _ev_path(side) + "?" + urllib.parse.urlencode(q))
        out += [e for e in d.get("items", []) if e.get("status") != "cancelled"]
        page = d.get("nextPageToken")
        if not page:
            return out


def _log_removed(action, side, event):
    os.makedirs(os.path.dirname(REMOVED_LOG), exist_ok=True)
    with open(REMOVED_LOG, "a") as f:
        f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"),
                            "action": action, "cal": CALS[side], "event": event},
                           ensure_ascii=False) + "\n")


def apply_item(item):
    """Carry out one planned item. Returns a one-line result."""
    if not item["auto"]:
        return f"skipped (manual): {item['why']}"
    if item["action"] == "delete_record":
        for r in item["remove"]:
            ev = _call("GET", _ev_path("jbm", r["id"]))
            if str(_private(ev).get("toggl_id")) != item["toggl_id"]:
                return "skipped: the event changed since the report"
            _log_removed("delete_record", "jbm", ev)
            _call("DELETE", _ev_path("jbm", r["id"]) + "?sendUpdates=none")
        return f"deleted {len(item['remove'])} extra archive copy"
    keep, lose = item["keep"], item["lose"]
    if item["action"] == "drop_attendee":
        ev = _call("GET", _ev_path(keep, item["ids"][keep]))
        left = [a for a in ev.get("attendees") or [] if _side_of(a.get("email")) != lose]
        if len(left) == len(ev.get("attendees") or []):
            return "skipped: your other address is no longer a guest"
        _log_removed("drop_attendee", keep, ev)
        _call("PATCH", _ev_path(keep, item["ids"][keep]) + "?sendUpdates=none", {"attendees": left})
        return f"kept on {LABEL[keep]}, removed from {LABEL[lose]}"
    if item["action"] == "delete_copy":
        ev = _call("GET", _ev_path(lose, item["ids"][lose]))
        if ev.get("iCalUID") != item["ical_uid"]:
            return "skipped: the event changed since the report"
        _log_removed("delete_copy", lose, ev)
        _call("DELETE", _ev_path(lose, item["ids"][lose]) + "?sendUpdates=none")
        return f"kept on {LABEL[keep]}, deleted the {LABEL[lose]} copy"
    return f"skipped: unknown action {item['action']}"


# ── report ──────────────────────────────────────────────────────────────────

def _when(iso):
    if "T" not in iso:
        return dt.date.fromisoformat(iso[:10]).strftime("%m-%d %a") + " all day"
    return dt.datetime.fromisoformat(iso).strftime("%m-%d %a %H:%M")


def render(plan):
    items = plan["items"]
    lines = [f"1cal {plan['from']} .. {plan['to']} · 个人 {plan['counts']['jbm']} events · m5x2 {plan['counts']['m5x2']} events"]
    inv = [(n, i) for n, i in enumerate(items, 1) if i["kind"] == "invite"]
    rec = [(n, i) for n, i in enumerate(items, 1) if i["kind"] == "record"]
    lines.append(f"\nInvites on both calendars: {len(inv)}")
    for n, i in inv:
        rep = f" (x{i['instances']}, repeats)" if i["recurring"] else ""
        mark = "auto" if i["auto"] else "manual"
        lines.append(f"  {n:>3}  {_when(i['start'])}  {i['summary'][:40]}{rep}")
        lines.append(f"       keep {LABEL[i['keep']]} [{mark}]: {i['why']}")
    lines.append(f"\nTime-record doubles on 个人: {len(rec)}")
    for n, i in rec:
        srcs = "+".join(r["src"] or "?" for r in i["remove"])
        lines.append(f"  {n:>3}  {_when(i['start'])}  {i['summary'][:40]}  (extra: {srcs})")
    if not items:
        lines.append("\nNo duplicates.")
    return "\n".join(lines)


def build_plan(start, end):
    jbm, m5 = list_events("jbm", start, end), list_events("m5x2", start, end)
    items = find_invite_dupes(jbm, m5) + find_record_dupes(jbm)
    return {"from": start, "to": end, "made": dt.datetime.now().isoformat(timespec="seconds"),
            "counts": {"jbm": len(jbm), "m5x2": len(m5)}, "items": items}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--apply", metavar="SPEC")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    today = dt.date.today()

    if a.apply:
        try:
            plan = json.load(open(STATE))
        except (OSError, ValueError):
            sys.exit("No saved plan. Run 1cal.py first, then --apply the numbers it shows.")
        if plan["made"][:10] != today.isoformat():
            sys.exit(f"The saved plan is from {plan['made'][:10]}. Run the report again first.")
        try:
            picks = parse_apply(a.apply, plan["items"])
        except ValueError as ex:
            sys.exit(str(ex))
        failed = 0
        for n, side in picks:
            item = plan["items"][n - 1]
            if side and side != item["keep"]:
                plan_invite(item, side)
            try:
                result = apply_item(item)
            except urllib.error.HTTPError as ex:
                result, failed = f"FAILED {ex.code}: {ex.read().decode()[:120]}", failed + 1
            print(f"{n:>3}  {item['summary'][:40]}: {result}")
        print(f"undo log: {REMOVED_LOG}")
        return 1 if failed else 0

    start = a.start or (today - dt.timedelta(days=DAYS_BACK)).isoformat()
    end = a.end or (today + dt.timedelta(days=DAYS_AHEAD)).isoformat()
    plan = build_plan(start, end)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(json.dumps(plan, ensure_ascii=False, indent=1) if a.json else render(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
