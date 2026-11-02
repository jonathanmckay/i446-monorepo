# /did Neon write reference

Write recipes for the /did background agent (Steps 2, 2b, 5, 1n). Read this
file only when executing /did steps in the agent; `run.py` / `did-fast.py`
already do all of this on the fast path.

**Daemon only (Neon-off-Excel step 2, 2026-10-19; this file migrated
2026-10-30).** Every write to 0分, 0n, 1n+ and hcbi goes through the
excel-http daemon client `lib/neon/excel.py` with a `src` label, so it is
journaled in the neon ledger (`~/vault/g245/neon-ledger/`). The client falls
back to a journaled ssh+osascript write on its own when the daemon is down.
Never pipe raw AppleScript for these sheets through `ix-osa.sh`, and never
run local `osascript`: a raw write is invisible to the ledger and trips the
chain check as "cell modified outside the daemon". To bless a deliberate
manual edit: `python3 ~/i446-monorepo/scripts/neon-ledger-audit.py --ack '0分!R' --date M/D --note '<why>'`.
Column letters come from `neon.cols` (never hard-code them).

Every snippet starts with:

```python
import sys; sys.path.insert(0, "/Users/mckay/i446-monorepo/lib")
from neon import excel, cols, weeks
```

## Substitution variables

- `TARGET_DATE` → M/D date string (e.g. `4/7`)
- `HABIT` → habit header exactly as in row 1 (e.g. `hiit`, `1 xk88`)
- `TIME` → number written to the habit cell
- `POINTS` → number appended to 0分

## Write to 0₦ (Step 2)

```python
r = excel.batch_write("0n", [{"col": cols.col("0n", "HABIT"), "value": TIME}],
                      date="TARGET_DATE", src="did HABIT")
print(r["results"][0]["value"] if r.get("ok") else r)
```

Cumulative columns (问学 and other `mode: "add"` habits): add `"mode": "add"`
to the write item; the daemon adds onto the existing number (a blank/0 cell
is just set). `add` needs the daemon; on `fallback: True` with an
`add_mode_needs_daemon` error, stop and report rather than overwriting.

## 0l completion time (Step 2b)

Only for habit `0l`, only for today's row, only when 0t is already done:

```python
from datetime import datetime
ot = excel.read("0n", cols.col("0n", "0t"), date="TARGET_DATE")
if ot.get("ok") and ot.get("value") not in ("", "0", "0.0"):
    now = datetime.now()
    excel.batch_write("0n", [{"col": cols.col("0n", "N color"), "value": now.hour * 100 + now.minute}],
                      date="TARGET_DATE", src="did 0l time")
```

## Append to 0分 (Step 5)

```python
col = cols.domain_col("0分", "<domain>")
r = excel.append("0分", col, date="TARGET_DATE", value="+POINTS", src="did <task>")
print(r.get("formula"), r.get("chain"))
```

The daemon normalizes the formula append (blank cell, bare number, existing
chain). If the response has `"chain": "broken"`, report it: the cell was
edited outside the daemon.

## 1n+ write (Step 1n)

The week row is addressed by its fiscal M.W label in 1n+ col B
(`weeks.fiscal_week_label`, a 4-4-5 ladder; never `ceil(day/7)`). The cell
gets the habit's row-3 point value:

```python
from datetime import date
col = cols.col("1n+", "HABIT")
mw = weeks.habit_week_label("HABIT", date.today())
pts = excel.read("1n+", col, row=3)["value"]
r = excel.batch_write("1n+", [{"col": col, "value": float(pts)}], date=mw, src="did HABIT")
week_row = r.get("row")
```

## 1n+ → 0分 cell reference (Step 1n, part 2)

```python
excel.append("0分", cols.to_0fen_col("HABIT"), date="TARGET_DATE",
             value=f"+'1n+'!{col}{week_row}", src="did HABIT")
```

## Todoist API

Token: never paste it here. Read it the way `lib/todoist.py` does:
`~/.config/todoist/token`, else `$TODOIST_TOKEN` / `$TODOIST_API_TOKEN`
(scripts on Ix also take `$TODOIST_API_KEY` or the `todoist-api-key`
keychain item). Prefer `lib/todoist.py`'s helpers over raw HTTP.

```bash
TOKEN=$(cat ~/.config/todoist/token)
curl -s -H "Authorization: Bearer $TOKEN" "https://api.todoist.com/api/v1/tasks?label=0neon&limit=200"
```

- Search 0neon: `GET /api/v1/tasks?label=0neon&limit=200`
- Search 1neon: `GET /api/v1/tasks?label=1neon&limit=200`
- Close task: `POST /api/v1/tasks/TASK_ID/close`
- Create task: `POST /api/v1/tasks` with JSON body `{content, labels, project_id, due_date}`

Base URL: `https://api.todoist.com`
