---
name: "早餐"
description: "Thin alias for /ate that forces the 辰 (breakfast) time band. Usage: /早餐 <name>, <kcal>, <protein_g> [(<group> <count>, ...)]"
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
to `/ate 辰 oatmeal with flax, 350, 2 (grains 1, flax 1)`.

## Response Style

Same as `/ate`'s own report line — e.g.
`ate oatmeal with flax (350 kcal, 2g protein) → hcbi 辰 band (AN/AO/AP), row N`.
