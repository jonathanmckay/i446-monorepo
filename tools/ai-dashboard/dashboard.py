#!/usr/bin/env python3
"""
AI Tools Usage Dashboard

A web dashboard showing LLM usage stats, costs, and GitHub activity.
Run: python3 dashboard.py
Then open: http://localhost:5555
"""

import json
import glob
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, render_template_string, jsonify
import pytz
import requests

app = Flask(__name__)

STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"
LLM_DB = Path.home() / "vault" / "i447" / "i446" / "llm-sessions.db"
GITHUB_USER = "jonathanmckay"

# Get GitHub token from gh CLI to avoid rate limits
try:
    GITHUB_TOKEN = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
except Exception:
    GITHUB_TOKEN = None

# Pricing
PRICING = {
    "opus": {"input": 5.00, "output": 25.00, "cache_write": 5.00, "cache_read": 0.50},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_write": 3.00, "cache_read": 0.30},
}


def _parse_jsonl_daily_stats(since_date_str):
    """Parse JSONL session files to get daily activity/token stats after since_date_str (YYYY-MM-DD).
    Returns (activity_by_day, tokens_by_day, cost_by_model).
    cost_by_model uses full pricing (input+output+cache) for accurate cost totals.
    tokens_by_day uses input+output only to match stats-cache.json chart methodology.
    """
    pacific = pytz.timezone("America/Los_Angeles")
    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {}, {}, {}

    daily_activity = defaultdict(lambda: {"messageCount": 0, "sessionCount": 0})
    daily_tokens = defaultdict(lambda: defaultdict(int))
    # model -> cost (for adding to total_cost)
    extra_model_costs = defaultdict(float)

    for fpath in session_dir.glob("*.jsonl"):
        try:
            session_counted = set()
            with open(fpath) as fh:
                for line in fh:
                    obj = json.loads(line.strip())
                    if obj.get("type") != "assistant":
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                    if day <= since_date_str:
                        continue

                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue

                    daily_activity[day]["messageCount"] += 1
                    session_id = obj.get("sessionId", str(fpath))
                    if session_id not in session_counted:
                        session_counted.add(session_id)
                        daily_activity[day]["sessionCount"] += 1

                    usage = msg.get("usage", {})
                    model = msg.get("model", "unknown")

                    # Chart tokens: input + output only (matches stats-cache.json)
                    daily_tokens[day][model] += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

                    # Cost: full pricing including cache tokens
                    model_type = "opus" if "opus" in model.lower() else "sonnet"
                    prices = PRICING[model_type]
                    cost = (
                        (usage.get("input_tokens", 0) / 1_000_000) * prices["input"] +
                        (usage.get("output_tokens", 0) / 1_000_000) * prices["output"] +
                        (usage.get("cache_read_input_tokens", 0) / 1_000_000) * prices["cache_read"] +
                        (usage.get("cache_creation_input_tokens", 0) / 1_000_000) * prices["cache_write"]
                    )
                    extra_model_costs[model_type] += cost
        except Exception:
            continue

    activity_out = {
        day: {"date": day, "messageCount": v["messageCount"], "sessionCount": v["sessionCount"]}
        for day, v in daily_activity.items()
    }
    tokens_out = {
        day: {"date": day, "tokensByModel": dict(models)}
        for day, models in daily_tokens.items()
    }
    return activity_out, tokens_out, dict(extra_model_costs)


def get_claude_stats():
    """Get Claude Code stats from stats-cache.json, supplemented with JSONL parsing for recent days."""
    if not STATS_CACHE.exists():
        return None

    with open(STATS_CACHE) as f:
        data = json.load(f)

    # Calculate all-time costs and turns
    total_cost = 0.0
    total_turns = 0
    model_costs = {}

    for model, usage in data.get("modelUsage", {}).items():
        model_type = "opus" if "opus" in model.lower() else "sonnet"
        prices = PRICING[model_type]

        inp = usage.get("inputTokens", 0)
        out = usage.get("outputTokens", 0)
        cache_read = usage.get("cacheReadInputTokens", 0)
        cache_write = usage.get("cacheCreationInputTokens", 0)

        cost = (
            (inp / 1_000_000) * prices["input"] +
            (out / 1_000_000) * prices["output"] +
            (cache_read / 1_000_000) * prices["cache_read"] +
            (cache_write / 1_000_000) * prices["cache_write"]
        )

        total_cost += cost
        model_costs[model_type] = model_costs.get(model_type, 0) + cost

    # Estimate turns from total messages
    total_messages = data.get("totalMessages", 0)
    total_turns = total_messages // 6  # Conservative estimate

    daily_activity = data.get("dailyActivity", [])
    daily_tokens = data.get("dailyModelTokens", [])

    # Supplement with JSONL-parsed data for any days after the last cache entry
    last_cached_date = daily_activity[-1]["date"] if daily_activity else "2000-01-01"
    today = datetime.now().strftime("%Y-%m-%d")
    if last_cached_date < today:
        extra_activity, extra_tokens, extra_costs = _parse_jsonl_daily_stats(last_cached_date)
        for day in sorted(extra_activity):
            daily_activity.append(extra_activity[day])
        for day in sorted(extra_tokens):
            daily_tokens.append(extra_tokens[day])
        for model_type, cost in extra_costs.items():
            total_cost += cost
            model_costs[model_type] = model_costs.get(model_type, 0) + cost

    return {
        "total_sessions": data.get("totalSessions", 0),
        "total_messages": total_messages,
        "total_turns": total_turns,
        "total_cost": total_cost,
        "model_costs": model_costs,
        "daily_activity": daily_activity[-30:],  # Last 30 days
        "daily_tokens": daily_tokens[-30:],
    }


def get_copilot_stats():
    """Get Copilot stats from llm-sessions.db

    NOTE: Copilot input tokens are broken/unreliable in the database.
    We ONLY use output_tokens for all calculations and cost estimates.
    DO NOT add input_tokens to any Copilot queries or calculations.
    """
    if not LLM_DB.exists():
        return None

    conn = sqlite3.connect(f"file:{LLM_DB}?mode=ro", uri=True)

    # All-time stats
    # NOTE: Deliberately NOT selecting input_tokens - they're broken for Copilot
    row = conn.execute("""
        SELECT
            COUNT(*) as sessions,
            SUM(message_count) as turns,
            SUM(output_tokens) as output
        FROM sessions
        WHERE provider = 'copilot'
    """).fetchone()

    sessions, turns, output = row if row else (0, 0, 0)

    # Output-only cost (Opus pricing)
    # NOTE: Input costs excluded because input_tokens are unreliable for Copilot
    total_cost = (output / 1_000_000) * PRICING["opus"]["output"]

    # Last 30 days
    # NOTE: Deliberately NOT selecting input_tokens - they're broken for Copilot
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    daily = conn.execute("""
        SELECT
            DATE(start_time) as day,
            COUNT(*) as sessions,
            SUM(message_count) as messages,
            SUM(output_tokens) as output
        FROM sessions
        WHERE provider = 'copilot' AND DATE(start_time) >= ?
        GROUP BY day
        ORDER BY day
    """, (month_ago,)).fetchall()

    conn.close()

    return {
        "total_sessions": sessions,
        "total_turns": turns,
        "total_cost": total_cost,
        "daily_activity": [
            {"date": d[0], "sessions": d[1], "turns": d[2], "output": d[3]}
            for d in daily
        ]
    }


def get_github_commits():
    """Get GitHub commits with daily breakdown for heatmap"""
    try:
        from datetime import timezone
        from collections import defaultdict
        import pytz

        # Use Pacific timezone (America/Los_Angeles)
        pacific_tz = pytz.timezone('America/Los_Angeles')

        # Get commits for last 90 days
        days_ago_90 = datetime.now(timezone.utc) - timedelta(days=90)
        daily_commits = defaultdict(int)
        repos = set()
        total_commits = 0

        # Strategy: Check ALL repos for recent commits
        # Use /user/repos for authenticated requests (includes private repos)
        # Fall back to /users/{username}/repos for unauthenticated (public only)
        if GITHUB_TOKEN:
            repos_url = "https://api.github.com/user/repos?sort=pushed&per_page=100&affiliation=owner"
            headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        else:
            repos_url = f"https://api.github.com/users/{GITHUB_USER}/repos?type=all&sort=pushed&per_page=100"
            headers = {}
        repos_response = requests.get(repos_url, headers=headers, timeout=10)

        if repos_response.status_code == 200:
            all_repos = repos_response.json()

            for repo in all_repos:
                pushed_at = repo.get("pushed_at")
                if not pushed_at:
                    continue

                push_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                if push_date < days_ago_90:
                    continue

                # Fetch commits from this repo
                try:
                    commits_url = f"https://api.github.com/repos/{repo['full_name']}/commits"
                    commits_params = {
                        "since": days_ago_90.isoformat(),
                        "per_page": 100
                        # NOTE: Not filtering by author - git configs vary (mckay vs jonathanmckay)
                    }
                    commits_response = requests.get(commits_url, params=commits_params, headers=headers, timeout=5)

                    if commits_response.status_code == 200:
                        repo_commits = commits_response.json()

                        if repo_commits:
                            repos.add(repo["full_name"])

                        for commit in repo_commits:
                            # Only count commits where author.login matches the authenticated user
                            # This matches GitHub's contribution graph behavior
                            author_login = commit.get("author", {}).get("login", "") if commit.get("author") else ""
                            if author_login != GITHUB_USER:
                                continue

                            commit_date_str = commit.get("commit", {}).get("author", {}).get("date", "")
                            if commit_date_str:
                                commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                                # Convert to Pacific timezone before formatting date
                                commit_date_pacific = commit_date.astimezone(pacific_tz)
                                date_key = commit_date_pacific.strftime("%Y-%m-%d")
                                daily_commits[date_key] += 1
                                total_commits += 1

                except Exception:
                    continue

        return {
            "total_commits": total_commits,
            "daily_commits": dict(daily_commits),
            "repos": list(repos)[:10],
            "note": "Public repos only" if total_commits == 0 else None
        }
    except Exception as e:
        return {"total_commits": 0, "daily_commits": {}, "repos": [], "error": str(e)}


def get_mcp_stats(days=30):
    """Get MCP tool call stats from Claude Code JSONL session logs.

    Scans session files for tool_use blocks with mcp__ prefix,
    groups by server name and day.
    """
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "servers": [], "total": 0}

    # date -> server -> count
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
                        # Extract server: mcp__google-workspace__read_sheet -> google-workspace
                        parts = name.split("__", 2)
                        server = parts[1] if len(parts) >= 3 else name
                        day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                        daily_by_server[day][server] += 1
                        all_servers.add(server)
                        total += 1
        except Exception:
            continue

    # Build sorted daily list
    servers = sorted(all_servers)
    daily = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        entry = {"date": date}
        day_total = 0
        for s in servers:
            count = daily_by_server.get(date, {}).get(s, 0)
            entry[s] = count
            day_total += count
        entry["total"] = day_total
        daily.append(entry)

    return {"daily": daily, "servers": servers, "total": total}


def get_latency_stats(days=30):
    """Compute TTFT and TTLT from Claude Code JSONL session files.

    TTFT = Time to First Turn: seconds from user's first message to assistant's first response.
    TTLT = Time to Last Turn: seconds from user's last message to assistant's final response.
    Returns daily averages and overall percentiles for the last `days` days.
    """
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "overall": {}}

    # date -> {"ttft": [seconds, ...], "ttlt": [seconds, ...]}
    daily_latencies = defaultdict(lambda: {"ttft": [], "ttlt": []})

    for fpath in session_dir.glob("*.jsonl"):
        try:
            messages = []
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg_type = obj.get("type")
                    if msg_type not in ("user", "assistant"):
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    messages.append((msg_type, ts))

            if len(messages) < 2:
                continue

            # For each user message, find the first and last consecutive assistant
            # messages before the next user message.
            #   TTFT = user_ts → first_assistant_ts  (time to first token)
            #   TTLT = user_ts → last_assistant_ts   (time to last token / prompt → completion)
            i = 0
            while i < len(messages):
                if messages[i][0] != "user":
                    i += 1
                    continue

                user_ts = messages[i][1]
                first_asst_ts = None
                last_asst_ts = None
                for j in range(i + 1, len(messages)):
                    if messages[j][0] == "assistant":
                        if first_asst_ts is None:
                            first_asst_ts = messages[j][1]
                        last_asst_ts = messages[j][1]
                    else:
                        break  # hit next user message

                if first_asst_ts is None:
                    i += 1
                    continue

                ttft = (first_asst_ts - user_ts).total_seconds()
                ttlt = (last_asst_ts - user_ts).total_seconds()

                if ttft <= 0 or ttft > 300 or ttlt > 300:
                    i += 1
                    continue

                if user_ts < cutoff:
                    i += 1
                    continue

                day = user_ts.astimezone(pacific).strftime("%Y-%m-%d")
                daily_latencies[day]["ttft"].append(ttft)
                daily_latencies[day]["ttlt"].append(ttlt)
                i += 1

        except Exception:
            continue

    daily = []
    all_ttft = []
    all_ttlt = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        d = daily_latencies.get(date, {"ttft": [], "ttlt": []})
        entry = {
            "date": date,
            "avg_ttft": round(sum(d["ttft"]) / len(d["ttft"]), 1) if d["ttft"] else None,
            "avg_ttlt": round(sum(d["ttlt"]) / len(d["ttlt"]), 1) if d["ttlt"] else None,
        }
        all_ttft.extend(d["ttft"])
        all_ttlt.extend(d["ttlt"])
        daily.append(entry)

    overall = {}
    if all_ttft:
        s = sorted(all_ttft)
        overall["avg_ttft"] = round(sum(s) / len(s), 1)
        overall["median_ttft"] = round(s[len(s) // 2], 1)
        overall["p95_ttft"] = round(s[min(int(len(s) * 0.95), len(s) - 1)], 1)
    if all_ttlt:
        s = sorted(all_ttlt)
        overall["avg_ttlt"] = round(sum(s) / len(s), 1)
        overall["median_ttlt"] = round(s[len(s) // 2], 1)
        overall["p95_ttlt"] = round(s[min(int(len(s) * 0.95), len(s) - 1)], 1)

    return {"daily": daily, "overall": overall}


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Tools Usage Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='35' fill='none' stroke='%23FF10F0' stroke-width='16' stroke-dasharray='55 165' transform='rotate(-90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2339FF14' stroke-width='16' stroke-dasharray='55 165' transform='rotate(0 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2300D4FF' stroke-width='16' stroke-dasharray='55 165' transform='rotate(90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23FFD700' stroke-width='16' stroke-dasharray='55 165' transform='rotate(180 50 50)'/></svg>">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #ffffff;
            color: #1a1a1a;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #FF10F0 0%, #39FF14 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
        }
        .subtitle { color: #666; margin-bottom: 30px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card {
            background: #ffffff;
            border: 2px solid #e0e0e0;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .card h2 {
            font-size: 1.2em;
            margin-bottom: 16px;
            color: #1a1a1a;
            font-weight: 700;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .metric:last-child { border-bottom: none; }
        .metric-label { color: #666; }
        .metric-value {
            font-weight: 700;
            color: #1a1a1a;
        }
        .cost { color: #39FF14; text-shadow: 0 0 10px rgba(57, 255, 20, 0.3); }
        .chart {
            margin-top: 20px;
            height: 350px;
            background: #fafafa;
            border-radius: 12px;
            padding: 16px;
            border: 1px solid #e0e0e0;
            position: relative;
        }
        .chart-container {
            display: flex;
            height: 100%;
        }
        .y-axis {
            display: flex;
            flex-direction: column-reverse;
            justify-content: space-between;
            width: 50px;
            height: 240px;
            font-size: 0.75em;
            color: #666;
            padding-right: 8px;
            text-align: right;
        }
        .bar-chart {
            display: flex;
            align-items: flex-end;
            height: 240px;
            gap: 2px;
            overflow-x: hidden;
            padding-bottom: 40px;
            flex: 1;
        }
        .bar {
            flex: 1;
            max-width: 40px;
            border-radius: 4px 4px 0 0;
            position: relative;
            min-height: 4px;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            cursor: pointer;
        }
        .bar-segment {
            width: 100%;
            position: relative;
        }
        .bar-segment.claude {
            background: linear-gradient(180deg, #FF10F0 0%, #FF6B9D 100%);
            box-shadow: 0 0 8px rgba(255, 16, 240, 0.3);
        }
        .bar-segment.copilot {
            background: linear-gradient(180deg, #00F0FF 0%, #0088FF 100%);
            box-shadow: 0 0 8px rgba(0, 240, 255, 0.3);
        }
        .bar-label {
            position: absolute;
            bottom: -24px;
            left: 50%;
            transform: translateX(-50%) rotate(-45deg);
            transform-origin: center;
            font-size: 0.65em;
            color: #333;
            white-space: nowrap;
        }
        .refresh {
            background: linear-gradient(135deg, #FF10F0 0%, #00F0FF 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            margin-top: 20px;
            box-shadow: 0 4px 12px rgba(255, 16, 240, 0.3);
        }
        .refresh:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(255, 16, 240, 0.4);
        }
        .last-updated {
            text-align: center;
            color: #999;
            margin-top: 30px;
            font-size: 0.9em;
        }
        .heatmap-grid {
            display: grid;
            grid-template-columns: repeat(53, 12px);
            grid-auto-rows: 12px;
            gap: 3px;
            font-size: 0.7em;
        }
        .heatmap-day {
            width: 12px;
            height: 12px;
            border-radius: 2px;
            background: #eee;
        }
        .heatmap-day.level-0 { background: #ebedf0; }
        .heatmap-day.level-1 { background: #9be9a8; }
        .heatmap-day.level-2 { background: #40c463; }
        .heatmap-day.level-3 { background: #30a14e; }
        .heatmap-day.level-4 { background: #216e39; }
        .legend {
            display: flex;
            gap: 20px;
            margin-top: 12px;
            font-size: 0.85em;
            justify-content: center;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .legend-color {
            width: 20px;
            height: 12px;
            border-radius: 2px;
        }
        .legend-color.claude {
            background: linear-gradient(90deg, #FF10F0 0%, #FF6B9D 100%);
        }
        .legend-color.copilot {
            background: linear-gradient(90deg, #00F0FF 0%, #0088FF 100%);
        }
        .chart-tooltip {
            position: absolute;
            background: rgba(0, 0, 0, 0.92);
            color: white;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 0.85em;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            display: none;
            line-height: 1.6;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        .chart-tooltip .tt-date {
            font-weight: 700;
            margin-bottom: 4px;
            border-bottom: 1px solid rgba(255,255,255,0.2);
            padding-bottom: 4px;
        }
        .chart-tooltip .tt-claude { color: #FF6B9D; }
        .chart-tooltip .tt-copilot { color: #00F0FF; }
        .chart-tooltip .tt-total { color: #fff; font-weight: 700; margin-top: 2px; }
        .heatmap-tooltip {
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.85em;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 AI Tools Usage Dashboard</h1>
        <p class="subtitle">Real-time tracking of LLM usage, costs, and developer activity</p>

        <div class="grid">
            <div class="card">
                <h2>📊 Claude Code</h2>
                <div class="metric">
                    <span class="metric-label">Sessions</span>
                    <span class="metric-value">{{ claude.total_sessions | default(0) }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Turns</span>
                    <span class="metric-value">{{ claude.total_turns | default(0) | format_number }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">All-time Cost</span>
                    <span class="metric-value cost">${{ "%.2f" | format(claude.total_cost | default(0)) }}</span>
                </div>
                {% if claude.model_costs %}
                <div class="metric">
                    <span class="metric-label">Opus</span>
                    <span class="metric-value cost">${{ "%.2f" | format(claude.model_costs.opus | default(0)) }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Sonnet</span>
                    <span class="metric-value cost">${{ "%.2f" | format(claude.model_costs.sonnet | default(0)) }}</span>
                </div>
                {% endif %}
            </div>

            <div class="card">
                <h2>🚀 Copilot CLI</h2>
                <div class="metric">
                    <span class="metric-label">Sessions</span>
                    <span class="metric-value">{{ copilot.total_sessions | default(0) }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Turns</span>
                    <span class="metric-value">{{ copilot.total_turns | default(0) }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Cost (output only)</span>
                    <span class="metric-value cost">${{ "%.2f" | format(copilot.total_cost | default(0)) }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label" style="font-size: 0.85em; color: #999;">
                        ⚠️ Input costs excluded
                    </span>
                </div>
            </div>

            <div class="card">
                <h2>💻 GitHub Activity</h2>
                <div style="margin-bottom: 16px;">
                    <span style="font-weight: 700; font-size: 1.2em;">{{ github.total_commits | default(0) }}</span>
                    <span style="color: #666; margin-left: 8px;">commits in the last 90 days</span>
                </div>
                <div id="github-heatmap" style="overflow-x: auto; position: relative;">
                    <!-- Will be populated by JavaScript -->
                    <div id="heatmap-tooltip" class="heatmap-tooltip"></div>
                </div>
                {% if github.note %}
                <div style="margin-top: 12px; font-size: 0.85em; color: #999;">
                    {{ github.note }}
                </div>
                {% endif %}
            </div>
        </div>

        <div class="card">
            <h2>📈 Last 30 Days - Tokens</h2>
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color claude"></div>
                    <span>Claude</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color copilot"></div>
                    <span>Copilot</span>
                </div>
            </div>
            <div class="chart" style="position:relative;">
                <div class="chart-container">
                    <div class="y-axis" id="tokens-y-axis"></div>
                    <div class="bar-chart" id="tokens-chart">
                        <!-- Will be populated by JavaScript -->
                    </div>
                </div>
                <div id="tokens-tooltip" class="chart-tooltip"></div>
            </div>
        </div>

        <div class="card">
            <h2>🔄 Last 30 Days - Turns</h2>
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color claude"></div>
                    <span>Claude</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color copilot"></div>
                    <span>Copilot</span>
                </div>
            </div>
            <div class="chart" style="position:relative;">
                <div class="chart-container">
                    <div class="y-axis" id="turns-y-axis"></div>
                    <div class="bar-chart" id="turns-chart">
                        <!-- Will be populated by JavaScript -->
                    </div>
                </div>
                <div id="turns-tooltip" class="chart-tooltip"></div>
            </div>
        </div>

        <div class="card">
            <h2>🔌 Last 30 Days - MCP Calls by Server</h2>
            <div class="legend" id="mcp-legend">
                <!-- Will be populated by JavaScript -->
            </div>
            <div class="chart">
                <div class="chart-container">
                    <div class="y-axis" id="mcp-y-axis"></div>
                    <div class="bar-chart" id="mcp-chart">
                        <!-- Will be populated by JavaScript -->
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>⚡ Response Latency — TTFT &amp; TTLT (Last 30 Days)</h2>
            <div class="grid" style="margin-bottom: 16px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.avg_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.median_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.p95_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.avg_ttlt | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.median_ttlt | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:#999; margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.p95_ttlt | default(0)) }}s
                    </div>
                </div>
            </div>
            <div style="display:flex; gap:20px; margin-bottom:12px; font-size:0.85em;">
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:24px; height:3px; background:#FF10F0; border-radius:2px;"></span>
                    TTFT (first response)
                </span>
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:24px; height:3px; background:#39FF14; border-radius:2px;"></span>
                    TTLT (last response)
                </span>
            </div>
            <div style="background:#fafafa; border:1px solid #e0e0e0; border-radius:12px; padding:16px; overflow:hidden;">
                <svg id="latency-chart" width="100%" height="200" style="overflow:visible;"></svg>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:#999;">
                Latency measured per Claude Code session (JSONL). TTFT = first user→assistant delta; TTLT = last user→assistant delta. Sessions with no message pairs or &gt;5 min gaps excluded.
            </div>
        </div>

        <button class="refresh" onclick="location.reload()">🔄 Refresh Data</button>

        <p class="last-updated">Last updated: {{ now }}</p>
    </div>

    <script>
        // Compute a "nice" ceiling for axis max and return clean tick values
        function niceMax(rawMax) {
            if (rawMax <= 0) return 1;
            const magnitude = Math.pow(10, Math.floor(Math.log10(rawMax)));
            const residual = rawMax / magnitude;
            let nice;
            if (residual <= 1) nice = 1;
            else if (residual <= 2) nice = 2;
            else if (residual <= 5) nice = 5;
            else nice = 10;
            return nice * magnitude;
        }

        function niceAxis(rawMax, steps = 5) {
            if (rawMax <= 0) return { max: 1, ticks: [0, 1] };
            const interval = niceMax(Math.ceil(rawMax / steps));
            const nMax = interval * steps;
            // If nMax is way too large (more than 2x raw), try fewer intervals
            const actualMax = Math.max(interval * Math.ceil(rawMax / interval), interval);
            const ticks = [];
            for (let v = 0; v <= actualMax; v += interval) {
                ticks.push(v);
            }
            return { max: actualMax, ticks };
        }

        function formatAxisLabel(n) {
            if (n >= 1_000_000) return (n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1) + 'M';
            if (n >= 1_000) return (n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 0) + 'k';
            return n.toString();
        }

        function createYAxis(containerId, axisDef) {
            const yAxis = document.getElementById(containerId);
            axisDef.ticks.forEach(v => {
                const label = document.createElement('div');
                label.textContent = formatAxisLabel(v);
                yAxis.appendChild(label);
            });
        }

        // Tooltip helpers
        function showTooltip(tooltipEl, chartEl, e, html) {
            tooltipEl.innerHTML = html;
            tooltipEl.style.display = 'block';
            const chartRect = chartEl.closest('.chart').getBoundingClientRect();
            let left = e.clientX - chartRect.left + 12;
            let top = e.clientY - chartRect.top - 10;
            // Keep tooltip on-screen within chart card
            const ttWidth = tooltipEl.offsetWidth;
            if (left + ttWidth > chartRect.width - 8) left = left - ttWidth - 24;
            if (top < 4) top = 4;
            tooltipEl.style.left = left + 'px';
            tooltipEl.style.top = top + 'px';
        }
        function hideTooltip(tooltipEl) { tooltipEl.style.display = 'none'; }

        // Render tokens chart (stacked)
        const dailyTokens = {{ daily_tokens | tojson | safe }};
        const tokensChart = document.getElementById('tokens-chart');
        const tokensTooltip = document.getElementById('tokens-tooltip');

        const rawMaxTokens = Math.max(...dailyTokens.map(d => d.total), 1);
        const tokensAxis = niceAxis(rawMaxTokens);
        createYAxis('tokens-y-axis', tokensAxis);

        dailyTokens.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            const totalHeight = tokensAxis.max > 0 ? (day.total / tokensAxis.max) * 100 : 5;
            bar.style.height = totalHeight + '%';

            // Claude segment (bottom)
            if (day.claude > 0) {
                const claudeSegment = document.createElement('div');
                claudeSegment.className = 'bar-segment claude';
                const claudeHeight = (day.claude / day.total) * 100;
                claudeSegment.style.height = claudeHeight + '%';
                bar.appendChild(claudeSegment);
            }

            // Copilot segment (top)
            if (day.copilot > 0) {
                const copilotSegment = document.createElement('div');
                copilotSegment.className = 'bar-segment copilot';
                const copilotHeight = (day.copilot / day.total) * 100;
                copilotSegment.style.height = copilotHeight + '%';
                copilotSegment.style.borderRadius = '4px 4px 0 0';
                bar.appendChild(copilotSegment);
            }

            bar.addEventListener('mouseenter', e => {
                const html = `<div class="tt-date">${day.date}</div>`
                    + `<div class="tt-claude">Claude: ${day.claude.toLocaleString()} tokens</div>`
                    + `<div class="tt-copilot">Copilot: ${day.copilot.toLocaleString()} tokens</div>`
                    + `<div class="tt-total">Total: ${day.total.toLocaleString()} tokens</div>`;
                showTooltip(tokensTooltip, tokensChart, e, html);
            });
            bar.addEventListener('mousemove', e => {
                const html = tokensTooltip.innerHTML;
                showTooltip(tokensTooltip, tokensChart, e, html);
            });
            bar.addEventListener('mouseleave', () => hideTooltip(tokensTooltip));

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5); // MM-DD
            bar.appendChild(label);

            tokensChart.appendChild(bar);
        });

        // Render turns chart (stacked)
        const dailyTurns = {{ daily_turns | tojson | safe }};
        const turnsChart = document.getElementById('turns-chart');
        const turnsTooltip = document.getElementById('turns-tooltip');

        const rawMaxTurns = Math.max(...dailyTurns.map(d => d.total), 1);
        const turnsAxis = niceAxis(rawMaxTurns);
        createYAxis('turns-y-axis', turnsAxis);

        dailyTurns.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            const totalHeight = turnsAxis.max > 0 ? (day.total / turnsAxis.max) * 100 : 5;
            bar.style.height = totalHeight + '%';

            // Claude segment (bottom)
            if (day.claude > 0) {
                const claudeSegment = document.createElement('div');
                claudeSegment.className = 'bar-segment claude';
                const claudeHeight = (day.claude / day.total) * 100;
                claudeSegment.style.height = claudeHeight + '%';
                bar.appendChild(claudeSegment);
            }

            // Copilot segment (top)
            if (day.copilot > 0) {
                const copilotSegment = document.createElement('div');
                copilotSegment.className = 'bar-segment copilot';
                const copilotHeight = (day.copilot / day.total) * 100;
                copilotSegment.style.height = copilotHeight + '%';
                copilotSegment.style.borderRadius = '4px 4px 0 0';
                bar.appendChild(copilotSegment);
            }

            bar.addEventListener('mouseenter', e => {
                const html = `<div class="tt-date">${day.date}</div>`
                    + `<div class="tt-claude">Claude: ${day.claude.toLocaleString()} turns</div>`
                    + `<div class="tt-copilot">Copilot: ${day.copilot.toLocaleString()} turns</div>`
                    + `<div class="tt-total">Total: ${day.total.toLocaleString()} turns</div>`;
                showTooltip(turnsTooltip, turnsChart, e, html);
            });
            bar.addEventListener('mousemove', e => {
                const html = turnsTooltip.innerHTML;
                showTooltip(turnsTooltip, turnsChart, e, html);
            });
            bar.addEventListener('mouseleave', () => hideTooltip(turnsTooltip));

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5); // MM-DD
            bar.appendChild(label);

            turnsChart.appendChild(bar);
        });

        // Render GitHub heatmap (last 90 days)
        const githubData = {{ github.daily_commits | tojson | safe }};
        const heatmapContainer = document.getElementById('github-heatmap');
        const tooltip = document.getElementById('heatmap-tooltip');

        // Create grid for last 90 days
        const today = new Date();
        const todayStr = today.toISOString().split('T')[0];
        const daysAgo = new Date(today);
        daysAgo.setDate(daysAgo.getDate() - 89);

        // Start from the first Sunday before 90 days ago
        const startDate = new Date(daysAgo);
        while (startDate.getDay() !== 0) {
            startDate.setDate(startDate.getDate() - 1);
        }

        const grid = document.createElement('div');
        grid.className = 'heatmap-grid';
        grid.style.gridTemplateColumns = 'repeat(14, 12px)';  // ~13 weeks for 90 days

        // Calculate max commits for color scaling
        const maxCommits = Math.max(...Object.values(githubData), 1);

        // Helper to format date
        function formatDate(dateStr) {
            const date = new Date(dateStr + 'T00:00:00');
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            return `${days[date.getDay()]}, ${months[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
        }

        // Generate ~13 weeks of days
        for (let week = 0; week < 14; week++) {
            for (let day = 0; day < 7; day++) {
                const currentDate = new Date(startDate);
                currentDate.setDate(currentDate.getDate() + (week * 7) + day);

                const dateStr = currentDate.toISOString().split('T')[0];
                if (dateStr > todayStr) continue;  // Don't show future dates
                const commits = githubData[dateStr] || 0;

                const square = document.createElement('div');
                square.className = 'heatmap-day';

                // Color levels based on commits
                let level = 0;
                if (commits > 0) {
                    const ratio = commits / maxCommits;
                    if (ratio >= 0.75) level = 4;
                    else if (ratio >= 0.5) level = 3;
                    else if (ratio >= 0.25) level = 2;
                    else level = 1;
                }
                square.classList.add(`level-${level}`);
                square.style.gridColumn = week + 1;
                square.style.gridRow = day + 1;

                // Add tooltip on hover
                square.addEventListener('mouseenter', function(e) {
                    const commitText = commits === 1 ? '1 commit' : `${commits} commits`;
                    tooltip.textContent = `${commitText} on ${formatDate(dateStr)}`;
                    tooltip.style.display = 'block';
                });

                square.addEventListener('mousemove', function(e) {
                    tooltip.style.left = (e.pageX - heatmapContainer.offsetLeft + 10) + 'px';
                    tooltip.style.top = (e.pageY - heatmapContainer.offsetTop - 30) + 'px';
                });

                square.addEventListener('mouseleave', function() {
                    tooltip.style.display = 'none';
                });

                grid.appendChild(square);
            }
        }

        heatmapContainer.appendChild(grid);

        // Render MCP calls chart (stacked by server)
        const mcpData = {{ mcp_daily | tojson | safe }};
        const mcpServers = {{ mcp_servers | tojson | safe }};
        const mcpChart = document.getElementById('mcp-chart');
        const mcpLegend = document.getElementById('mcp-legend');

        // Color palette for MCP servers
        const mcpColors = {
            'google-workspace': { bg: 'linear-gradient(180deg, #4285F4 0%, #669DF6 100%)', shadow: 'rgba(66, 133, 244, 0.3)', label: '#4285F4' },
            'toggl': { bg: 'linear-gradient(180deg, #E57CD8 0%, #F0A0E8 100%)', shadow: 'rgba(229, 124, 216, 0.3)', label: '#E57CD8' },
            'todoist': { bg: 'linear-gradient(180deg, #E44332 0%, #F06B5D 100%)', shadow: 'rgba(228, 67, 50, 0.3)', label: '#E44332' },
            'excel-mcp': { bg: 'linear-gradient(180deg, #217346 0%, #33A06F 100%)', shadow: 'rgba(33, 115, 70, 0.3)', label: '#217346' },
            'appfolio': { bg: 'linear-gradient(180deg, #FF8C00 0%, #FFA733 100%)', shadow: 'rgba(255, 140, 0, 0.3)', label: '#FF8C00' },
            'google-calendar': { bg: 'linear-gradient(180deg, #0B8043 0%, #34A853 100%)', shadow: 'rgba(11, 128, 67, 0.3)', label: '#0B8043' },
            'quickbooks': { bg: 'linear-gradient(180deg, #2CA01C 0%, #5BBF4A 100%)', shadow: 'rgba(44, 160, 28, 0.3)', label: '#2CA01C' },
            'neon': { bg: 'linear-gradient(180deg, #39FF14 0%, #7AFF5C 100%)', shadow: 'rgba(57, 255, 20, 0.3)', label: '#39FF14' },
        };
        // Fallback colors for unknown servers
        const fallbackColors = [
            { bg: 'linear-gradient(180deg, #9C27B0 0%, #BA68C8 100%)', shadow: 'rgba(156, 39, 176, 0.3)', label: '#9C27B0' },
            { bg: 'linear-gradient(180deg, #FF5722 0%, #FF8A65 100%)', shadow: 'rgba(255, 87, 34, 0.3)', label: '#FF5722' },
            { bg: 'linear-gradient(180deg, #607D8B 0%, #90A4AE 100%)', shadow: 'rgba(96, 125, 139, 0.3)', label: '#607D8B' },
        ];
        let fallbackIdx = 0;
        function getServerColor(server) {
            if (mcpColors[server]) return mcpColors[server];
            const c = fallbackColors[fallbackIdx % fallbackColors.length];
            fallbackIdx++;
            mcpColors[server] = c;
            return c;
        }

        // Build legend
        mcpServers.forEach(server => {
            const color = getServerColor(server);
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `<div class="legend-color" style="background: ${color.label};"></div><span>${server}</span>`;
            mcpLegend.appendChild(item);
        });

        const maxMcp = Math.min(Math.max(...mcpData.map(d => d.total), 1), 300);
        const mcpAxis = niceAxis(maxMcp);
        createYAxis('mcp-y-axis', mcpAxis);

        mcpData.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            const totalHeight = mcpAxis.max > 0 ? Math.min((day.total / mcpAxis.max) * 100, 100) : 0;
            bar.style.height = totalHeight + '%';

            // Stack segments bottom-to-top in server order
            let isTop = true;
            for (let i = mcpServers.length - 1; i >= 0; i--) {
                const server = mcpServers[i];
                const count = day[server] || 0;
                if (count <= 0) continue;
                const seg = document.createElement('div');
                seg.className = 'bar-segment';
                const segHeight = day.total > 0 ? (count / day.total) * 100 : 0;
                seg.style.height = segHeight + '%';
                const color = getServerColor(server);
                seg.style.background = color.bg;
                seg.style.boxShadow = `0 0 8px ${color.shadow}`;
                if (isTop) {
                    seg.style.borderRadius = '4px 4px 0 0';
                    isTop = false;
                }
                bar.appendChild(seg);
            }

            // Tooltip
            let tooltipLines = [day.date + ':'];
            mcpServers.forEach(s => {
                const c = day[s] || 0;
                if (c > 0) tooltipLines.push(`${s}: ${c}`);
            });
            tooltipLines.push(`Total: ${day.total}`);
            bar.title = tooltipLines.join('\\n');

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5);
            bar.appendChild(label);

            mcpChart.appendChild(bar);
        });

        // Render TTFT / TTLT line chart
        (function() {
            const latencyData = {{ latency_daily | tojson | safe }};
            const svg = document.getElementById('latency-chart');
            if (!svg) return;

            const W = svg.parentElement.clientWidth - 32;
            const H = 200;
            const PAD = { top: 10, right: 20, bottom: 30, left: 44 };
            svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
            svg.setAttribute('width', W);

            const inner_w = W - PAD.left - PAD.right;
            const inner_h = H - PAD.top - PAD.bottom;

            const ttfts = latencyData.map(d => d.avg_ttft);
            const ttlts = latencyData.map(d => d.avg_ttlt);
            const allVals = [...ttfts, ...ttlts].filter(v => v !== null && v !== undefined);
            const maxVal = Math.max(...allVals, 1);
            const n = latencyData.length;

            function xPos(i) { return PAD.left + (i / (n - 1)) * inner_w; }
            function yPos(v) { return PAD.top + inner_h - (v / maxVal) * inner_h; }

            function makePath(vals, color) {
                const pts = vals.map((v, i) => v != null ? `${xPos(i)},${yPos(v)}` : null);
                // Split at nulls to avoid connecting gaps
                let d = '';
                let inPath = false;
                pts.forEach((p, i) => {
                    if (p === null) { inPath = false; return; }
                    if (!inPath) { d += `M${p}`; inPath = true; }
                    else { d += ` L${p}`; }
                });
                if (!d) return;
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', d);
                path.setAttribute('fill', 'none');
                path.setAttribute('stroke', color);
                path.setAttribute('stroke-width', '2');
                path.setAttribute('stroke-linejoin', 'round');
                path.setAttribute('stroke-linecap', 'round');
                svg.appendChild(path);

                // Dots for data points
                pts.forEach((p, i) => {
                    if (!p) return;
                    const [cx, cy] = p.split(',');
                    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                    circle.setAttribute('cx', cx);
                    circle.setAttribute('cy', cy);
                    circle.setAttribute('r', '3');
                    circle.setAttribute('fill', color);
                    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                    title.textContent = `${latencyData[i].date}: ${vals[i]}s`;
                    circle.appendChild(title);
                    svg.appendChild(circle);
                });
            }

            // Y axis gridlines + labels
            const steps = 4;
            for (let s = 0; s <= steps; s++) {
                const v = (maxVal * s) / steps;
                const y = yPos(v);
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', PAD.left);
                line.setAttribute('x2', PAD.left + inner_w);
                line.setAttribute('y1', y);
                line.setAttribute('y2', y);
                line.setAttribute('stroke', '#e0e0e0');
                line.setAttribute('stroke-width', '1');
                svg.appendChild(line);

                const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                label.setAttribute('x', PAD.left - 6);
                label.setAttribute('y', y + 4);
                label.setAttribute('text-anchor', 'end');
                label.setAttribute('font-size', '10');
                label.setAttribute('fill', '#999');
                label.textContent = v.toFixed(1) + 's';
                svg.appendChild(label);
            }

            // X axis date labels (every ~7 days)
            latencyData.forEach((d, i) => {
                if (i % 7 !== 0 && i !== n - 1) return;
                const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                label.setAttribute('x', xPos(i));
                label.setAttribute('y', H - 4);
                label.setAttribute('text-anchor', 'middle');
                label.setAttribute('font-size', '9');
                label.setAttribute('fill', '#999');
                label.textContent = d.date.substring(5);
                svg.appendChild(label);
            });

            makePath(ttfts, '#FF10F0');
            makePath(ttlts, '#39FF14');
        })();
    </script>
</body>
</html>
"""


@app.template_filter('format_number')
def format_number(value):
    """Format number with commas"""
    return f"{value:,}" if value else "0"


@app.route('/')
def dashboard():
    """Main dashboard page"""
    claude = get_claude_stats() or {}
    copilot = get_copilot_stats() or {}
    github = get_github_commits()

    # Combine daily data for charts (last 30 days)
    daily_tokens = []
    daily_turns = []
    for i in range(30):
        date = (datetime.now() - timedelta(days=29-i)).strftime("%Y-%m-%d")

        claude_tokens = 0
        claude_turns = 0
        for activity in claude.get("daily_tokens", []):
            if activity["date"] == date:
                # Sum tokens from all models (tokensByModel structure)
                tokens_by_model = activity.get("tokensByModel", {})
                claude_tokens = sum(tokens_by_model.values())
                break

        for activity in claude.get("daily_activity", []):
            if activity["date"] == date:
                claude_turns = activity.get("messageCount", 0) // 6  # Estimate turns
                break

        copilot_tokens = 0
        copilot_turns = 0
        for activity in copilot.get("daily_activity", []):
            if activity["date"] == date:
                # Output tokens only - Copilot input tokens are broken, never use them
                copilot_tokens = activity.get("output", 0)
                copilot_turns = activity.get("turns", 0)
                break

        daily_tokens.append({
            "date": date,
            "claude": claude_tokens,
            "copilot": copilot_tokens,
            "total": claude_tokens + copilot_tokens
        })

        daily_turns.append({
            "date": date,
            "claude": claude_turns,
            "copilot": copilot_turns,
            "total": claude_turns + copilot_turns
        })

    mcp = get_mcp_stats()
    latency = get_latency_stats()

    return render_template_string(
        HTML_TEMPLATE,
        claude=claude,
        copilot=copilot,
        github=github,
        daily_tokens=daily_tokens,
        daily_turns=daily_turns,
        mcp_daily=mcp["daily"],
        mcp_servers=mcp["servers"],
        latency=latency,
        latency_daily=latency["daily"],
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@app.route('/api/stats')
def api_stats():
    """API endpoint for raw stats"""
    return jsonify({
        "claude": get_claude_stats(),
        "copilot": get_copilot_stats(),
        "github": get_github_commits(),
        "mcp": get_mcp_stats(),
        "latency": get_latency_stats(),
    })


if __name__ == "__main__":
    print("🚀 Starting AI Tools Usage Dashboard...")
    print("📊 Dashboard: http://localhost:5555")
    print("🔌 API:       http://localhost:5555/api/stats")
    print("\nPress Ctrl+C to stop")
    app.run(host='0.0.0.0', port=5555, debug=False)
