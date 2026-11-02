---
name: "ص"
description: "Log prayers to Neon 0n tab (ص column). No args: +1. With number: set total. Usage: /ص [count]"
user-invocable: true
---

# Prayer Counter (/ص)

Log salah count to the ص column of the 0n sheet in Neon, then stamp the ☀️
marker on the current 地支 block.

## Execution

**Always run the fast script.** Do NOT write AppleScript or reason about
numerals/rows yourself; the script handles everything (non-Latin numeral
normalization — Arabic-Indic `٠١٢٣٤٥٦٧٨٩`, Persian `۰۱۲۳۴۵۶۷۸۹`, CJK
`零一二三四五六七八九`/`十` → `0123456789`, validated with `int()` — the
excel-http daemon on ix with ssh fallback, date-row lookup, column resolution
via neon-cols.json, and the local build-order ☀️ prayer marker):

```bash
python3 ~/i446-monorepo/tools/did/salat-fast.py [count]
```

- **No arguments** (`/ص`): pass no args — increments today's value by 1.
- **With a number** (`/ص 3`, `/ص ٣`): pass it verbatim — sets today's value.

Echo the script's one-line output (`ص: N`). Done. If it reports a failure
(`ص: write failed — ...`), relay the error; if it notes the prayer marker
failed, the count still landed — say so.

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
