# AI Usage Tracking — Setup

## Prerequisites

- Claude Code installed
- SSH access to the `m5x2` GitHub org (`m5x2/ai-stats` repo)

## One-time setup

Run this in Terminal from the folder containing this file:

```bash
bash setup-ai-tracking.sh lx
```

That's it. After running it, your Claude Code stats will automatically sync to the shared repo after every session — no further action needed day-to-day.

**What it does:**
1. Clones `m5x2/ai-stats` to `~/m5x2-ai-stats/`
2. Adds your user ID (`lx`) to your shell profile
3. Installs a hook in Claude Code that syncs stats on session end

## Optional — tag sessions to a property

```bash
python3 m5x2-tag-session.py -u lx -p r202
```
