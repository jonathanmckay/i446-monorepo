---
name: "s"
description: "Thin alias for /salat (prayer counter). Usage: /s [count]"
user-invocable: true
---

# s Proxy (/s)

Thin alias for [`/salat`](../salat/SKILL.md), itself a proxy for `/ص`.
Identical behavior: no args → +1, number → set total.

## Execution

Run the same fast script — do NOT duplicate any logic here:

```bash
python3 ~/i446-monorepo/tools/did/salat-fast.py [count]
```

Echo its one-line output verbatim (it reads `ص: N`, not `s: N`, keeping
logs consistent with `/ص`/`/salat` invocations).

## Response Style

Minimal. One line. Do NOT explain. Do NOT ask for confirmation.
