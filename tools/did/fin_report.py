#!/usr/bin/env python3
"""fin_report — the final report /fin prints for a finished task (2026-11-14).

JM: "a summary message of here's what was accomplished, the time I spent on it,
the time / cost the model spent on it, the number of points earned ... like a
final report". done_session.py builds one per task BEFORE closing it (the claim
and the timer link are gone afterwards) and the /fin skill renders it, writing
the "accomplished" bullets itself from the thread and the 😈 turn notes here.

  jm        Toggl minutes on the task since the claim: the entry linked to it
            (agent_claims.timer_link) plus any entry whose description is the
            task's annotation-free name (older timers, the phone).
  model     from the session transcript (~/.claude/projects/*/<sid>.jsonl):
            JM turns, assistant messages, tokens by model since the claim, and
            an API-equivalent cost from PRICING (JM is on a Max plan, so this is
            what the work would have cost at list price, not a charge).
  notes     the 😈 two-sentence turn summaries agent_claims posted on the task.
  points    the task's [N] (lib/fen_split.total_points).
"""
from __future__ import annotations

import datetime as _dt
import glob
import importlib.util
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = Path.home() / "i446-monorepo" / "lib"
PROJECTS = Path.home() / ".claude" / "projects"
ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")

# $ per million tokens (claude-api skill table, cached 2026-10-06; cache reads
# per the per-model notes). cache_write_5m is the standard 1.25× input,
# cache_write_1h is 2× input. Matched by substring of the model id, first hit.
PRICING = [
    ("fable-5",  {"input": 10.0, "output": 50.0, "cache_read": 0.25}),
    ("mythos-5", {"input": 10.0, "output": 50.0, "cache_read": 0.25}),
    ("opus-5-5", {"input": 4.0, "output": 20.0, "cache_read": 0.20}),
    ("opus-5",   {"input": 5.0, "output": 25.0, "cache_read": 0.50}),
    ("opus-4",   {"input": 5.0, "output": 25.0, "cache_read": 0.50}),
    ("sonnet-5", {"input": 2.0, "output": 10.0, "cache_read": 0.20}),
    ("sonnet-4", {"input": 3.0, "output": 15.0, "cache_read": 0.30}),
    ("haiku-5",  {"input": 0.10, "output": 0.50, "cache_read": 0.01}),
    ("haiku-4",  {"input": 1.0, "output": 5.0, "cache_read": 0.10}),
]
INJECTED = ("<command-", "<local-command-", "<task-notification", "[SYSTEM NOTIFICATION",
            "<system-reminder", "[claim hook]")


def _mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def timer_key(s: str) -> str:
    return " ".join(ANNOT_RE.sub("", s.replace("😈", "")).split()).lower()


def prices_for(model: str) -> dict | None:
    m = (model or "").lower()
    return next((p for key, p in PRICING if key in m), None)


def cost_usd(by_model: dict[str, dict]) -> float | None:
    """None when any model is unpriced, so a report never shows a wrong figure."""
    total = 0.0
    for model, u in by_model.items():
        p = prices_for(model)
        if not p:
            return None
        total += (u.get("input", 0) * p["input"] + u.get("output", 0) * p["output"]
                  + u.get("cache_read", 0) * p["cache_read"]
                  + u.get("cache_write_5m", 0) * p["input"] * 1.25
                  + u.get("cache_write_1h", 0) * p["input"] * 2.0) / 1_000_000
    return round(total, 2)


def transcript_path(sid: str) -> Path | None:
    hits = glob.glob(str(PROJECTS / "*" / f"{sid}.jsonl"))
    return Path(max(hits, key=os.path.getmtime)) if hits else None


def _ts(o: dict) -> float:
    t = o.get("timestamp") or ""
    try:
        return _dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _is_jm_turn(o: dict) -> bool:
    if o.get("type") != "user" or o.get("isSidechain"):
        return False
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str) or not c.strip():
        return False
    return not c.lstrip().startswith(INJECTED)


def model_usage(path: Path | None, since: float = 0.0, now: float | None = None) -> dict:
    """Turns, messages and tokens since `since`. Streamed assistant messages
    repeat their usage on every content-block line: the last line per
    message id wins."""
    now = now or time.time()
    out = {"turns": 0, "messages": 0, "first": None, "last": None, "by_model": {}, "tokens": {}}
    if not path or not path.exists():
        return out
    per_msg: dict[str, tuple[str, dict]] = {}
    with open(path, "rb") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            ts = _ts(o)
            if ts and ts < since:
                continue
            if _is_jm_turn(o):
                out["turns"] += 1
            elif o.get("type") == "assistant":
                msg = o.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                per_msg[msg.get("id") or o.get("uuid") or str(ts)] = (msg.get("model") or "", u)
            else:
                continue
            if ts:
                out["first"] = ts if out["first"] is None else min(out["first"], ts)
                out["last"] = ts if out["last"] is None else max(out["last"], ts)
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
    for model, u in per_msg.values():
        bm = out["by_model"].setdefault(model, dict.fromkeys(tot, 0))
        cc = u.get("cache_creation") or {}
        w1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
        w5m = int(cc.get("ephemeral_5m_input_tokens") or 0)
        if not cc:
            w5m = int(u.get("cache_creation_input_tokens") or 0)
        for k, v in (("input", u.get("input_tokens")), ("output", u.get("output_tokens")),
                     ("cache_read", u.get("cache_read_input_tokens")),
                     ("cache_write_5m", w5m), ("cache_write_1h", w1h)):
            bm[k] += int(v or 0)
            tot[k] += int(v or 0)
    out["messages"] = len(per_msg)
    out["tokens"] = tot
    out["cost_usd"] = cost_usd(out["by_model"]) if per_msg else 0.0
    out["active_minutes"] = round((out["last"] - out["first"]) / 60) if out["first"] and out["last"] else 0
    return out


def jm_minutes(entries: list[dict], content: str, linked_entry_id=None, now: float | None = None) -> tuple[int, int]:
    """(minutes, entries) on this task: the linked entry plus same-named ones."""
    now = now or time.time()
    key = timer_key(content)
    secs, n = 0, 0
    for e in entries or []:
        linked = linked_entry_id is not None and str(e.get("id")) == str(linked_entry_id)
        if not linked and (not key or timer_key(e.get("description") or "") != key):
            continue
        d = int(e.get("duration") or 0)
        if d < 0:   # running: Toggl stores -start_epoch
            try:
                st = _dt.datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).timestamp()
                d = max(0, int(now - st))
            except (ValueError, TypeError):
                d = 0
        secs += d
        n += 1
    return round(secs / 60), n


def toggl_entries_since(since: float) -> list[dict]:
    try:
        tc = _mod(Path.home() / "i446-monorepo" / "mcp" / "toggl_server" / "toggl_cli.py", "toggl_cli_for_fin")
        start = _dt.datetime.fromtimestamp(since).date().isoformat()
        end = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        return tc.toggl_api.get_entries(start_date=start, end_date=end) or []
    except Exception:  # noqa: BLE001 — the report must never block the close
        return []


def demon_notes(tid: str) -> list[str]:
    try:
        import sys
        sys.path.insert(0, str(LIB))
        import todoist
        return [str(c.get("content", "")).replace("😈", "", 1).strip()
                for c in (todoist.get_comments(tid) or [])
                if str(c.get("content", "")).lstrip().startswith("😈")]
    except Exception:  # noqa: BLE001
        return []


def task_points(content: str) -> int:
    try:
        import sys
        sys.path.insert(0, str(LIB))
        import fen_split
        return int(fen_split.total_points(content) or 0)
    except Exception:  # noqa: BLE001
        m = re.search(r"\[(\d+)\]", content or "")
        return int(m.group(1)) if m else 0


def build(sid: str, tid: str, content: str, since: float, *, credited: bool,
          linked_entry_id=None, now: float | None = None) -> dict:
    now = now or time.time()
    mins, n = jm_minutes(toggl_entries_since(since), content, linked_entry_id, now)
    mu = model_usage(transcript_path(sid), since, now)
    return {
        "task": content,
        "points": task_points(content) if credited else 0,
        "since": int(since),
        "wall_minutes": round((now - since) / 60) if since else None,
        "jm": {"minutes": mins, "entries": n},
        "model": {"turns": mu["turns"], "messages": mu["messages"],
                  "active_minutes": mu["active_minutes"], "tokens": mu["tokens"],
                  "by_model": sorted(mu["by_model"]), "cost_usd": mu.get("cost_usd")},
        "notes": demon_notes(tid),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        sys.exit("usage: fin_report.py <session id> <task id> <content> [since_epoch]")
    since = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    print(json.dumps(build(sys.argv[1], sys.argv[2], sys.argv[3], since, credited=True),
                     ensure_ascii=False, indent=2))
