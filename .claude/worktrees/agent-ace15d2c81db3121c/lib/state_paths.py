"""Single source of truth for machine-local runtime state paths.

These files (task cache, completed-today, skip lists, the shortnames sidecar,
recording state) are per-machine *state*, not durable content. They must NOT
live in the Syncthing-synced vault or the git-synced monorepo, or they leak
across machines (stale PIDs, foreign caches). See
vault/z_meta/architecture.md hazard #1.

They live under $XDG_STATE_HOME (default ~/.local/state) in a `jm/` subdir.
Importing this module ensures the directory exists.
"""
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "jm"
STATE_DIR.mkdir(parents=True, exist_ok=True)

TASK_QUEUE      = STATE_DIR / "task-queue.json"        # dtd/next task cache
COMPLETED_TODAY = STATE_DIR / "completed-today.json"   # today's completed names
DTD_SKIPPED     = STATE_DIR / "dtd-skipped-today.txt"  # dtd.sh skip list (+ .date)
SKIPPED_TODAY   = STATE_DIR / "skipped-today.json"     # next-task skip ids
TASK_SHORTNAMES = STATE_DIR / "task-shortnames.json"   # Haiku short-name sidecar
D357_STATE      = STATE_DIR / "d357-state.json"         # active recording state
