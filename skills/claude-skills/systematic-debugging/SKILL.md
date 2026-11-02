---
name: "systematic-debugging"
description: "Use when a bug, test failure, flaky job or unexpected behaviour shows up and before proposing any fix: the symptom is reported but the cause is not yet proven, a previous fix did not hold, or you are about to try something and see. Loaded by /bug and by the rubber-duck failure-recovery trigger."
user-invocable: false
---

# Systematic Debugging

Adapted from obra/superpowers `systematic-debugging` (MIT), cut to this
household. The iron law is the whole skill:

```
NO FIX WITHOUT A PROVEN ROOT CAUSE
```

Violating the letter of this process is violating its spirit. A fix proposed
before Phase 1 is complete is a guess, however obvious it looks.

## Use it especially when

- "Just one quick fix" is obvious. Simple bugs have root causes too.
- A previous fix did not work. The second guess is no better than the first.
- You are under time pressure. Systematic is faster than thrashing; JM's time
  is the expensive part, and a wrong fix costs a second round trip.
- You do not fully understand the issue.

## The four phases, in order

### Phase 1: Root cause

1. **Read the whole error.** Stack trace to the bottom, line numbers, file
   paths, exit codes. The answer is often in the part that was skipped.
2. **Reproduce.** Exact steps, reliably. Not reproducible means gather data,
   not guess.
3. **Check what changed.** `git log -S`, recent `auto:` commits on origin,
   config, a dependency, the other machine.
4. **Check this household's usual suspects** (the same list `/forreal`
   attacks):
   - **Stale process or wrong target.** janus, dtd and ibx bake scripts into
     `/tmp` at launch and read Syncthing'd or symlinked copies
     (`~/i446-monorepo` is the vault; `~/.claude/skills` is the monorepo).
     Is the edit where the running code reads, and was the process respawned?
   - **Wrong machine.** Excel and launchd jobs run on Ix, not Straylight.
     Ix's autopush may already have committed a mid-edit file.
   - **Three clients.** janus is the terminal, mobile.py and the iOS app;
     a symptom in one is often a divergence from the shared `lib/` rule.
   - **Partial data.** Unicode, double spaces, overdue versus today, a
     comma inside a task name, the midnight boundary, the viewed day.
   - **Wrong account, calendar, date or timezone.** MCP writes land on the
     account the MCP is authed to.
5. **Instrument the boundaries** in a multi-component path (dtd -> did-fast
   -> excel-http daemon -> Excel; janus -> did-fast -> Toggl). Log what enters
   and leaves each component once, find the layer that breaks, then look
   only there.
6. **Trace data flow backward** when the error is deep in the call stack:
   see `root-cause-tracing.md`. Fix at the source, not where it surfaced.

### Phase 2: Pattern

1. Find the working example of the same thing in this codebase.
2. Read the reference implementation completely. Do not skim.
3. List every difference between working and broken, however small.
4. Name the dependencies: config, environment, assumptions.

### Phase 3: Hypothesis

1. One hypothesis, written down: "X is the root cause because Y."
2. Test it with the smallest possible change. One variable.
3. Wrong? New hypothesis. Never stack a second fix on a failed one.
4. Do not know? Say "I do not understand X" and research. No pretending.

### Phase 4: Fix

1. **Failing test first.** The simplest reproduction, in the module's test
   file; a one-off script if there is no framework. It must fail on the
   broken code.
2. **One fix** at the root cause. No "while I'm here", no bundled refactor.
3. **Verify sideways.** Test passes, no other test broke, and the symptom is
   gone through a different path than the one that produced it (run the real
   binary, read the persisted artifact, hit the API). This is `/forreal`'s
   rule; apply it before claiming done.
4. **Fix failed?** Stop and count. Under three attempts: back to Phase 1 with
   the new evidence. **Three or more:** stop and question the architecture
   (each fix revealing a new coupling elsewhere is the signature). Say so to
   JM before attempting a fourth; that is a wrong design, not a failed
   hypothesis.

## Red flags: stop and return to Phase 1

- "Quick fix for now, investigate later"
- "Just try changing X and see"
- "Add a few changes, run the tests"
- "Skip the test, I verified it manually"
- "It's probably X, let me fix that"
- "Here are the main problems:" followed by fixes with no investigation
- "One more fix attempt" after two have failed
- Each fix reveals a new problem somewhere else

JM's signals that you are doing it wrong: "is that not happening?" (you
assumed), "will it show us...?" (you should have gathered evidence), "stop
guessing", "we're stuck?". All of them mean Phase 1.

## Rationalizations

| Excuse | Reality |
|---|---|
| "It's simple, no process needed" | Simple bugs have root causes. The process is fast for them. |
| "Emergency, no time" | Systematic is faster than guess-and-check. |
| "Try this first, then investigate" | The first fix sets the pattern. |
| "Test after I confirm the fix" | A test written after proves nothing failed. |
| "Several fixes at once saves time" | You cannot tell which one worked, and you created new bugs. |
| "Reference is long, I'll adapt the pattern" | Partial understanding guarantees a wrong fix. Read it all. |
| "I see the problem, let me fix it" | Seeing a symptom is not understanding a cause. |
| "One more attempt" after two failures | Three failures is an architecture problem. |

## When there is truly no root cause

If the investigation ends at timing, environment or an external service:
document what was ruled out, add the handling that fits (retry, condition
polling per `condition-based-waiting.md`, a clear error), and add logging so
the next occurrence carries evidence. Most "no root cause" cases are an
incomplete Phase 1.

## Files

- `root-cause-tracing.md`: trace backward from the symptom to the trigger.
- `condition-based-waiting.md`: wait for the condition, never for N seconds.
