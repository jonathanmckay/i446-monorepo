# `_lib/` — shared helpers for ix-only writes

The canonical Excel workbook (`Neon分v12.2.xlsx`) lives on OneDrive
and is **only** written from the remote Mac `ix`. If two Macs ever
edit the workbook simultaneously, OneDrive produces a merge-conflict
copy that has to be reconciled by hand and silently drops data.

These helpers exist so every skill can route writes through ix
without duplicating ssh boilerplate, and so the policy ("never write
locally") is enforced in one place.

## Helpers

### `ix-osa.sh`

Bash wrapper. Reads AppleScript from stdin, runs it on ix via
`ssh ix osascript -`, prints the remote stdout/stderr.

```bash
~/.claude/skills/_lib/ix-osa.sh <<'AS'
tell application "Microsoft Excel"
    return name of active workbook
end tell
AS
```

Exit codes:
- `0` success
- `2` AppleScript ran but returned `ERROR:` / `ERR:`
- `3` ssh transport failure (ix unreachable). Stderr message:
  `ERROR: ix unreachable — write aborted to prevent OneDrive merge conflict. ...`
- `4` usage error (no script on stdin)

Env:
- `IX_HOST` (default `ix`) — ssh alias to use
- `IX_DEBUG` — verbose
- `IX_QUEUE=1` — on exit code 3 (ix unreachable), append the script to
  `~/.claude/ix-write-queue.jsonl` for later replay. The skill still
  exits 3 (callers see the failure), but the write is preserved.

### `ix-osa.py`

Python wrapper with the same contract. Use as a library:

```python
import sys
sys.path.insert(0, "/Users/mckay/.claude/skills/_lib")
from ix_osa import run

res = run(applescript_text)
if res.returncode != 0:
    raise RuntimeError(res.stderr)
```

The /did background agent uses this directly so AppleScript templates
in `did/applescript-ref.md` execute on ix instead of locally.

### `ix-chart-insert.py`

Inserts a PNG chart into the workbook on ix. `scp`'s the PNG to
`ix:~/tmp/charts/` then runs xlwings (or AppleScript `add picture` as
fallback) over ssh. Used by `/0t` and `/1n` after generating donut
charts.

```bash
~/.claude/skills/_lib/ix-chart-insert.py \
    --png ~/Desktop/toggl_2026-04-23.png \
    --sheet '0分' --cell BB12
```

### `ix-drain-queue.sh`

Replays queued writes from `~/.claude/ix-write-queue.jsonl` when ix is
back online. Stops on first transport failure (preserves remaining queue).

```bash
~/.claude/skills/_lib/ix-drain-queue.sh           # replay all
~/.claude/skills/_lib/ix-drain-queue.sh --dry-run  # preview
```

Skills that set `IX_QUEUE=1` before calling `ix-osa.sh` will
automatically queue on failure. Idempotent cell-value writes (habits,
points, scores) are safe to queue. Row-insert operations should NOT
use the queue.

## Policy

**No skill may invoke local `/usr/bin/osascript` to drive Microsoft
Excel.** No skill may use local `xlwings` against `Neon分v12.2.xlsx`.
If `ssh ix` fails, the skill must hard-fail with a clear error — do
not "fall back to local with orange terminal." The merge-conflict cost
of a local write is much higher than the friction of asking the user
to restore the ix tunnel.

Reads (excel-mcp, openpyxl, cached snapshots) are unaffected.
