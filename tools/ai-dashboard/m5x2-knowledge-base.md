---
title: "m5x2 Knowledge Base"
date: 2026-03-01
type: doc
tags: [i446, m5x2, tools]
source: manual
status: active
updated: 2026-03-02
---
Static HTML knowledge base for McKay Capital SOPs and roadmaps.

## URLs
- **Live site:** https://jonathanmckay.github.io/m5x2-kb/
- **GitHub repo:** https://github.com/jonathanmckay/m5x2-kb

## Local paths
- **Build script:** `~/i446-monorepo/m5x2-kb/build.py`
- **Generated HTML:** `~/i446-monorepo/m5x2-kb/index.html`
- **Pages deploy repo:** `~/m5x2-kb-deploy/` (clone of jonathanmckay/m5x2-kb)
- **Drive copy:** `~/vault/h335/m5x2/drive-main/SOPs/index.html`
- **SOP owners config:** `~/vault/h335/m5x2/drive-main/SOPs/sop-owners.json`
- **Vault symlink:** `~/vault/i447/i446/ai-dashboard/m5x2-kb` → monorepo

## How it works
`build.py` scans the Drive SOPs folder (`~/vault/h335/m5x2/drive-main/SOPs/`), reads `.gddoc` files (which contain Google Docs URLs as JSON), categorizes them, and generates a self-contained `index.html`.

### Data sources
| Source | What |
|---|---|
| `drive-main/SOPs/**/*.gddoc` | SOP documents — URLs extracted from JSON |
| `drive-main/**/Roadmap*` | Current quarter roadmaps (filtered to 26.Q1) |
| `drive-main/SOPs/sop-owners.json` | POC assignments per SOP |
| File modification time | "Last Edit" column |

### Categories
Roadmaps & Goals, Leasing & Tenants, Work Orders & Maintenance, HR & People, Safety & Compliance, Finance & Admin, Guest & Property Ops, Reference.

Categorization rules are hardcoded in `build.py` `CATEGORY_RULES`. New SOPs in existing Drive subfolders (A. R202, B. R203, C. R888, D. H5c7) are auto-categorized. SOPs that don't match any rule fall into Reference.

## Update workflow
```bash
# 1. Rebuild the site (picks up new/renamed SOPs from Drive)
python3 ~/i446-monorepo/m5x2-kb/build.py

# 2. Deploy to GitHub Pages
cp ~/i446-monorepo/m5x2-kb/index.html ~/m5x2-kb-deploy/index.html
cd ~/m5x2-kb-deploy && git add -A && git commit -m "update" && git push

# 3. Commit source changes to monorepo
cd ~/i446-monorepo && git add -A && git commit -m "update m5x2-kb" && git push
```

Insync syncs Google Drive → `drive-main/`, so new SOPs added in Drive appear automatically after Insync syncs.

## Assigning SOP owners
Edit `sop-owners.json` in the shared Drive SOPs folder. Map SOP relative path to a name:

```json
{
  "A. R202/SOP-Application Screening Process.gddoc": "Sarah",
  "B. R203/SOP: Work Orders processing (r203).gddoc": "Mike"
}
```

Then rebuild + push.

## Roadmap naming convention
All current-quarter roadmaps use format: `26.Q1 [Name] Roadmap ([Entity]).gddoc`

Chart-only `.gdsheet` files are excluded from the index (linked from their parent roadmap doc instead).

## Analytics
`ANALYTICS_URL` in `build.py` — set to a Google Apps Script web app URL to enable page view tracking. Currently disabled. See `~/m5x2-kb/analytics-apps-script.js` for setup instructions.
