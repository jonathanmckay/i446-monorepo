---
name: "push-more"
description: "Log a 'push more' moment: append the canonical points (0n!X2, currently 20) to today's row in 0分!Q (g245) and increment the push counter at 0n!X370. Usage: /push-more"
user-invocable: true
---

# Push More (/push-more)

A single ritual write: when you push past the comfortable stopping point, log it.

- **+N → 0分 today!Q** (g245/0g column) — N is read live from **0n!X2**, the
  "push" column's row-2 scoring weight, not hardcoded here. That cell is the
  canonical point value — change it there (not in this file) to change what
  a push is worth. (Changed 2026-09-08 per JM: was a hardcoded +30 to the i9
  column; now reads the weight and posts to g245 instead.)
- **+1 → 0n!X370** (push counter, fixed cell)

The 0分 write is appended to the existing formula (`=<old>+N`), not an overwrite — so repeated invocations stack the same way `/did` does.

## Execution

Two writes, split by destination sheet. Read the weight and bump the counter in the SAME AppleScript call (both touch the 0n sheet), then use that weight in the daemon call.

### 0n: read weight + bump counter (daemon)

Both through the excel-http daemon (journaled; Neon-off-Excel step 2c,
2026-10-19). Read the canonical weight from `0n!X2`, then append `+1` to the
`0n!X370` counter (row-addressed, formula append).

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel
w = excel.read("0n", "X", row=2)
if not w.get("ok"): raise SystemExit("ERROR: could not read 0n!X2: %s" % (w.get("error") or w))
weight = int(float(w["value"]))
c = excel.append("0n", "X", row=370, value="+1", src="push-more counter")
print("OK: weight=%d 0n.X370=%s" % (weight, c.get("value")) if c.get("ok") else "ERROR: counter: %s" % (c.get("error") or c))
PY
```

Parse `weight=<N>` from the output — that's the N to use below. If the read fails, stop and surface the error; do not fall back to a hardcoded number.

### 0分 write (daemon)

Writes to the 0分 sheet MUST go through the excel-http daemon on Ix — daemon writes are journaled in an audit ledger with a `src` label and chain-checked. Never raw AppleScript/ix-osa.sh for 0分. Formula-append semantics are handled server-side (empty cell, bare number, existing formula chain all normalized automatically).

```bash
ssh ix "curl -s -X POST localhost:9876/append -H 'Content-Type: application/json' \
    -d '{\"sheet\":\"0分\",\"col\":\"Q\",\"date\":\"<M/D>\",\"value\":\"+<N>\",\"src\":\"push-more bonus\"}'"
```

`<M/D>` = today's date (e.g. `7/28`). `<N>` = the weight parsed above. The response includes `"chain": "ok"|"broken"|"new"`; if `"chain": "broken"` appears, report it to the user — the cell was edited outside the daemon.

## Post-write refresh (fire-and-forget)

The dashboard caches `/api/data` for 5 minutes. After the write, ping the refresh hook so the new g245 total shows up on the next render:

```bash
curl -fsS -X POST --max-time 2 http://ix:5558/api/refresh >/dev/null 2>&1 &
disown
```

## Response

One line, terse — surface the new 0分 value from the daemon response and the counter from the AppleScript output:

```
push-more → g245 +<N> (0分.Q<row>=<new>) · push +1 (0n.X370=<new>)
```

If the daemon call fails or the helper exits non-zero (Ix unreachable), surface the error verbatim and do **not** fall back to local `osascript` — local writes cause OneDrive merge conflicts.

## Notes

- No Toggl entry, no Todoist close, no points override. This is a pure two-cell write (plus the read of the weight).
- The two writes are separate calls (AppleScript for 0n, daemon for 0分). If one succeeds and the other fails, say exactly which cell was updated and which was not.
- `0n!X370` is a fixed counter cell — does **not** look up today's row. Different from how `0₦` habit writes work.
- `0n!X2` is the row-2 "scoring weight" cell under the push header — the same convention g245/CLAUDE.md documents for the 0n sheet generally ("Headers in row 1, scoring weights in row 2"). This skill is the one place that actually reads it live; nothing else in the codebase currently derives points from row 2 automatically.
