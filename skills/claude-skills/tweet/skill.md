---
name: "tweet"
description: "Add a micro-post to personal-twitter.md. Usage: /tweet <thought>"
user-invocable: true
---

# Tweet (/tweet)

Append a micro-post to `~/vault/hcmp/o314/personal-twitter.md`.

## Response Style

**Minimal output.** Confirm in one line:
```
tweeted: <thought>
```

Do NOT explain what you're doing. Do NOT ask for confirmation. Just execute.

## Usage

```
/tweet <thought>
```

- `<thought>` — a short, tweet-length thought (required)

## Steps

1. **Get today's date.** Run `date +%Y-%m-%d` to get the current date.

2. **Read the file.** Read `~/vault/hcmp/o314/personal-twitter.md`.

3. **Insert the new entry.** Add a new line immediately after the description line ("Micro-posts from the journal..."), before the first existing entry:

   ```
   **YYYY-MM-DD** — <thought>
   ```

4. **Update the `updated:` field** in the frontmatter to today's date.

5. **Report:**
   ```
   tweeted: <thought>
   ```
