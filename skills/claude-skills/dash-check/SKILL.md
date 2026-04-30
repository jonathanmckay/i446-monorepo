---
name: "dash-check"
description: "Health check for personal dashboard data sources. Verifies all feeds are live. Usage: /dash-check"
user-invocable: true
---

# Dashboard Health Check (/dash-check)

Verify all data sources feeding the JM personal dashboard (localhost:5558) and AI dashboard (localhost:5555) are operational.

## Usage

```
/dash-check
```

No arguments.

## Checks

Run all checks in parallel where possible:

### 1. Dashboard processes

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5558/ 2>/dev/null
curl -s -o /dev/null -w "%{http_code}" http://localhost:5555/ 2>/dev/null
```

- 200 = running
- Otherwise = down. Report: `launchctl list | grep dashboard` to show agent status.

### 2. Points cache freshness

```bash
stat -f "%m" ~/i446-monorepo/tools/personal-dashboard/.points-cache.json
stat -f "%m" ~/OneDrive/vault-excel/Neon-current.xlsx
```

- Cache newer than Excel = fresh
- Cache older than Excel = stale (dashboard will auto-refresh on next load, but report the lag)
- Cache missing = warn

### 3. Toggl API

```bash
curl -s -u "$TOGGL_API_KEY:api_token" "https://api.track.toggl.com/api/v9/me" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('fullname','OK'))"
```

- Success = show username
- Fail = API key missing or expired

### 4. Todoist API

```bash
curl -s -H "Authorization: Bearer 7eb82f47aba8b334769351368e4e3e3284f980e5" "https://api.todoist.com/rest/v2/tasks?limit=1" 2>/dev/null | python3 -c "import json,sys; print('OK' if isinstance(json.load(sys.stdin), list) else 'FAIL')"
```

### 5. iMessage response DB

```bash
stat -f "%Sm" ~/vault/i447/i446/imsg-responses.db 2>/dev/null
```

- Report last modified time. If >24h old, warn about stale response data.

### 6. Email stats gist

```bash
curl -s "https://api.github.com/gists/7c08fd1a83c8f3bbab3917bdb3d33df1" | python3 -c "import json,sys; g=json.load(sys.stdin); print(g['updated_at'])"
```

- Report last update time. If >48h old, warn.

### 7. GA4 (o315 pageviews)

```bash
ls ~/i446-monorepo/tools/personal-dashboard/ga4-tokens.json 2>/dev/null
```

- File exists = tokens present (may still be expired)
- Missing = "Run localhost:5558/auth/ga4 to authenticate"

### 8. Ix connectivity

```bash
ssh -o ConnectTimeout=3 -o BatchMode=yes ix echo OK 2>/dev/null
```

- OK = connected
- Fail = warn. Check for queued writes: `wc -l ~/.claude/ix-write-queue.jsonl 2>/dev/null`

### 9. AI dashboard (turns)

```bash
curl -s http://localhost:5555/api/turns | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} days')"
```

## Report format

```
Dashboard Health Check
======================

Personal dashboard (5558):  OK
AI dashboard (5555):        OK
Points cache:               fresh (2m ago)
Toggl API:                  OK (McKay Jensen)
Todoist API:                OK
iMessage response DB:       OK (updated 3h ago)
Email stats gist:           OK (updated 18h ago)
GA4 (o315):                 MISSING — run localhost:5558/auth/ga4
Ix connectivity:            OK (0 queued writes)
AI turns:                   OK (30 days)
```

Use color indicators: green checkmark for OK, red X for failures, yellow warning for stale data.
