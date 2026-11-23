"""分 per Toggl entry, shared by every client and host (JM 2026-11-05: "task <>
time entry <> minutes used <> 分 achieved").

One append-only file per host in the Syncthing-synced vault, as janus_events
does it: ~/vault/z_meta/events/entry-points.<host>.jsonl. Each host writes only
its own file, so Syncthing has nothing to conflict on, and a reader unions them
with the newest line winning per id. A line with pts null is a tombstone: the
record was undone or merged away, and it beats an older value, so a local
sidecar or another host's line can't bring the points back.

did-fast writes the entries it created or stopped; janus and mobile write what
only they know (^G corrections, pins). Never raises: an entry's points must not
cost a completion.

A task closed with no time entry leaves an open credit (entry-credits.<host>.jsonl)
until its card's cycle ends. The next entry of that name made after the close
takes the points, once: link() when a client fetches entries, claim() when done
is pressed on a row. Only fixed-value tasks get credits; a per-minute habit's
entry has minutes of its own to log.

A dtd split ("done so far") credits part of a task whose entry is not finished
(2026-11-23). Its 分 are a PART line: {"part": <split key>} beside the id. An
entry shows base + Σparts, where base is the newest plain line. So the later
close, which writes the completion's own points as the base, adds to the split
instead of replacing it, and undoing either one leaves the other. A writer that
holds a row's whole shown total (^G, a pin, a mobile adjust) writes
total - parts_sum() as the base. base_for_day() answers "was this entry
completed": a part alone is not a completion.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

HOST = os.uname().nodename.split(".")[0].lower()
_cache: dict = {"key": None, "days": {}, "base": {}, "parts": {}}
_credit_cache: dict = {"key": None, "credits": {}}
_ANNOT = re.compile(r"\(\d*\)|[\[【]\d*G?[\]】]|\{\d*\}|(?<!\S)[@#]\S+")
_DATE_SUFFIX = re.compile(r"\s+\d{1,2}[./]\d{1,2}\s*$")


def _dir() -> Path | None:
    env = os.environ.get("ENTRY_POINTS_DIR")
    if env:
        return Path(env)
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path.home() / "vault" / "z_meta" / "events"


def _iso(day) -> str:
    return day.isoformat() if isinstance(day, dt.date) else str(day)


def record(day, points: dict, src: str = "") -> None:
    """Append {entry id: 分, or None to clear it} for `day`."""
    d = _dir()
    if d is None or not points:
        return
    now = dt.datetime.now().isoformat(timespec="seconds")
    lines = [json.dumps({"ts": now, "day": _iso(day), "id": str(k), "pts": v, "src": src},
                        ensure_ascii=False) for k, v in points.items()]
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"entry-points.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _load(d: Path) -> dict:
    """{day: {id: pts or None}}, re-read only when a host's file changed:
    janus asks once per row per render."""
    try:
        files = sorted(d.glob("entry-points.*.jsonl"))
        key = (str(d), tuple((str(p), p.stat().st_mtime_ns) for p in files))
    except OSError:
        return {}
    if key == _cache["key"]:
        return _cache["days"]
    rows = []
    for p in files:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue     # a half-synced last line
    base: dict = {}
    parts: dict = {}
    for r in sorted(rows, key=lambda r: str(r.get("ts"))):
        if not (r.get("day") and r.get("id")):
            continue
        if r.get("part"):
            held = parts.setdefault(r["day"], {}).setdefault(str(r["id"]), {})
            if r.get("pts"):
                held[str(r["part"])] = r["pts"]
            else:
                held.pop(str(r["part"]), None)
        else:
            base.setdefault(r["day"], {})[str(r["id"])] = r.get("pts")
    days: dict = {}
    for day in set(base) | set(parts):
        out = dict(base.get(day, {}))
        for eid, held in parts.get(day, {}).items():
            if held:
                out[eid] = (out.get(eid) or 0) + sum(held.values())
        days[day] = out
    _cache.update(key=key, days=days, base=base, parts=parts)
    return days


def for_day(day) -> dict:
    """{entry id: 分 shown on the entry (base + split parts), or None for a
    tombstone with no parts}."""
    d = _dir()
    return {} if d is None else dict(_load(d).get(_iso(day), {}))


def base_for_day(day) -> dict:
    """{entry id: 分} for entries a completion was logged on. A split part
    alone is not one, and neither is a tombstone."""
    d = _dir()
    if d is None:
        return {}
    _load(d)
    return {k: v for k, v in _cache["base"].get(_iso(day), {}).items() if v is not None}


def parts_for_day(day) -> dict:
    """{entry id: {split key: 分}} for entries holding split parts."""
    d = _dir()
    if d is None:
        return {}
    _load(d)
    return {k: dict(v) for k, v in _cache["parts"].get(_iso(day), {}).items() if v}


def parts_sum(day, entry_ids) -> int:
    """Σ split parts over a row's ids: what a whole-total writer subtracts."""
    held = parts_for_day(day)
    return sum(sum(held.get(str(i), {}).values()) for i in (entry_ids or []))


def record_part(day, entry_id, key: str, pts, src: str = "") -> None:
    """A split's 分 on an entry, keyed by the split; pts None takes it back."""
    d = _dir()
    if d is None or not entry_id or not key:
        return
    line = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "day": _iso(day),
            "id": str(entry_id), "pts": pts, "part": str(key), "src": src}
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"entry-points.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def move_parts(day, from_ids, to_id, src: str = "") -> int:
    """did-fast replaced a finished row's entry with a new one: its split
    parts follow. -> 分 moved."""
    if to_id is None:
        return 0
    held, moved = parts_for_day(day), 0
    for i in from_ids or []:
        if str(i) == str(to_id):
            continue
        for key, pts in held.get(str(i), {}).items():
            record_part(day, i, key, None, src)
            record_part(day, to_id, key, pts, src)
            moved += pts
    return moved


def overlay(local: dict, day) -> dict:
    """`local` {id: 分} with the shared records on top, tombstoned ids gone."""
    out = {str(k): v for k, v in (local or {}).items()}
    for k, v in for_day(day).items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


# ── open credits: a closed task waiting for its time entry ──────────────────

def normalize(name: str) -> str:
    """"1 i447 10.31 (20) [25]" and "1 i447 @i447" -> "1 i447"."""
    s = _DATE_SUFFIX.sub("", _ANNOT.sub(" ", name or ""))
    return re.sub(r"[\s\-—–]+", " ", s).strip().lower()


def _append_credit(d: Path, line: dict) -> None:
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"entry-credits.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **line}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_credit(cid: str, names: list, pts: int, day, until, src: str = "") -> None:
    d = _dir()
    if d is not None:
        _append_credit(d, {"kind": "credit", "credit": cid, "src": src, "pts": int(pts),
                           "keys": sorted({normalize(n) for n in names if normalize(n)}),
                           "day": _iso(day), "until": _iso(until)})


def _credits(d: Path) -> dict:
    """{credit id: {..., "epoch", "links": [(day, entry id)], "void": bool}}."""
    try:
        files = sorted(d.glob("entry-credits.*.jsonl"))
        key = (str(d), tuple((str(p), p.stat().st_mtime_ns) for p in files))
    except OSError:
        return {}
    if key == _credit_cache["key"]:
        return _credit_cache["credits"]
    rows = []
    for p in files:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    out: dict = {}
    for r in sorted(rows, key=lambda r: r.get("ts") or 0):
        c = r.get("credit")
        if r.get("kind") == "credit":
            out.setdefault(c, {"links": [], "void": False}).update(
                r, epoch=r.get("ts") or 0)
        elif c in out and r.get("kind") == "link":
            out[c]["links"].append((r.get("day"), str(r.get("id"))))
        elif c in out and r.get("kind") == "void":
            out[c]["void"] = True
    _credit_cache.update(key=key, credits=out)
    return out


def _open_credits(d: Path, today: str) -> list:
    return sorted((c for c in _credits(d).values()
                   if not c["void"] and not c["links"] and today <= c.get("until", "")),
                  key=lambda c: c["epoch"])


def _take(d: Path, c: dict, day: str, entry_id) -> int:
    record(day, {str(entry_id): c["pts"]}, f"credit {c['credit']}")
    _append_credit(d, {"kind": "link", "credit": c["credit"], "id": str(entry_id), "day": day})
    c["links"].append((day, str(entry_id)))
    return c["pts"]


def _epoch(at) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def logged_for_day(day) -> dict:
    """{entry id: base 分, or None for an undone log}: the ids a completion
    was recorded on. A split part alone leaves the entry open, so guards
    against a second log read this, not for_day()."""
    d = _dir()
    if d is None:
        return {}
    _load(d)
    return _cache["base"].get(_iso(day), {})


def link(entries: list, today=None) -> dict:
    """Give open credits to matching entries made after the close. `entries`
    are client rows: id, desc, at, running, and start or start_dt. Earliest
    start wins, so every host picks the same one. -> {entry id: 分}."""
    d = _dir()
    if d is None:
        return {}
    credits = _open_credits(d, _iso(today or dt.date.today()))
    if not credits:
        return {}
    linked: dict = {}
    for c in credits:
        pool = []
        for e in entries:
            start = e.get("start_dt") or e.get("start")
            at = _epoch(e.get("at"))
            if e.get("running") or not start or at is None or at < c["epoch"]:
                continue
            if normalize(e.get("desc", "")) not in c["keys"] or str(e.get("id")) in linked:
                continue
            day = start.date().isoformat()
            # Any record keeps a credit off, a split part included: the split
            # was earned in this entry, the close was not (JM 2026-11-23: "1分
            # associated with the time entry, then 2分 credited without one").
            if c["day"] <= day <= c["until"] and str(e.get("id")) not in for_day(day):
                pool.append((start, str(e.get("id")), day))
        if pool:
            start, eid, day = min(pool)
            linked[eid] = _take(d, c, day, eid)
    return linked


def claim(entry_id, desc: str, day) -> int | None:
    """Done pressed on a row: an open credit of its name is this row's, so the
    press must not credit again. -> 分 taken, or None."""
    d = _dir()
    if d is None:
        return None
    key = normalize(desc)
    for c in _open_credits(d, _iso(day)):
        if key in c["keys"]:
            return _take(d, c, _iso(day), entry_id)
    return None


def void_credit(cid: str) -> None:
    """Undo: the credit is gone, and so are the points on any entry it reached."""
    d = _dir()
    if d is None:
        return
    for day, eid in (_credits(d).get(cid) or {}).get("links", []):
        record(day, {eid: None}, "undo")
    _append_credit(d, {"kind": "void", "credit": cid})


# ── split parts: which entry a "done so far" belongs to ─────────────────────

_TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
_CLAIMS = Path.home() / "i446-monorepo/tools/did/agent_claims.py"


def _name_key(name: str) -> str:
    return normalize((name or "").replace("\U0001F608", ""))


def pick_entry(names: list, task_id, current: dict | None, entries: list,
               task_for_entry=None) -> dict | None:
    """The Toggl entry (v9 shape) a split's 分 belong to: the running timer when
    it was started for `task_id`, or is unlinked and carries one of `names`;
    else the latest entry in `entries` with one of those names; else None. A
    timer linked to another task is never it, whatever its name."""
    keys = {_name_key(n) for n in names if _name_key(n)}
    if current and current.get("id"):
        linked = None
        try:
            linked = task_for_entry(current["id"]) if task_for_entry else None
        except Exception:  # noqa: BLE001 -- the link store is advisory
            linked = None
        if linked:
            if task_id and str(linked) == str(task_id):
                return current
        elif _name_key(current.get("description", "")) in keys:
            return current
    same = [e for e in entries or [] if e.get("id") and e.get("start")
            and _name_key(e.get("description", "")) in keys
            and str(e.get("id")) != str((current or {}).get("id"))]
    return max(same, key=lambda e: _epoch(e["start"]) or 0) if same else None


def _load_module(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _toggl_now() -> tuple:
    """(running entry or None, a thunk for today's entries). Live, not the
    shared cache: a timer started on the phone a minute ago is the one."""
    api = _load_module("toggl_cli_ep", _TOGGL_CLI).toggl_api
    try:
        current = api.get_current()
    except Exception:  # noqa: BLE001 -- quota: the cache is better than nothing
        current = api.get_current_cached()

    def today_entries():
        day = dt.date.today()
        return api.get_entries(start_date=day.isoformat(),
                               end_date=(day + dt.timedelta(days=1)).isoformat()) or []
    return current, today_entries


def credit_part(names: list, pts: int, key: str, task_id=None, toggl=None,
                task_for_entry=None) -> dict | None:
    """Record a split's `pts` on the entry JM is working in. -> {"day", "id",
    "pts", "key"}, or None when no entry of today's is that task (the split
    then credits the day alone, as before). Never raises."""
    try:
        if not pts or pts <= 0 or not key or _dir() is None:
            return None
        current, today_entries = (toggl or _toggl_now)()
        if task_for_entry is None:
            try:
                task_for_entry = _load_module("agent_claims_ep", _CLAIMS).task_for_entry
            except Exception:  # noqa: BLE001
                task_for_entry = None
        e = pick_entry(names, task_id, current, [], task_for_entry)
        if e is None:
            e = pick_entry(names, task_id, current, today_entries(), task_for_entry)
        start = _epoch((e or {}).get("start"))
        if e is None or start is None:
            return None
        day = dt.datetime.fromtimestamp(start).date().isoformat()
        record_part(day, e["id"], key, int(pts), f"split {key}")
        return {"day": day, "id": str(e["id"]), "pts": int(pts), "key": str(key)}
    except Exception:  # noqa: BLE001 -- a record must not cost the split
        return None


def _main(argv: list) -> int:
    """entry_points.py credit-part --task ID --key K --pts N --name A [--name B]
    -> the credit_part JSON (null when nothing matched). For dtd.sh's split."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["credit-part"])
    ap.add_argument("--task", default="")
    ap.add_argument("--key", required=True)
    ap.add_argument("--pts", type=int, required=True)
    ap.add_argument("--name", action="append", default=[])
    a = ap.parse_args(argv)
    print(json.dumps(credit_part(a.name, a.pts, a.key, task_id=a.task or None)))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
