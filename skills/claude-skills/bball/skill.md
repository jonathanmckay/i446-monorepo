---
name: "bball"
description: "Log a basketball game to bball.md. Usage: /bball <shots> <pts> <opp> <len> [notes...]"
user-invocable: true
---

# BBall (/bball)

Append a basketball game row to `~/vault/hcbi/hcbp/bball.md`.

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

4. **Read** `~/vault/hcbi/hcbp/bball.md`.

5. **Append a row** to the end of the **2026 table** in the Game Log section (the last year table before "## Shooting Practice"):

   ```
   | M.DD | <shots> | <pts> | <opp> | <±net> | | | <notes> |
   ```

   Date format is `M.DD` (e.g. `5.3`). Net = pts - opp, prefixed with `+` if >= 0. Players and Location columns left empty (the user can fill them in later). Leave the notes cell empty if no notes were provided.

6. **Update frontmatter `updated:`** to today's date.

7. **Report:**
   ```
   logged: YYYY-MM-DD | <shots> shots | <pts>-<opp> | <len>m | <±margin>[ | <notes>]
   ```

   Include the `| <notes>` segment only if notes were provided.
