"""Column map lookups against ~/i446-monorepo/config/neon-cols.json.

Skills must NEVER hard-code column letters. Always go through `col(sheet, header)`
or `domain_col(sheet, domain)` so the live spreadsheet remains the source of truth.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONFIG = Path.home() / "i446-monorepo/config/neon-cols.json"


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return json.loads(CONFIG.read_text())


def reload() -> None:
    """Drop the cache so the next lookup re-reads neon-cols.json."""
    _cfg.cache_clear()


def col(sheet: str, header: str) -> str:
    """Return the column letter for `header` in `sheet`. KeyError if missing."""
    return _cfg()["sheets"][sheet]["headers"][header]


def maybe_col(sheet: str, header: str) -> str | None:
    return _cfg()["sheets"][sheet]["headers"].get(header)


def domain_col(sheet: str, domain: str) -> str:
    """Resolve a domain code (e.g. 'i9', 'm5x2') to a column on `sheet`.

    Looks up `domain_aliases` first (for 0分), falls back to `headers`."""
    s = _cfg()["sheets"][sheet]
    aliases = s.get("domain_aliases", {})
    if domain in aliases:
        return aliases[domain]
    if domain in s["headers"]:
        return s["headers"][domain]
    raise KeyError(f"no column for domain {domain!r} on sheet {sheet!r}")


def date_col(sheet: str) -> str:
    return _cfg()["sheets"][sheet]["date_col"]


def to_0fen_col(header_1n: str) -> str:
    """For 1n+ headers, return the 0分 column to append the +1n+!ref into."""
    m = _cfg()["sheets"]["1n+"]["to_0fen_col_map"]
    if header_1n not in m:
        raise KeyError(f"no 1n+→0分 mapping for {header_1n!r}")
    return m[header_1n]


def hcbi_band(hour: int) -> dict:
    """Return the hcbi /ate band entry covering the given hour (0-23)."""
    bands = _cfg()["sheets"]["hcbi"]["ate_bands"]
    for b in bands:
        lo_s, hi_s = b["hours"].split("-")
        lo = int(lo_s.split(":")[0])
        hi = int(hi_s.split(":")[0])
        if lo <= hi:
            if lo <= hour <= hi:
                return b
        else:
            if hour >= lo or hour <= hi:
                return b
    return bands[-1]


def daily_dozen_col(name: str) -> str:
    return _cfg()["sheets"]["hcbi"]["daily_dozen"][name]
