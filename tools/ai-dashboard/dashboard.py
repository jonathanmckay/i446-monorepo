#!/usr/bin/env python3
"""
AI Tools Usage Dashboard

A web dashboard showing LLM usage stats, costs, and GitHub activity.
Run: python3 dashboard.py
Then open: http://localhost:5555
"""

import json
import glob
import os
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


def ingest_claude_sessions():
    """Upsert Claude Code JSONL sessions into llm-sessions.db (provider='claude').

    Scans all project dirs under ~/.claude/projects/. For each JSONL file, builds
    a session row (one row per file) with aggregated token/cost/message stats,
    then upserts by session_id so re-runs are idempotent.
    """
    if not LLM_DB.exists():
        return
    pacific = pytz.timezone("America/Los_Angeles")
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return

    conn = sqlite3.connect(str(LLM_DB))
    cur = conn.cursor()

    for fpath in projects_root.rglob("*.jsonl"):
        session_id = fpath.stem
        project_dir = fpath.parent.name
        try:
            messages = []
            for line in fpath.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            if not messages:
                continue

            # Timestamps
            timestamps = [
                datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                for m in messages if m.get("timestamp")
            ]
            if not timestamps:
                continue
            start_ts = min(timestamps).isoformat()
            end_ts = max(timestamps).isoformat()
            start_day = min(timestamps).astimezone(pacific).strftime("%Y-%m-%d")

            # Message counts
            user_msgs = sum(1 for m in messages if m.get("type") == "user")
            asst_msgs = sum(1 for m in messages if m.get("type") == "assistant")
            total_msgs = user_msgs + asst_msgs

            # Tokens + cost (from assistant messages)
            input_tok = output_tok = cache_read_tok = cache_write_tok = 0
            cost = 0.0
            model_seen = None
            for m in messages:
                if m.get("type") != "assistant":
                    continue
                msg = m.get("message", {})
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage", {})
                model_seen = msg.get("model", model_seen)
                it = usage.get("input_tokens", 0)
                ot = usage.get("output_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cw = usage.get("cache_creation_input_tokens", 0)
                input_tok += it
                output_tok += ot
                cache_read_tok += cr
                cache_write_tok += cw
                model_type = "opus" if model_seen and "opus" in model_seen.lower() else "sonnet"
                prices = PRICING[model_type]
                cost += (
                    (it / 1_000_000) * prices["input"] +
                    (ot / 1_000_000) * prices["output"] +
                    (cr / 1_000_000) * prices["cache_read"] +
                    (cw / 1_000_000) * prices["cache_write"]
                )

            cur.execute("""
                INSERT INTO sessions
                    (session_id, provider, product, model, start_time, end_time,
                     message_count, input_tokens, output_tokens, total_tokens,
                     cached_tokens, cache_write_tokens, cost_usd,
                     project_dir, user_id, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    end_time=excluded.end_time,
                    message_count=excluded.message_count,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    total_tokens=excluded.total_tokens,
                    cached_tokens=excluded.cached_tokens,
                    cache_write_tokens=excluded.cache_write_tokens,
                    cost_usd=excluded.cost_usd,
                    model=excluded.model
            """, (
                session_id, "claude", "cli", model_seen, start_ts, end_ts,
                total_msgs, input_tok, output_tok, input_tok + output_tok,
                cache_read_tok, cache_write_tok, cost,
                project_dir, "jm", "completed"
            ))
        except Exception:
            continue

    conn.commit()
    conn.close()


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

    # Supplement with JSONL-parsed data for the last cached date (often stale) and beyond
    last_cached_date = daily_activity[-1]["date"] if daily_activity else "2000-01-01"
    today = datetime.now().strftime("%Y-%m-%d")
    # Parse from the day BEFORE the last cached date so we re-check it
    prev_date = (datetime.strptime(last_cached_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    extra_activity, extra_tokens, extra_costs = _parse_jsonl_daily_stats(prev_date)
    for day in sorted(extra_activity):
        # For the last cached date, merge with cache (take max message count)
        existing = next((a for a in daily_activity if a["date"] == day), None)
        if existing:
            if extra_activity[day]["messageCount"] > existing["messageCount"]:
                existing["messageCount"] = extra_activity[day]["messageCount"]
                existing["sessionCount"] = max(existing["sessionCount"], extra_activity[day]["sessionCount"])
        else:
            daily_activity.append(extra_activity[day])
    for day in sorted(extra_tokens):
        existing = next((t for t in daily_tokens if t["date"] == day), None)
        if existing:
            # Merge token counts — take max per model
            cached_models = existing.get("tokensByModel", {})
            extra_models = extra_tokens[day].get("tokensByModel", {})
            for model, count in extra_models.items():
                cached_models[model] = max(cached_models.get(model, 0), count)
            existing["tokensByModel"] = cached_models
        else:
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


def get_permissions_stats(days=30):
    """Get daily permission grant counts from ~/.claude/timing/permissions.jsonl"""
    log_file = os.path.expanduser("~/.claude/timing/permissions.jsonl")
    daily = {}
    if os.path.exists(log_file):
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    date = entry.get("date", "")
                    if date:
                        daily[date] = daily.get(date, 0) + 1
                except (json.JSONDecodeError, KeyError):
                    continue
    return daily


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
                            # Accept commits where author.login matches, OR where the git
                            # author name contains "mckay" (catches unlinked local commits)
                            author_login = commit.get("author", {}).get("login", "") if commit.get("author") else ""
                            git_author_name = commit.get("commit", {}).get("author", {}).get("name", "").lower()
                            if author_login != GITHUB_USER and "mckay" not in git_author_name:
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

    # Top 8 servers by call count in last 7 days (for legend)
    recent_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    server_7d_counts = Counter()
    for date, counts in daily_by_server.items():
        if date >= recent_cutoff:
            server_7d_counts.update(counts)
    top_servers = [s for s, _ in server_7d_counts.most_common(8)]

    return {"daily": daily, "servers": servers, "top_servers": top_servers, "total": total}


def get_skill_stats(days=30):
    """Get slash-command (skill) invocation stats from Claude Code JSONL session logs.

    Scans user messages for <command-name>/skill</command-name> tags,
    groups by skill name and day.
    """
    import re
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "skills": [], "total": 0}

    # Exclude built-in CLI commands that aren't user-defined skills
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
                    if obj.get("type") != "user":
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue

                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content", [])
                    # Collect all text to search
                    texts = []
                    if isinstance(content, str):
                        texts.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, str):
                                texts.append(block)
                            elif isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                    for text in texts:
                        for match in re.finditer(r"<command-name>(/[^<]+)</command-name>", text):
                            skill = match.group(1)
                            if skill in EXCLUDED:
                                continue
                            day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                            daily_by_skill[day][skill] += 1
                            all_skills.add(skill)
                            total += 1
        except Exception:
            continue

    skills = sorted(all_skills)
    daily = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        entry = {"date": date}
        day_total = 0
        for s in skills:
            count = daily_by_skill.get(date, {}).get(s, 0)
            entry[s] = count
            day_total += count
        entry["total"] = day_total
        daily.append(entry)

    # Top 10 skills by call count in last 7 days (for legend)
    recent_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    skill_7d_counts = Counter()
    for date, counts in daily_by_skill.items():
        if date >= recent_cutoff:
            skill_7d_counts.update(counts)
    top_skills = [s for s, _ in skill_7d_counts.most_common(10)]

    return {"daily": daily, "skills": skills, "top_skills": top_skills, "total": total}


def _wait_buckets(wall_times):
    """Split wall times into percentile buckets and return % of total wait in each."""
    if not wall_times:
        return None
    s = sorted(wall_times)
    n = len(s)
    total = sum(s)
    if total <= 0:
        return None
    p50_idx = n // 2
    p90_idx = min(int(n * 0.90), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    bucket_le_p50 = sum(s[:p50_idx + 1])
    bucket_p50_p90 = sum(s[p50_idx + 1:p90_idx + 1])
    bucket_p90_p99 = sum(s[p90_idx + 1:p99_idx + 1])
    bucket_p99_max = sum(s[p99_idx + 1:])
    return {
        "le_p50": round(bucket_le_p50 / total * 100, 1),
        "p50_p90": round(bucket_p50_p90 / total * 100, 1),
        "p90_p99": round(bucket_p90_p99 / total * 100, 1),
        "p99_max": round(bucket_p99_max / total * 100, 1),
        "total_s": round(total, 1),
    }


def _load_hook_timing(days=30):
    """Load true wall-clock turn timings from the hook-based timing log.
    Also reads the pending LAST_FILE for the in-progress turn.
    Returns {date_str: [elapsed_seconds, ...]}.
    """
    timing_file = Path.home() / ".claude" / "timing" / "turns.jsonl"
    last_file = Path("/tmp/claude-turn-last")
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = defaultdict(list)

    # Read finalized turns
    if timing_file.exists():
        try:
            with open(timing_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    date = obj.get("date", "")
                    elapsed = obj.get("elapsed_s", 0)
                    if date > cutoff_date and 0 < elapsed <= 7200:
                        result[date].append(elapsed)
        except Exception:
            pass

    # Include pending (in-progress) turn if available
    if last_file.exists():
        try:
            obj = json.loads(last_file.read_text().strip())
            date = obj.get("date", "")
            elapsed = obj.get("elapsed_s", 0)
            if date > cutoff_date and 0 < elapsed <= 7200:
                result[date].append(elapsed)
        except Exception:
            pass

    return dict(result)


def get_latency_stats(days=30):
    """Compute TTFT, TTLT, and wall-clock latency from Claude Code session data.

    TTFT = Time to First Turn: seconds from user's first message to assistant's first response.
    TTLT = Time to Last Turn: seconds from user's last message to assistant's final response.
    Wall = true wall-clock from hook timing log (prompt_start → stop), falls back to
           inter-message wall-clock from JSONL timestamps.
    Returns daily averages and overall percentiles for the last `days` days.
    """
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "overall": {}}

    # date -> {"ttft": [seconds, ...], "ttlt": [seconds, ...], "wall": [seconds, ...]}
    daily_latencies = defaultdict(lambda: {"ttft": [], "ttlt": [], "wall": []})

    for fpath in session_dir.glob("*.jsonl"):
        try:
            # Collect assistant/user messages for TTFT/TTLT
            messages = []
            # Collect ALL timestamped messages for wall-clock
            all_messages = []
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg_type = obj.get("type")
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    all_messages.append((msg_type, ts))
                    if msg_type in ("user", "assistant"):
                        messages.append((msg_type, ts))

            if len(messages) < 2:
                continue

            # Build wall-clock map: for each user message index in all_messages,
            # find the last message of any type before the next user message.
            user_indices = [i for i, (t, _) in enumerate(all_messages) if t == "user"]

            for idx, ui in enumerate(user_indices):
                user_ts = all_messages[ui][1]
                # Find boundary: next user message or end of list
                end = user_indices[idx + 1] if idx + 1 < len(user_indices) else len(all_messages)
                if end <= ui + 1:
                    continue
                last_any_ts = all_messages[end - 1][1]
                wall = (last_any_ts - user_ts).total_seconds()
                if wall <= 0 or wall > 1800 or user_ts < cutoff:
                    continue
                day = user_ts.astimezone(pacific).strftime("%Y-%m-%d")
                daily_latencies[day]["wall"].append(wall)

            # TTFT / TTLT: user → first/last consecutive assistant messages
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

                if ttft <= 0 or ttft > 1800 or ttlt > 1800:
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

    # Load hook-based timing (preferred source for wall-clock)
    hook_timing = _load_hook_timing(days)

    daily = []
    all_ttft = []
    all_ttlt = []
    all_wall = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        d = daily_latencies.get(date, {"ttft": [], "ttlt": [], "wall": []})
        # Prefer hook timing for wall-clock when available, fall back to JSONL
        wall_data = hook_timing.get(date, d["wall"])
        entry = {
            "date": date,
            "avg_ttft": round(sum(d["ttft"]) / len(d["ttft"]), 1) if d["ttft"] else None,
            "avg_ttlt": round(sum(d["ttlt"]) / len(d["ttlt"]), 1) if d["ttlt"] else None,
            "avg_wall": round(sum(wall_data) / len(wall_data), 1) if wall_data else None,
            "max_wall": round(max(wall_data), 1) if wall_data else None,
            "n_queries": len(wall_data),
            "p50_wall": round(sorted(wall_data)[len(wall_data) // 2], 1) if wall_data else None,
            "p95_wall": round(sorted(wall_data)[min(int(len(wall_data) * 0.95), len(wall_data) - 1)], 1) if wall_data else None,
            "p99_wall": round(sorted(wall_data)[min(int(len(wall_data) * 0.99), len(wall_data) - 1)], 1) if wall_data else None,
            "wait_buckets": _wait_buckets(wall_data),
            "hook_source": bool(hook_timing.get(date)),
        }
        all_ttft.extend(d["ttft"])
        all_ttlt.extend(d["ttlt"])
        all_wall.extend(wall_data)
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
    if all_wall:
        s = sorted(all_wall)
        overall["avg_wall"] = round(sum(s) / len(s), 1)
        overall["median_wall"] = round(s[len(s) // 2], 1)
        overall["p95_wall"] = round(s[min(int(len(s) * 0.95), len(s) - 1)], 1)
        overall["p99_wall"] = round(s[min(int(len(s) * 0.99), len(s) - 1)], 1)

    return {"daily": daily, "overall": overall}


def get_greatest_hits(days=7, top_n=20):
    """Find the top N longest wall-time turns from the last `days` days.
    Returns list of {wall_s, prompt_preview, name, date, time} sorted by wall_s desc.
    Calls Haiku to generate a short name for each query.
    """
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return []

    # Collect (wall_seconds, prompt_text, timestamp, tokens) for all turns
    all_turns = []

    for fpath in session_dir.glob("*.jsonl"):
        try:
            all_messages = []  # (type, ts, content_or_none, usage_or_none)
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg_type = obj.get("type")
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    # Extract user prompt text
                    content = None
                    usage = None
                    if msg_type == "user":
                        msg = obj.get("message", {})
                        raw = msg.get("content", "")
                        if isinstance(raw, list):
                            # Content blocks — extract text parts
                            content = " ".join(
                                b.get("text", "") for b in raw
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        elif isinstance(raw, str):
                            content = raw
                    elif msg_type == "assistant":
                        msg = obj.get("message", {})
                        if isinstance(msg, dict):
                            usage = msg.get("usage", {})
                    all_messages.append((msg_type, ts, content, usage))

            if len(all_messages) < 2:
                continue

            user_indices = [i for i, (t, _, _, _) in enumerate(all_messages) if t == "user"]
            for idx, ui in enumerate(user_indices):
                user_ts = all_messages[ui][1]
                user_content = all_messages[ui][2] or ""
                if user_ts < cutoff:
                    continue
                # Wall = user prompt → last logged message before next user prompt
                # This captures all API calls + tool execution but NOT human idle time
                end = user_indices[idx + 1] if idx + 1 < len(user_indices) else len(all_messages)
                if end <= ui + 1:
                    continue
                last_any_ts = all_messages[end - 1][1]
                wall = (last_any_ts - user_ts).total_seconds()
                if wall <= 0 or wall > 3600:
                    continue
                # Strip system reminders and keep first 500 chars
                preview = user_content[:500].strip()
                if not preview or preview.startswith("<system-reminder>"):
                    continue
                # Sum tokens for this turn (all assistant messages between this user msg and the next)
                turn_tokens = 0
                for mi in range(ui + 1, end):
                    u = all_messages[mi][3]
                    if u:
                        turn_tokens += u.get("input_tokens", 0) + u.get("output_tokens", 0)
                all_turns.append((wall, preview, user_ts, turn_tokens))
        except Exception:
            continue

    # Sort by wall time descending, take top N
    all_turns.sort(key=lambda x: x[0], reverse=True)
    top = all_turns[:top_n]

    if not top:
        return []

    # Call Haiku to name each query
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    hits = []
    if api_key:
        # Batch all prompts into a single Haiku call
        numbered = "\n".join(
            f"{i+1}. ({t[0]:.0f}s) {t[1][:200]}" for i, t in enumerate(top)  # t = (wall, preview, ts, tokens)
        )
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content":
                        f"Give a short 2-5 word name for each of these AI queries. "
                        f"Return ONLY numbered lines like '1. Dashboard Bug Fix'. "
                        f"No explanations.\n\n{numbered}"
                    }],
                },
                timeout=15,
            )
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"]
                names = {}
                for line in text.strip().split("\n"):
                    line = line.strip()
                    if line and line[0].isdigit():
                        parts = line.split(".", 1)
                        if len(parts) == 2:
                            names[int(parts[0].strip())] = parts[1].strip()
        except Exception:
            names = {}
    else:
        names = {}

    for i, (wall, preview, ts, tokens) in enumerate(top):
        local_ts = ts.astimezone(pacific)
        # Format token count
        if tokens >= 1_000_000:
            tokens_fmt = f"{tokens / 1_000_000:.1f}M"
        elif tokens >= 1_000:
            tokens_fmt = f"{tokens / 1_000:.0f}k"
        else:
            tokens_fmt = str(tokens)
        hits.append({
            "rank": i + 1,
            "wall_s": round(wall),
            "wall_fmt": f"{int(wall // 60)}m {int(wall % 60)}s",
            "name": names.get(i + 1, preview[:40]),
            "prompt_preview": preview[:120],
            "date": local_ts.strftime("%a %m/%d"),
            "time": local_ts.strftime("%H:%M"),
            "tokens": tokens,
            "tokens_fmt": tokens_fmt,
        })

    return hits


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Tools Usage Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='35' fill='none' stroke='%23FF10F0' stroke-width='16' stroke-dasharray='55 165' transform='rotate(-90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2339FF14' stroke-width='16' stroke-dasharray='55 165' transform='rotate(0 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2300D4FF' stroke-width='16' stroke-dasharray='55 165' transform='rotate(90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23FFD700' stroke-width='16' stroke-dasharray='55 165' transform='rotate(180 50 50)'/></svg>">
    <style>
        :root {
            --bg: #ffffff; --card-bg: #ffffff; --card-border: #e0e0e0;
            --text: #1a1a1a; --muted: #666; --faint: #f0f0f0;
            --chart-bg: #fafafa; --chart-border: #e0e0e0;
            --bar-label: #333; --heatmap-empty: #ebedf0;
            --shadow: rgba(0,0,0,0.05); --last-updated: #999;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #111; --card-bg: #1a1a1a; --card-border: #2e2e2e;
                --text: #e8e8e8; --muted: #888; --faint: #252525;
                --chart-bg: #1e1e1e; --chart-border: #333;
                --bar-label: #bbb; --heatmap-empty: #2d333b;
                --shadow: rgba(0,0,0,0.3); --last-updated: #555;
            }
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
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
        .subtitle { color: var(--muted); margin-bottom: 30px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card {
            background: var(--card-bg);
            border: 2px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 2px 8px var(--shadow);
        }
        .card h2 {
            font-size: 1.2em;
            margin-bottom: 16px;
            color: var(--text);
            font-weight: 700;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
            padding: 8px 0;
            border-bottom: 1px solid var(--faint);
        }
        .metric:last-child { border-bottom: none; }
        .metric-label { color: var(--muted); }
        .metric-value {
            font-weight: 700;
            color: var(--text);
        }
        .cost { color: #39FF14; text-shadow: 0 0 10px rgba(57, 255, 20, 0.3); }
        .chart {
            margin-top: 20px;
            height: 350px;
            background: var(--chart-bg);
            border-radius: 12px;
            padding: 16px;
            border: 1px solid var(--chart-border);
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
            color: var(--muted);
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
        .bar-segment.permissions {
            background: linear-gradient(180deg, #FFD700 0%, #FFA500 100%);
            box-shadow: 0 0 8px rgba(255, 215, 0, 0.3);
        }
        .bar-label {
            position: absolute;
            bottom: -24px;
            left: 50%;
            transform: translateX(-50%) rotate(-45deg);
            transform-origin: center;
            font-size: 0.65em;
            color: var(--bar-label);
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
            color: var(--last-updated);
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
            background: var(--heatmap-empty);
        }
        .heatmap-day.level-0 { background: var(--heatmap-empty); }
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
                    <span class="metric-label" style="font-size: 0.85em; color: var(--muted);">
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
                <div style="margin-top: 12px; font-size: 0.85em; color: var(--muted);">
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
            <h2>⚡ Skill Calls / Day (Last 30 Days)</h2>
            <div class="legend" id="skill-legend">
                <!-- Will be populated by JavaScript -->
            </div>
            <div class="chart">
                <div class="chart-container">
                    <div class="y-axis" id="skill-y-axis"></div>
                    <div class="bar-chart" id="skill-chart">
                        <!-- Will be populated by JavaScript -->
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>⚡ Response Latency — TTFT, TTLT &amp; Wall-Clock (Last 30 Days)</h2>
            <div class="grid" style="margin-bottom: 16px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.avg_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.median_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0; text-shadow: 0 0 10px rgba(255,16,240,0.3);">
                        {{ "%.1f" | format(latency.overall.p95_ttft | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.avg_ttlt | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.median_ttlt | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 TTLT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#39FF14; text-shadow: 0 0 10px rgba(57,255,20,0.3);">
                        {{ "%.1f" | format(latency.overall.p95_ttlt | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF; text-shadow: 0 0 10px rgba(0,212,255,0.3);">
                        {{ "%.1f" | format(latency.overall.avg_wall | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF; text-shadow: 0 0 10px rgba(0,212,255,0.3);">
                        {{ "%.1f" | format(latency.overall.median_wall | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF; text-shadow: 0 0 10px rgba(0,212,255,0.3);">
                        {{ "%.1f" | format(latency.overall.p95_wall | default(0)) }}s
                    </div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p99 Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF; text-shadow: 0 0 10px rgba(0,212,255,0.3);">
                        {{ "%.1f" | format(latency.overall.p99_wall | default(0)) }}s
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
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:24px; height:3px; background:#00D4FF; border-radius:2px;"></span>
                    Wall-clock (full turn incl. approvals)
                </span>
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:24px; height:3px; background:#FFD700; border-radius:2px;"></span>
                    Daily max wall-clock
                </span>
            </div>
            <div style="background:var(--chart-bg); border:1px solid var(--chart-border); border-radius:12px; padding:16px; overflow:hidden;">
                <svg id="latency-chart" width="100%" height="200" style="overflow:visible;"></svg>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:var(--muted);">
                Latency measured per Claude Code session (JSONL). Y-axis is log scale. TTFT = first user→assistant delta; TTLT = last user→assistant delta.
            </div>
        </div>

        <div class="card">
            <h2>⏳ Wait Time Distribution (% of Total)</h2>
            <div style="display:flex; gap:20px; margin-bottom:12px; font-size:0.85em;">
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:12px; height:12px; background:#00D4FF; border-radius:2px;"></span>
                    ≤p50
                </span>
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:12px; height:12px; background:#FF10F0; border-radius:2px;"></span>
                    p50→p90
                </span>
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:12px; height:12px; background:#FFD700; border-radius:2px;"></span>
                    p90→p99
                </span>
                <span style="display:flex; align-items:center; gap:6px;">
                    <span style="display:inline-block; width:12px; height:12px; background:#FF5722; border-radius:2px;"></span>
                    p99→max
                </span>
            </div>
            <div style="background:var(--chart-bg); border:1px solid var(--chart-border); border-radius:12px; padding:16px; overflow:hidden;">
                <svg id="wait-pct-chart" width="100%" height="200" style="overflow:visible;"></svg>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:var(--muted);">
                What % of total daily wait time comes from each latency tier? Hover bars for totals.
            </div>
        </div>

        {% if greatest_hits %}
        <div class="card">
            <h2>🏆 Greatest Hits — Top {{ greatest_hits | length }} Longest Turns (Last 7 Days)</h2>
            <div style="overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.85em;">
                    <thead>
                        <tr style="border-bottom:2px solid var(--chart-border); text-align:left;">
                            <th style="padding:8px 12px; color:var(--muted); font-weight:600;">#</th>
                            <th style="padding:8px 12px; color:var(--muted); font-weight:600;">Wall</th>
                            <th style="padding:8px 12px; color:var(--muted); font-weight:600;">Tokens</th>
                            <th style="padding:8px 12px; color:var(--muted); font-weight:600;">Name</th>
                            <th style="padding:8px 12px; color:var(--muted); font-weight:600;">When</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for hit in greatest_hits %}
                        <tr style="border-bottom:1px solid var(--chart-border);" title="{{ hit.prompt_preview }}">
                            <td style="padding:8px 12px; color:var(--muted);">{{ hit.rank }}</td>
                            <td style="padding:8px 12px; font-weight:700; color:{% if hit.wall_s >= 600 %}#FF5722{% elif hit.wall_s >= 300 %}#FFD700{% else %}#00D4FF{% endif %}; white-space:nowrap;">{{ hit.wall_fmt }}</td>
                            <td style="padding:8px 12px; color:var(--muted); white-space:nowrap;">{{ hit.tokens_fmt }}</td>
                            <td style="padding:8px 12px;">{{ hit.name }}</td>
                            <td style="padding:8px 12px; color:var(--muted); white-space:nowrap;">{{ hit.date }} {{ hit.time }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:var(--muted);">
                Hover rows for prompt preview. Names generated by Haiku. Wall time = full turn including tool approvals.
            </div>
        </div>
        {% endif %}

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

            // Copilot segment (middle)
            if (day.copilot > 0) {
                const copilotSegment = document.createElement('div');
                copilotSegment.className = 'bar-segment copilot';
                const copilotHeight = (day.copilot / day.total) * 100;
                copilotSegment.style.height = copilotHeight + '%';
                bar.appendChild(copilotSegment);
            }

            // Permissions segment (top)
            if (day.permissions > 0) {
                const permissionsSegment = document.createElement('div');
                permissionsSegment.className = 'bar-segment permissions';
                const permissionsHeight = (day.permissions / day.total) * 100;
                permissionsSegment.style.height = permissionsHeight + '%';
                permissionsSegment.style.borderRadius = '4px 4px 0 0';
                bar.appendChild(permissionsSegment);
            }

            bar.addEventListener('mouseenter', e => {
                const html = `<div class="tt-date">${day.date}</div>`
                    + `<div class="tt-claude">Claude: ${day.claude.toLocaleString()} turns</div>`
                    + `<div class="tt-copilot">Copilot: ${day.copilot.toLocaleString()} turns</div>`
                    + `<div class="tt-permissions">Permissions: ${(day.permissions||0).toLocaleString()}</div>`
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

        // Build legend (top 8 servers by 7-day call count)
        const mcpTopServers = {{ mcp_top_servers | tojson | safe }};
        mcpTopServers.forEach(server => {
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

        // Render Skill calls chart (stacked by skill)
        const skillData = {{ skill_daily | tojson | safe }};
        const skillNames = {{ skill_names | tojson | safe }};
        const skillChart = document.getElementById('skill-chart');
        const skillLegend = document.getElementById('skill-legend');

        // Neon color palette mapped to meta categories:
        // g245 (goals/tracking) = neon green, i9 (work) = cyan, hcb (health) = magenta,
        // hcmc (media) = gold, m5x2 (real estate) = orange, xk87/s897 (social) = pink,
        // i447 (infra) = purple, n156 (time) = blue
        const skillColors = {
            '/did':    { bg: 'linear-gradient(180deg, #39FF14 0%, #7AFF5C 100%)', shadow: 'rgba(57, 255, 20, 0.3)', label: '#39FF14' },     // g245 - neon green
            '/tg':     { bg: 'linear-gradient(180deg, #00D4FF 0%, #66E5FF 100%)', shadow: 'rgba(0, 212, 255, 0.3)', label: '#00D4FF' },     // i156/toggl - cyan
            '/-1g':    { bg: 'linear-gradient(180deg, #39FF14 0%, #5BFF3E 100%)', shadow: 'rgba(57, 255, 20, 0.2)', label: '#5BFF3E' },     // g245 - green variant
            '/0t':     { bg: 'linear-gradient(180deg, #4D9EFF 0%, #79B8FF 100%)', shadow: 'rgba(77, 158, 255, 0.3)', label: '#4D9EFF' },    // n156 - blue
            '/1n':     { bg: 'linear-gradient(180deg, #4D9EFF 0%, #66AFFF 100%)', shadow: 'rgba(77, 158, 255, 0.2)', label: '#66AFFF' },    // n156 - blue variant
            '/1nd':    { bg: 'linear-gradient(180deg, #39FF14 0%, #8FFF7A 100%)', shadow: 'rgba(57, 255, 20, 0.15)', label: '#8FFF7A' },    // g245 - green light
            '/0g':     { bg: 'linear-gradient(180deg, #39FF14 0%, #AAFF8F 100%)', shadow: 'rgba(57, 255, 20, 0.1)', label: '#AAFF8F' },    // g245 - green lighter
            '/todo':   { bg: 'linear-gradient(180deg, #FF10F0 0%, #FF6BD6 100%)', shadow: 'rgba(255, 16, 240, 0.3)', label: '#FF10F0' },    // i447 - magenta
            '/notes':  { bg: 'linear-gradient(180deg, #9C27FF 0%, #BB6BFF 100%)', shadow: 'rgba(156, 39, 255, 0.3)', label: '#9C27FF' },    // i447 - purple
            '/ibx':    { bg: 'linear-gradient(180deg, #FFD700 0%, #FFE44D 100%)', shadow: 'rgba(255, 215, 0, 0.3)', label: '#FFD700' },     // comms - gold
            '/commit': { bg: 'linear-gradient(180deg, #607D8B 0%, #90A4AE 100%)', shadow: 'rgba(96, 125, 139, 0.3)', label: '#607D8B' },    // infra - grey
            '/defer':  { bg: 'linear-gradient(180deg, #FF8C00 0%, #FFB347 100%)', shadow: 'rgba(255, 140, 0, 0.3)', label: '#FF8C00' },     // m5x2 - orange
            '/tasks':  { bg: 'linear-gradient(180deg, #E57CD8 0%, #F0A0E8 100%)', shadow: 'rgba(229, 124, 216, 0.3)', label: '#E57CD8' },   // tracking - pink
            '/1hcb':   { bg: 'linear-gradient(180deg, #FF10F0 0%, #FF50F5 100%)', shadow: 'rgba(255, 16, 240, 0.2)', label: '#FF50F5' },    // hcb - magenta variant
            '/1s897':  { bg: 'linear-gradient(180deg, #FF6B9D 0%, #FF9DBF 100%)', shadow: 'rgba(255, 107, 157, 0.3)', label: '#FF6B9D' },   // s897 - pink
        };
        const skillFallbackColors = [
            { bg: 'linear-gradient(180deg, #9C27B0 0%, #BA68C8 100%)', shadow: 'rgba(156, 39, 176, 0.3)', label: '#9C27B0' },
            { bg: 'linear-gradient(180deg, #FF5722 0%, #FF8A65 100%)', shadow: 'rgba(255, 87, 34, 0.3)', label: '#FF5722' },
            { bg: 'linear-gradient(180deg, #795548 0%, #A1887F 100%)', shadow: 'rgba(121, 85, 72, 0.3)', label: '#795548' },
            { bg: 'linear-gradient(180deg, #CDDC39 0%, #DCE775 100%)', shadow: 'rgba(205, 220, 57, 0.3)', label: '#CDDC39' },
            { bg: 'linear-gradient(180deg, #3F51B5 0%, #7986CB 100%)', shadow: 'rgba(63, 81, 181, 0.3)', label: '#3F51B5' },
        ];
        let skillFbIdx = 0;
        function getSkillColor(skill) {
            if (skillColors[skill]) return skillColors[skill];
            const c = skillFallbackColors[skillFbIdx % skillFallbackColors.length];
            skillFbIdx++;
            skillColors[skill] = c;
            return c;
        }

        // Build legend (top 10 skills by 7-day call count)
        const skillTopNames = {{ skill_top_names | tojson | safe }};
        skillTopNames.forEach(skill => {
            const color = getSkillColor(skill);
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `<div class="legend-color" style="background: ${color.label};"></div><span>${skill}</span>`;
            skillLegend.appendChild(item);
        });

        const maxSkill = Math.min(Math.max(...skillData.map(d => d.total), 1), 100);
        const skillAxis = niceAxis(maxSkill);
        createYAxis('skill-y-axis', skillAxis);

        skillData.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            const totalHeight = skillAxis.max > 0 ? Math.min((day.total / skillAxis.max) * 100, 100) : 0;
            bar.style.height = totalHeight + '%';

            let isTop = true;
            for (let i = skillNames.length - 1; i >= 0; i--) {
                const skill = skillNames[i];
                const count = day[skill] || 0;
                if (count <= 0) continue;
                const seg = document.createElement('div');
                seg.className = 'bar-segment';
                const segHeight = day.total > 0 ? (count / day.total) * 100 : 0;
                seg.style.height = segHeight + '%';
                const color = getSkillColor(skill);
                seg.style.background = color.bg;
                seg.style.boxShadow = `0 0 8px ${color.shadow}`;
                if (isTop) {
                    seg.style.borderRadius = '4px 4px 0 0';
                    isTop = false;
                }
                bar.appendChild(seg);
            }

            let tooltipLines = [day.date + ':'];
            skillNames.forEach(s => {
                const c = day[s] || 0;
                if (c > 0) tooltipLines.push(`${s}: ${c}`);
            });
            tooltipLines.push(`Total: ${day.total}`);
            bar.title = tooltipLines.join('\\n');

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5);
            bar.appendChild(label);

            skillChart.appendChild(bar);
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
            const walls = latencyData.map(d => d.avg_wall);
            const maxWalls = latencyData.map(d => d.max_wall);
            const allVals = [...ttfts, ...ttlts, ...walls, ...maxWalls].filter(v => v !== null && v !== undefined);
            const maxVal = Math.max(...allVals, 1);
            const minVal = Math.max(Math.min(...allVals.filter(v => v > 0)), 0.1);
            const logMin = Math.log10(minVal);
            const logMax = Math.log10(maxVal);
            const n = latencyData.length;

            function xPos(i) { return PAD.left + (i / (n - 1)) * inner_w; }
            function yPos(v) {
                if (v <= 0) return PAD.top + inner_h;
                const logV = Math.log10(v);
                return PAD.top + inner_h - ((logV - logMin) / (logMax - logMin)) * inner_h;
            }

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

            // Y axis gridlines + labels (log scale)
            const logTicks = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].filter(v => v >= minVal * 0.9 && v <= maxVal * 1.1);
            logTicks.forEach(v => {
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
                label.textContent = v >= 60 ? (v / 60).toFixed(0) + 'm' : v + 's';
                svg.appendChild(label);
            });

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
            makePath(walls, '#00D4FF');
            makePath(maxWalls, '#FFD700');
        })();

        // % of total wait time by percentile bucket (stacked bar)
        (function() {
            const data = {{ latency_daily | tojson | safe }};
            const svg = document.getElementById('wait-pct-chart');
            if (!svg) return;

            const W = svg.getBoundingClientRect().width || 800;
            const H = 200;
            svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
            const PAD = { top: 10, right: 10, bottom: 25, left: 45 };
            const inner_w = W - PAD.left - PAD.right;
            const inner_h = H - PAD.top - PAD.bottom;
            const n = data.length;

            const buckets = [
                { key: 'le_p50',   color: '#00D4FF', label: '≤p50' },
                { key: 'p50_p90',  color: '#FF10F0', label: 'p50→p90' },
                { key: 'p90_p99',  color: '#FFD700', label: 'p90→p99' },
                { key: 'p99_max',  color: '#FF5722', label: 'p99→max' },
            ];

            const barWidth = (inner_w / n) * 0.7;
            const barGap = (inner_w / n) * 0.3;

            // Y axis (always 0-100%)
            const ySteps = 4;
            for (let s = 0; s <= ySteps; s++) {
                const v = (100 * s) / ySteps;
                const y = PAD.top + inner_h - (v / 100) * inner_h;
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
                label.textContent = v + '%';
                svg.appendChild(label);
            }

            // Stacked bars
            data.forEach((d, i) => {
                const wb = d.wait_buckets;
                if (!wb) return;
                const x = PAD.left + i * (inner_w / n) + barGap / 2;
                let yOffset = 0;

                buckets.forEach(b => {
                    const pct = wb[b.key] || 0;
                    if (pct <= 0) return;
                    const h = (pct / 100) * inner_h;
                    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('x', x);
                    rect.setAttribute('y', PAD.top + inner_h - yOffset - h);
                    rect.setAttribute('width', barWidth);
                    rect.setAttribute('height', h);
                    rect.setAttribute('fill', b.color);
                    rect.setAttribute('opacity', '0.85');
                    rect.setAttribute('rx', '1');
                    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                    title.textContent = `${d.date} ${b.label}: ${pct}% (total: ${wb.total_s}s)`;
                    rect.appendChild(title);
                    svg.appendChild(rect);
                    yOffset += h;
                });

                // X label
                if (i % 7 === 0 || i === n - 1) {
                    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    label.setAttribute('x', x + barWidth / 2);
                    label.setAttribute('y', H - 4);
                    label.setAttribute('text-anchor', 'middle');
                    label.setAttribute('font-size', '9');
                    label.setAttribute('fill', '#999');
                    label.textContent = d.date.substring(5);
                    svg.appendChild(label);
                }
            });
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
    ingest_claude_sessions()
    claude = get_claude_stats() or {}
    copilot = get_copilot_stats() or {}
    permissions_daily = get_permissions_stats()
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

        permission_count = permissions_daily.get(date, 0)

        daily_turns.append({
            "date": date,
            "claude": claude_turns,
            "copilot": copilot_turns,
            "permissions": permission_count,
            "total": claude_turns + copilot_turns + permission_count
        })

    mcp = get_mcp_stats()
    latency = get_latency_stats()
    skills = get_skill_stats()
    greatest_hits = get_greatest_hits()

    return render_template_string(
        HTML_TEMPLATE,
        claude=claude,
        copilot=copilot,
        github=github,
        daily_tokens=daily_tokens,
        daily_turns=daily_turns,
        mcp_daily=mcp["daily"],
        mcp_servers=mcp["servers"],
        mcp_top_servers=mcp["top_servers"],
        skill_daily=skills["daily"],
        skill_names=skills["skills"],
        skill_top_names=skills["top_skills"],
        latency=latency,
        latency_daily=latency["daily"],
        greatest_hits=greatest_hits,
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
        "skills": get_skill_stats(),
        "latency": get_latency_stats(),
    })


@app.route('/api/turns')
def api_turns():
    """Pre-computed daily turns for consumption by other dashboards."""
    claude = get_claude_stats() or {}
    copilot = get_copilot_stats() or {}
    permissions_daily = get_permissions_stats()
    result = []
    for i in range(30):
        d = (datetime.now() - timedelta(days=29-i)).strftime("%Y-%m-%d")
        claude_turns = 0
        for activity in claude.get("daily_activity", []):
            if activity["date"] == d:
                claude_turns = activity.get("messageCount", 0) // 6
                break
        copilot_turns = 0
        for activity in copilot.get("daily_activity", []):
            if activity["date"] == d:
                copilot_turns = activity.get("turns", 0)
                break
        permission_count = permissions_daily.get(d, 0)
        result.append({"date": d, "claude": claude_turns, "copilot": copilot_turns,
                        "permissions": permission_count,
                        "total": claude_turns + copilot_turns + permission_count})
    return jsonify(result)


if __name__ == "__main__":
    print("🚀 Starting AI Tools Usage Dashboard...")
    print("📊 Dashboard: http://localhost:5555")
    print("🔌 API:       http://localhost:5555/api/stats")
    print("\nPress Ctrl+C to stop")
    app.run(host='0.0.0.0', port=5555, debug=False)
