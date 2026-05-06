"""inbound_sources — single source of truth for which inbox sources are
reachable on the current host.

Decision rule (in order of precedence):

1. ``IBX_FORCE_SOURCES`` env var: if set, the result is exactly the
   comma-separated list (intersected with the universe). Other rules are
   ignored. Empty-string means "no sources at all" — useful in tests.
2. Cred-presence: if the source's required config file / binary is missing,
   it is excluded regardless of host or other env vars. The Agency MCP
   binary at ``~/.config/agency/CurrentVersion/agency`` is the gate for
   Outlook + Teams.
3. ``IBX_DISABLE_SOURCES``: comma-separated list to subtract from defaults.
4. Hostname:
   - ``straylight*`` (case-insensitive) → all sources eligible.
   - ``*Mac-mini*`` or ``ix*`` → exclude Outlook + Teams (no work email).
   - anything else → behave like Straylight (best effort; cred check still
     applies).

Public surface:

    available_sources() -> list[str]      # subset of ALL_SOURCES, ordered
    is_available(name)  -> bool
    skip_reason(name)   -> str | None     # human-readable reason if not avail

The list of source names is stable and matches the per-source ``processed.json``
directories under ``~/.config/{name}/``.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Iterable

ALL_SOURCES: tuple[str, ...] = ("email", "imsg", "slack", "outlook", "teams")

# Sources that need Microsoft Graph (Agency MCP) creds.
_WORK_SOURCES = frozenset({"outlook", "teams"})

# Hostname patterns. Comparison is case-insensitive.
_STRAYLIGHT_PREFIX = "straylight"
_IX_TOKENS = ("mac-mini", "ix")  # substring/prefix match (lowercased)


def _hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _is_ix_host(host: str) -> bool:
    h = host.lower()
    if h.startswith("straylight"):
        return False
    if "mac-mini" in h:
        return True
    # Match leading "ix" only — don't catch "ixmac" style words by accident.
    return h.startswith("ix") or h.startswith("ix.") or h == "ix"


def _agency_binary_present() -> bool:
    """The Agency MCP binary backs both Outlook + Teams."""
    p = Path.home() / ".config" / "agency" / "CurrentVersion" / "agency"
    try:
        return p.exists()
    except OSError:
        return False


def _split_env(name: str) -> set[str] | None:
    """Parse a comma/whitespace-separated env var. Returns None if unset.

    An explicit empty string yields an empty set (meaning "none")."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
    return {p for p in parts if p}


def _cred_present(source: str) -> bool:
    if source in _WORK_SOURCES:
        return _agency_binary_present()
    # Other sources (email/imsg/slack) are assumed reachable on any host the
    # TUI is running on. Their fetchers already swallow their own auth errors
    # and return [], so there's no value in second-guessing them here.
    return True


def skip_reason(source: str) -> str | None:
    """Return a one-line reason this source is skipped, or None if available."""
    source = source.lower()
    if source not in ALL_SOURCES:
        return f"unknown source: {source}"

    forced = _split_env("IBX_FORCE_SOURCES")
    if forced is not None:
        if source not in forced:
            return f"{source} not in IBX_FORCE_SOURCES"
        # Even when forced, missing creds still kill it — better than a
        # confusing hang inside a fetch.
        if not _cred_present(source):
            return f"{source} forced on but creds missing"
        return None

    disabled = _split_env("IBX_DISABLE_SOURCES") or set()
    if source in disabled:
        return f"{source} disabled via IBX_DISABLE_SOURCES"

    if not _cred_present(source):
        if source in _WORK_SOURCES:
            return f"{source} unavailable (no Agency MCP binary)"
        return f"{source} unavailable (missing creds)"

    if source in _WORK_SOURCES and _is_ix_host(_hostname()):
        return f"{source} not available on this host ({_hostname()})"

    return None


def is_available(source: str) -> bool:
    return skip_reason(source) is None


def available_sources(universe: Iterable[str] = ALL_SOURCES) -> list[str]:
    """Return the subset of ``universe`` that is currently reachable, in
    the order of ``ALL_SOURCES``."""
    wanted = {s.lower() for s in universe}
    return [s for s in ALL_SOURCES if s in wanted and is_available(s)]


def skipped_sources(universe: Iterable[str] = ALL_SOURCES) -> list[tuple[str, str]]:
    """Return (source, reason) pairs for everything in ``universe`` that is
    currently *not* reachable. Order matches ``ALL_SOURCES``."""
    wanted = {s.lower() for s in universe}
    out: list[tuple[str, str]] = []
    for s in ALL_SOURCES:
        if s not in wanted:
            continue
        r = skip_reason(s)
        if r is not None:
            out.append((s, r))
    return out
