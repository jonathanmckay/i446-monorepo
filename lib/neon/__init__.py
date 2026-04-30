"""
neon — shared library for skills that touch the Neon system.

Three submodules:
- `cols`   — read/lookup the column map from `~/i446-monorepo/config/neon-cols.json`
- `blocks` — Earthly-Branch (地支) 2h block math + build-order parsing
- `excel`  — write to live Excel on ix (via HTTP daemon if reachable, else ssh+osascript)

All skills (/did, /ate, /-1g, /-2n, /inbound, /0g, /0r) should import from here.
"""

from . import blocks, cols  # noqa: F401

try:
    from . import excel  # noqa: F401
except ImportError:
    pass  # excel module is optional (ssh-only fallback)
