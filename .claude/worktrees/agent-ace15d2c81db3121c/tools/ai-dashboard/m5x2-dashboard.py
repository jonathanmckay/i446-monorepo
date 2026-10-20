#!/usr/bin/env python3
"""
m5x2 AI Dashboard

Personal AI usage dashboard for McKay Capital showing:
- Claude Code usage (sessions, messages, tokens, cost)
- Model breakdown (Opus vs Sonnet)
- Daily activity trends
- GitHub commit activity

Data source: ~/.claude/stats-cache.json (Claude Code native stats)

Run: python3 m5x2-dashboard.py
Then open: http://localhost:5556
"""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter, defaultdict
from flask import Flask, render_template_string, jsonify, request
import pytz
import requests

app = Flask(__name__)

EXPECTED_HOST = "ix"
_host_marker = Path.home() / ".claude" / ".host-name"
_current_host = _host_marker.read_text().strip() if _host_marker.exists() else "unknown"


@app.before_request
def _enforce_canonical_host():
    if _current_host == EXPECTED_HOST:
        return None
    return (
        f"""<!doctype html><html><head><title>Wrong Host</title>
<style>
  html,body{{margin:0;height:100%;background:#b00020;color:#fff;
    font:600 28px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;
    display:flex;align-items:center;justify-content:center;text-align:center}}
  .box{{padding:2em;max-width:640px}}
  code{{background:rgba(0,0,0,.25);padding:.1em .4em;border-radius:4px;font-size:.9em}}
</style></head><body><div class="box">
<div style="font-size:64px">⚠</div>
<div>This dashboard only serves canonical data from <code>{EXPECTED_HOST}</code>.</div>
<div style="margin-top:.6em;font-weight:400;font-size:.7em;opacity:.85">
You're on <code>{_current_host}</code> — bookmark <code>http://ix.local:5556</code> instead.
</div></div></body></html>""",
        503,
        {"Content-Type": "text/html; charset=utf-8"},
    )


# Paths
CONFIG_FILE = Path(__file__).parent / "m5x2-config.json"
STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"

# Load config
with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

GITHUB_USER = "jonathanmckay"
PORT = CONFIG.get("port", 5556)
STATS_REPO = Path(CONFIG.get("stats_repo", {}).get("local_path", "~/m5x2-ai-stats")).expanduser()

# Get GitHub token
try:
    GITHUB_TOKEN = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
except Exception:
    GITHUB_TOKEN = None

# Pricing per million tokens
PRICING = {
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_write": 15.00, "cache_read": 1.50},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00, "cache_write": 3.00, "cache_read": 0.30},
}

MODEL_LABELS = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
}


def pull_stats_repo():
    """Pull latest stats from the shared git repo (best-effort)."""
    if not (STATS_REPO / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=STATS_REPO, capture_output=True, timeout=10
        )
    except Exception:
        pass


def get_freshness():
    """Per-user staleness based on last git commit touching their stats files.

    Returns list of dicts: {user_id, name, last_update_ts, hours_ago, level}
    where level is 'ok' (<2h), 'warn' (<8h), 'stale' (>=8h), or 'missing'.
    """
    out = []
    if not (STATS_REPO / ".git").exists():
        return out
    now = datetime.now().timestamp()
    for uid, user in CONFIG.get("users", {}).items():
        try:
            r = subprocess.run(
                ["git", "log", "-1", "--format=%at", "--",
                 f"{uid}/stats-cache.json", f"{uid}/session-stats.json"],
                cwd=STATS_REPO, capture_output=True, text=True, timeout=5,
            )
            ts_str = r.stdout.strip()
            if not ts_str:
                out.append({"user_id": uid, "name": user.get("name", uid),
                            "hours_ago": None, "level": "missing"})
                continue
            ts = int(ts_str)
            hours = (now - ts) / 3600
            if hours < 2:
                level = "ok"
            elif hours < 8:
                level = "warn"
            else:
                level = "stale"
            out.append({"user_id": uid, "name": user.get("name", uid),
                        "hours_ago": hours, "level": level})
        except Exception:
            out.append({"user_id": uid, "name": user.get("name", uid),
                        "hours_ago": None, "level": "missing"})
    return out


def load_stats(user_id=None):
    """Load stats for the given user.

    JM reads the merged file in m5x2-ai-stats (Claude + Copilot CLI). Falls
    back to the raw Claude cache if the merged file is missing. Other users
    read from the shared stats repo as before.
    """
    if not user_id or user_id == "jm":
        merged = STATS_REPO / "jm" / "stats-cache.json"
        candidate = merged if merged.exists() else STATS_CACHE
        if not candidate.exists():
            return None
        with open(candidate) as f:
            return json.load(f)
    else:
        repo_file = STATS_REPO / user_id / "stats-cache.json"
        if not repo_file.exists():
            return None
        with open(repo_file) as f:
            return json.load(f)


def compute_model_costs(model_usage):
    """Compute cost breakdown by model from aggregate token counts"""
    costs = {}
    for model_id, usage in model_usage.items():
        rates = PRICING.get(model_id, PRICING.get("claude-sonnet-4-5-20250929"))
        label = MODEL_LABELS.get(model_id, model_id)

        input_cost = (usage.get("inputTokens", 0) / 1_000_000) * rates["input"]
        output_cost = (usage.get("outputTokens", 0) / 1_000_000) * rates["output"]
        cache_write_cost = (usage.get("cacheCreationInputTokens", 0) / 1_000_000) * rates["cache_write"]
        cache_read_cost = (usage.get("cacheReadInputTokens", 0) / 1_000_000) * rates["cache_read"]
        total = input_cost + output_cost + cache_write_cost + cache_read_cost

        costs[label] = {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
            "cache_write_tokens": usage.get("cacheCreationInputTokens", 0),
            "input_cost": input_cost,
            "output_cost": output_cost,
            "cache_write_cost": cache_write_cost,
            "cache_read_cost": cache_read_cost,
            "total_cost": total,
        }
    return costs


def get_daily_activity(stats, days=30):
    """Get daily activity for the last N days"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    daily = stats.get("dailyActivity", [])
    return [d for d in daily if d["date"] >= cutoff]


def get_daily_tokens(stats, days=30):
    """Get daily token output by model for the last N days"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    daily = stats.get("dailyModelTokens", [])
    return [d for d in daily if d["date"] >= cutoff]


def get_daily_turns(days=30):
    """Compute daily turns per user from JSONL session logs (live, not from stats-cache).

    A 'turn' = one assistant message (type=assistant with a message).
    Returns {"daily": [{"date": ..., "jm": N, "lx": N, ...}], "users": ["jm", "lx", ...]}
    """
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = Path.home() / ".claude" / "projects"

    # Map directory prefixes to user IDs
    user_dirs = {"jm": base / "-Users-mckay"}
    for uid in CONFIG.get("users", {}):
        if uid == "jm":
            continue
        repo_dir = STATS_REPO / uid
        if repo_dir.exists():
            user_dirs[uid] = repo_dir

    daily_by_user = defaultdict(Counter)  # date -> {user: count}
    all_users = set()
    cutoff_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # JM: compute from local JSONL session files (live data)
    jm_dir = base / "-Users-mckay"
    if jm_dir.exists():
        for fpath in jm_dir.glob("*.jsonl"):
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
                        day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                        daily_by_user[day]["jm"] += 1
                        all_users.add("jm")
            except Exception:
                continue

    # JM: also count Copilot CLI turns from ~/.copilot/session-store.db
    # (one assistant response per turn row). Without this, the headline Turns
    # chart undercounts JM whenever work happens in Copilot CLI.
    copilot_db = Path.home() / ".copilot" / "session-store.db"
    if copilot_db.exists():
        try:
            import sqlite3
            with sqlite3.connect(f"file:{copilot_db}?mode=ro", uri=True) as conn:
                rows = conn.execute(
                    "SELECT timestamp FROM turns "
                    "WHERE assistant_response IS NOT NULL AND assistant_response != ''"
                ).fetchall()
            for (ts_str,) in rows:
                if not ts_str:
                    continue
                try:
                    s = ts_str.replace("Z", "+00:00").replace(" ", "T", 1)
                    ts = datetime.fromisoformat(s)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                daily_by_user[day]["jm"] += 1
                all_users.add("jm")
        except Exception:
            pass

    # Other users: read from stats-cache.json in the shared stats repo
    # Uses messageCount from dailyActivity as proxy for turns
    for uid in CONFIG.get("users", {}):
        if uid == "jm":
            continue
        stats_file = STATS_REPO / uid / "stats-cache.json"
        if not stats_file.exists():
            continue
        try:
            user_stats = json.loads(stats_file.read_text())
            for entry in user_stats.get("dailyActivity", []):
                d = entry.get("date", "")
                count = entry.get("messageCount", 0)
                if d >= cutoff_str and count > 0:
                    daily_by_user[d][uid] += count
                    all_users.add(uid)
        except Exception:
            continue

    # Build sorted daily list
    all_dates = sorted(daily_by_user.keys())
    users = sorted(all_users)
    daily = []
    for d in all_dates:
        entry = {"date": d}
        for u in users:
            entry[u] = daily_by_user[d].get(u, 0)
        entry["total"] = sum(entry.get(u, 0) for u in users)
        daily.append(entry)

    return {"daily": daily, "users": users}


def get_github_activity():
    """Get personal GitHub commit activity (last 90 days)"""
    try:
        from datetime import timezone
        import pytz

        pacific_tz = pytz.timezone('America/Los_Angeles')
        days_ago_90 = datetime.now(timezone.utc) - timedelta(days=90)
        daily_commits = defaultdict(int)
        repos = set()
        total_commits = 0

        headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

        # Get user's repos
        repos_url = f"https://api.github.com/users/{GITHUB_USER}/repos?type=all&sort=pushed&per_page=100"
        resp = requests.get(repos_url, headers=headers, timeout=10)
        all_repos = resp.json() if resp.status_code == 200 else []

        # Also get org repos
        org = CONFIG.get("github", {}).get("org")
        if org:
            org_url = f"https://api.github.com/orgs/{org}/repos?sort=pushed&per_page=100"
            org_resp = requests.get(org_url, headers=headers, timeout=10)
            if org_resp.status_code == 200:
                all_repos.extend(org_resp.json())

        # Deduplicate
        seen = set()
        unique_repos = []
        for repo in all_repos:
            if repo["full_name"] not in seen:
                seen.add(repo["full_name"])
                unique_repos.append(repo)

        for repo in unique_repos:
            pushed_at = repo.get("pushed_at")
            if not pushed_at:
                continue
            push_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            if push_date < days_ago_90:
                continue

            try:
                commits_url = f"https://api.github.com/repos/{repo['full_name']}/commits"
                commits_params = {
                    "author": GITHUB_USER,
                    "since": days_ago_90.isoformat(),
                    "per_page": 100
                }
                commits_response = requests.get(commits_url, params=commits_params, headers=headers, timeout=5)
                if commits_response.status_code == 200:
                    repo_commits = commits_response.json()
                    if repo_commits:
                        repos.add(repo["full_name"])
                    for commit in repo_commits:
                        commit_date_str = commit.get("commit", {}).get("author", {}).get("date", "")
                        if commit_date_str:
                            commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                            commit_date_pacific = commit_date.astimezone(pacific_tz)
                            date_key = commit_date_pacific.strftime("%Y-%m-%d")
                            daily_commits[date_key] += 1
                            total_commits += 1
            except Exception:
                continue

        return {
            "total_commits": total_commits,
            "daily_commits": dict(daily_commits),
            "repos": sorted(repos)[:15]
        }
    except Exception as e:
        return {"total_commits": 0, "daily_commits": {}, "repos": [], "error": str(e)}


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
    p95_idx = min(int(n * 0.95), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    bucket_le_p50 = sum(s[:p50_idx + 1])
    bucket_p50_p95 = sum(s[p50_idx + 1:p95_idx + 1])
    bucket_p95_p99 = sum(s[p95_idx + 1:p99_idx + 1])
    bucket_p99_max = sum(s[p99_idx + 1:])
    return {
        "le_p50": round(bucket_le_p50 / total * 100, 1),
        "p50_p95": round(bucket_p50_p95 / total * 100, 1),
        "p95_p99": round(bucket_p95_p99 / total * 100, 1),
        "p99_max": round(bucket_p99_max / total * 100, 1),
        "total_s": round(total, 1),
    }


def _load_hook_timing(days=30):
    """Load true wall-clock turn timings from the hook-based timing log."""
    timing_file = Path.home() / ".claude" / "timing" / "turns.jsonl"
    if not timing_file.exists():
        return {}
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = defaultdict(list)
    try:
        with open(timing_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                date = obj.get("date", "")
                elapsed = obj.get("elapsed_s", 0)
                if date > cutoff_date and 0 < elapsed <= 1800:
                    result[date].append(elapsed)
    except Exception:
        pass
    return dict(result)


def get_mcp_stats(days=30):
    """Get MCP tool call stats from Claude Code JSONL session logs."""
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "servers": [], "total": 0}

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
                        parts = name.split("__", 2)
                        server = parts[1] if len(parts) >= 3 else name
                        day = ts.astimezone(pacific).strftime("%Y-%m-%d")
                        daily_by_server[day][server] += 1
                        all_servers.add(server)
                        total += 1
        except Exception:
            continue

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


def get_skill_stats(days=30):
    """Get slash-command (skill) invocation stats from Claude Code JSONL session logs."""
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "skills": [], "total": 0}

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

    return {"daily": daily, "skills": skills, "total": total}


def get_latency_stats(days=30):
    """Compute TTFT, TTLT, and wall-clock latency from Claude Code session data."""
    pacific = pytz.timezone("America/Los_Angeles")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    session_dir = Path.home() / ".claude" / "projects" / "-Users-mckay"
    if not session_dir.exists():
        return {"daily": [], "overall": {}}

    daily_latencies = defaultdict(lambda: {"ttft": [], "ttlt": [], "wall": []})

    for fpath in session_dir.glob("*.jsonl"):
        try:
            messages = []
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

            user_indices = [i for i, (t, _) in enumerate(all_messages) if t == "user"]
            for idx, ui in enumerate(user_indices):
                user_ts = all_messages[ui][1]
                end = user_indices[idx + 1] if idx + 1 < len(user_indices) else len(all_messages)
                if end <= ui + 1:
                    continue
                last_any_ts = all_messages[end - 1][1]
                wall = (last_any_ts - user_ts).total_seconds()
                if wall <= 0 or wall > 1800 or user_ts < cutoff:
                    continue
                day = user_ts.astimezone(pacific).strftime("%Y-%m-%d")
                daily_latencies[day]["wall"].append(wall)

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
                        break
                if first_asst_ts is None:
                    i += 1
                    continue
                ttft = (first_asst_ts - user_ts).total_seconds()
                ttlt = (last_asst_ts - user_ts).total_seconds()
                if ttft <= 0 or ttft > 1800 or ttlt > 1800 or user_ts < cutoff:
                    i += 1
                    continue
                day = user_ts.astimezone(pacific).strftime("%Y-%m-%d")
                daily_latencies[day]["ttft"].append(ttft)
                daily_latencies[day]["ttlt"].append(ttlt)
                i += 1

        except Exception:
            continue

    hook_timing = _load_hook_timing(days)

    daily = []
    all_ttft = []
    all_ttlt = []
    all_wall = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        d = daily_latencies.get(date, {"ttft": [], "ttlt": [], "wall": []})
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


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>m5x2 AI Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='35' fill='none' stroke='%23FF6B35' stroke-width='16' stroke-dasharray='55 165' transform='rotate(-90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23004E89' stroke-width='16' stroke-dasharray='55 165' transform='rotate(0 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2300D4FF' stroke-width='16' stroke-dasharray='55 165' transform='rotate(90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23FFD700' stroke-width='16' stroke-dasharray='55 165' transform='rotate(180 50 50)'/></svg>">
    <style>
        :root {
            --bg: #ffffff; --card-bg: #ffffff; --card-border: #e0e0e0;
            --text: #1a1a1a; --muted: #666; --faint: #f0f0f0;
            --chart-bg: #fafafa; --chart-border: #e0e0e0;
            --bar-label: #333; --shadow: rgba(0,0,0,0.05);
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #111; --card-bg: #1a1a1a; --card-border: #2e2e2e;
                --text: #e8e8e8; --muted: #888; --faint: #222;
                --chart-bg: #1e1e1e; --chart-border: #333;
                --bar-label: #bbb; --shadow: rgba(0,0,0,0.3);
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
            margin-bottom: 6px;
            background: linear-gradient(135deg, #FF6B35 0%, #004E89 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
        }
        .subtitle { color: #666; margin-bottom: 24px; }

        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 24px; }
        .card {
            background: var(--card-bg);
            border: 2px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .card h2 {
            font-size: 1.15em;
            margin-bottom: 16px;
            color: #1a1a1a;
            font-weight: 700;
        }
        .card.wide { grid-column: 1 / -1; }

        .metric {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .metric:last-child { border-bottom: none; }
        .metric-label { color: var(--muted); }
        .metric-value { font-weight: 700; color: var(--text); }
        .cost { color: #FF6B35; }

        .big-number {
            font-size: 2.5em;
            font-weight: 800;
            color: #1a1a1a;
            line-height: 1.1;
        }
        .big-number.accent {
            background: linear-gradient(135deg, #FF6B35 0%, #004E89 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .big-label {
            font-size: 0.85em;
            color: #999;
            margin-top: 4px;
        }
        .stat-row {
            display: flex;
            gap: 32px;
            margin-bottom: 16px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 8px;
        }
        th {
            text-align: left;
            padding: 10px;
            background: var(--faint);
            font-weight: 700;
            font-size: 0.85em;
            color: var(--muted);
        }
        th:not(:first-child) { text-align: right; }
        td { padding: 10px; border-bottom: 1px solid var(--faint); }
        td:not(:first-child) { text-align: right; }
        tr:hover { background: var(--faint); }
        .model-name { font-weight: 600; color: #004E89; }

        .chart {
            height: 280px;
            background: var(--chart-bg);
            border-radius: 12px;
            padding: 16px;
            border: 1px solid var(--chart-border);
            position: relative;
        }
        .chart-container { display: flex; height: 100%; }
        .y-axis {
            display: flex;
            flex-direction: column-reverse;
            justify-content: space-between;
            width: 60px;
            height: 200px;
            font-size: 0.75em;
            color: var(--muted);
            padding-right: 8px;
            text-align: right;
        }
        .bar-chart {
            display: flex;
            align-items: flex-end;
            height: 200px;
            gap: 2px;
            overflow-x: auto;
            padding-bottom: 40px;
            flex: 1;
        }
        .bar {
            flex: 1;
            min-width: 8px;
            max-width: 40px;
            border-radius: 4px 4px 0 0;
            position: relative;
            min-height: 4px;
        }
        .bar.messages {
            background: linear-gradient(180deg, #004E89 0%, #3d8bd4 100%);
            box-shadow: 0 0 8px rgba(0, 78, 137, 0.3);
        }
        .bar.tokens {
            background: linear-gradient(180deg, #FF6B35 0%, #FF9A76 100%);
            box-shadow: 0 0 8px rgba(255, 107, 53, 0.3);
        }
        .bar-label {
            position: absolute;
            bottom: -24px;
            left: 50%;
            transform: translateX(-50%) rotate(-45deg);
            transform-origin: center;
            font-size: 0.6em;
            color: var(--bar-label);
            white-space: nowrap;
        }
        .bar-segment {
            width: 100%;
            min-height: 2px;
        }

        .legend {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 12px;
            font-size: 0.85em;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 2px;
        }

        .chart-tooltip {
            position: absolute;
            background: rgba(20, 20, 20, 0.92);
            color: #fff;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 0.8em;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            display: none;
        }
        .chart-tooltip .tt-date { color: #aaa; margin-bottom: 2px; font-size: 0.9em; }

        .heatmap-grid {
            display: grid;
            grid-template-columns: repeat(14, 12px);
            grid-auto-rows: 12px;
            gap: 3px;
        }
        .heatmap-day {
            width: 12px;
            height: 12px;
            border-radius: 2px;
        }
        .heatmap-day.level-0 { background: #ebedf0; }
        .heatmap-day.level-1 { background: #9be9a8; }
        .heatmap-day.level-2 { background: #40c463; }
        .heatmap-day.level-3 { background: #30a14e; }
        .heatmap-day.level-4 { background: #216e39; }


        .refresh {
            background: linear-gradient(135deg, #FF6B35 0%, #004E89 100%);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            margin-top: 20px;
            box-shadow: 0 4px 12px rgba(255, 107, 53, 0.3);
        }
        .refresh:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(255, 107, 53, 0.4);
        }
        .last-updated {
            text-align: center;
            color: #999;
            margin-top: 24px;
            font-size: 0.85em;
        }
        .repos-list {
            margin-top: 12px;
            font-size: 0.85em;
            color: #666;
        }
        .repos-list a {
            color: #004E89;
            text-decoration: none;
        }
        .repos-list a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>m5x2 AI Dashboard</h1>
        <p class="subtitle">Claude Code Usage &mdash; McKay Capital</p>

        {% set stale = freshness | selectattr('level', 'in', ['warn','stale','missing']) | list %}
        {% if stale %}
        <div style="margin-bottom:16px;padding:12px 16px;border-radius:10px;border:1px solid;
                    background:{% if stale | selectattr('level','equalto','stale') | list or stale | selectattr('level','equalto','missing') | list %}#fde8e8;border-color:#f5b5b5;color:#8a1f1f{% else %}#fff7d6;border-color:#f0d97a;color:#7a5b00{% endif %};font-size:14px;">
            <strong>⚠ Data freshness:</strong>
            {% for f in stale %}
                <span style="margin-right:14px;">
                    {{ f.name }}:
                    {% if f.hours_ago is none %}<em>no data</em>
                    {% elif f.hours_ago < 24 %}{{ '%.1f'|format(f.hours_ago) }}h ago
                    {% else %}{{ '%.1f'|format(f.hours_ago / 24) }}d ago
                    {% endif %}
                </span>
            {% endfor %}
            <span style="opacity:0.7;">— check periodic-sync.sh on the affected device</span>
        </div>
        {% endif %}

        <div style="display:flex;gap:12px;margin-bottom:24px;padding:16px;background:#f8f9fa;border-radius:12px;">
            <div style="display:flex;flex-direction:column;gap:4px;">
                <label style="font-size:0.85em;color:#666;font-weight:600;">User</label>
                <select id="user-filter" onchange="updateFilter()" style="padding:8px 12px;border:2px solid #e0e0e0;border-radius:8px;background:white;font-size:14px;cursor:pointer;">
                    <option value="">All Users</option>
                    {% for uid, user in users.items() %}
                    <option value="{{ uid }}" {% if selected_user == uid %}selected{% endif %}>{{ user.name }}</option>
                    {% endfor %}
                </select>
            </div>
        </div>
        <script>
            function updateFilter() {
                const user = document.getElementById('user-filter').value;
                const params = new URLSearchParams();
                if (user) params.set('user', user);
                window.location.search = params.toString();
            }
        </script>

        <!-- Top stats -->
        <div class="grid">
            <div class="card">
                <h2>Overview</h2>
                <div class="stat-row">
                    <div>
                        <div class="big-number">{{ total_sessions }}</div>
                        <div class="big-label">sessions</div>
                    </div>
                    <div>
                        <div class="big-number">{{ "{:,}".format(total_messages) }}</div>
                        <div class="big-label">messages</div>
                    </div>
                </div>
                <div class="metric">
                    <span class="metric-label">First session</span>
                    <span class="metric-value">{{ first_session }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Longest session</span>
                    <span class="metric-value">{{ longest_session_msgs }} msgs ({{ longest_session_hrs }})</span>
                </div>
                {% if max_daily_msgs %}
                <div class="metric">
                    <span class="metric-label">Max daily msgs</span>
                    <span class="metric-value">{{ "{:,}".format(max_daily_msgs) }}</span>
                </div>
                {% endif %}
            </div>

            <div class="card">
                <h2>Estimated Cost</h2>
                <div class="big-number accent">${{ "%.2f" | format(total_cost) }}</div>
                <div class="big-label">total estimated spend</div>
                <table style="margin-top: 16px;">
                    <tr><th>Model</th><th>Output Tokens</th><th>Cost</th></tr>
                    {% for name, mc in model_costs.items() %}
                    <tr>
                        <td class="model-name">{{ name }}</td>
                        <td>{{ "{:,}".format(mc.output_tokens) }}</td>
                        <td class="cost">${{ "%.2f" | format(mc.total_cost) }}</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>

            <div class="card">
                <h2>GitHub Commits</h2>
                <div style="margin-bottom: 12px;">
                    <span class="big-number">{{ github.total_commits }}</span>
                    <span class="big-label" style="display: inline; margin-left: 8px;">commits (90d)</span>
                </div>
                <div id="github-heatmap" style="overflow-x: auto;"></div>
                {% if github.repos %}
                <div class="repos-list">
                    {% for repo in github.repos %}
                    <a href="https://github.com/{{ repo }}" target="_blank">{{ repo.split('/')[1] }}</a>{% if not loop.last %}, {% endif %}
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </div>

        <!-- Cost breakdown -->
        <div class="card" style="margin-bottom: 24px;">
            <h2>Cost Breakdown by Model</h2>
            <table>
                <tr>
                    <th>Model</th>
                    <th>Input</th>
                    <th>Output</th>
                    <th>Cache Write</th>
                    <th>Cache Read</th>
                    <th>Total</th>
                </tr>
                {% for name, mc in model_costs.items() %}
                <tr>
                    <td class="model-name">{{ name }}</td>
                    <td>${{ "%.2f" | format(mc.input_cost) }}<br><span style="color:#999;font-size:0.8em">{{ "{:,}".format(mc.input_tokens) }} tok</span></td>
                    <td>${{ "%.2f" | format(mc.output_cost) }}<br><span style="color:#999;font-size:0.8em">{{ "{:,}".format(mc.output_tokens) }} tok</span></td>
                    <td>${{ "%.2f" | format(mc.cache_write_cost) }}<br><span style="color:#999;font-size:0.8em">{{ "{:,}".format(mc.cache_write_tokens) }} tok</span></td>
                    <td>${{ "%.2f" | format(mc.cache_read_cost) }}<br><span style="color:#999;font-size:0.8em">{{ "{:,}".format(mc.cache_read_tokens) }} tok</span></td>
                    <td class="cost" style="font-weight:700">${{ "%.2f" | format(mc.total_cost) }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <!-- Turns / Day (top chart, full width) -->
        <div class="card" style="margin-bottom: 24px;">
            <h2>Turns / Day (Last 30d)</h2>
            <div class="chart">
                <div class="chart-container">
                    <div class="y-axis" id="turns-y-axis"></div>
                    <div class="bar-chart" id="turns-chart"></div>
                </div>
            </div>
            <div style="margin-top: 8px; font-size: 0.8em; color: #999; display: flex; gap: 16px;" id="turns-legend"></div>
        </div>

        <!-- Charts -->
        <div class="grid">
            <div class="card">
                <h2>Messages per Day (Last 30d)</h2>
                <div class="chart">
                    <div class="chart-container">
                        <div class="y-axis" id="msg-y-axis"></div>
                        <div class="bar-chart" id="msg-chart"></div>
                    </div>
                </div>
            </div>
            <div class="card">
                <h2>Tokens per Day (Last 30d)</h2>
                <div class="chart">
                    <div class="chart-container">
                        <div class="y-axis" id="tok-y-axis"></div>
                        <div class="bar-chart" id="tok-chart"></div>
                    </div>
                </div>
                <div style="margin-top: 8px; font-size: 0.8em; color: #999; display: flex; gap: 16px;">
                    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#004E89;margin-right:4px;"></span>Opus 4.6</span>
                    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#FF6B35;margin-right:4px;"></span>Sonnet 4.5</span>
                </div>
            </div>
        </div>


        <!-- MCP Calls chart -->
        <div class="card" style="margin-bottom: 24px;">
            <h2>MCP Calls / Day (Last 30 Days)</h2>
            <div class="legend" id="mcp-legend"></div>
            <div class="chart">
                <div class="chart-container">
                    <div class="y-axis" id="mcp-y-axis"></div>
                    <div class="bar-chart" id="mcp-chart"></div>
                </div>
            </div>
        </div>

        <!-- Skill Calls chart -->
        <div class="card" style="margin-bottom: 24px;">
            <h2>Skill Calls / Day (Last 30 Days)</h2>
            <div class="legend" id="skill-legend"></div>
            <div class="chart">
                <div class="chart-container">
                    <div class="y-axis" id="skill-y-axis"></div>
                    <div class="bar-chart" id="skill-chart"></div>
                </div>
            </div>
        </div>

        <!-- Latency section -->
        <div class="card" style="margin-bottom: 24px;">
            <h2>Response Latency &mdash; TTFT, TTLT &amp; Wall-Clock (Last 30 Days)</h2>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:16px; margin-bottom:16px;">
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0;">{{ "%.1f" | format(latency.overall.avg_ttft | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0;">{{ "%.1f" | format(latency.overall.median_ttft | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 TTFT</div>
                    <div style="font-size:1.6em; font-weight:700; color:#FF10F0;">{{ "%.1f" | format(latency.overall.p95_ttft | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Avg Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF;">{{ "%.1f" | format(latency.overall.avg_wall | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">Median Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF;">{{ "%.1f" | format(latency.overall.median_wall | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p95 Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF;">{{ "%.1f" | format(latency.overall.p95_wall | default(0)) }}s</div>
                </div>
                <div>
                    <div style="font-size:0.75em; color:var(--muted); margin-bottom:4px; text-transform:uppercase; letter-spacing:.05em;">p99 Wall</div>
                    <div style="font-size:1.6em; font-weight:700; color:#00D4FF;">{{ "%.1f" | format(latency.overall.p99_wall | default(0)) }}s</div>
                </div>
            </div>
            <div style="display:flex; gap:20px; margin-bottom:12px; font-size:0.85em;">
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:24px;height:3px;background:#FF10F0;border-radius:2px;"></span>TTFT</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:24px;height:3px;background:#39FF14;border-radius:2px;"></span>TTLT</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:24px;height:3px;background:#00D4FF;border-radius:2px;"></span>Avg Wall</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:24px;height:3px;background:#FFD700;border-radius:2px;"></span>Max Wall</span>
            </div>
            <div style="background:var(--chart-bg); border:1px solid var(--chart-border); border-radius:12px; padding:16px; overflow:hidden;">
                <svg id="latency-chart" width="100%" height="200" style="overflow:visible;"></svg>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:var(--muted);">Log scale. TTFT = first user→assistant delta; TTLT = last user→assistant delta. Wall = full turn incl. approvals.</div>
        </div>

        <div class="card" style="margin-bottom: 24px;">
            <h2>Wait Time Distribution (% of Total)</h2>
            <div style="display:flex; gap:20px; margin-bottom:12px; font-size:0.85em;">
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:12px;height:12px;background:#00D4FF;border-radius:2px;"></span>≤p50</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:12px;height:12px;background:#FF10F0;border-radius:2px;"></span>p50→p95</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:12px;height:12px;background:#FFD700;border-radius:2px;"></span>p95→p99</span>
                <span style="display:flex; align-items:center; gap:6px;"><span style="display:inline-block;width:12px;height:12px;background:#FF5722;border-radius:2px;"></span>p99→max</span>
            </div>
            <div style="background:var(--chart-bg); border:1px solid var(--chart-border); border-radius:12px; padding:16px; overflow:hidden;">
                <svg id="wait-pct-chart" width="100%" height="200" style="overflow:visible;"></svg>
            </div>
            <div style="margin-top:8px; font-size:0.75em; color:var(--muted);">% of total daily wait time from each latency tier. Hover bars for totals.</div>
        </div>

        <button class="refresh" onclick="location.reload()">Refresh Data</button>
        <p class="last-updated">Last updated: {{ now }} &mdash; Stats computed through {{ stats_date }}</p>
    </div>

    <script>
        // --- Shared formatting ---
        function fmtNum(val) {
            if (val >= 1_000_000) return (val / 1_000_000).toFixed(1) + 'M';
            if (val >= 1_000) return Math.round(val / 1_000) + 'k';
            return Math.round(val).toString();
        }

        function buildYAxis(elementId, maxVal, steps) {
            const el = document.getElementById(elementId);
            for (let i = 0; i <= steps; i++) {
                const label = document.createElement('div');
                label.textContent = fmtNum(maxVal * i / steps);
                el.appendChild(label);
            }
        }

        // --- Turns chart (stacked by user) ---
        const turnsDaily = {{ turns_daily | tojson | safe }};
        const turnsUsers = {{ turns_users | tojson | safe }};
        const turnsChart = document.getElementById('turns-chart');
        const turnsLegend = document.getElementById('turns-legend');
        const USER_COLORS = {jm: '#4ade80', lx: '#60a5fa', ian: '#f97316', matt: '#a78bfa'};

        const maxTurns = Math.max(...turnsDaily.map(d => d.total || 0), 1);
        buildYAxis('turns-y-axis', maxTurns, 4);

        turnsDaily.forEach(day => {
            const barPct = (day.total / maxTurns) * 100;
            const wrapper = document.createElement('div');
            wrapper.style.cssText = `flex:1;min-width:8px;max-width:40px;height:${barPct}%;display:flex;flex-direction:column;justify-content:flex-end;position:relative;`;
            wrapper.title = `${day.date}: ${day.total} turns (${turnsUsers.map(u => u + ':' + (day[u]||0)).join(', ')})`;

            turnsUsers.forEach(u => {
                const count = day[u] || 0;
                if (count > 0 && day.total > 0) {
                    const seg = document.createElement('div');
                    const segPct = (count / day.total) * 100;
                    seg.style.cssText = `height:${segPct}%;background:${USER_COLORS[u]||'#888'};border-radius:2px 2px 0 0;min-height:1px;`;
                    wrapper.appendChild(seg);
                }
            });

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5);
            wrapper.appendChild(label);
            turnsChart.appendChild(wrapper);
        });

        turnsUsers.forEach(u => {
            const span = document.createElement('span');
            span.innerHTML = `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${USER_COLORS[u]||'#888'};margin-right:4px;"></span>${u}`;
            turnsLegend.appendChild(span);
        });

        // --- Messages chart ---
        const dailyActivity = {{ daily_activity | tojson | safe }};
        const msgChart = document.getElementById('msg-chart');
        const maxMsgs = Math.max(...dailyActivity.map(d => d.messageCount || 0), 1);
        buildYAxis('msg-y-axis', maxMsgs, 4);

        dailyActivity.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar messages';
            const height = maxMsgs > 0 ? ((day.messageCount || 0) / maxMsgs) * 100 : 5;
            bar.style.height = height + '%';
            bar.title = `${day.date}: ${(day.messageCount || 0).toLocaleString()} messages, ${day.sessionCount} sessions`;

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5);
            bar.appendChild(label);
            msgChart.appendChild(bar);
        });

        // --- Tokens chart (stacked by model) ---
        const dailyTokens = {{ daily_tokens | tojson | safe }};
        const tokChart = document.getElementById('tok-chart');
        const OPUS_KEY = 'claude-opus-4-6';
        const SONNET_KEY = 'claude-sonnet-4-5-20250929';

        const tokParsed = dailyTokens.map(d => {
            const models = d.tokensByModel || {};
            const opus = models[OPUS_KEY] || 0;
            const sonnet = models[SONNET_KEY] || 0;
            return { date: d.date, opus, sonnet, total: opus + sonnet };
        });
        const maxTok = Math.max(...tokParsed.map(d => d.total), 1);
        buildYAxis('tok-y-axis', maxTok, 4);

        tokParsed.forEach(day => {
            // Bar height as % of container (same pattern as personal dashboard)
            const barPct = (day.total / maxTok) * 100;
            const wrapper = document.createElement('div');
            wrapper.style.cssText = `flex:1;min-width:8px;max-width:40px;height:${barPct}%;display:flex;flex-direction:column;justify-content:flex-end;position:relative;`;

            if (day.total > 0) {
                // Opus on bottom (blue) — height as % of bar
                if (day.opus > 0) {
                    const seg = document.createElement('div');
                    const h = (day.opus / day.total) * 100;
                    seg.style.cssText = `height:${h}%;background:#004E89;border-radius:${day.sonnet > 0 ? '0' : '4px 4px 0 0'};`;
                    wrapper.appendChild(seg);
                }
                // Sonnet on top (orange) — height as % of bar
                if (day.sonnet > 0) {
                    const seg = document.createElement('div');
                    const h = (day.sonnet / day.total) * 100;
                    seg.style.cssText = `height:${h}%;background:#FF6B35;border-radius:4px 4px 0 0;`;
                    wrapper.appendChild(seg);
                }
            } else {
                const seg = document.createElement('div');
                seg.style.cssText = 'height:2px;background:#eee;border-radius:4px 4px 0 0;';
                wrapper.appendChild(seg);
            }

            wrapper.title = `${day.date}: ${day.total.toLocaleString()} tokens (Opus: ${day.opus.toLocaleString()}, Sonnet: ${day.sonnet.toLocaleString()})`;

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.textContent = day.date.substring(5);
            wrapper.appendChild(label);

            tokChart.appendChild(wrapper);
        });

        // --- GitHub heatmap ---
        const githubData = {{ github.daily_commits | tojson | safe }};
        const heatmapContainer = document.getElementById('github-heatmap');
        const today = new Date();
        const todayStr = today.toISOString().split('T')[0];
        const daysAgo = new Date(today);
        daysAgo.setDate(daysAgo.getDate() - 89);
        const startDate = new Date(daysAgo);
        while (startDate.getDay() !== 0) startDate.setDate(startDate.getDate() - 1);

        const grid = document.createElement('div');
        grid.className = 'heatmap-grid';
        const maxCommits = Math.max(...Object.values(githubData), 1);

        for (let week = 0; week < 14; week++) {
            for (let day = 0; day < 7; day++) {
                const currentDate = new Date(startDate);
                currentDate.setDate(currentDate.getDate() + (week * 7) + day);
                const dateStr = currentDate.toISOString().split('T')[0];
                if (dateStr > todayStr) continue;
                const commits = githubData[dateStr] || 0;
                const square = document.createElement('div');
                square.className = 'heatmap-day';
                let level = 0;
                if (commits > 0) {
                    const ratio = commits / maxCommits;
                    if (ratio >= 0.75) level = 4;
                    else if (ratio >= 0.5) level = 3;
                    else if (ratio >= 0.25) level = 2;
                    else level = 1;
                }
                square.classList.add(`level-${level}`);
                square.title = `${commits} commits on ${dateStr}`;
                grid.appendChild(square);
            }
        }
        heatmapContainer.appendChild(grid);

        // --- Shared axis utilities ---
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
            const actualMax = Math.max(interval * Math.ceil(rawMax / interval), interval);
            const ticks = [];
            for (let v = 0; v <= actualMax; v += interval) ticks.push(v);
            return { max: actualMax, ticks };
        }
        function fmtAxis(n) {
            if (n >= 1_000_000) return (n / 1_000_000).toFixed(0) + 'M';
            if (n >= 1_000) return (n / 1_000).toFixed(0) + 'k';
            return n.toString();
        }
        function createYAxis(id, axisDef) {
            const el = document.getElementById(id);
            axisDef.ticks.forEach(v => {
                const label = document.createElement('div');
                label.textContent = fmtAxis(v);
                el.appendChild(label);
            });
        }

        // --- MCP chart ---
        const mcpData = {{ mcp_daily | tojson | safe }};
        const mcpServers = {{ mcp_servers | tojson | safe }};
        const mcpChart = document.getElementById('mcp-chart');
        const mcpLegend = document.getElementById('mcp-legend');
        const mcpColors = {
            'google-workspace': '#4285F4', 'toggl': '#E57CD8', 'todoist': '#E44332',
            'excel-mcp': '#217346', 'appfolio': '#FF8C00', 'google-calendar': '#0B8043',
            'quickbooks': '#2CA01C',
        };
        const mcpFallback = ['#9C27B0','#FF5722','#607D8B','#00BCD4'];
        let mcpFbIdx = 0;
        function getMcpColor(s) {
            if (!mcpColors[s]) mcpColors[s] = mcpFallback[mcpFbIdx++ % mcpFallback.length];
            return mcpColors[s];
        }
        mcpServers.forEach(s => {
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `<div class="legend-color" style="background:${getMcpColor(s)}"></div><span>${s}</span>`;
            mcpLegend.appendChild(item);
        });
        const mcpAxis = niceAxis(Math.min(Math.max(...mcpData.map(d => d.total), 1), 300));
        createYAxis('mcp-y-axis', mcpAxis);
        mcpData.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.style.height = (mcpAxis.max > 0 ? Math.min((day.total / mcpAxis.max) * 100, 100) : 0) + '%';
            let isTop = true;
            for (let i = mcpServers.length - 1; i >= 0; i--) {
                const s = mcpServers[i]; const count = day[s] || 0;
                if (count <= 0) continue;
                const seg = document.createElement('div');
                seg.className = 'bar-segment';
                seg.style.height = (day.total > 0 ? (count / day.total) * 100 : 0) + '%';
                seg.style.background = getMcpColor(s);
                if (isTop) { seg.style.borderRadius = '4px 4px 0 0'; isTop = false; }
                bar.appendChild(seg);
            }
            bar.title = day.date + ':\\n' + mcpServers.filter(s => day[s]).map(s => `${s}: ${day[s]}`).join('\\n') + `\\nTotal: ${day.total}`;
            const lbl = document.createElement('div'); lbl.className = 'bar-label'; lbl.textContent = day.date.substring(5);
            bar.appendChild(lbl);
            mcpChart.appendChild(bar);
        });

        // --- Skill chart ---
        const skillData = {{ skill_daily | tojson | safe }};
        const skillNames = {{ skill_names | tojson | safe }};
        const skillChart = document.getElementById('skill-chart');
        const skillLegend = document.getElementById('skill-legend');
        const skillColors = {
            '/did': '#4CAF50', '/tg': '#E57CD8', '/-1g': '#FF9800', '/0t': '#2196F3',
            '/1n': '#9C27B0', '/1nd': '#00BCD4', '/commit': '#607D8B', '/ibx': '#FF6B35',
            '/todo': '#E44332', '/0g': '#FFD700',
        };
        const skillFallback = ['#FF5722','#795548','#CDDC39','#3F51B5'];
        let skillFbIdx = 0;
        function getSkillColor(s) {
            if (!skillColors[s]) skillColors[s] = skillFallback[skillFbIdx++ % skillFallback.length];
            return skillColors[s];
        }
        skillNames.forEach(s => {
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `<div class="legend-color" style="background:${getSkillColor(s)}"></div><span>${s}</span>`;
            skillLegend.appendChild(item);
        });
        const skillAxis = niceAxis(Math.min(Math.max(...skillData.map(d => d.total), 1), 100));
        createYAxis('skill-y-axis', skillAxis);
        skillData.forEach(day => {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.style.height = (skillAxis.max > 0 ? Math.min((day.total / skillAxis.max) * 100, 100) : 0) + '%';
            let isTop = true;
            for (let i = skillNames.length - 1; i >= 0; i--) {
                const s = skillNames[i]; const count = day[s] || 0;
                if (count <= 0) continue;
                const seg = document.createElement('div');
                seg.className = 'bar-segment';
                seg.style.height = (day.total > 0 ? (count / day.total) * 100 : 0) + '%';
                seg.style.background = getSkillColor(s);
                if (isTop) { seg.style.borderRadius = '4px 4px 0 0'; isTop = false; }
                bar.appendChild(seg);
            }
            bar.title = day.date + ':\\n' + skillNames.filter(s => day[s]).map(s => `${s}: ${day[s]}`).join('\\n') + `\\nTotal: ${day.total}`;
            const lbl = document.createElement('div'); lbl.className = 'bar-label'; lbl.textContent = day.date.substring(5);
            bar.appendChild(lbl);
            skillChart.appendChild(bar);
        });

        // --- Latency line chart ---
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
            const allVals = [...ttfts, ...ttlts, ...walls, ...maxWalls].filter(v => v != null);
            if (!allVals.length) return;
            const maxVal = Math.max(...allVals, 1);
            const minVal = Math.max(Math.min(...allVals.filter(v => v > 0)), 0.1);
            const logMin = Math.log10(minVal), logMax = Math.log10(maxVal);
            const n = latencyData.length;
            function xPos(i) { return PAD.left + (i / (n - 1)) * inner_w; }
            function yPos(v) {
                if (v <= 0) return PAD.top + inner_h;
                return PAD.top + inner_h - ((Math.log10(v) - logMin) / (logMax - logMin)) * inner_h;
            }
            function makePath(vals, color) {
                let d = ''; let inPath = false;
                vals.forEach((v, i) => {
                    if (v == null) { inPath = false; return; }
                    const p = `${xPos(i)},${yPos(v)}`;
                    if (!inPath) { d += `M${p}`; inPath = true; } else { d += ` L${p}`; }
                });
                if (!d) return;
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', d); path.setAttribute('fill', 'none');
                path.setAttribute('stroke', color); path.setAttribute('stroke-width', '2');
                path.setAttribute('stroke-linejoin', 'round'); path.setAttribute('stroke-linecap', 'round');
                svg.appendChild(path);
                vals.forEach((v, i) => {
                    if (v == null) return;
                    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                    circle.setAttribute('cx', xPos(i)); circle.setAttribute('cy', yPos(v)); circle.setAttribute('r', '3'); circle.setAttribute('fill', color);
                    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                    title.textContent = `${latencyData[i].date}: ${v}s`; circle.appendChild(title);
                    svg.appendChild(circle);
                });
            }
            [1, 2, 5, 10, 20, 50, 100, 200].filter(v => v >= minVal * 0.9 && v <= maxVal * 1.1).forEach(v => {
                const y = yPos(v);
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', PAD.left); line.setAttribute('x2', PAD.left + inner_w);
                line.setAttribute('y1', y); line.setAttribute('y2', y);
                line.setAttribute('stroke', '#e0e0e0'); line.setAttribute('stroke-width', '1');
                svg.appendChild(line);
                const lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                lbl.setAttribute('x', PAD.left - 6); lbl.setAttribute('y', y + 4);
                lbl.setAttribute('text-anchor', 'end'); lbl.setAttribute('font-size', '10'); lbl.setAttribute('fill', '#999');
                lbl.textContent = v >= 60 ? (v/60).toFixed(0)+'m' : v+'s';
                svg.appendChild(lbl);
            });
            latencyData.forEach((d, i) => {
                if (i % 7 !== 0 && i !== n - 1) return;
                const lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                lbl.setAttribute('x', xPos(i)); lbl.setAttribute('y', H - 4);
                lbl.setAttribute('text-anchor', 'middle'); lbl.setAttribute('font-size', '9'); lbl.setAttribute('fill', '#999');
                lbl.textContent = d.date.substring(5); svg.appendChild(lbl);
            });
            makePath(ttfts, '#FF10F0'); makePath(ttlts, '#39FF14');
            makePath(walls, '#00D4FF'); makePath(maxWalls, '#FFD700');
        })();

        // --- Wait pct chart ---
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
                { key: 'le_p50', color: '#00D4FF', label: '≤p50' },
                { key: 'p50_p95', color: '#FF10F0', label: 'p50→p95' },
                { key: 'p95_p99', color: '#FFD700', label: 'p95→p99' },
                { key: 'p99_max', color: '#FF5722', label: 'p99→max' },
            ];
            const barWidth = (inner_w / n) * 0.7;
            const barGap = (inner_w / n) * 0.3;
            for (let s = 0; s <= 4; s++) {
                const v = 100 * s / 4;
                const y = PAD.top + inner_h - (v / 100) * inner_h;
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', PAD.left); line.setAttribute('x2', PAD.left + inner_w);
                line.setAttribute('y1', y); line.setAttribute('y2', y);
                line.setAttribute('stroke', '#e0e0e0'); line.setAttribute('stroke-width', '1');
                svg.appendChild(line);
                const lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                lbl.setAttribute('x', PAD.left - 6); lbl.setAttribute('y', y + 4);
                lbl.setAttribute('text-anchor', 'end'); lbl.setAttribute('font-size', '10'); lbl.setAttribute('fill', '#999');
                lbl.textContent = v + '%'; svg.appendChild(lbl);
            }
            data.forEach((d, i) => {
                const wb = d.wait_buckets; if (!wb) return;
                const x = PAD.left + i * (inner_w / n) + barGap / 2;
                let yOffset = 0;
                buckets.forEach(b => {
                    const pct = wb[b.key] || 0; if (pct <= 0) return;
                    const h = (pct / 100) * inner_h;
                    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('x', x); rect.setAttribute('y', PAD.top + inner_h - yOffset - h);
                    rect.setAttribute('width', barWidth); rect.setAttribute('height', h);
                    rect.setAttribute('fill', b.color); rect.setAttribute('opacity', '0.85'); rect.setAttribute('rx', '1');
                    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                    title.textContent = `${d.date} ${b.label}: ${pct}% (total: ${wb.total_s}s)`;
                    rect.appendChild(title); svg.appendChild(rect); yOffset += h;
                });
                if (i % 7 === 0 || i === n - 1) {
                    const lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    lbl.setAttribute('x', x + barWidth / 2); lbl.setAttribute('y', H - 4);
                    lbl.setAttribute('text-anchor', 'middle'); lbl.setAttribute('font-size', '9'); lbl.setAttribute('fill', '#999');
                    lbl.textContent = d.date.substring(5); svg.appendChild(lbl);
                }
            });
        })();

    </script>
</body>
</html>
"""


@app.route('/')
def dashboard():
    """Main dashboard page"""
    selected_user = request.args.get('user') or "jm"

    if selected_user != "jm":
        pull_stats_repo()

    stats = load_stats(selected_user)
    if not stats:
        stats = {"totalSessions": 0, "totalMessages": 0, "longestSession": {}, "firstSessionDate": ""}
        model_costs = {}
        total_cost = 0
        daily_activity = []
        daily_tokens = []
    else:
        model_costs = compute_model_costs(stats.get("modelUsage", {}))
        total_cost = sum(mc["total_cost"] for mc in model_costs.values())
        daily_activity = get_daily_activity(stats)
        daily_tokens = get_daily_tokens(stats)

    github = get_github_activity()
    turns_data = get_daily_turns()
    freshness = get_freshness()

    # Session stats: JM reads local JSONL; others read precomputed JSON from stats repo
    is_jm = selected_user in ("jm", "")
    if is_jm:
        mcp = get_mcp_stats()
        skills = get_skill_stats()
        latency = get_latency_stats()
    else:
        session_stats_file = STATS_REPO / selected_user / "session-stats.json"
        if session_stats_file.exists():
            with open(session_stats_file) as f:
                ss = json.load(f)
            mcp = ss.get("mcp", {"daily": [], "servers": [], "total": 0})
            skills = ss.get("skills", {"daily": [], "skills": [], "total": 0})
            latency = ss.get("latency", {"daily": [], "overall": {}})
        else:
            mcp = {"daily": [], "servers": [], "total": 0}
            skills = {"daily": [], "skills": [], "total": 0}
            latency = {"daily": [], "overall": {}}

    # Longest session info
    longest = stats.get("longestSession", {})
    longest_msgs = longest.get("messageCount", 0)
    longest_duration_ms = longest.get("duration", 0)
    longest_hrs = longest_duration_ms / 3_600_000
    if longest_hrs >= 1:
        longest_str = f"{longest_hrs:.1f}h"
    else:
        longest_str = f"{longest_duration_ms / 60_000:.0f}m"

    first_session = stats.get("firstSessionDate", "")[:10]

    # Max daily messages from dailyActivity
    max_daily_msgs = max((d.get("messageCount", 0) for d in daily_activity), default=0) if daily_activity else 0

    return render_template_string(
        HTML_TEMPLATE,
        users=CONFIG.get("users", {}),
        selected_user=selected_user,
        total_sessions=stats.get("totalSessions", 0),
        total_messages=stats.get("totalMessages", 0),
        first_session=first_session,
        longest_session_msgs=longest_msgs,
        longest_session_hrs=longest_str,
        max_daily_msgs=max_daily_msgs,
        model_costs=model_costs,
        total_cost=total_cost,
        daily_activity=daily_activity,
        daily_tokens=daily_tokens,
        github=github,
        mcp_daily=mcp["daily"],
        mcp_servers=mcp["servers"],
        skill_daily=skills["daily"],
        skill_names=skills["skills"],
        latency=latency,
        latency_daily=latency["daily"],
        turns_daily=turns_data["daily"],
        turns_users=turns_data["users"],
        stats_date=stats.get("lastComputedDate", "unknown"),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        freshness=freshness,
    )


@app.route('/api/stats')
def api_stats():
    """API endpoint for raw stats"""
    stats = load_stats()
    if not stats:
        return jsonify({"error": "No stats found"})

    model_costs = compute_model_costs(stats.get("modelUsage", {}))
    return jsonify({
        "totalSessions": stats.get("totalSessions", 0),
        "totalMessages": stats.get("totalMessages", 0),
        "modelCosts": model_costs,
        "dailyActivity": get_daily_activity(stats),
        "dailyTokens": get_daily_tokens(stats),
        "github": get_github_activity(),
        "mcp": get_mcp_stats(),
        "skills": get_skill_stats(),
        "latency": get_latency_stats(),
    })


if __name__ == "__main__":
    print("Starting m5x2 AI Dashboard...")
    print(f"Dashboard: http://localhost:{PORT}")
    print(f"API:       http://localhost:{PORT}/api/stats")
    print(f"Data:      {STATS_CACHE}")
    print("\nPress Ctrl+C to stop")
    app.run(host='0.0.0.0', port=PORT, debug=False)
