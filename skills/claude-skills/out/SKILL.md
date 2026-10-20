---
name: "out"
description: "Log outdoor/outside score to hcbi AI column in Neon. Usage: /out <score>"
user-invocable: true
---

# Log Outdoor Score (/out)

Write a value to the `AI` column in the `hcbi` sheet of Neon for today.

## Usage

```
/out <score>
```

Common values: `10` (went outside), `-1` (didn't go out).

## Response Style

**Minimal output.** Confirm in one line:
```
out → 10 (hcbi AI, row 113)
```

## Steps

1. **Write to hcbi AI column** through the excel-http daemon (`neon.excel.batch_write`)
   (executes the AppleScript on Ix). Do NOT call local `osascript`.

   - Workbook: `Neon分v12.2.xlsx`
   - Sheet: `hcbi`
   - Column: `AI` (idempotently write header "AI" to AI1 on each run)
   - Date column: `B` (M/D format)
   - Find today's row, write the score value

2. **Report** the result.

## Write (excel-http daemon)

Through the daemon on Ix (journaled; Neon-off-Excel step 2c, 2026-10-19). The
client falls back to ssh+osascript by itself if the daemon is down. Substitute
`SCORE` with the argument.

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel
import daytime
t = daytime.today()
r = excel.batch_write("hcbi", [{"col": "AI", "value": SCORE}], date=f"{t.month}/{t.day}", src="out score")
print("OK: wrote SCORE to AI%s" % r.get("row") if r.get("ok") else "ERROR: %s" % (r.get("error") or r))
PY
```

## Notes

- All writes go through the excel-http daemon on Ix (journaled). If
  Ix is unreachable the helper exits 3 with a clear error — do NOT
  fall back to local `osascript`. Local writes cause OneDrive merge
  conflicts against the canonical workbook on Ix.
- Neon must be open on Ix.
