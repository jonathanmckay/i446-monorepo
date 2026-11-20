"""Write intents: the durable record of a write request, made BEFORE Excel is
touched (Neon off Excel step 4b, 2026-11-19).

Until now a write existed only once Excel had taken it: if Excel hung, the
request was gone and the caller could not tell whether it had landed. With the
queue on, every /append /write /batch /batch_write is first appended here and
fsynced; Excel is then applied at once when it is healthy, and replayed in
order by the daemon's drain when it is not.

File: append-only JSONL (default ~/.local/share/neon/write-intents.jsonl).
  {"id", "seq", "ts", "path", "body"}                    a request
  {"id", "event": "attempt", "at"}                       written before each dispatch
  {"id", "event": "done" | "rejected" | "held", "at", "note"}   terminal

An intent with no terminal event is pending. One with an attempt but no
terminal event may or may not have reached Excel: the daemon resolves that
against its journal (entries carry `intent` + `item`) before replaying.

Duplicate detection (stage 2, 2026-11-20): a request id (`rid`, set by the
client per write call) identifies one request for good, pending or finished,
so a caller whose reply was lost (timeout) can ask what became of it
(`by_rid`) and a resend of the same call is not a second write. A finished
intent keeps the handler's reply (`result`) for that. Without a rid, the
caller's `idem` keys still dedupe against pending intents only: two deliberate
identical requests with neither are two writes.
"""
from __future__ import annotations

import datetime
import json
import os
import threading

TERMINAL = ("done", "rejected", "held")
COMPACT_AFTER = 5000          # terminal intents kept in the file before a rewrite
KEEP_DONE = 500               # finished intents a rewrite keeps, so a late rid lookup still answers


def _idems(path: str, body: dict) -> tuple:
    keys = []
    if body.get("idem"):
        keys.append(str(body["idem"]))
    for item in body.get("appends") or body.get("writes") or []:
        if isinstance(item, dict) and item.get("idem"):
            keys.append(str(item["idem"]))
    return (path, tuple(sorted(keys))) if keys else ()


class Intents:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}      # insertion order = arrival order
        self._rids: dict[str, str] = {}        # request id → intent id
        self._seq = 0
        self._load()

    # ── storage ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        lines = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue                # torn trailing line
        except FileNotFoundError:
            return
        for rec in lines:
            self._apply(rec)
        done = [i for i in self._items.values() if i["state"] in TERMINAL]
        if len(done) > COMPACT_AFTER:
            self._compact()

    def _apply(self, rec: dict) -> None:
        iid = rec.get("id")
        if not iid:
            return
        if "event" not in rec:
            self._items[iid] = {"id": iid, "seq": rec.get("seq", 0), "ts": rec.get("ts"),
                                "path": rec.get("path"), "body": rec.get("body") or {},
                                "state": None, "attempts": 0, "note": "", "result": None}
            self._seq = max(self._seq, int(rec.get("seq", 0)))
            rid = (rec.get("body") or {}).get("rid")
            if rid:
                self._rids[str(rid)] = iid
            return
        it = self._items.get(iid)
        if it is None:
            return
        if rec["event"] == "attempt":
            it["attempts"] += 1
        elif rec["event"] in TERMINAL:
            it["state"], it["note"] = rec["event"], rec.get("note") or ""
            it["result"] = rec.get("result")

    def _append(self, rec: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _compact(self) -> None:
        """Rewrite the file with only the intents that still matter."""
        fin = [i for i in self._items.values() if i["state"] in ("done", "rejected")]
        recent = {i["id"] for i in fin[-KEEP_DONE:]}
        keep = [i for i in self._items.values()
                if i["state"] not in ("done", "rejected") or i["id"] in recent]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for it in keep:
                f.write(json.dumps({k: it[k] for k in ("id", "seq", "ts", "path", "body")},
                                   ensure_ascii=False) + "\n")
                for _ in range(it["attempts"]):
                    f.write(json.dumps({"id": it["id"], "event": "attempt"}) + "\n")
                if it["state"]:
                    f.write(json.dumps({"id": it["id"], "event": it["state"], "note": it["note"],
                                        "result": it.get("result")},
                                       ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._items = {i["id"]: i for i in keep}
        self._rids = {str(i["body"]["rid"]): i["id"] for i in keep if i["body"].get("rid")}

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── API ──────────────────────────────────────────────────────────────
    def add(self, path: str, body: dict) -> tuple[dict, bool]:
        """Record a request durably. → (intent, dup). dup=True when the same
        request id was already recorded (in any state), or a pending intent
        carries the same idem keys (a caller retry): that intent is returned
        and nothing is added."""
        key = _idems(path, body)
        rid = body.get("rid")
        with self._lock:
            if rid and str(rid) in self._rids and self._rids[str(rid)] in self._items:
                return self._items[self._rids[str(rid)]], True
            if key:
                for it in self._items.values():
                    if it["state"] is None and _idems(it["path"], it["body"]) == key:
                        return it, True
            self._seq += 1
            iid = "%s-%06d" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), self._seq)
            rec = {"id": iid, "seq": self._seq, "ts": self._now(), "path": path, "body": body}
            self._append(rec)
            self._apply(rec)
            return self._items[iid], False

    def attempting(self, iid: str) -> int:
        """Durably note that Excel is about to be touched for this intent."""
        with self._lock:
            rec = {"id": iid, "event": "attempt", "at": self._now()}
            self._append(rec)
            self._apply(rec)
            return self._items[iid]["attempts"]

    def finish(self, iid: str, state: str, note: str = "", result: dict | None = None) -> None:
        assert state in TERMINAL, state
        with self._lock:
            if self._items.get(iid, {}).get("state") in TERMINAL:
                return
            rec = {"id": iid, "event": state, "at": self._now(), "note": note}
            if result is not None:
                rec["result"] = result
            self._append(rec)
            self._apply(rec)

    def get(self, iid: str) -> dict | None:
        return self._items.get(iid)

    def by_rid(self, rid: str) -> dict | None:
        with self._lock:
            return self._items.get(self._rids.get(str(rid), ""))

    def pending(self, before_seq: int | None = None) -> list[dict]:
        """Unfinished intents, oldest first (optionally only those older than seq)."""
        with self._lock:
            return [i for i in self._items.values()
                    if i["state"] is None and (before_seq is None or i["seq"] < before_seq)]

    def held(self) -> list[dict]:
        with self._lock:
            return [i for i in self._items.values() if i["state"] == "held"]

    def stats(self) -> dict:
        with self._lock:
            pend = [i for i in self._items.values() if i["state"] is None]
            held = [i for i in self._items.values() if i["state"] == "held"]
        return {"pending": len(pend), "held": len(held),
                "oldest_ts": pend[0]["ts"] if pend else None}
