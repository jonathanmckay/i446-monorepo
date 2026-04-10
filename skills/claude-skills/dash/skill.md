---
name: "dash"
description: "Open all local dashboards (personal, AI, stats) in Chrome."
user-invocable: true
---

# Open Dashboards (/dash)

Opens all three local dashboards in Chrome.

## Dashboards

| Name | URL | Source |
|------|-----|--------|
| Personal Dashboard | http://localhost:5558 | `i446-monorepo/tools/personal-dashboard/dashboard.py` |
| AI Dashboard (m5x2) | http://localhost:5556 | `i446-monorepo/tools/ai-dashboard/m5x2-dashboard.py` |
| AI Stats Dashboard | http://localhost:5555 | `i446-monorepo/tools/ai-dashboard/dashboard.py` |

## Execution

Open all three URLs in Chrome:

```bash
open -a "Google Chrome" "http://localhost:5558" "http://localhost:5556" "http://localhost:5555"
```

If any dashboard isn't running (port not listening), report which one is down instead of silently failing.

## Response

Confirm in one line:
```
Opened 3 dashboards (personal :5558, ai :5556, stats :5555)
```

Or if some are down:
```
Opened 2/3 dashboards — stats :5555 is not running
```
