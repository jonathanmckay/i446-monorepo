---
name: "travel"
description: "Set, clear, or check the explicit DTD/Janus timezone override for international travel. Usage: /travel <city-or-tz> | home | status"
user-invocable: true
---

# Travel Mode (/travel)

Explicit timezone override for DTD and Janus while traveling. Deliberately
NOT auto-detected — every day-boundary check in DTD/Janus works by polling
and reacting to a freshly-computed "now," never to elapsed real time, so
building reliable auto-detection is a lot of code for a tool that already
knows exactly when it's boarding a plane. This command is the single,
explicit, idempotent trigger instead.

## Usage

```
/travel <city-or-tz>   # e.g. /travel Tokyo, /travel Asia/Seoul
/travel home           # clear the override, back to system local time
/travel status          # show current state
```

## Fast path

```bash
python3 ~/i446-monorepo/tools/travel/travel.py <city-or-tz>
python3 ~/i446-monorepo/tools/travel/travel.py home
python3 ~/i446-monorepo/tools/travel/travel.py status
```

## What it does

1. Resolves the input to an IANA timezone — either a known city/region alias
   (see `CITY_ALIASES` in travel.py) or a raw IANA zone name typed directly
   (e.g. `Asia/Seoul`).
2. Writes `~/.local/state/jm/travel.json` (`active_tz`, `home_tz`,
   `switched_at_utc`). `lib/daytime.py`'s `active_zone()` checks this file
   before falling back to the OS's own local timezone — every DTD/Janus
   "now"/"today" call that routes through `daytime.py` (directly in Python,
   or via the `TZ` environment variable `dtd.sh` exports from this same
   file at startup) picks up the switch immediately, no other code changes
   needed.
3. Fires one best-effort resync: absorbs any pending cross-machine
   completions (`mark-completed.py --absorb-remote`), force-refreshes the
   task cache (`did-fast.py --refresh-cache`), and nudges an already-open
   janus to redraw. Each step is independent — a failure in one never blocks
   the others or the TZ switch itself, which is already committed by the
   time resync runs.

No special-casing for a short (eastward) or long (westward) first day: once
"now" resolves through the new zone, the existing forward-only date gates
(`mark-completed.py`, `dtd-skipped-today`, `dtd-block-snooze`) handle an
early or late local midnight correctly on their own.

## Response style

Print the script's own output verbatim. Do not add commentary unless the
command failed (unknown city/zone) — then just say so.
