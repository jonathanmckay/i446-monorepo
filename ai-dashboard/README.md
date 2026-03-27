# AI Dashboards

This folder contains AI usage tracking dashboards for both personal and m5x2 (McKay Capital) use.

## Dashboards

### Personal Dashboard (dashboard.py)
- **Port:** 5555
- **Usage:** Personal AI usage tracking
- **Run:** `python3 dashboard.py`
- **URL:** http://localhost:5555

### m5x2 Dashboard (m5x2-dashboard.py)
- **Port:** 5556
- **Usage:** Multi-user tracking for McKay Capital
- **Run:** `bash start-m5x2.sh`
- **URL:** http://localhost:5556

## Quick Start

### Start m5x2 Dashboard
```bash
cd ~/vault/i447/i446/ai-dashboard
bash start-m5x2.sh
```

### Tag Sessions
```bash
cd ~/vault/i447/i446/ai-dashboard

# Tag latest session
python3 m5x2-tag-session.py -u jm -p r202

# List recent sessions
python3 m5x2-tag-session.py --list
```

## Documentation

- **m5x2-quickstart.md** - Quick reference guide
- **M5X2-DASHBOARD-README.md** - Full setup documentation
- **m5x2-usage-examples.md** - Usage examples
- **m5x2-dashboard-summary.md** - Build summary

## Files

- `dashboard.py` - Personal dashboard
- `m5x2-dashboard.py` - m5x2 multi-user dashboard
- `m5x2-config.json` - Configuration (users, properties, GitHub)
- `m5x2-tag-session.py` - Session tagging tool
- `migrate-m5x2-db.py` - Database migration script
- `start-m5x2.sh` - Startup script for m5x2 dashboard

## Database

Both dashboards use: `~/vault/i447/i446/llm-sessions.db`

The database is shared but the dashboards provide different views:
- Personal: Single user view
- m5x2: Multi-user with property allocation
