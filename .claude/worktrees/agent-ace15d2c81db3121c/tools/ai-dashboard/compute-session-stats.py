#!/usr/bin/env python3
"""
compute-session-stats.py — compute MCP, skill, and latency stats from local
Claude Code JSONL session files and write to a JSON file for the m5x2 dashboard.

Run this on your own machine as part of the sync workflow:
    python3 compute-session-stats.py --user ian --out ~/m5x2-ai-stats/ian/session-stats.json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import pytz
    pacific = pytz.timezone("America/Los_Angeles")
except ImportError:
    pacific = None


def get_session_dir():
    """Find the Claude Code projects directory for the current user."""
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return None
    # Pick the subdirectory that has the most JSONL files
    candidates = [d for d in base.iterdir() if d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: len(list(d.glob("*.jsonl"))))


def compute_mcp_stats(session_dir, days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    daily_by_server = defaultdict(Counter)
    all_servers = set()
    total = 0

    for fpath in session_dir.glob("*.jsonl"):
        try:
            with open(fpath) as fh:
                for line in fh:
                    obj = json.loads(line.strip())
                    if obj.get("type") != "assistant" or "message" not in obj:
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                    msg = obj["message"]
                    if not isinstance(msg, dict):
                        continue
                    for block in msg.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        if not name.startswith("mcp__"):
                            continue
                        parts = name.split("__")
                        server = parts[1] if len(parts) > 1 else "unknown"
                        if pacific:
                            date = ts.astimezone(pacific).strftime("%Y-%m-%d")
                        else:
                            date = ts.strftime("%Y-%m-%d")
                        daily_by_server[date][server] += 1
                        all_servers.add(server)
                        total += 1
        except Exception:
            continue

    dates = sorted(daily_by_server)
    servers = sorted(all_servers)
    daily = [
        {"date": d, **{s: daily_by_server[d].get(s, 0) for s in servers},
         "total": sum(daily_by_server[d].values())}
        for d in dates
    ]
    return {"daily": daily, "servers": servers, "total": total}


def compute_skill_stats(session_dir, days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    EXCLUDED = {"/clear", "/exit", "/model", "/help", "/compact", "/config", "/fast",
                "/login", "/logout", "/status", "/doctor", "/permissions", "/memory",
                "/review", "/cost", "/init", "/terminal-setup", "/vim", "/bug"}

    daily_by_skill = defaultdict(Counter)
    all_skills = set()
    total = 0

    for fpath in session_dir.glob("*.jsonl"):
        try:
            with open(fpath) as fh:
                for line in fh:
                    obj = json.loads(line.strip())
                    if obj.get("type") != "user" or "message" not in obj:
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                    msg = obj["message"]
                    if not isinstance(msg, dict):
                        continue
                    for block in msg.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "text":
                            continue
                        text = block.get("text", "").strip()
                        if not text.startswith("/"):
                            continue
                        cmd = text.split()[0].lower()
                        if cmd in EXCLUDED:
                            continue
                        if pacific:
                            date = ts.astimezone(pacific).strftime("%Y-%m-%d")
                        else:
                            date = ts.strftime("%Y-%m-%d")
                        daily_by_skill[date][cmd] += 1
                        all_skills.add(cmd)
                        total += 1
        except Exception:
            continue

    dates = sorted(daily_by_skill)
    skills = sorted(all_skills)
    daily = [
        {"date": d, **{s: daily_by_skill[d].get(s, 0) for s in skills},
         "total": sum(daily_by_skill[d].values())}
        for d in dates
    ]
    return {"daily": daily, "skills": skills, "total": total}


def compute_latency_stats(session_dir, days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    daily_latencies = defaultdict(lambda: {"ttft": [], "ttlt": [], "wall": []})

    for fpath in session_dir.glob("*.jsonl"):
        try:
            messages = []
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if "timestamp" in obj and "message" in obj:
                        messages.append(obj)

            messages.sort(key=lambda x: x.get("timestamp", ""))
            for i, obj in enumerate(messages):
                if obj.get("type") != "assistant":
                    continue
                ts_str = obj.get("timestamp")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                if pacific:
                    date = ts.astimezone(pacific).strftime("%Y-%m-%d")
                else:
                    date = ts.strftime("%Y-%m-%d")

                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage", {})
                ttft = usage.get("time_to_first_token_ms")
                ttlt = usage.get("time_to_last_token_ms")
                if ttft and 0 < ttft < 60000:
                    daily_latencies[date]["ttft"].append(ttft / 1000)
                if ttlt and 0 < ttlt < 300000:
                    daily_latencies[date]["ttlt"].append(ttlt / 1000)

                # Wall clock: time from preceding user message
                if i > 0:
                    prev = messages[i - 1]
                    if prev.get("type") == "user":
                        try:
                            prev_ts = datetime.fromisoformat(prev["timestamp"].replace("Z", "+00:00"))
                            wall = (ts - prev_ts).total_seconds()
                            if 0 < wall < 1800:
                                daily_latencies[date]["wall"].append(wall)
                        except Exception:
                            pass
        except Exception:
            continue

    daily = []
    for date in sorted(daily_latencies):
        d = daily_latencies[date]
        row = {"date": date}
        for key in ("ttft", "ttlt", "wall"):
            vals = sorted(d[key])
            n = len(vals)
            if n:
                row[f"p50_{key}"] = round(vals[int(n * 0.5)], 1)
                row[f"p95_{key}"] = round(vals[min(int(n * 0.95), n - 1)], 1)
            else:
                row[f"p50_{key}"] = None
                row[f"p95_{key}"] = None
        daily.append(row)

    return {"daily": daily, "overall": {}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True, help="User ID (e.g. ian, lx)")
    parser.add_argument("--out", required=True, help="Output JSON file path")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    session_dir = get_session_dir()
    if not session_dir:
        print("ERROR: could not find ~/.claude/projects/ directory", file=sys.stderr)
        sys.exit(1)

    print(f"Reading sessions from: {session_dir}")

    result = {
        "user": args.user,
        "computed_at": datetime.now().isoformat(),
        "mcp": compute_mcp_stats(session_dir, args.days),
        "skills": compute_skill_stats(session_dir, args.days),
        "latency": compute_latency_stats(session_dir, args.days),
    }

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote session stats to: {out}")
    print(f"  MCP calls: {result['mcp']['total']}")
    print(f"  Skill calls: {result['skills']['total']}")


if __name__ == "__main__":
    main()
