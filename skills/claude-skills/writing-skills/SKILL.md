---
name: "writing-skills"
description: "Use when creating a new skill or editing an existing SKILL.md in skills/claude-skills: writing its name and description, deciding whether it should be a skill at all, shaping its rules, or when a skill keeps firing at the wrong time or not firing. Not for one-off fixes or project conventions, which go in CLAUDE.md."
user-invocable: false
---

# Writing Skills

Adapted from obra/superpowers `writing-skills` (MIT), merged with this
repo's `README.md` (strict YAML, self-clearing skills). Read both before
touching a SKILL.md.

A skill is a reusable technique, pattern or reference that a future agent
can find and apply. It is not the story of how one problem was solved once.

## Should this be a skill?

**Yes** when the technique was not obvious, it will be needed again, and it
applies beyond one file.

**No** when it is a one-off fix, a standard practice documented elsewhere, a
project convention (that goes in the directory's CLAUDE.md), or a mechanical
constraint. If a regex or a hook can enforce it, automate it and keep the
skill for judgment calls.

Three kinds: a **technique** (steps to follow), a **pattern** (a way of
thinking), a **reference** (docs, an API, a sheet's columns).

## The description decides whether the skill fires

The `description` says only **when** to use the skill: the triggering
conditions and symptoms, in the third person, starting with "Use when". It
never summarizes the workflow, because an agent that reads a summary follows
the summary instead of the skill. Include the words a user would type and
the conditions under which the skill must **not** fire; most wrong-skill
invocations in this repo come from descriptions that name the domain but
not the boundary.

```yaml
# Weak: a summary, no boundary
description: "Logs sleep and refreshes the dashboard."

# Strong: triggers, symptoms, exclusions
description: "Use when JM says he woke up, asks to log sleep, or the 0t card is open and last night's Toggl 睡觉 entry exists. Not for naps (use /did 睡觉 <range>)."
```

Keep every frontmatter string double-quoted and under the README's YAML
rules; a loose value parses in one host and silently breaks in the other.

## Match the form to the failure

| The failure you saw | The form that fixes it |
|---|---|
| The rule is known and skipped under pressure | A prohibition, with the loopholes closed (see `bulletproofing.md`) |
| The output has the wrong shape | A positive recipe or a template with required fields |
| Something is omitted | A required field, not a reminder |
| Behaviour depends on a condition | An explicit conditional, one branch per case |

Do not add nuance clauses and exemptions to a prohibition; they are the
loopholes.

## Writing it

- **One complete, realistic example** in the language the skill is about,
  with comments. Not three languages, not a sketch.
- **Semantic labels**: `resolve_fen_col`, not `step3`.
- **Keep inline**: principles, patterns under about fifty lines. **Split
  out**: a reference over a hundred lines, reusable scripts, templates.
- **Household conventions** every skill here follows: no em-dashes; one-line
  confirmations for quick-add and mark-done skills; a skill that performs a
  dtd card closes that card itself (self-clearing, see README); column
  letters resolved through `neon-cols.json`, never hardcoded; writes to
  Excel go through the excel-http daemon on Ix.
- **Cross-reference** the sibling skill by name (`/did`, `/forreal`) rather
  than restating its rules.

## Check it before you call it done

Upstream tests skills by running pressure scenarios in subagents before and
after writing (the RED-GREEN-REFACTOR loop). That is worth it for a
discipline skill that will fire hundreds of times (`/did`, `/d`,
`systematic-debugging`); for the rest, this is enough:

1. Read the description alone and ask: on which messages would this fire
   wrongly? On which should it fire and the words are missing?
2. Run the skill once on the real case that prompted it.
3. For a discipline skill, add the rationalization table and red-flags list
   from `bulletproofing.md`, seeded with the excuses actually observed.

## Anti-patterns

- A narrative ("first I tried X, then...") instead of a technique.
- The same example in several languages.
- Code inside a flowchart.
- A description that summarizes the steps.
- Generic labels and hedged rules.
