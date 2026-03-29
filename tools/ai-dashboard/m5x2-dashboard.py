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
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from flask import Flask, render_template_string, jsonify, request
import requests

app = Flask(__name__)

# Paths
CONFIG_FILE = Path(__file__).parent / "m5x2-config.json"
STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"

# Load config
with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

GITHUB_USER = "jonathanmckay"
PORT = CONFIG.get("port", 5556)

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


def load_stats():
    """Load stats from Claude Code's stats-cache.json"""
    if not STATS_CACHE.exists():
        return None
    with open(STATS_CACHE) as f:
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


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>m5x2 AI Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='35' fill='none' stroke='%23FF6B35' stroke-width='16' stroke-dasharray='55 165' transform='rotate(-90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23004E89' stroke-width='16' stroke-dasharray='55 165' transform='rotate(0 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%2300D4FF' stroke-width='16' stroke-dasharray='55 165' transform='rotate(90 50 50)'/><circle cx='50' cy='50' r='35' fill='none' stroke='%23FFD700' stroke-width='16' stroke-dasharray='55 165' transform='rotate(180 50 50)'/></svg>">
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
            margin-bottom: 6px;
            background: linear-gradient(135deg, #FF6B35 0%, #004E89 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
        }
        .subtitle { color: #666; margin-bottom: 24px; }

        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 24px; }
        .card {
            background: #ffffff;
            border: 2px solid #e0e0e0;
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
        .metric-label { color: #666; }
        .metric-value { font-weight: 700; color: #1a1a1a; }
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
            background: #f8f9fa;
            font-weight: 700;
            font-size: 0.85em;
            color: #666;
        }
        th:not(:first-child) { text-align: right; }
        td { padding: 10px; border-bottom: 1px solid #f0f0f0; }
        td:not(:first-child) { text-align: right; }
        tr:hover { background: #f8f9fa; }
        .model-name { font-weight: 600; color: #004E89; }

        .chart {
            height: 280px;
            background: #fafafa;
            border-radius: 12px;
            padding: 16px;
            border: 1px solid #e0e0e0;
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
            color: #666;
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
            color: #333;
            white-space: nowrap;
        }

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
            const wrapper = document.createElement('div');
            wrapper.style.cssText = 'flex:1;min-width:8px;max-width:40px;display:flex;flex-direction:column;justify-content:flex-end;position:relative;';

            const opusPct = maxTok > 0 ? (day.opus / maxTok) * 100 : 0;
            const sonnetPct = maxTok > 0 ? (day.sonnet / maxTok) * 100 : 0;

            // Sonnet on top (orange), Opus on bottom (blue)
            if (day.sonnet > 0) {
                const seg = document.createElement('div');
                seg.style.cssText = `height:${sonnetPct}%;background:#FF6B35;border-radius:4px 4px 0 0;min-height:2px;`;
                wrapper.appendChild(seg);
            }
            if (day.opus > 0) {
                const seg = document.createElement('div');
                seg.style.cssText = `height:${opusPct}%;background:#004E89;border-radius:${day.sonnet > 0 ? '0' : '4px 4px 0 0'};min-height:2px;`;
                wrapper.appendChild(seg);
            }
            if (day.total === 0) {
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

    </script>
</body>
</html>
"""


@app.route('/')
def dashboard():
    """Main dashboard page"""
    selected_user = request.args.get('user')

    stats = load_stats()
    if not stats:
        return "<h1>No data found</h1><p>Claude Code stats-cache.json not found at ~/.claude/stats-cache.json</p>"

    # Currently all local data is JM's. When user filter is set to someone
    # else, show empty state (future: load their stats file).
    if selected_user and selected_user != "jm":
        model_costs = {}
        total_cost = 0
        daily_activity = []
        daily_tokens = []
        stats = {**stats, "totalSessions": 0, "totalMessages": 0,
                 "longestSession": {}, "firstSessionDate": ""}
    else:
        model_costs = compute_model_costs(stats.get("modelUsage", {}))
        total_cost = sum(mc["total_cost"] for mc in model_costs.values())
        daily_activity = get_daily_activity(stats)
        daily_tokens = get_daily_tokens(stats)

    github = get_github_activity()

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

    return render_template_string(
        HTML_TEMPLATE,
        users=CONFIG.get("users", {}),
        selected_user=selected_user,
        total_sessions=stats.get("totalSessions", 0),
        total_messages=stats.get("totalMessages", 0),
        first_session=first_session,
        longest_session_msgs=longest_msgs,
        longest_session_hrs=longest_str,
        model_costs=model_costs,
        total_cost=total_cost,
        daily_activity=daily_activity,
        daily_tokens=daily_tokens,
        github=github,
        stats_date=stats.get("lastComputedDate", "unknown"),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    })


if __name__ == "__main__":
    print("Starting m5x2 AI Dashboard...")
    print(f"Dashboard: http://localhost:{PORT}")
    print(f"API:       http://localhost:{PORT}/api/stats")
    print(f"Data:      {STATS_CACHE}")
    print("\nPress Ctrl+C to stop")
    app.run(host='0.0.0.0', port=PORT, debug=False)
