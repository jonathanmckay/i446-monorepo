"""Regression: inside `tell application "Microsoft Excel"` blocks, the bare
AppleScript `tab` constant is shadowed by Excel's dictionary and coerces to
the literal text "tab" (found live 2026-07-28 in the excel-http daemon, then
2026-07-29 in did-fast's PRE pre-image lines, which silently emptied every
undo pre-image: emitter wrote "PREtab17tab...", parser expected "PRE\t").
Use `(character id 9)` instead."""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

FILES = [
    os.path.join(HERE, "did-fast.py"),
    os.path.join(HERE, "undo-fast.py"),
    os.path.join(ROOT, "tools", "ibx", "-2n.py"),
    os.path.join(ROOT, "services", "excel-http", "server.py"),
    os.path.join(ROOT, "lib", "neon", "excel.py"),
]

BARE_TAB = re.compile(r"&\s*tab\s*(&|$)", re.MULTILINE)


def test_no_bare_tab_constant_in_applescript_sources():
    offenders = []
    for path in FILES:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if BARE_TAB.search(line):
                    offenders.append(f"{os.path.basename(path)}:{i}: {line.strip()}")
    assert not offenders, (
        "bare AppleScript `tab` constant (coerces to the literal text 'tab' "
        "inside Excel tell blocks — use `(character id 9)`):\n" + "\n".join(offenders))
