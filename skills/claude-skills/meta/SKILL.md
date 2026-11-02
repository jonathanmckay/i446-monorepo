---
name: "meta"
description: "Answer a question about the inner loop itself: how skills, apps and agents tie together for picking tasks, doing them, tracking time and crediting points, for JM and for agents. Maps the question onto the live system, names the seam, recommends one change. Read-only unless asked. Usage: /meta <question>"
user-invocable: true
---

# /meta — questions about the loop, not in it

The user is asking about the machinery: how the skills, apps, daemons and
agents fit together (or fail to) in the loop of **pick → do → track → close
→ review**. They want an architect's answer, not a tour. Find where their
question lands in the system, check that part against the real code, and
give one opinion.

## Arguments

`/meta <question>`. Examples:
- "why do agent sessions not show up in Toggl?"
- "should /do and /d be one skill?"
- "where do points get lost between dtd and Neon?"
- "what's the cleanest way for an agent to log its own time?"

No question means: audit the loop for its single worst seam right now.

## The map (starting point; verify before citing)

| Stage | Human surface | Agent surface | Engine | Source of truth |
|---|---|---|---|---|
| Capture | `/todo`, `/notes`, `/ibx`, `/inbound` | `/d new`, `/inkwelltodo` | Todoist MCP / API | Todoist |
| Plan | `/0g`, `/-1g`, `/1g`, `/1-2g` audit | — | `scripts/0g-sync.py`, `-1g-cron.py` (Ix cron) | build order ↔ Todoist `#0g`/`#-1g` |
| Pick | dtd (fzf TUI `tools/did/dtd.sh`, phone `tools/dtd/dtd.py` on Ix:5560), `卯` | `/d`, `/claim` (`tools/did/agent_claims.py`, UserPromptSubmit hook) | `task-queue.json` cache via `did-fast --refresh-cache` | Todoist |
| Do + time | `/do`, `/tg`, janus (`tools/tg/janus.py`) | `cc` wrapper (`claude-tracked`, `llm-sessions.db`), transcript export cron | `tg-fast.py`, toggl_server MCP | Toggl |
| Close + points | `/did`, `/done`, dtd swipe/Enter | `/d done` (marks agent-finished, does not credit) | `tools/did/did-fast.py` → Todoist close + Neon write via excel-http daemon on Ix | Neon (0₦, 1n+, 0分) |
| Backfill | `/0n`, `/1n`, `/2n` gap fill | — | `0n-backfill.py`, `1n-backfill.py` | Neon / Toggl |
| Rituals | `/inbound`, `/-2n`, `/ibx0`, `/ص` | — | `did-fast --ritual` | Neon 0n |
| Review | `/0r`, `/0s`, `/1s`, `/1-1n`, `/2s`, `/3s`, `/dash`, janus | `/1-i446` vault health | personal-dashboard points cache (Ix cron) | Neon + Toggl |

Machines: **Straylight** (work Mac, MDM, no inbound ssh; Excel writes go
through `ssh ix`), **Ix** (always-on server, Excel open, dtd, cron, excel-http).
Crons and daemons: `docs/cron-and-daemons.md`. Self-clearing skills and
frontmatter rules: `skills/claude-skills/README.md`.

Known overlap worth checking before recommending anything new: `/do` vs
`/doing`, `/did` vs `/d1d` vs `/done`, `/d` vs `/claim`, `/bug` vs `/bugg`,
`/s` vs `/salat` vs `/ص`, `/inbound` vs `/-2n`.

The map goes stale. A skill's frontmatter describes intent; `did-fast.py` and
the hooks do the real work. Where the two disagree, trust the code and tell
the user about the drift.

## Steps

1. **Locate.** Restate the question in one line and name which stage(s) and
   which surface (human, agent, or the boundary between them) it touches.
2. **Ground.** Read only what the question needs, in this order:
   - the relevant `skills/claude-skills/<name>/SKILL.md` files
   - the engine script or tool README (`tools/did/`, `tools/tg/`, `tools/dtd/`,
     `scripts/`), plus any `plans/*.md` for that component, newest first
   - `~/.claude/settings.json` hooks if the question involves automatic behavior
   - live state (Toggl today, Todoist, `task-queue.json`, `agent_claims.py`
     state) only if the question is about what actually happens rather than
     what is supposed to happen
   For questions spanning 3+ components, send one Explore agent to sweep
   instead of reading everything into context.
3. **Trace the handoff.** Most loop problems sit at a seam: an identity that
   doesn't carry across (Todoist id ↔ Toggl entry ↔ Neon cell ↔ agent
   session), two writers to one store, a cache read as truth, a step that
   only one surface does (e.g. a human `/did` credits points, an agent `/d
   done` does not). Name the seam precisely: file, function, field.
4. **Answer.** Structure:
   - **Answer**: the direct answer, 1–3 sentences.
   - **How it actually flows**: a short trace or ASCII diagram, with
     `file:line` references.
   - **The seam**: what breaks or leaks, and why.
   - **Recommendation**: one change, with the edit surface (files touched)
     and what it removes. If an existing skill already covers it, say so
     rather than proposing a new one. Prefer merging or deleting over adding.
   Mention a second option only if it is a real trade-off.

## Rules

- **Read-only.** Don't edit skills, scripts or settings, and don't write to
  Todoist, Toggl or Neon, unless the user asks you to. If they then ask you to
  build it, follow the Rubber Duck policy in the global CLAUDE.md, since loop
  changes almost always touch 3+ locations.
- **Opinion over inventory.** Don't list every skill in a stage. Pick.
- **Distinguish human time from agent time.** Toggl measures JM's attention;
  agent sessions run in parallel and shouldn't be added on top as if they
  were extra hours. If the question blurs the two, point that out.
- 分 are points, never dollars.
- No closing offers or "next steps" teasers. Include it in the answer or cut it.
