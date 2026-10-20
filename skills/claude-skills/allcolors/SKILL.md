---
name: "allcolors"
description: "Stamp completion time of all habit colors. Writes current time (HHMM) to 0n column AG (⎣∀clr) for today's row. Usage: /allcolors"
user-invocable: true
---

# All Colors (/allcolors)

Mark the moment all habit colors are completed for the day. Writes the current time as HHMM into column AG (`⎣∀clr`) of today's row in the `0n` sheet.

## Execution

Write through the excel-http daemon on Ix (journaled, numbers stay numbers;
Neon-off-Excel step 2c, 2026-10-19). The client falls back to ssh+osascript
on its own if the daemon is down; never pipe raw AppleScript here.

```bash
python3 - <<'PY'
import sys, datetime as dt
sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel
import daytime
now = daytime.local_now()
r = excel.batch_write("0n", [{"col": "AG", "value": now.hour * 100 + now.minute}],
                      date=f"{now.month}/{now.day}", src="allcolors")
res = (r.get("results") or [{}])[0]
print("OK: wrote %s to AG%s" % (res.get("value"), r.get("row")) if r.get("ok") else "ERROR: %s" % (r.get("error") or r))
PY
```

## Response

One line:

```
allcolors → HHMM (AG<row>)
```

Echo the `OK:` value back to the user. No explanation, no confirmation prompt. If the helper exits non-zero, surface the error verbatim.
