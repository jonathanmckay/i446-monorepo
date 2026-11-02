#!/usr/bin/env python3
"""todo-fast.py — the /todo quick-add without a model in the loop.

dtd's Enter router calls this when the query matches no row and carries a
`[N]` value (2026-11-01: "if I enter a task that isn't a match to something in
the fzf box and has points [xx], that gets translated as a /todo"). Same
parsing as the /todo skill, minus the inference a model would do:

  - `@code` tokens become labels and leave the content. Multi-domain split
    (`[30] @h335 [10] @i9`): a `[N] @code` pair stays in the content verbatim
    and only the FIRST bound code becomes a label.
  - a standalone trailing 地支 glyph (卯辰巳午未申酉戌亥) becomes a label (dtd
    hides the card until that block) and leaves the content.
  - bare http(s) URLs become `[(link)](url)` (clickable "(link)" in dtd).
  - `(N)` and `[N]` stay in the content, where dtd and /did read them.
  - a leading `/todo ` is dropped, so the slash form works too.

Usage: todo-fast.py "<task text>"   -> prints `+ <content> @label ...`
       todo-fast.py --parse "<text>" -> prints the parsed JSON, creates nothing
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = Path.home() / "i446-monorepo" / "lib"
BLOCKS = "卯辰巳午未申酉戌亥"
_URL = re.compile(r"(?<![\(<])https?://[^\s\)>\]]+")
_BOUND = re.compile(r"\[(\d+)\]\s*@(\S+)")          # [N] @code pair (split share)
_LABEL = re.compile(r"(?:^|(?<=\s))@(\S+)")
_VALUE = re.compile(r"\[(\d+)\]")


def parse(text: str) -> dict:
    s = text.strip()
    s = re.sub(r"^/todo\s+", "", s)
    s = _URL.sub(lambda m: f"[(link)]({m.group(0)})", s)
    bound = [m.group(2) for m in _BOUND.finditer(s)]
    if len(_VALUE.findall(s)) >= 2 and bound:
        # multi-domain split: `[N] @code` pairs stay verbatim in the content,
        # the first bound code is the label, unbound @codes are plain labels
        unbound = re.compile(r"(?<!\]\s)(?<!\])@(\S+)")
        labels = [bound[0]] + [m.group(1) for m in unbound.finditer(s)]
        s = unbound.sub("", s)
    else:
        labels = [m.group(1) for m in _LABEL.finditer(s)]
        s = _LABEL.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    toks = s.split(" ")
    if toks and len(toks[-1]) == 1 and toks[-1] in BLOCKS:
        labels.append(toks[-1])
        s = " ".join(toks[:-1]).strip()
    # dedupe, keep order
    seen: set[str] = set()
    labels = [l for l in labels if not (l in seen or seen.add(l))]
    vals = [int(v) for v in _VALUE.findall(s)]   # a split's value is the sum of its shares
    return {"content": s, "labels": labels, "value": sum(vals) if vals else None}


def create(text: str) -> dict:
    p = parse(text)
    if not p["content"]:
        raise SystemExit("todo-fast: empty task")
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import todoist  # noqa: E402
    task = todoist.create_task(p["content"], labels=p["labels"] or None, due_string="today")
    try:
        subprocess.Popen([sys.executable, str(HERE / "did-fast.py"), "--refresh-cache"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass
    return {"task": task, **p}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 2
    if argv[0] == "--parse":
        print(json.dumps(parse(" ".join(argv[1:])), ensure_ascii=False))
        return 0
    r = create(" ".join(argv))
    tags = "".join(f" @{l}" for l in r["labels"])
    print(f"+ {r['content']}{tags}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
