---
name: "jmdash"
description: "Edit the personal dashboard, restart the server, and force-reload any open Chrome tabs so the change is visible immediately. Usage: /jmdash <description of edit>"
user-invocable: true
---

# Edit Personal Dashboard (/jmdash)

Make a change to the personal dashboard, then close the loop so the user sees it without lifting a finger: restart the server (module-level constants like color maps and card configs only re-load on process start) and reload any Chrome tabs already pointing at it.

## Files & endpoints

- **Source**: `~/i446-monorepo/tools/personal-dashboard/dashboard.py`
- **LaunchAgent**: `com.jm.dashboard-personal` (KeepAlive=true, lives on `ix`)
- **URL**: `http://ix:5558`
- **Refresh hook**: `POST http://ix:5558/api/refresh` invalidates the 300s in-memory data cache without a process restart.

## Decision: restart vs. /api/refresh

| Change | Action |
|--------|--------|
| Color, label, layout, HTML, JS, route handler, new constants, anything in module scope | **Restart** (`launchctl kickstart -k`) |
| Just want fresh neon values | `POST /api/refresh` only — much faster, no Chrome reload needed |

When in doubt, restart — it's cheap.

## Execution

1. **Make the edit.** Read `dashboard.py` (or whichever file the user named), apply the change with `Edit`. Confirm the diff is what the user asked for before moving on.

2. **Restart the server.** Always via `ssh ix` so the command works whether Claude is running on Ix or Straylight:

   ```bash
   ssh ix 'launchctl kickstart -k gui/$(id -u)/com.jm.dashboard-personal'
   ```

3. **Wait until the port is back.** The launchd ThrottleInterval is 10s; the server itself usually binds in 2-4s. Poll instead of sleeping a fixed amount:

   ```bash
   for i in 1 2 3 4 5 6 7 8 9 10; do
     curl -fsS -o /dev/null --max-time 1 http://ix:5558/api/data && break
     sleep 1
   done
   ```

4. **Reload any open Chrome tabs.** Local AppleScript — Chrome lives on the user's current machine, not on Ix. Match `ix:5558` and `localhost:5558` (the latter for cases where the user is on Ix and used localhost):

   ```bash
   osascript <<'OSA'
   tell application "Google Chrome"
       set reloaded to 0
       repeat with w in (every window)
           repeat with t in (every tab of w)
               set u to URL of t
               if u contains "ix:5558" or u contains "localhost:5558" then
                   reload t
                   set reloaded to reloaded + 1
               end if
           end repeat
       end repeat
       return "reloaded=" & reloaded
   end tell
   OSA
   ```

5. **Verify** (optional but cheap). If the change was a constant the API exposes — e.g. a card color — curl `/api/data` and confirm the new value is present. Useful when the user explicitly wants confirmation.

## Response

One line, terse:

```
jmdash → <one-line summary of edit> · restarted · chrome reloaded N
```

If no Chrome tab matched (`reloaded=0`), say so and suggest opening it via `/dash`. If the port poll timed out, surface the launchd log path (`/tmp/dashboard-personal.err`) instead of pretending it worked.

## Notes

- Don't `kill -9` the process — `KeepAlive=true` means launchd will respawn it anyway, but `launchctl kickstart -k` is the documented restart path and respects ThrottleInterval.
- Don't restart for changes that only touch data (e.g., editing a Neon cell). Use `/api/refresh` instead.
- AppleScript for Chrome runs **locally** on whatever machine the user is on — never wrap in `ssh ix`. The Excel `ssh ix` rule does not apply.
