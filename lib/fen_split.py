"""Multi-domain 分 on one task: "[N] @code [N] @code" (JM 2026-11-01).

One task may carry several "[N] @code" pairs. Grammar: each [N] binds to the
@code immediately after it; a [N] with no @code after it binds to the task's
own domain (its first project label), which is exactly today's single-[N]
behaviour. The total is the sum. Only the FIRST domain becomes a Todoist
label; the other codes live in the content only.

    "fix faucet (30) [8]"                     -> [(8, None)]
    "mock WAU tree (30) [30] @h335 [10] @i9"  -> [(30, "h335"), (10, "i9")]
    "prep call [20] @m5x2 [10]"               -> [(20, "m5x2"), (10, None)]

{N} (the 0g bonus, column Q) is a separate mechanism and is never touched
here. Every consumer that reads a task's points goes through this module:
did-fast (credit + defer), dtd (sort/display), janus desktop + mobile
(display, the task/ask decision), edit-fast (prefill/rewrite), the audits.
A first-match r"\\[(\\d+)\\]" elsewhere silently drops every domain but the
first; grep for it before adding another.
"""
from __future__ import annotations

import re
from typing import Optional

# "[30]" optionally followed by " @code". The code may carry a hyphen
# ("-1g"-style labels) and CJK (家). Full-width 【30】 (a Chinese IME's [ ])
# counts the same (JM 2026-11-08); rewrites always emit ASCII [N]. A "[1/m]" rate marker never matches
# (digits only inside the brackets).
PAIR_RE = re.compile(r"[\[【](\d+)[\]】](?:[ \t]*@([\w\-一-鿿]+))?")
# A standalone "[N] @code" pair anchored on both sides, for callers that must
# consume whole tokens out of typed input (did-fast's parse_input).
TYPED_PAIR_RE = re.compile(r"(?<!\S)[\[【](\d+)[\]】][ \t]*@([\w\-一-鿿]+)(?!\S)")


def fullwidth_points(text: str) -> str:
    """【N】 -> [N]. For input boundaries (janus) so what gets written to
    Toggl/Todoist is ASCII and the many local r"\\[(\\d+)\\]" regexes see it."""
    return re.sub(r"【(\d+)】", r"[\1]", text or "")


def parse_points(content: str) -> list[tuple[int, Optional[str]]]:
    """Every [N] in `content`, in order, with the @code bound to it (None
    when the [N] has no @code directly after it)."""
    return [(int(m.group(1)), m.group(2)) for m in PAIR_RE.finditer(content or "")]


def total_points(content: str) -> Optional[int]:
    """Sum of every [N]; None when the content carries no [N] at all (so a
    caller can still tell "no points" from "[0]")."""
    pairs = parse_points(content)
    return sum(n for n, _ in pairs) if pairs else None


def has_split(content: str) -> bool:
    """True when the content carries more than one [N], i.e. a real split
    (a single "[N] @code" is just the ordinary form with its label spelled
    out)."""
    return len(parse_points(content)) > 1


def split_points(content: str, default_code: Optional[str]) -> list[tuple[Optional[str], int]]:
    """[(code, points)] merged per code, first-seen order kept. A [N] with no
    @code takes `default_code` (the task's own label; None stays None and the
    caller falls back to 个 exactly as it does today)."""
    merged: dict[Optional[str], int] = {}
    for n, code in parse_points(content):
        key = code if code is not None else default_code
        merged[key] = merged.get(key, 0) + n
    return list(merged.items())


def first_code(content: str) -> Optional[str]:
    """The @code of the first pair that has one — what /todo turns into the
    task's single Todoist label."""
    for _, code in parse_points(content):
        if code:
            return code
    return None


def set_total(content: str, pts: int) -> str:
    """Rewrite the points to one plain [pts]: the first [N] (and its @code,
    if it is part of a split) is replaced, every later "[N] @code" pair is
    removed. No [N] at all -> append. This is what an explicit points edit
    means for a split task: the split is collapsed, never silently kept with
    a wrong total."""
    matches = list(PAIR_RE.finditer(content or ""))
    if not matches:
        return f"{(content or '').rstrip()} [{pts}]"
    out = content
    # Remove later pairs back-to-front so earlier offsets stay valid.
    for m in reversed(matches[1:]):
        out = out[:m.start()].rstrip() + (" " if m.end() < len(out) and not out[m.end():].startswith(" ") else "") + out[m.end():]
    first = PAIR_RE.search(out)
    assert first is not None
    keep_code = first.group(2) if len(matches) == 1 else None
    repl = f"[{pts}]" + (f" @{keep_code}" if keep_code else "")
    out = out[:first.start()] + repl + out[first.end():]
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def take_points(content: str, claimed: int) -> tuple[str, list[tuple[int, Optional[str]]]]:
    """Take `claimed` points off the task, walking the pairs in order and
    clamping each at 0 (the defer flow: points logged today come off the
    task's remaining value). Returns (new content, what was taken as
    [(n, code)]) so the caller credits EXACTLY the pairs it deducted (never
    the label's column for a share that came off another domain). Codes are
    preserved; a pair that reaches 0 stays as "[0] @code" so the user sees
    where the deduction landed."""
    remaining = max(int(claimed), 0)
    out = []
    taken: list[tuple[int, Optional[str]]] = []
    last = 0
    for m in PAIR_RE.finditer(content or ""):
        n = int(m.group(1))
        take = min(n, remaining)
        remaining -= take
        if take:
            taken.append((take, m.group(2)))
        new = n - take
        out.append(content[last:m.start()])
        out.append(f"[{new}]" + (f" @{m.group(2)}" if m.group(2) else ""))
        last = m.end()
    out.append((content or "")[last:])
    return "".join(out), taken


def deduct_points(content: str, claimed: int) -> str:
    """take_points, content only."""
    return take_points(content, claimed)[0]


def strip_points(content: str) -> str:
    """The content with every [N] (and the @code bound to it) removed."""
    return re.sub(r"[ \t]{2,}", " ", PAIR_RE.sub("", content or "")).strip()
