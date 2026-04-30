---
name: "bball"
description: "Log a basketball game to bball-log.md. Usage: /bball <shots> <pts> <opp> <len> [notes...]"
user-invocable: true
---

# BBall (/bball)

Append a basketball game row to `~/vault/hcbi/hcbp/bball-log.md`.

## Response Style

**Minimal output.** Confirm in one line:
```
logged: <date> | <shots> shots | <pts>-<opp> | <len>m | <margin>[ | <notes>]
```

Do NOT explain. Do NOT ask for confirmation. Just execute.

## Usage

```
/bball <shots> <pts> <opp> <len> [notes...]
```

- `<shots>` — shots taken (integer, required)
- `<pts>` — my points (integer, required)
- `<opp>` — opponent points (integer, required)
- `<len>` — game length in minutes (integer, required)
- `[notes]` — optional free-text notes (everything after the 4th integer)

The first four tokens are integers (comma- or space-separated). Everything after token 4 is treated as notes — a free-form string.

Examples:
- `/bball 3 4 0 15`
- `/bball 3,4,0,15`
- `/bball 3 4 0 15 vs Theo, hot from 3`
- `/bball 3,4,0,15 missed two layups`

If notes contain `|`, escape as `\|` so the markdown table doesn't break.

## Steps

1. **Parse args.** Pull the first four integers; the rest of the string (trimmed) is notes. Notes may be empty.

2. **Get today's date.** Run `date +%Y-%m-%d`.

3. **Compute margin.** `margin = pts - opp`. Prefix with `+` if >= 0, else `-`.

4. **Read** `~/vault/hcbi/hcbp/bball-log.md`.

5. **Append a row** to the end of the markdown table:

   ```
   | YYYY-MM-DD | <shots> | <pts> | <opp> | <len> | <±margin> | <notes> |
   ```

   Pad cells for alignment; leave the notes cell empty (with whitespace) if no notes were provided.

6. **Update frontmatter `updated:`** to today's date.

7. **Report:**
   ```
   logged: YYYY-MM-DD | <shots> shots | <pts>-<opp> | <len>m | <±margin>[ | <notes>]
   ```

   Include the `| <notes>` segment only if notes were provided.
