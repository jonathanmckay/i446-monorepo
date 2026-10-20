---
name: "早餐"
description: "Thin alias for /ate that forces the 辰 (breakfast) time band. Any of kcal/protein/food-groups you omit gets filled in with a best estimate. Usage: /早餐 <name>[, <kcal>[, <protein_g>]] [(<group> <count>, ...)]"
user-invocable: true
---

# Breakfast Log Proxy (/早餐)

Thin alias for [`/ate`](../ate/SKILL.md), forcing the 辰 (08:00–09:59,
breakfast) time band regardless of what time it's actually invoked.

## Execution

Prepend `辰 ` to whatever arguments follow `/早餐`, then read and follow
`/ate`'s instructions exactly against that prefixed input. Do not duplicate
the parsing/writing logic here — keep `/ate` the single source of truth so
future edits to food-logging behavior only need to happen in one place.

Example: `/早餐 oatmeal with flax, 350, 2 (grains 1, flax 1)` is equivalent
to `/ate 辰 oatmeal with flax, 350, 2 (grains 1, flax 1)`. Kcal/protein/groups
are all optional here too — `/早餐 2 eggs and toast` is equivalent to
`/ate 辰 2 eggs and toast`, and `/ate`'s own estimation step fills in
whatever's missing.

After `/ate`'s write succeeds, mark the `早餐` 0₦ habit done — `/ate` only
logs macros to `hcbi`, it has no knowledge of the `早餐` habit column, so this
skill (the one place that knows "breakfast was just logged") owns closing it:

```bash
python3 ~/i446-monorepo/tools/did/did-fast.py "早餐"
# Foreground refresh so dtd's watcher picks up the closed habit immediately
# (backgrounding it gets torn down before the ~2s refresh finishes — same
# regression class as the /0g cache-refresh note).
python3 ~/i446-monorepo/tools/did/did-fast.py --refresh-cache >/dev/null 2>&1
```

If `did-fast.py "早餐"` reports `already done today`, that's fine — don't
treat it as an error, just skip mentioning it.

## Response Style

Same as `/ate`'s own report line — e.g.
`ate oatmeal with flax (350 kcal, 2g protein) → hcbi 辰 band (AN/AO/AP), row N`.
