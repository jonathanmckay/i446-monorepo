---
name: "msftshare"
description: "Shadow a vault markdown doc into Work OneDrive as a shareable Word doc. Add the 'msft' arg to flip source of truth to OneDrive and reduce the vault file to a pointer. Usage: /msftshare <doc> [msft]"
user-invocable: true
---

# MSFT Share (/msftshare)

Put a shareable Word copy of a vault doc into Work OneDrive so coworkers can read it, and optionally make OneDrive the source of truth.

## Execution

Run the helper with the args verbatim and echo its output:

```bash
python3 ~/i446-monorepo/skills/claude-skills/msftshare/msftshare.py "<doc>" [msft]
```

The helper does everything (resolve, convert, place, stub, index). Report its stdout to the user. If it exits non-zero, surface the error verbatim — do **not** retry with a guessed document; an ambiguous name is refused on purpose.

## Two modes

**`/msftshare <doc>`** — default. Vault stays source of truth.
- Resolves `<doc>` (vault-relative path, or unambiguous filename).
- Converts the markdown to `.docx` via pandoc.
- Writes it to `~/Library/CloudStorage/OneDrive-Microsoft/vault-shared/<vault-dir>/<name>.docx` (mirrors the vault folder path).
- Records `msft_shadow:` in the vault doc's frontmatter.
- Re-running refreshes the shadow.
- To actually share: in OneDrive, right-click the `.docx` → **Copy link** (wait for the sync badge to clear first). No Graph/enterprise access needed.

**`/msftshare <doc> msft`** — flip source of truth to OneDrive.
- Ensures the `.docx` exists, and preserves the original markdown as a `.md` sidecar next to it in OneDrive.
- Replaces the vault file with a **pointer stub**: a banner, a local `file://` link to the Word doc, the preserved-markdown path, and an `msft_share_url` frontmatter slot for the link you copy from OneDrive.
- Updates the folder index note (the file named after the folder) with a link under a `## MSFT-shared` heading.

## Safety / behavior notes

- **Resolution never guesses.** With 13k+ vault docs and many duplicate basenames, an ambiguous name prints the candidates and stops. Pass a vault-relative path to disambiguate.
- **msft re-runs are safe.** Once a vault file is a stub, re-running regenerates the `.docx` from the OneDrive `.md` sidecar, never from the stub. If the sidecar is missing it errors (restore from `.stversions`).
- **The sidecar is written and size-checked before the vault file is truncated** — the markdown is never lost in one step.
- **Fidelity:** pandoc drops `![[embeds]]`, renders `[[wikilinks]]` as dead text, and can't resolve relative images. The helper warns when the source contains these; fix the source or accept the lossy share.
- **Machine guard:** only runs on Straylight (where OneDrive-Microsoft is synced); errors clearly elsewhere.

## To share the link with coworkers

OneDrive web share URLs can't be auto-generated here (no enterprise Graph access). After the shadow syncs, copy the link from OneDrive and paste it into the stub's `msft_share_url` frontmatter so it lives with the doc.
