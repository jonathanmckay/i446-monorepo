#!/usr/bin/env python3
"""Top-level entry for /inbound.

Thin orchestrator that loads the sibling `-2n.py` module (filename's leading
dash makes it un-importable via plain `import`) and calls its `main()`.

Exit codes mirror the wrapper contract:
  0  — clean exit
  2  — user quit (Ctrl+C / explicit stop); wrapper will not restart
  *  — unexpected error; wrapper will trigger Claude auto-fix + retry
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TWO_N_PATH = _HERE / "-2n.py"


def _load_two_n():
    spec = importlib.util.spec_from_file_location("_two_n", _TWO_N_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {_TWO_N_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not _TWO_N_PATH.exists():
        print(f"inbound: missing dependency {_TWO_N_PATH}", file=sys.stderr)
        return 1
    two_n = _load_two_n()
    if not hasattr(two_n, "main"):
        print("inbound: -2n.py has no main()", file=sys.stderr)
        return 1
    try:
        rc = two_n.main()
    except KeyboardInterrupt:
        return 2
    return int(rc) if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
