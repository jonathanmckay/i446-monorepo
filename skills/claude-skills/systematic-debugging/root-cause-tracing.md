# Root Cause Tracing

Part of `systematic-debugging`. Adapted from obra/superpowers (MIT).

Bugs surface deep in a call chain: a Toggl entry created on the wrong day, a
点 written to a dead column, a posthoc task with a mangled name. The instinct
is to patch where the error appears. That is a symptom fix; the bad value
came from somewhere.

**Rule:** trace backward through the callers until you find where the bad
value originated, then fix there.

## Use when

- The error is deep in execution, not at the entry point.
- The stack trace is long.
- It is unclear where the invalid data came from.
- You need the test or input that triggers it.

## The trace

1. **Observe the symptom.** `+2028 written to 0分!R`.
2. **Find the immediate cause.** The line that wrote it:
   `fen_appends.append((r.fen_col, r.fen_points))` with `fen_points == 2028`.
3. **Ask what called this with that value.**
   `route_items` set `fen_points = item.time_value` ...
   `parse_input` set `time_value` from a trailing bare number ...
   janus `/done` sent `"... readjustment 2028 1922-1956 @i9"`.
4. **Keep going up** until the value is first wrong. Here: `parse_input`
   treats a trailing integer as minutes even when a HHMM-HHMM range is
   present; the "2028" was part of the task's name.
5. **Fix at the source.** The range means the trailing number is text. One
   guard in `parse_input`, one regression test that sends that exact string.

## Add instrumentation when the chain crosses a process

dtd -> did-fast -> excel-http daemon -> Excel on Ix, or janus -> did-fast ->
Toggl CLI. Print what enters and leaves each hop once (stderr, or the
daemon's journal), run the failing case, and read which hop first shows the
bad value. Then investigate only that hop.

## Do not stop at the first fixable place

A guard at the symptom (clamp the column, refuse the write) hides the bug
and leaves the next caller to hit it differently. Guards are fine *after*
the source fix, when they turn a silent wrong write into a loud refusal;
they are never the fix.
