---
name: "ship"
description: "Brainstorm a feature (options, tradeoffs, pick one), then implement it end-to-end with tests and a process restart."
user-invocable: true
---

# Ship a Feature

Two phases, one flow: design it out loud, then build the chosen design. Do not
stop between phases unless a decision is genuinely the user's to make.

## Arguments

The user message after `/ship` is the feature description. It may name the
target tool (janus, dtd, did-fast, ...); if it doesn't, infer it from the
description and recent conversation.

## Phase 1 — Brainstorm

1. **Restate the goal** in one sentence: what the user can do after this ships
   that they can't now. If the request is ambiguous about WHAT (not how),
   ask via AskUserQuestion before designing; never ask about implementation
   details you can decide yourself.
2. **Read the relevant code first** — the affected module's existing patterns,
   state files, key bindings, render paths, test conventions. Designs invented
   before reading the code get vetoed by reality.
3. **Sketch 2–4 approaches.** For each: one-paragraph mechanism, what it
   composes with, what it breaks or complicates. Include at least one
   "smallest thing that works" option.
4. **Pick one and say why** — favor the option that reuses existing plumbing
   (chokepoints, state files, established UI patterns) over new machinery.
   Present the pick with its tradeoffs to the user in the running text, but
   only PAUSE for input when options differ in product behavior the user
   would notice and care about (use AskUserQuestion, recommended option
   first). Pure implementation choices are yours.
5. **Rubber-duck the pick** (per the global Rubber Duck Policy): invoke the
   rubber-duck agent if available, otherwise do an explicit self-critique
   pass — edge cases, interactions with existing features, what a skeptic
   would attack. Fold the surviving objections into the design.

## Phase 2 — Implement

1. **Build the chosen design.** Match the surrounding code's style and reuse
   its helpers. Minimal surface: no drive-by refactors.
2. **Tests.** Add a focused test file (or extend the module's existing one)
   named for the feature, covering the new behavior AND the nearest behavior
   it must not break. Follow the module's test conventions (importlib load
   for hyphenated files, tmp_path for state files).
3. **Run the module's full test suite**, not just the new file. Fix what the
   change broke; update tests that encoded the superseded design (say so in
   the test's docstring, citing the user request and date).
4. **Deploy.** If the feature lives in a running process (janus, dtd, ...):
   find its surface via `cmux top --all --processes | grep <name>`, then
   `cmux respawn-pane --surface surface:<N> --command "<launch command>"`.
   Surface ids change across respawns — always re-look them up. Bust any
   on-disk caches whose schema the change affected. If the code also runs on
   Ix, verify it synced (`ssh ix grep ...`) when a scheduled job depends on it.
5. **Verify live** — read the screen (`cmux read-screen --surface ...`) or
   run the tool once to confirm the feature actually behaves as designed,
   not just that tests pass.

## Report

End with: the goal, the chosen design and the strongest rejected alternative
(one line each), what changed (files + tests + suite count), and proof it's
live. No cliffhangers — if something is left undone, it's a bug, not a note.
