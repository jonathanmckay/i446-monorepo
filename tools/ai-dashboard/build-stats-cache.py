#!/usr/bin/env python3
"""build-stats-cache.py — rebuild ~/.claude/stats-cache.json from JSONL session files.

Why: Claude Code 2.1.96 (cmux-bundled) no longer auto-maintains stats-cache.json,
so the m5x2 AI dashboard pipeline froze on 2026-04-19. This script walks the raw
session JSONLs (which ARE up to date) and produces the same JSON shape the
dashboard already consumes.

Sources scanned (any that exist):
  ~/.claude/projects/        — local Claude sessions
  ~/.claude/projects-ix/     — ix Claude sessions (mirrored by pull-from-ix.sh)

Output: ~/.claude/stats-cache.json (the existing periodic-sync pipeline takes
it from there and pushes to the m5x2 repo).
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

LOCAL_TZ_OFFSET_HOURS = -7  # PDT; matches the existing hourCounts buckets

SOURCES = [
    Path.home() / ".claude" / "projects",
    Path.home() / ".claude" / "projects-ix",
]
OUT = Path.home() / ".claude" / "stats-cache.json"


def parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # "2026-04-23T22:01:07.559Z"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def local_date(ts: datetime) -> str:
    return (ts + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)).date().isoformat()


def local_hour(ts: datetime) -> int:
    return (ts + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)).hour


def main():
    # Per-day aggregates
    msgs_per_day = Counter()                    # date -> message count
    tool_calls_per_day = Counter()              # date -> tool_use block count
    sessions_per_day = defaultdict(set)         # date -> {sessionId}
    tokens_per_day_model = defaultdict(Counter) # date -> Counter(model -> input+output tokens)

    # Lifetime aggregates
    model_usage = defaultdict(lambda: {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "costUSD": 0,
        "contextWindow": 0,
        "maxOutputTokens": 0,
    })
    all_session_ids = set()
    total_messages = 0
    earliest_ts: datetime | None = None
    hour_counts = Counter()

    # Per-session tracking for "longest session" (by duration in ms)
    session_first_ts: dict[str, datetime] = {}
    session_last_ts: dict[str, datetime] = {}
    session_msg_count: Counter = Counter()
    session_first_seen_at: dict[str, datetime] = {}  # for tiebreak/timestamp field

    # Dedupe across mirrored sources: (sessionId, uuid) is unique per record
    seen_uuids: set[tuple[str, str]] = set()

    files = []
    for src in SOURCES:
        if src.exists():
            files.extend(src.rglob("*.jsonl"))
    if not files:
        print(f"no JSONL sources found under {[str(s) for s in SOURCES]}", file=sys.stderr)
        sys.exit(1)

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    rtype = d.get("type")
                    if rtype not in ("user", "assistant"):
                        continue

                    sid = d.get("sessionId") or ""
                    uuid = d.get("uuid") or ""
                    key = (sid, uuid)
                    if uuid and key in seen_uuids:
                        continue
                    if uuid:
                        seen_uuids.add(key)

                    ts_raw = d.get("timestamp")
                    ts = parse_ts(ts_raw)
                    if ts is None:
                        continue
                    date = local_date(ts)
                    hour = local_hour(ts)

                    total_messages += 1
                    msgs_per_day[date] += 1
                    hour_counts[hour] += 1
                    if sid:
                        all_session_ids.add(sid)
                        sessions_per_day[date].add(sid)
                        session_msg_count[sid] += 1
                        if sid not in session_first_ts or ts < session_first_ts[sid]:
                            session_first_ts[sid] = ts
                            session_first_seen_at[sid] = ts
                        if sid not in session_last_ts or ts > session_last_ts[sid]:
                            session_last_ts[sid] = ts
                    if earliest_ts is None or ts < earliest_ts:
                        earliest_ts = ts

                    # Assistant-only: token usage + tool calls
                    if rtype == "assistant":
                        msg = d.get("message") or {}
                        if not isinstance(msg, dict):
                            continue
                        model = msg.get("model") or ""
                        if model and model != "<synthetic>":
                            usage = msg.get("usage") or {}
                            in_t = int(usage.get("input_tokens", 0) or 0)
                            out_t = int(usage.get("output_tokens", 0) or 0)
                            cr_t = int(usage.get("cache_read_input_tokens", 0) or 0)
                            cc_t = int(usage.get("cache_creation_input_tokens", 0) or 0)
                            stu = usage.get("server_tool_use") or {}
                            web_search = int(stu.get("web_search_requests", 0) or 0) if isinstance(stu, dict) else 0

                            mu = model_usage[model]
                            mu["inputTokens"] += in_t
                            mu["outputTokens"] += out_t
                            mu["cacheReadInputTokens"] += cr_t
                            mu["cacheCreationInputTokens"] += cc_t
                            mu["webSearchRequests"] += web_search

                            # Per-day tokensByModel: input + output (matches dashboard.py L249)
                            tokens_per_day_model[date][model] += in_t + out_t

                        # Tool calls: count tool_use blocks in content
                        content = msg.get("content")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    tool_calls_per_day[date] += 1
        except Exception as e:
            print(f"  warn: failed to read {fp}: {e}", file=sys.stderr)
            continue

    # Build dailyActivity / dailyModelTokens (sorted by date asc)
    all_dates = sorted(set(msgs_per_day) | set(tokens_per_day_model) | set(sessions_per_day))
    daily_activity = []
    daily_model_tokens = []
    for d in all_dates:
        daily_activity.append({
            "date": d,
            "messageCount": msgs_per_day[d],
            "sessionCount": len(sessions_per_day[d]),
            "toolCallCount": tool_calls_per_day[d],
        })
        daily_model_tokens.append({
            "date": d,
            "tokensByModel": dict(tokens_per_day_model[d]),
        })

    # Longest session: by duration (last - first) in ms
    longest_sid = None
    longest_dur_ms = -1
    for sid in all_session_ids:
        first = session_first_ts.get(sid)
        last = session_last_ts.get(sid)
        if not first or not last:
            continue
        dur_ms = int((last - first).total_seconds() * 1000)
        if dur_ms > longest_dur_ms:
            longest_dur_ms = dur_ms
            longest_sid = sid
    longest_session = {
        "sessionId": longest_sid or "",
        "duration": max(longest_dur_ms, 0),
        "messageCount": session_msg_count[longest_sid] if longest_sid else 0,
        "timestamp": session_first_seen_at[longest_sid].isoformat().replace("+00:00", "Z")
                     if longest_sid and longest_sid in session_first_seen_at else "",
    }

    today = datetime.now(timezone.utc) + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    out = {
        "version": 3,
        "lastComputedDate": today.date().isoformat(),
        "dailyActivity": daily_activity,
        "dailyModelTokens": daily_model_tokens,
        "modelUsage": dict(model_usage),
        "totalSessions": len(all_session_ids),
        "totalMessages": total_messages,
        "longestSession": longest_session,
        "firstSessionDate": (earliest_ts.isoformat().replace("+00:00", "Z")
                             if earliest_ts else ""),
        "hourCounts": {str(h): hour_counts[h] for h in sorted(hour_counts)},
        "totalSpeculationTimeSavedMs": 0,
    }

    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    print(f"  files scanned: {len(files)}")
    print(f"  date range:    {all_dates[0] if all_dates else '?'} .. {all_dates[-1] if all_dates else '?'}")
    print(f"  totals:        sessions={len(all_session_ids)}  messages={total_messages}")
    print(f"  models:        {sorted(model_usage)}")


if __name__ == "__main__":
    main()
