---
name: "salat"
description: "Proxy for /ص (prayer counter) — use when Copilot can't input Arabic. Usage: /salat [count]"
user-invocable: true
---

# Salat Proxy (/salat)

Thin alias for [`/ص`](../ص/SKILL.md), for keyboards/IMEs that can't type ص.
Identical behavior: no args → +1, number → set total.

## Execution

Run the same fast script — do NOT duplicate any logic here:

```bash
python3 ~/i446-monorepo/tools/did/salat-fast.py [count]
```

Echo its one-line output verbatim (it reads `ص: N`, not `salat: N`, keeping
logs consistent with `/ص` invocations).

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
