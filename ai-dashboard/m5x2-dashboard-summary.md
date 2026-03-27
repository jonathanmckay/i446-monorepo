---
title: "m5x2 AI Dashboard - Build Summary"
date: 2026-03-25
type: doc
tags: [m5x2, i446, summary]
---

# m5x2 AI Dashboard - Build Complete ✅

Built a minimal viable m5x2 dashboard with multi-user tracking, property allocation, portfolio views, team GitHub activity, and basic access control.

## What Was Built

### Core Features ✅

**#1 - Multi-User Tracking**
- Database now tracks `user_id` (jm, lx, etc.)
- User dropdown filter in UI
- Stats by user table
- Config-driven user management

**#5b - Portfolio Overview**
- Aggregate view across all users and properties
- Total sessions, turns, costs
- Breakdown tables for users and properties
- 30-day cost trend chart

**#5c - Property Drill-Down**
- Property dropdown filter
- Filter by user + property combined
- Cost allocation per property
- Untagged sessions highlighted

**#7 - GitHub Activity (Org-level)**
- Team commit heatmap (90 days)
- Tracks multiple GitHub users
- Configurable org or user aggregation
- Pacific timezone for commits

**#10 - Access Control**
- Role-based user config (admin role)
- Extensible for future permission levels
- User metadata (name, email, role)

### Files Created

```
~/vault/i447/i446/ai-dashboard/
├── m5x2-dashboard.py           # Main dashboard app
├── m5x2-config.json            # User/property config
├── m5x2-tag-session.py         # Session tagging helper
├── migrate-m5x2-db.py          # Database migration
├── start-m5x2.sh               # Startup script
├── M5X2-DASHBOARD-README.md    # Full documentation
├── m5x2-usage-examples.md      # Usage examples
├── m5x2-quickstart.md          # Quick reference
└── m5x2-dashboard-summary.md   # This file
```

### Database Schema

Added to `llm-sessions.db`:
```sql
ALTER TABLE sessions ADD COLUMN user_id TEXT DEFAULT 'jm';
ALTER TABLE sessions ADD COLUMN property_code TEXT;
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_property ON sessions(property_code);
```

## Quick Start

### Start Dashboard
```bash
bash ~/vault/i447/i446/ai-dashboard/start-m5x2.sh
```
Visit: **http://localhost:5556**

### Tag Sessions
```bash
# JM working on r202
python3 ~/vault/i447/i446/ai-dashboard/m5x2-tag-session.py -u jm -p r202

# LX working on fund-i
python3 ~/vault/i447/i446/ai-dashboard/m5x2-tag-session.py -u lx -p fund-i

# List recent sessions
python3 ~/vault/i447/i446/ai-dashboard/m5x2-tag-session.py --list
```

### View Data
- Dashboard: http://localhost:5556
- API: http://localhost:5556/api/stats
- Filtered: http://localhost:5556/?user=jm&property=r202

## Configuration

### Users
```json
{
  "jm": { "name": "Jonathan McKay", "email": "mckay@m5x2.com", "role": "admin" },
  "lx": { "name": "Lexi McKay", "email": "lexi@m5x2.com", "role": "admin" }
}
```

### Properties
```json
["r202", "r203", "r888", "m5c7", "portfolio", "fund-0", "fund-i",
 "fund-ii", "fund-iii", "finance", "operations", "legal", "other"]
```

### GitHub
```json
{
  "org": "mckay-capital",
  "users": ["jonathanmckay", "lexiemckay"]
}
```

## Next Steps to Track with Lexi

### 1. Verify Lexi's GitHub Username
```bash
# Update m5x2-config.json if needed
# Change "lexiemckay" to correct username
```

### 2. Set Up Lexi's Environment
1. Install Claude Code / Copilot CLI (if needed)
2. Ensure session tracking is enabled
3. Test with a simple session

### 3. Tag Lexi's First Session
```bash
# After Lexi uses Claude/Copilot
python3 m5x2-tag-session.py -u lx -p portfolio
```

### 4. Establish Workflow
- **Start dashboard daily:** `bash start-m5x2.sh`
- **Tag after each session:** `python3 m5x2-tag-session.py -u [user] -p [property]`
- **Weekly review:** Check dashboard for cost allocation

### 5. Customize Properties
Edit `m5x2-config.json` to add/remove properties as needed:
```bash
nano ~/vault/i447/i446/ai-dashboard/m5x2-config.json
# Add or remove from "properties" array
# Restart dashboard to apply changes
```

## Comparison: Personal vs m5x2

| Feature | Personal Dashboard | m5x2 Dashboard |
|---------|-------------------|----------------|
| Port | 5555 | 5556 |
| Users | Single (JM) | Multi (JM + LX) |
| Projects | None | Property codes |
| GitHub | Personal repos | Org/team repos |
| Filters | None | User + Property |
| Cost Tracking | Total only | By user, by property |
| Database | llm-sessions.db | llm-sessions.db (same) |

**Both dashboards use the same database** — just different views/filters!

## Dashboard Features Detail

### Top Bar
- User dropdown filter (All Users, JM, LX)
- Property dropdown filter (All Properties, r202, r203, etc.)
- Filters combine (can select both user AND property)

### Cards
1. **Total Usage** - Aggregate sessions, turns, costs
2. **By User** - Table showing each user's usage
3. **Team GitHub** - Commit heatmap for last 90 days
4. **By Property** - Full table of all properties
5. **30-Day Cost** - Bar chart of daily costs

### Charts
- Cost trend (last 30 days)
- GitHub heatmap (last 90 days, Green squares like GitHub's contribution graph)

### API Endpoints
- `GET /` - Dashboard HTML
- `GET /api/stats` - JSON stats (all data)
- `GET /api/stats?user=jm` - Filtered by user
- `GET /api/stats?property=r202` - Filtered by property
- `GET /api/stats?user=jm&property=r202` - Combined filter

## Tagging Workflow

### Automatic (Default)
Sessions created with `user_id='jm'` by default. No property_code set.

### Manual Tagging (Recommended)
Tag sessions as you work:
```bash
# Latest session
python3 m5x2-tag-session.py -u jm -p r202

# Specific session by ID
python3 m5x2-tag-session.py --session claude-abc123 -u lx -p fund-i

# Bulk tag all untagged
python3 m5x2-tag-session.py -u jm -p portfolio --all-untagged
```

### Reviewing Before Tagging
```bash
# List recent 10 sessions
python3 m5x2-tag-session.py --list

# List recent 20 sessions
python3 m5x2-tag-session.py --list --limit 20
```

## Testing Checklist

- [x] Database migration successful
- [x] Dashboard starts on port 5556
- [x] API returns data
- [x] Session tagging works
- [x] User filter works
- [x] Property filter works
- [x] GitHub activity loads
- [x] Cost chart renders
- [x] Config loads properly

## Known Limitations

1. **Manual tagging required** - Sessions must be tagged manually after creation
2. **No real-time updates** - Must refresh page to see new data
3. **GitHub rate limits** - May hit limits with frequent refreshes
4. **No authentication** - Anyone on localhost can access (fine for 2-person team)
5. **Cost estimates only** - Copilot input tokens unreliable (output-only costs)

## Future Enhancements (Not Built)

- Auto-prompt for tagging after sessions
- Weekly cost report emails
- Budget alerts when costs exceed threshold
- AppFolio integration (property performance correlation)
- Export to Excel
- Mobile-responsive design
- Multi-org GitHub support
- Session context search
- Time-of-day usage patterns

## Documentation Reference

- **Setup:** `M5X2-DASHBOARD-README.md`
- **Examples:** `m5x2-usage-examples.md`
- **Quickstart:** `m5x2-quickstart.md`
- **This summary:** `m5x2-dashboard-summary.md`

## Support

**Dashboard won't start:**
```bash
pkill -f m5x2-dashboard
bash start-m5x2.sh
```

**Need to reset database:**
```bash
# Backup first
cp ~/vault/i447/i446/ai-dashboard/llm-sessions.db ~/vault/i447/i446/ai-dashboard/llm-sessions.db.backup

# Re-run migration (safe to run multiple times)
python3 ~/vault/i447/i446/ai-dashboard/migrate-m5x2-db.py
```

**Sessions not appearing:**
Check that session_tracker.py is running (for Claude) or Copilot is configured to track.

## Success Metrics

After 1 week of use, you should be able to:
- ✅ See total AI costs for m5x2
- ✅ Break down costs by property (which properties use most AI)
- ✅ Break down costs by user (JM vs LX usage)
- ✅ Track team GitHub activity
- ✅ Filter views to drill down into specific work

---

**Ready to start!** Run `bash ~/vault/i447/i446/ai-dashboard/start-m5x2.sh` and tag your first session.
