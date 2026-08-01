# Skill Authoring Guide

## YAML Frontmatter — Strict Encoding Rules

Skills must use **strict YAML encoding** in their frontmatter so both Claude Code
and GitHub Copilot CLI can parse them reliably. The two tools use different YAML
parsers; loose encoding that works in one will silently break in the other.

### Rules

1. **Always double-quote string values** — especially `description`.
   ```yaml
   # ✅ correct
   description: "Quick-add a task to Todoist due today."

   # ❌ wrong — unquoted string with special chars
   description: Quick-add a task to Todoist due today. Usage: /todo <task> @tag
   ```

2. **Quote names that start with special characters** (hyphen, digit, etc.).
   ```yaml
   # ✅ correct
   name: "-1g"

   # ❌ wrong — bare hyphen prefix is ambiguous YAML
   name: -1g
   ```

3. **Escape these characters inside quoted strings** or avoid them:
   - `<` `>` (angle brackets) — always quote the whole value
   - `|` (pipe) — YAML block scalar indicator
   - `#` (hash) — YAML comment delimiter
   - `:` followed by a space — YAML key-value separator
   - `@` `!` `&` `*` `%` — YAML reserved indicators

4. **Unicode is fine** (`₦`, `分`, `中文`) — just keep the value quoted.

5. **Boolean-like words** (`yes`, `no`, `on`, `off`, `true`, `false`) must be
   quoted if you mean the literal string, not a YAML boolean.

### Minimal valid frontmatter

```yaml
---
name: "my-skill"
description: "One-line summary of what this skill does."
user-invocable: true
---
```

## Self-clearing skills

A skill is **self-clearing** when performing it IS the completion of a dtd
card, so the skill closes its own card as its final step (did-fast habit
completion or `--ritual <tag>`, followed by a FOREGROUND
`did-fast.py --refresh-cache` so an open dtd reloads at once). The user should
never have to check off a card for work a skill just did.

Current roster: `/0t` (0t card), `/0g` (0g card), `/-1g` (😈 -1g ritual card,
current block only), `/notes` (notes card), `/ibx0` (inbox habit set),
`/allcolors` (⎣∀clr stamp). When adding a skill that corresponds to a dtd
card, make it self-clearing and add it to this list.
