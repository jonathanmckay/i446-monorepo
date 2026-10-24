#!/usr/bin/env python3
"""Resolve where a /picturebook book is filed.

A book lives in the target kid's *current* grade folder under the curriculum:

    ~/vault/xk87/xk23-mckay-curriculum/<kid>-<level>-<YYYY-MM>/<slug>/<slug>.md
                                                                 /<slug>-NN.png
                                                                 /<slug>.pdf

"Current" = the newest `<kid>-<level>-YYYY-MM` folder by its YYYY-MM suffix
(e.g. theo-pk3-2025-08, ren-pk2-2025-09). A new school year gets a new grade
folder and books follow it automatically.

Usage:
    python3 book_dir.py <theo|ren> <slug> [--mkdir]

Prints JSON: {"book_dir", "md", "pdf", "grade_dir", "tracker"}.
"""
import json
import re
import sys
from pathlib import Path

CURRICULUM = Path.home() / "vault" / "xk87" / "xk23-mckay-curriculum"
TRACKERS = {  # kid -> reader tracker (lives at the curriculum root)
    "theo": "xk23-chinese-reader-tracker.md",
    "ren": "xk23-english-reader-tracker.md",
}
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def current_grade_dir(kid: str, base: Path = CURRICULUM) -> Path:
    """Newest `<kid>-<level>-YYYY-MM` folder under base; ValueError if none."""
    kid = kid.lower()
    pat = re.compile(rf"^{re.escape(kid)}-[a-z0-9]+-(\d{{4}})-(\d{{2}})$")
    found = []
    for d in base.iterdir():
        m = pat.match(d.name)
        if d.is_dir() and m:
            found.append(((int(m.group(1)), int(m.group(2))), d))
    if not found:
        raise ValueError(f"no {kid}-<level>-YYYY-MM grade folder in {base}")
    return max(found)[1]


def book_paths(kid: str, slug: str, base: Path = CURRICULUM) -> dict:
    kid = kid.lower()
    if kid not in TRACKERS:
        raise ValueError(f"unknown kid {kid!r} (expected one of {sorted(TRACKERS)})")
    if not _SLUG_RE.match(slug):
        raise ValueError(f"slug {slug!r} must be ASCII kebab-case")
    grade = current_grade_dir(kid, base)
    book = grade / slug
    return {
        "grade_dir": str(grade),
        "book_dir": str(book),
        "md": str(book / f"{slug}.md"),
        "pdf": str(book / f"{slug}.pdf"),
        "tracker": str(base / TRACKERS[kid]),
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--mkdir"]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        paths = book_paths(args[0], args[1])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if "--mkdir" in argv:
        Path(paths["book_dir"]).mkdir(parents=False, exist_ok=True)
    print(json.dumps(paths, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
