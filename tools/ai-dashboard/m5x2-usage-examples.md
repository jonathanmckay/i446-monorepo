---
title: "m5x2 Dashboard Usage Examples"
date: 2026-03-25
type: doc
tags: [m5x2, i446, examples]
---

# m5x2 Dashboard Usage Examples

## Daily Workflow

### Morning - Start Work Session

```bash
# Open terminal, start dashboard
bash ~/vault/i447/i446/ai-dashboard/start-m5x2.sh

# In browser: http://localhost:5556
```

### During Work - Tag Your Sessions

**JM working on r202 property:**
```bash
# After using Claude/Copilot for r202 work
python3 m5x2-tag-session.py -u jm -p r202
```

**LX working on m221 property:**
```bash
# After using Claude/Copilot for m221 work
python3 m5x2-tag-session.py -u lx -p m221
```

**Working on portfolio-wide tasks:**
```bash
python3 m5x2-tag-session.py -u jm -p portfolio
```

**Working on finance/accounting:**
```bash
python3 m5x2-tag-session.py -u jm -p finance
```

### End of Day - Review Usage

```bash
# List today's sessions
python3 m5x2-tag-session.py --list --limit 10

# Check dashboard for cost breakdown
# Visit: http://localhost:5556
```

## Bulk Tagging

### Tag all untagged sessions for a property
```bash
# If you forgot to tag sessions, catch them up
python3 m5x2-tag-session.py -u jm -p r202 --all-untagged
```

### Tag specific session by ID
```bash
# Find session ID from --list, then tag it
python3 m5x2-tag-session.py --session claude-abc123 -u lx -p m221
```

## Dashboard Views

### View all usage (portfolio overview)
Visit: http://localhost:5556

### View only JM's usage
http://localhost:5556/?user=jm

### View only r202 property
http://localhost:5556/?property=r202

### View JM's work on r202 specifically
http://localhost:5556/?user=jm&property=r202

### View LX's work on m221
http://localhost:5556/?user=lx&property=m221

## Weekly/Monthly Reviews

### Export costs by property (API)
```bash
curl http://localhost:5556/api/stats | jq '.by_property'
```

### Export costs by user (API)
```bash
curl http://localhost:5556/api/stats | jq '.by_user'
```

### Get specific user stats (API)
```bash
curl "http://localhost:5556/api/stats?user=jm" | jq '.stats'
```

## Common Scenarios

### Scenario 1: Research session (not property-specific)
```bash
python3 m5x2-tag-session.py -u jm -p other
```

### Scenario 2: Working across multiple properties
Tag each session separately as you switch context:
```bash
# After working on r202
python3 m5x2-tag-session.py -u jm -p r202

# Later, after working on m221
python3 m5x2-tag-session.py -u jm -p m221
```

### Scenario 3: LX's first session
```bash
# Ensure Lexi's GitHub username is in config
# Then tag her session
python3 m5x2-tag-session.py -u lx -p portfolio
```

### Scenario 4: Review untagged sessions
```bash
# List recent sessions to see what needs tagging
python3 m5x2-tag-session.py --list --limit 20

# Tag them individually or in bulk
python3 m5x2-tag-session.py --session <id> -u jm -p r202
```

## Tips

1. **Tag right after sessions** - Easier to remember what you were working on
2. **Use --list frequently** - Review what needs tagging
3. **Use property codes consistently** - r202, m221, portfolio, finance, operations, other
4. **Check dashboard weekly** - Review cost allocation across properties
5. **Refresh dashboard** - Click refresh button after tagging to see updates

## Automation Ideas

### Auto-prompt for tagging (future)
Create a wrapper that prompts after each Claude session:
```bash
# After Claude session ends, automatically ask:
# "Which property? [r202/m221/portfolio/finance/operations/other]"
# "Which user? [jm/lx]"
# Then auto-tag
```

### Weekly cost report (future)
```bash
# Send weekly email with costs by property and user
# via cron job or GitHub Action
```
