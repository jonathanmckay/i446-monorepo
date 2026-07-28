#!/usr/bin/env python3
"""Regression: no raw AppleScript writes to the 0分 sheet.

Every 0分 write must go through lib/neon/excel.py (the excel-http daemon
client) so it lands in the neon audit ledger. These tests scan the SOURCE
of the migrated writers and fail if a raw `set formula/value` AppleScript
path capable of targeting 0分 reappears, and check that every
excel.append/excel.write call carries a src= ledger label.

Scope notes (deliberate):
  - 0n / 1n+ raw writes are allowed for now (undo-fast's 0n/1n+ pre-image
    restores, build-order-daemon's toggl-sync) — only 0分-capable paths
    are locked down here.
  - -2n.py has no direct Excel writes (it delegates to did-fast.py); it is
    scanned anyway so a future raw write path fails loudly.
"""
import re
from pathlib import Path

MONO = Path.home() / "i446-monorepo"
FILES = {
    "-2n": MONO / "tools" / "ibx" / "-2n.py",
    "build-order-daemon": MONO / "scripts" / "build-order-daemon.py",
    "undo-fast": MONO / "tools" / "did" / "undo-fast.py",
    "neon-write": MONO / "scripts" / "neon-write.py",
    "did-fast": MONO / "tools" / "did" / "did-fast.py",
}
# Files that, post-migration, must contain at least one client write call.
MIGRATED = ("build-order-daemon", "undo-fast", "neon-write", "did-fast")

WRITE_RE = re.compile(r"set\s+(formula|value)\s+of")
EXCEL_TELL = 'tell application "Microsoft Excel"'
# A script template whose target sheet is interpolated at runtime — a generic
# writer like this can be pointed at 0分, so it counts as a raw 0分 write path.
GENERIC_SHEET_RE = re.compile(r'sheet\s+"\{sheet\}"')
CLIENT_CALL_RE = re.compile(r"excel\.(batch_append|append|write)\(")


def _blocks(src):
    """Yield (name, text) for the module preamble and each top-level
    def/class block, split on column-0 def/class lines. Function-level
    granularity keeps a module constant like NEON_SHEET = "0分" from
    falsely condemning an unrelated AppleScript elsewhere in the file."""
    starts = [m.start() for m in re.finditer(r"(?m)^(?:def|class)\s+\w+", src)]
    if not starts:
        yield "<module>", src
        return
    yield "<module>", src[: starts[0]]
    bounds = starts + [len(src)]
    for i, s in enumerate(starts):
        name = re.match(r"(?:def|class)\s+(\w+)", src[s:]).group(1)
        yield name, src[s: bounds[i + 1]]


def _mentions_0fen(text):
    return "0分" in text or "NEON_SHEET" in text


def test_no_raw_0fen_applescript_writes():
    offenders = []
    for fname, path in FILES.items():
        src = path.read_text(encoding="utf-8")
        for block, text in _blocks(src):
            where = f"{fname}:{block}"
            has_write = bool(WRITE_RE.search(text))
            has_tell = EXCEL_TELL in text
            if has_write and _mentions_0fen(text):
                offenders.append(f"{where}: AppleScript set formula/value near 0分")
            elif has_tell and _mentions_0fen(text):
                offenders.append(f"{where}: Excel AppleScript block references 0分")
            elif has_tell and has_write and GENERIC_SHEET_RE.search(text):
                offenders.append(f"{where}: generic sheet-parameterized AppleScript writer")
    assert not offenders, (
        "raw AppleScript 0分 write paths found (use lib/neon/excel.py):\n  "
        + "\n  ".join(offenders)
    )


def _call_args(src, open_paren):
    """Return the argument text of a call whose '(' is at open_paren."""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1: i]
    return src[open_paren + 1:]


def test_every_client_write_has_src_label():
    missing = []
    for fname, path in FILES.items():
        src = path.read_text(encoding="utf-8")
        for m in CLIENT_CALL_RE.finditer(src):
            args = _call_args(src, m.end() - 1)
            if "src=" not in args:
                line = src.count("\n", 0, m.start()) + 1
                missing.append(f"{fname}:{line}: excel.{m.group(1)}(...) without src=")
    assert not missing, (
        "excel client writes must label the ledger entry with src=:\n  "
        + "\n  ".join(missing)
    )


def test_migrated_files_use_client():
    for fname in MIGRATED:
        src = FILES[fname].read_text(encoding="utf-8")
        assert CLIENT_CALL_RE.search(src), (
            f"{fname} has no excel.append/excel.write client calls — "
            "0分 writes must go through lib/neon/excel.py"
        )


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
