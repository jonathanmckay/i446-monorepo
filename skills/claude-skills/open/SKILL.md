---
name: "open"
description: "Find and open a file in the vault FAST via Spotlight. Resolves a query to the best vault file and opens it with the right app (PDF→Preview, .md→Obsidian, web/HTML→Chrome). Use this instead of slow `find ~` walks whenever the user says 'open the X'. Usage: /open <query>"
user-invocable: true
---

# Open a vault file (/open)

The vault (`~/vault`) is **150k+ files, ~70% of them `.stversions` junk**. Never
`find ~ -iname ...` (multi-minute cold walk). Use the Spotlight-backed resolver —
~0.4s, searches filenames AND content.

## Steps

1. **Resolve candidates** (fast):
   ```bash
   python3 ~/i446-monorepo/tools/open/vopen.py <query>
   ```
   Output is `TAG\tPATH`, ranked: `FILE` (filename match) before `TEXT` (content
   match), with derived/junk locations (`.stversions`, `ai-transcripts`,
   `dream-runs`, `readwise`, `z_old`) deprioritized. Add `--names` for
   filename-only matches when the query is clearly a file/title.

2. **Pick the best.** Prefer the **openable artifact the user means** — the
   `.pdf` / `.md` / `.html` they'd actually want — in the most **canonical**
   location (e.g. `h335/i9/xbox/...`, not a `recordings/*.txt` transcript or a
   `build.py` source file). If a query names a dashboard/app, open its rendered
   artifact (e.g. `index.html`), not its source.

3. **Open with the right app by extension:**
   | Ext | Command |
   |-----|---------|
   | `.pdf` | `open -a "Preview" "<path>"` |
   | `.md` | `open "obsidian://open?path=<urlencoded-abs-path>"` — ALWAYS the URI form. `open -a "Obsidian" "<path>"` silently fails to load the file on filenames with special chars (`≥`, `₦`, CJK, spaces): it focuses the app but the doc never opens (verified 2026-07-04) |
   | `.html` `.htm` / any URL | `open -a "Google Chrome" "<path>"` |
   | anything else | `open "<path>"` (system default app) |

   **Never** use a bare `open` on a URL or `.html` — the cmux shim hijacks web
   content into a cmux surface; always route web/HTML to Chrome. (See the
   `open-links-chrome` memory.)

4. **Confirm in one line:** `opened <basename> → <app>`.

## Disambiguation

If two or more candidates are plausibly what the user meant (e.g. a memo and its
`-no-europa` variant, or English vs 中文 versions), open the single best guess and
**name the alternatives in one line** so the user can redirect — don't block with a
question unless genuinely 50/50.

## Notes

- This skill is the fast path for **"open the X"** requests. Outside `/open`,
  still reach for `vopen.py` / `mdfind -onlyin ~/vault` rather than `find ~`.
- Content grep in the vault: `rg --glob '!.stversions' <pattern> ~/vault` (ripgrep
  is installed; `fd` is not).
