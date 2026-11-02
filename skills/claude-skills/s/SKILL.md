---
name: "s"
description: "Prayer counter: log prayers to the Neon 0n tab (ص column). No args: +1. With number: set total. Usage: /s [count]"
user-invocable: true
---

# Prayer counter (/s)

No args → +1, number → set total. `/salat` and `/ص` were retired to
`_archive/` on 2026-11-01; `/s` is the only prayer-counter skill.

## Execution

Run the fast script — do NOT duplicate any logic here:

```bash
python3 ~/i446-monorepo/tools/did/salat-fast.py [count]
```

Echo its one-line output verbatim (it reads `ص: N`, not `s: N`, keeping
logs consistent with earlier `/ص` invocations).

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
