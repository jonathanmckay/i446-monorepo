#!/usr/bin/env python3
"""edit-fast.py — unified task edit for dtd's ctrl-g.

Usage: edit-fast.py <query> <edit_string> <cache_file>
       edit-fast.py --current --id <id> <cache_file>        (form prefill)
       edit-fast.py --fields --id <id> <cache_file> [--name X] [--domain D]
                    [--points N] [--desc TEXT] [--line L]    (form submit)

The ctrl-o/ctrl-g form (2026-10-11) edits one labeled field at a time: title,
domain, points, description. --current prints the prefill as shell
assignments; --fields applies only the fields passed, each taken literally.

One prompt edits three things on the highlighted task from a single line:
  - any @code token       → domain label   (swap; mirrors domain-fast)
  - any standalone integer → points [N]     (last number wins)
  - all remaining text     → new task name  (trailing annotations preserved)

Resolves the task from the dtd snapshot cache, applies the changes in Todoist
(content for name/points, labels for domain), patches the cache so the row
updates on reload, and prints a one-line summary for the dtd header. Reuses
domain-fast (DOMAINS/swap_domain) and points-fast (set_points/resolve/_api).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m
    spec.loader.exec_module(m)
    return m


_dom = _load("domain_fast", "domain-fast.py")  # DOMAINS, swap_domain, patch_cache_labels
_pf = _load("points_fast", "points-fast.py")   # set_points, resolve_from_cache, patch_cache
_df = _pf._df                                   # Todoist _api transport
_short = _load("shorten_mod", "shorten.py")     # shorten_tasks — same fn the cache refresh uses

DOMAINS = _dom.DOMAINS

# One or more trailing (time)/[pts]/{bonus} annotation groups at end of content.
_TRAIL_ANNOT = re.compile(r"(?:\s*[\(\[\{][^\)\]\}]*[\)\]\}])+\s*$")
# A single annotation token of any bracket kind, anywhere.
_ANNOT_TOK = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")


def set_name(content: str, new_name: str) -> str:
    """Replace the leading name, preserving trailing (time)/[pts]/{bonus}
    annotations — EXCEPT the bracket kinds the new name itself carries, which
    would otherwise duplicate (bug 2026-07-24: retyping the full line
    "…photos (15) [20]" over "…photos (15) [15]" produced "(15) [20] (15)
    [15]" — the typed annotations landed in the name and the old tail was
    appended after them)."""
    m = _TRAIL_ANNOT.search(content)
    tail = content[m.start():].strip() if m else ""
    base = new_name.strip()
    kinds = {t[0] for t in _ANNOT_TOK.findall(base)}
    if kinds and tail:
        tail = " ".join(t for t in _ANNOT_TOK.findall(tail) if t[0] not in kinds)
    return (base + (" " + tail if tail else "")).strip()


def _resync_short(cache: dict, task_id: str, new_content: str) -> bool:
    """Drop the cached Haiku `short` name for the OLD content, then
    regenerate one for the NEW content immediately (via the same
    shorten_tasks() the periodic cache refresh uses) if it's long enough to
    need one. Without this, a rename that crosses PROSE_CAP just fell back to
    fzf's raw middle-truncation — eating the trailing (N)/[N] estimate —
    until whenever the next full --refresh-cache happened to run (bug
    2026-07-28: an edited task stayed unshortened, unlike every other long
    task, which gets shortened by that same refresh pass).

    Returns False when the new title needs a short name and none came back
    (2026-10-13: dtd runs on ix, which had no ANTHROPIC_API_KEY, so every
    rename there silently kept its over-long title). The caller says so in
    the dtd header instead of failing quietly."""
    short = _short.shorten_tasks([{"id": task_id, "content": new_content}]).get(task_id)
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("id") == task_id:
                    if short:
                        t["short"] = short
                    else:
                        t.pop("short", None)
    needs = len(_short.split_estimates(new_content)[0]) > _short.PROSE_CAP
    return bool(short) or not needs


def parse_edits(edit_string: str) -> tuple[str | None, str | None, int | None]:
    """Parse a ctrl-g edit line into (new_name, domain, points).

    @code → domain; a standalone integer → points (last one wins, earlier
    numbers fall back into the name); everything else → name. Any field is
    None when absent."""
    domain: str | None = None
    points: int | None = None
    name_tokens: list[str] = []
    for tok in edit_string.split():
        if tok.startswith("@") and len(tok) > 1:
            domain = tok[1:]
        elif re.fullmatch(r"\d+", tok):
            if points is not None:
                name_tokens.append(str(points))  # demote the earlier number to name
            points = int(tok)
        elif re.fullmatch(r"\[\d+\]", tok):
            # "[20]" typed in display syntax means points, not name text —
            # as a name token it duplicated next to the preserved [N] tail
            # (bug 2026-07-24: "changed points and it doubled up").
            if points is not None:
                name_tokens.append(str(points))
            points = int(tok[1:-1])
        else:
            name_tokens.append(tok)
    new_name = " ".join(name_tokens).strip() or None
    return new_name, domain, points


# The numeric tags the row shows separately: (time), [pts], [rate/m], {bonus}.
# Same set dtd's edit prompt has always stripped from the prefill; other
# parentheticals ("(see doc)") are part of the name.
_NUM_ANNOT = re.compile(r"\s*(?:\(\d*\)|\[\d*\]|\[[\d.+]*/m\]|\{\d*\})")


def current_fields(task: dict, description: str) -> dict:
    """The form's prefill: clean name, domain label, [N] points, description."""
    content = task.get("content") or ""
    m = re.search(r"\[(\d+)\]", content)
    dom = [l for l in task.get("labels", []) if l in DOMAINS]
    return {
        "name": re.sub(r"  +", " ", _NUM_ANNOT.sub("", content)).strip(),
        "domain": dom[0] if dom else "",
        "points": m.group(1) if m else "",
        "desc": description or "",
    }


# One-line form (2026-10-13: "it shouldn't ask for things in multiple
# questions ... it should parse what I put in to make the necessary changes in
# one screen"). The prompt is prefilled with format_line(cur) and parsed back
# by parse_line(): @code → domain, [N] → points, text after a standalone //
# → description, the rest → title. Unlike parse_edits, a bare number stays in
# the title (the prefill always spells points as [N], so "call 3 tenants"
# must not become [3]). A part that's absent means keep; "//" with nothing
# after it clears the description.
_DESC_SEP = re.compile(r"(?:^|\s)//(?:\s|$)")


def format_line(cur: dict) -> str:
    parts = [cur.get("name") or ""]
    if cur.get("domain"):
        parts.append("@" + cur["domain"])
    if cur.get("points"):
        parts.append(f"[{cur['points']}]")
    line = " ".join(p for p in parts if p)
    if cur.get("desc"):
        line += " // " + cur["desc"]
    return line


def parse_line(line: str) -> dict:
    out: dict = {}
    m = _DESC_SEP.search(line)
    if m:
        out["desc"] = line[m.end():].strip()
        line = line[:m.start()]
    name_tokens: list[str] = []
    for tok in line.split():
        if tok.startswith("@") and len(tok) > 1:
            out["domain"] = tok[1:]
        elif re.fullmatch(r"\[\d+\]", tok):
            out["points"] = tok[1:-1]
        else:
            name_tokens.append(tok)
    out["name"] = " ".join(name_tokens)
    return out


def _description(task_id: str) -> str:
    """Description from task-detail's per-task cache (the form just rendered
    it), else live from Todoist."""
    import os
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "jm"
    try:
        bundle = json.loads((state / "task-detail" / f"{task_id}.json").read_text())
        return ((bundle.get("task") or {}).get("description")) or ""
    except (OSError, ValueError):
        pass
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import todoist
        return (todoist.get_task(task_id) or {}).get("description") or ""
    except Exception:  # noqa: BLE001
        return ""


def changed_fields(cur: dict, new: dict) -> dict:
    """Only the fields whose submitted value differs from the prefill.
    Name, domain and points are compared stripped; an emptied name, domain
    or points means "leave it" (there's no removing a title); an emptied
    description clears it."""
    out = {}
    for k in ("name", "domain", "points"):
        v = (new.get(k) or "").strip()
        if v and v != (cur.get(k) or "").strip():
            out[k] = v.lstrip("@") if k == "domain" else v
    if "desc" in new and (new["desc"] or "").rstrip() != (cur.get("desc") or "").rstrip():
        out["desc"] = (new["desc"] or "").rstrip()
    return out


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--current"]:
        return _main_current(argv[1:])
    if argv[:1] == ["--fields"]:
        return _main_fields(argv[1:])
    # Forms:  edit-fast.py --id <id> <edits> <cache_file>   ← dtd
    #         edit-fast.py <query>   <edits> <cache_file>   ← fallback
    task_id = None
    if argv and argv[0] == "--id":
        task_id = argv[1] if len(argv) > 1 else None
        argv = argv[2:]
    if (task_id is None and len(argv) < 3) or (task_id is not None and len(argv) < 2):
        print("✗ usage: edit-fast.py [--id <id>] <query> <edits> <cache_file>")
        return 2
    if task_id is not None:
        query, edits, cache_file = None, argv[0], argv[1]
    else:
        query, edits, cache_file = argv[0], argv[1], argv[2]
    new_name, domain, points = parse_edits(edits)
    if new_name is None and domain is None and points is None:
        print("✗ nothing to change")
        return 1
    if domain is not None and domain not in DOMAINS:
        print(f"✗ unknown domain: {domain}")
        return 1

    try:
        cache = json.loads(Path(cache_file).read_text())
    except Exception as e:
        print(f"✗ cache unreadable: {e}")
        return 1

    task = _pf.resolve_by_id(cache, task_id) if task_id else _pf.resolve_from_cache(cache, query)
    if not task:
        print(f"✗ no task matched: {task_id or query}")
        return 1
    return apply(task, cache, cache_file, new_name, domain, points)


def _load_task(task_id: str, cache_file: str):
    try:
        cache = json.loads(Path(cache_file).read_text())
    except Exception as e:  # noqa: BLE001
        print(f"✗ cache unreadable: {e}")
        return None, None
    task = _pf.resolve_by_id(cache, task_id)
    if not task:
        print(f"✗ no task matched: {task_id}")
    return cache, task


def _main_current(argv: list[str]) -> int:
    import shlex
    if len(argv) < 3 or argv[0] != "--id":
        return 2
    _, task = _load_task(argv[1], argv[2])
    if not task:
        return 1
    cur = current_fields(task, _description(argv[1]))
    for k, v in cur.items():
        print(f"cur_{k}={shlex.quote(v)}")
    print(f"cur_line={shlex.quote(format_line(cur))}")
    print(f"domains={shlex.quote(' '.join(sorted(DOMAINS)))}")
    return 0


def _main_fields(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] != "--id":
        print("✗ usage: edit-fast.py --fields --id <id> <cache_file> [--name X] [--domain D] [--points N] [--desc T]")
        return 2
    task_id, cache_file, rest = argv[1], argv[2], argv[3:]
    new: dict = {}
    flags = {"--name": "name", "--domain": "domain", "--points": "points", "--desc": "desc"}
    i = 0
    while i + 1 < len(rest):
        if rest[i] == "--line":
            new.update(parse_line(rest[i + 1]))
        elif rest[i] in flags:
            new[flags[rest[i]]] = rest[i + 1]
        i += 2
    cache, task = _load_task(task_id, cache_file)
    if not task:
        return 1
    ch = changed_fields(current_fields(task, _description(task_id)), new)
    if not ch:
        print(f"✎ {_pf._ANNOT.sub('', task['content']).strip()}: no change")
        return 0
    if "domain" in ch and ch["domain"] not in DOMAINS:
        print(f"✗ unknown domain: {ch['domain']}")
        return 1
    pts = None
    if "points" in ch:
        if not re.fullmatch(r"\d+", ch["points"].strip("[]")):
            print(f"✗ points must be a number: {ch['points']}")
            return 1
        pts = int(ch["points"].strip("[]"))
    return apply(task, cache, cache_file, ch.get("name"), ch.get("domain"), pts,
                 description=ch.get("desc"))


def apply(task: dict, cache: dict, cache_file: str, new_name, domain, points,
          description: str | None = None) -> int:
    orig_content = task["content"]
    orig_clean = _pf._ANNOT.sub("", orig_content).strip()
    summary: list[str] = []

    # 1. content (name and/or points) — one Todoist call
    new_content = orig_content
    if new_name is not None:
        new_content = set_name(new_content, new_name)
    if points is not None:
        new_content = _pf.set_points(new_content, points)
    body = {}
    if new_content != orig_content:
        body["content"] = new_content
    if description is not None:
        body["description"] = description
    if body:
        try:
            _df._api("POST", f"/tasks/{task['id']}", body)
        except Exception as e:
            print(f"✗ Todoist update failed: {e}")
            return 1
    if description is not None:
        summary.append("description cleared" if not description else "description updated")
    if new_content != orig_content:
        # Resync the Haiku short on EVERY content change, not just renames —
        # dtd renders `short` preferentially, so a points-only edit ("10")
        # that skipped this kept displaying the OLD [N] until the next full
        # refresh (bug 2026-07-31: "[30]→[10] didn't reflect in render").
        _pf.patch_cache(cache, task["id"], new_content)
        short_ok = _resync_short(cache, task["id"], new_content)  # regenerate for the NEW content, don't just drop
        if new_name is not None:
            summary.append(f'name→"{new_name}"')
        if points is not None:
            summary.append(f"[{points}]")
        if short_ok is False:
            summary.append("⚠ no short name (Haiku call failed; check ANTHROPIC_API_KEY)")

    # 2. domain (labels) — separate Todoist call
    if domain is not None:
        old = [l for l in task.get("labels", []) if l in DOMAINS]
        new_labels = _dom.swap_domain(task.get("labels", []), domain)
        try:
            _df._api("POST", f"/tasks/{task['id']}", {"labels": new_labels})
        except Exception as e:
            print(f"✗ Todoist domain update failed: {e}")
            return 1
        _dom.patch_cache_labels(cache, task["id"], new_labels)
        summary.append(f"{old[0]}→{domain}" if old else f"@{domain}")

    try:
        Path(cache_file).write_text(json.dumps(cache))
    except Exception:
        pass  # Todoist already updated; cache catches up on next refresh

    if not summary:
        print(f"✎ {orig_clean}: no change")
    else:
        print(f"✎ {orig_clean}: " + " · ".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
