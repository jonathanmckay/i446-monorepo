"""Line-based three-way merge for the phone's full-text edits of new-notes.

2026-10-21: a put from the phone carried a base the desktop had since moved
past (/notes cleared the inbox); the server kept the desktop copy as a
conflict file and let the phone's stale text win, so the live file lost the
clear. Now the server merges: base = the version the phone edited (kept in
the snapshot store), ours = the file on ix, theirs = the phone's text.

Rules, per region of the base that differs: changed on one side only takes
that side; identical changes apply once; where both sides touched the same
region, lines one side merely ADDED are kept after the other side's version
(an inbox is append-mostly, so "desktop cleared, phone appended" yields just
the appended lines); anything else keeps BOTH texts between conflict markers
so nothing is ever lost.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple


def _inserts_only(base: List[str], side: List[str]) -> Optional[List[str]]:
    """If `side` differs from `base` only by added lines, return them in order."""
    ins: List[str] = []
    for tag, _i1, _i2, j1, j2 in SequenceMatcher(None, base, side, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            return None
        ins.extend(side[j1:j2])
    return ins


def _nl(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def merge3(base: str, ours: str, theirs: str, ours_label: str = "ix", theirs_label: str = "phone") -> Tuple[str, bool]:
    """Returns (merged_text, had_conflict). Output always ends in a newline."""
    b = _nl(base).splitlines(keepends=True)
    a = _nl(ours).splitlines(keepends=True)
    c = _nl(theirs).splitlines(keepends=True)
    a_of: Dict[int, int] = {}
    c_of: Dict[int, int] = {}
    for blk in SequenceMatcher(None, b, a, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            a_of[blk.a + k] = blk.b + k
    for blk in SequenceMatcher(None, b, c, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            c_of[blk.a + k] = blk.b + k
    out: List[str] = []
    conflict = False
    bi = ai = ci = 0
    n = len(b)
    while True:
        # base lines stable on both sides pass straight through
        while bi < n and a_of.get(bi) == ai and c_of.get(bi) == ci:
            out.append(b[bi])
            bi += 1
            ai += 1
            ci += 1
        if bi >= n and ai >= len(a) and ci >= len(c):
            break
        # the region up to the next line stable on both sides
        bj = bi
        while bj < n and not (bj in a_of and bj in c_of and a_of[bj] >= ai and c_of[bj] >= ci):
            bj += 1
        aj, cj = (a_of[bj], c_of[bj]) if bj < n else (len(a), len(c))
        bchunk, achunk, cchunk = b[bi:bj], a[ai:aj], c[ci:cj]
        if achunk == cchunk:
            out.extend(achunk)
        elif achunk == bchunk:
            out.extend(cchunk)
        elif cchunk == bchunk:
            out.extend(achunk)
        else:
            c_ins = _inserts_only(bchunk, cchunk)
            a_ins = _inserts_only(bchunk, achunk)
            if c_ins is not None and a_ins is not None:
                # both only added: one side's additions may extend the other's
                if c_ins[: len(a_ins)] == a_ins:
                    out.extend(cchunk)
                elif a_ins[: len(c_ins)] == c_ins:
                    out.extend(achunk)
                else:
                    out.extend(achunk)
                    out.extend(c_ins)
            elif c_ins is not None:
                out.extend(achunk)
                out.extend(c_ins)
            elif a_ins is not None:
                out.extend(cchunk)
                out.extend(a_ins)
            else:
                conflict = True
                out.append(f"<<<<<<< {ours_label}\n")
                out.extend(achunk)
                out.append("=======\n")
                out.extend(cchunk)
                out.append(f">>>>>>> {theirs_label}\n")
        bi, ai, ci = bj, aj, cj
    return "".join(out), conflict
