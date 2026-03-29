# Vault Improvements - Remaining Tasks

Created: 2026-03-28

## Completed ✅
1. ✅ Glossary - z_meta/glossary.md
2. ✅ READMEs - g245, hcmp, h335, m5x2
3. ✅ Topic indexes - Chinese learning, career transitions, parenting
4. ✅ Tag taxonomy - z_meta/tag-taxonomy.md

## Remaining Tasks

### 5. ✅ d359/CLAUDE.md - People Doc Format (15 min)

**Location:** `/Users/mckay/vault/d359/CLAUDE.md`

**Content needed:**
- People doc format and conventions
- Profile section at top (kids, hobbies, work context)
- Meeting notes below with YYYY.MM.DD headers
- Never push profile section down
- Examples of good people docs
- When to create new docs
- Naming conventions

**Current state:** Format documented in memory/MEMORY.md but not in vault

---

### 6. ✅ Entry Templates (20 min)

**Location:** `/Users/mckay/vault/z_meta/templates/`

**Templates to create:**

#### journal-entry.md
```yaml
---
title: "Entry Title"
date: YYYY-MM-DD
type: journal
tags: [o314, domain, topic]
source: manual
---

# Entry content here
```

#### review.md
```yaml
---
title: "Media Title"
date: YYYY-MM-DD
type: review
tags: [hcmc, review, media-type]
media: book | film | tv | game
author: "Author Name"
score: 1-5
source: manual
---

# Review content
```

#### people-doc.md (d359)
```yaml
---
title: "Person Name"
date: YYYY-MM-DD
type: people
tags: [d359, relationship, organization]
source: manual
---

# Profile

**Context:** Role, relationship, how we know each other

**Kids:** (if applicable)
**Hobbies:** (if known)
**Work:** Current role/company

---

# YYYY.MM.DD - Meeting/Interaction Title

Notes...
```

#### meeting-note.md (d358)
```yaml
---
title: "Meeting Title"
date: YYYY-MM-DD
type: meeting
tags: [d358, domain, meeting-type]
attendees: [names]
source: manual
---

# Agenda

# Notes

# Action Items
```

#### annual-review.md
```yaml
---
title: "YYYY Annual Review"
date: YYYY-12-31
type: review
tags: [g245, review, annual]
source: manual
---

# Year in Review: YYYY

## Themes

## Accomplishments

## Lessons Learned

## Goals for Next Year
```

---

### 7. o314 Pre-2010 Multi-Entry File Splitting (ongoing)

**Tracker:** `/Users/mckay/vault/hcmp/o314/split-remaining.md`

**Resume with:** "Continue splitting o314 multi-entry files — read split-remaining.md for context"

**What:** Split pre-2010 journal files that contain multiple entries (pasted from Word) into individual .md files with proper frontmatter. Delete originals + `-1` Obsidian sync backups. Update year indexes.

**Status:** 2005, 2007, 2008 Feb/Mar, 2009 Jan done. ~7 multi-entry files remaining in 2008 (Apr–Dec), ~12 in 2009 (Feb–Dec), plus `-1` backup cleanup and year index updates.

---

## Future Improvements (Lower Priority)

- Add more domain READMEs (qz12, i447, xk88, etc.)
- Standardize frontmatter across old entries
- Create more topic indexes as themes emerge
- Cross-linking script/tool
- Review and consolidate tags quarterly

---

## Notes

- Session ended 2026-03-28
- Resume with: "Continue vault improvements - d359/CLAUDE.md and templates"
- All 5 quick wins completed except templates
- Vault is now much more AI-friendly and documented
