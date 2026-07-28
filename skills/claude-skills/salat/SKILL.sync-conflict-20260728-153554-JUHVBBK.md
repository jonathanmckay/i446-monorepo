---
name: "salat"
description: "Proxy for /ص (prayer counter) — use when Copilot can't input Arabic. Usage: /salat [count]"
user-invocable: true
---

# Salat Proxy (/salat)

This is a thin alias for [`/ص`](../ص/SKILL.md). Use it when the Copilot CLI can't input the Arabic letter ص on the current keyboard / IME / client.

## Behavior

Identical to `/ص`:

- **No arguments** (`/salat`): increment today's value by 1.
- **With a number** (`/salat 3`): set today's value to that number.

## Execution

Read and follow the instructions in `~/.copilot/skills/ص/SKILL.md` exactly. All argument-parsing, numeral normalization, AppleScript, and output rules are defined there. Do not duplicate the logic here — keep ص as the single source of truth so future edits to the prayer-counter behavior only need to happen in one place.

Output line should still read `ص: N` (not `salat: N`) to keep logs consistent with `/ص` invocations.
