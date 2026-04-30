"""Canonical task registry resolver.

Single source of truth for the (Toggl, Todoist, Neon, vault) cross-system mapping.
Reads ~/i446-monorepo/config/tasks.json. Skills must lookup via this module
instead of hand-rolling word-overlap or column-letter dictionaries.

Public API:
    get_habit(name_or_id)  → Habit | None
    get_domain(code)       → Domain | None
    resolve_fen_col(domain) → "R"   # via neon.cols
    iter_habits()          → Iterator[Habit]
    by_neon_header(header) → Habit | None
    by_aliases()           → dict[str, Habit]   # all aliases → habit
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

CONFIG = Path.home() / "i446-monorepo/config/tasks.json"


@dataclass
class Domain:
    code: str
    display: str
    fen_header: str
    toggl_project_id: int
    cal_color: Optional[str] = None


@dataclass
class Habit:
    id: str
    name: str
    category: str  # "0n", "1n+", "夜neon", etc.
    neon_header: str
    domain: str
    toggl_desc: Optional[str] = None
    toggl_project: Optional[str] = None
    toggl_tags: list = field(default_factory=list)
    todoist_label: Optional[str] = None
    points_default: Optional[int] = None
    minutes_default: Optional[int] = None
    cumulative: bool = False
    cumulative_increment: Optional[int] = None  # for 一起饭 etc
    aliases: list = field(default_factory=list)
    neon_fen_header: Optional[str] = None  # for 1n+ habits, which 0分 col to append into

    def all_names(self) -> list:
        return [self.name, self.id, *self.aliases]


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(CONFIG.read_text())


def reload() -> None:
    _raw.cache_clear()
    _by_alias.cache_clear()


@lru_cache(maxsize=1)
def _by_alias() -> dict:
    out: dict = {}
    for hid, hdata in _raw()["habits"].items():
        h = Habit(id=hid, **hdata)
        for n in h.all_names():
            out[_norm(n)] = h
    return out


def _norm(s: str) -> str:
    """Normalize a name for matching: lowercase, strip dashes/spaces around words."""
    return " ".join(s.lower().replace(" - ", " ").split())


def get_habit(name_or_id: str) -> Optional[Habit]:
    return _by_alias().get(_norm(name_or_id))


def get_domain(code: str) -> Optional[Domain]:
    raw = _raw()
    if code in raw["domains"]:
        return Domain(code=code, **raw["domains"][code])
    return None


def by_neon_header(header: str, category: Optional[str] = None) -> Optional[Habit]:
    """Reverse lookup: which habit corresponds to this 0n/1n+ row-1 header?"""
    for hid, hdata in _raw()["habits"].items():
        if category and hdata.get("category") != category:
            continue
        if hdata.get("neon_header") == header:
            return Habit(id=hid, **hdata)
    return None


def iter_habits(category: Optional[str] = None) -> Iterator[Habit]:
    for hid, hdata in _raw()["habits"].items():
        if category and hdata.get("category") != category:
            continue
        yield Habit(id=hid, **hdata)


def iter_domains() -> Iterator[Domain]:
    for code, ddata in _raw()["domains"].items():
        yield Domain(code=code, **ddata)


def resolve_fen_col(domain_code: str) -> str:
    """Domain → 0分 column letter, via neon.cols (live spreadsheet)."""
    from neon import cols  # local import to avoid hard dep
    d = get_domain(domain_code)
    if not d:
        raise KeyError(f"unknown domain {domain_code!r}")
    return cols.col("0分", d.fen_header)


def resolve_neon_col(habit: Habit) -> str:
    """Habit → 0n or 1n+ column letter, via neon.cols."""
    from neon import cols
    sheet = "0n" if habit.category == "0n" else "1n+"
    return cols.col(sheet, habit.neon_header)


# ── invariant checks ─────────────────────────────────────────────────────────

def validate() -> list[str]:
    """Return list of inconsistencies between registry and live spreadsheet.

    Run by `audit-registry.py`. Catches: domain.fen_header pointing to a
    column that doesn't exist on 0分; habit.neon_header pointing to a column
    that doesn't exist; aliases colliding across habits.
    """
    from neon import cols
    errs: list[str] = []
    seen_aliases: dict = {}

    for d in iter_domains():
        try:
            cols.col("0分", d.fen_header)
        except KeyError:
            errs.append(f"domain {d.code}: fen_header {d.fen_header!r} not in 0分 row 1")

    for h in iter_habits():
        sheet = "0n" if h.category == "0n" else "1n+"
        try:
            cols.col(sheet, h.neon_header)
        except KeyError:
            errs.append(f"habit {h.id}: neon_header {h.neon_header!r} not in {sheet} row 1")
        if h.domain not in {d.code for d in iter_domains()}:
            errs.append(f"habit {h.id}: domain {h.domain!r} not registered")
        for a in h.all_names():
            n = _norm(a)
            if n in seen_aliases and seen_aliases[n] != h.id:
                errs.append(f"alias collision: {a!r} → {seen_aliases[n]} and {h.id}")
            seen_aliases[n] = h.id
    return errs
