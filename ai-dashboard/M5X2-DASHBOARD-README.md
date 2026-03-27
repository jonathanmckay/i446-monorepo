---
title: "m5x2 AI Dashboard Setup"
date: 2026-03-25
type: doc
tags: [m5x2, i446, dashboard]
---

# m5x2 AI Dashboard

Multi-user AI usage tracking dashboard for McKay Capital.

## Features

✅ **Multi-user tracking** - Track JM and LX usage separately
✅ **Property allocation** - Tag sessions by property (r202, m221, portfolio, etc.)
✅ **Cost tracking** - Real-time cost monitoring and allocation
✅ **GitHub activity** - Team commit heatmap
✅ **Portfolio overview** - Stats across all properties
✅ **Property drill-down** - Filter by user and property

## Quick Start

### 1. Run Database Migration

```bash
cd ~/vault/i447/i446/ai-dashboard
python3 migrate-m5x2-db.py
```

This adds `user_id` and `property_code` columns to the llm-sessions.db.

### 2. Start the Dashboard

```bash
python3 m5x2-dashboard.py
```

Open: http://localhost:5556

### 3. Tag Sessions

Tag your sessions with user and property:

```bash
# Tag latest session
python3 m5x2-tag-session.py --user jm --property r202

# Tag Lexi's latest session
python3 m5x2-tag-session.py --user lx --property m221

# List recent sessions to review
python3 m5x2-tag-session.py --list
```

## Configuration

Edit `m5x2-config.json` to customize:

```json
{
  "users": {
    "jm": {
      "name": "Jonathan McKay",
      "email": "mckay@m5x2.com",
      "role": "admin"
    },
    "lx": {
      "name": "Lexi McKay",
      "email": "lexi@m5x2.com",
      "role": "admin"
    }
  },
  "properties": [
    "r202",
    "m221",
    "portfolio",
    "finance",
    "operations",
    "other"
  ],
  "github": {
    "org": "mckay-capital",
    "users": ["jonathanmckay", "lexiemckay"]
  },
  "port": 5556
}
```

## Usage Workflow

### For JM

1. Start a Claude/Copilot session and work on something
2. After session, tag it:
   ```bash
   python3 m5x2-tag-session.py -u jm -p r202
   ```

### For LX

1. Start a Claude/Copilot session
2. Tag it:
   ```bash
   python3 m5x2-tag-session.py -u lx -p m221
   ```

### Viewing Data

1. Open dashboard: http://localhost:5556
2. Filter by user or property using dropdowns
3. View:
   - Total usage and costs
   - Breakdown by user
   - Breakdown by property
   - Daily cost trends
   - Team GitHub activity

## Dashboard Views

### Portfolio Overview (Default)
Shows aggregate stats across all users and properties.

### User Filter
Select a user to see only their usage.

### Property Filter
Select a property to see usage for that specific property.

### Combined Filters
Select both user AND property to drill down (e.g., "JM's work on r202").

## Session Tracking

Sessions are automatically tracked by:
- `session_tracker.py` (for Claude Code)
- Copilot CLI (native tracking)

Default user is `jm`. Use the tagging script to reassign or add property codes.

## Files

- **m5x2-dashboard.py** - Main dashboard application
- **m5x2-config.json** - User and property configuration
- **m5x2-tag-session.py** - Helper to tag sessions
- **migrate-m5x2-db.py** - Database migration script
- **llm-sessions.db** - Shared database (both dashboards use this)

## Next Steps

### To automate tagging:
Create a wrapper script that prompts for property after each session.

### To add more properties:
Edit `m5x2-config.json` and add to the `properties` array.

### To add team members:
Add them to `users` in `m5x2-config.json`.

### To track at org level:
Update `github.org` in config to your GitHub org name.

## Comparison with Personal Dashboard

| Feature | Personal (port 5555) | m5x2 (port 5556) |
|---------|---------------------|------------------|
| Users | Single (JM) | Multi-user |
| Projects | N/A | Property codes |
| GitHub | Personal repos | Org/team repos |
| Filters | None | User + Property |
| Cost breakdown | Total only | By user, by property |

Both dashboards read from the same `llm-sessions.db` database.
