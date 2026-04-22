---
name: "bball"
description: "Log a basketball game to bball-log.md. Usage: /bball <shots> <pts> <opp> <len>"
user-invocable: true
---

# BBall (/bball)

Append a basketball game row to `~/vault/hcbi/hcbp/bball-log.md`.

## Response Style

**Minimal output.** Confirm in one line:
```
logged: <date> | <shots> shots | <pts>-<opp> | <len>m | <margin>
```

Do NOT explain. Do NOT ask for confirmation. Just execute.

## Usage

```
/bball <shots> <pts> <opp> <len>
```

- `<shots>` — shots taken (integer, required)
- `<pts>` — my points (integer, required)
- `<opp>` — opponent points (integer, required)
- `<len>` — game length in minutes (integer, required)

Accepts comma- or space-separated. Example: `/bball 3 4 0 15` or `/bball 3,4,0,15`.

## Steps

1. **Get today's date.** Run `date +%Y-%m-%d`.

2. **Compute margin.** `margin = pts - opp`. Prefix with `+` if >= 0, else `-`.

3. **Read** `~/vault/hcbi/hcbp/bball-log.md`.

4. **Append a row** to the end of the markdown table:

   ```
   | YYYY-MM-DD | <shots> | <pts> | <opp> | <len> | <±margin> |
   ```

   Pad each cell so column widths match the header (visual alignment only; markdown renders fine either way).

5. **Update frontmatter `updated:`** to today's date.

6. **Report:**
   ```
   logged: YYYY-MM-DD | <shots> shots | <pts>-<opp> | <len>m | <±margin>
   ```
