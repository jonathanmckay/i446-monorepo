---
title: "m5x2 Dashboard Quickstart"
date: 2026-03-25
type: doc
tags: [m5x2, i446, quickstart]
---

# m5x2 AI Dashboard - Quickstart

## 🎯 What You Built

A multi-user AI usage dashboard for m5x2 (McKay Capital) with:

- ✅ Multi-user tracking (JM + LX)
- ✅ Property-based cost allocation (r202, m221, etc.)
- ✅ Portfolio overview + drill-down views
- ✅ Team GitHub activity heatmap
- ✅ Real-time cost monitoring

## 🚀 Start Using It Now

### 1. Start the Dashboard
```bash
bash ~/vault/i447/i446/ai-dashboard/start-m5x2.sh
```

Then open: **http://localhost:5556**

### 2. Tag Your Current Session
```bash
cd ~/vault/i447/i446/ai-dashboard

# JM working on r202
python3 m5x2-tag-session.py -u jm -p r202

# LX working on m221
python3 m5x2-tag-session.py -u lx -p m221
```

### 3. View Your Data
- **Dashboard:** http://localhost:5556
- **Filter by user:** Use dropdown at top
- **Filter by property:** Use dropdown at top
- **API:** http://localhost:5556/api/stats

## 📊 What You'll See

### Portfolio Overview
- Total sessions, turns, costs across all users/properties
- Breakdown by user (JM vs LX)
- Breakdown by property (r202, m221, portfolio, etc.)
- Team GitHub commits (last 90 days)

### Filtered Views
- **By User:** See only JM or LX usage
- **By Property:** See only r202 or m221 usage
- **Combined:** See JM's work on r202 specifically

### Charts
- Last 30 days cost trend
- GitHub commit heatmap

## 🔄 Daily Workflow

1. **Morning:** Start dashboard (`bash start-m5x2.sh`)
2. **During work:** Use Claude/Copilot normally
3. **After session:** Tag it (`python3 m5x2-tag-session.py -u jm -p r202`)
4. **End of day:** Check dashboard to review costs

## ⚙️ Configuration

Edit `~/vault/i447/i446/ai-dashboard/m5x2-config.json`:

```json
{
  "users": {
    "jm": { "name": "Jonathan McKay", "email": "mckay@m5x2.com", "role": "admin" },
    "lx": { "name": "Lexi McKay", "email": "lexi@m5x2.com", "role": "admin" }
  },
  "properties": ["r202", "m221", "portfolio", "finance", "operations", "other"],
  "github": {
    "org": "mckay-capital",
    "users": ["jonathanmckay", "lexiemckay"]
  },
  "port": 5556
}
```

## 📝 Quick Commands

```bash
# Tag latest session
python3 m5x2-tag-session.py -u jm -p r202

# List recent sessions
python3 m5x2-tag-session.py --list

# Tag specific session
python3 m5x2-tag-session.py --session <id> -u lx -p m221

# Bulk tag all untagged
python3 m5x2-tag-session.py -u jm -p portfolio --all-untagged
```

## 🎓 Next Steps

1. **Add Lexi's GitHub username** to config if different
2. **Add more properties** as needed (edit config)
3. **Set up tagging habit** - tag after each session
4. **Weekly review** - check cost allocation

## 📚 Documentation

- **Full setup:** `M5X2-DASHBOARD-README.md`
- **Usage examples:** `m5x2-usage-examples.md`
- **Personal dashboard:** Still at http://localhost:5555 (port 5555)

## 🔧 Troubleshooting

**Dashboard won't start:**
```bash
# Check if port is in use
lsof -i :5556

# Kill existing process
pkill -f m5x2-dashboard
```

**Sessions not showing:**
```bash
# Check database
sqlite3 ~/vault/i447/i446/ai-dashboard/llm-sessions.db "SELECT COUNT(*) FROM sessions"

# List recent sessions
python3 m5x2-tag-session.py --list
```

**GitHub not showing commits:**
- Verify GitHub token: `gh auth status`
- Check config: usernames in `m5x2-config.json`
- Check org name (if using org instead of personal repos)

## 💡 Key Differences from Personal Dashboard

| Feature | Personal (5555) | m5x2 (5556) |
|---------|----------------|-------------|
| Users | Single | Multi-user |
| Properties | None | Property codes |
| GitHub | Personal | Org/team |
| Filters | None | User + Property |
| Port | 5555 | 5556 |

Both use the same database (`llm-sessions.db`), just with different views!
