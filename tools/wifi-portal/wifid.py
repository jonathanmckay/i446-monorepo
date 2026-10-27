#!/usr/bin/env python3
"""launchd entry point: one tick of every wifi rule. Each rule is isolated
so one crashing can't stop the other."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    portal = _load("portal")
    try:
        portal.run()
    except Exception as e:
        portal.log(f"portal tick crashed: {e!r}")
    if portal.DISABLED.exists():
        return 0
    try:
        hotspot = _load("hotspot")
        hotspot.run(notify_fn=portal.notify)
    except Exception as e:
        portal.log(f"hotspot tick crashed: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
