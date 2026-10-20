#!/usr/bin/env python3
"""Regression: dtd domain search (2026-09-16).

Typing a domain code (e.g. "m5x2") into dtd's query box scopes the list to
that domain. This can't be done with fzf's own fuzzy matcher: domain is
conveyed PURELY via row color (COLORS in the list generator; deliberately no
project-name text in the row, per its own "no project-name prefix... the
names just add clutter" comment) — so the matcher has nothing to search.
$DTD_DOMAINSEARCH (bound to change:) detects an exact domain-code match and
reloads the list pre-filtered server-side (where the true domain, via
domain_of(), is available), then clears the query.

Bug caught building this (2026-09-16): the list generator's own final
python invocation line forwarded only "$1".."$9" to python's sys.argv,
silently dropping a 10th shell argument even after the domain_filter
parsing/filtering logic was added to read sys.argv[10] — the filter was a
complete no-op end-to-end (82 rows unfiltered, still 82 "filtered") until
the invocation line was fixed to add "${10}" (braced — bare $10 in zsh means
${1}0, not the 10th positional param). test_domain_filter_actually_filters
below is an end-to-end functional test specifically so a regression here
fails loudly again instead of silently no-op'ing.

Second bug caught building this: the descriptive comment introducing the
feature (in the LISTEOF python payload, embedded in a double-quoted zsh
`python3 -c "..."` string) used literal double quotes around an example
domain code — zsh sees those before python ever runs, so it closed the -c
string early. test_dtd_listgen_no_shell_meta.py's existing
test_payload_has_no_unescaped_double_quotes catches this class of bug
generically; not re-tested here.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _invocation_line() -> str:
    lines = DTD.splitlines()
    return next(l for l in lines if l.startswith('" "$1"'))


def _domainsearch_block() -> str:
    i = DTD.index('DTD_DOMAINSEARCH="/tmp/dtd-$DTD_ID.domainsearch.sh"')
    j = DTD.index("chmod +x \"$DTD_DOMAINSEARCH\"", i)
    return DTD[i:j]


def _run_listgen(tmp, cache_obj, domain_filter=None):
    def _w(name, obj_or_text):
        p = tmp / name
        p.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
        return str(p)
    cache = _w("cache.json", cache_obj)
    done = _w("done.json", {"date": "2026-09-16", "names": [], "ids": {}})
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", "")
    blockpick = _w("blockpick", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    args = [sys.executable, str(payload), cache, done, removed,
            "2026-09-16", "120", skipped, timer, view, blockpick]
    if domain_filter is not None:
        args.append(domain_filter)
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def _ids_in_order(output: str) -> list[str]:
    return [line.rsplit("\t", 1)[-1] for line in output.splitlines() if line.strip()]


def _task(task_id, content, label, priority=3, due="2026-09-16"):
    return {"id": task_id, "content": content, "labels": [label],
            "priority": priority, "due": due, "recurring": False}


TASKS = [
    _task("M5X001", "check lease renewal (10) [5]", "m5x2"),
    _task("I9001", "review PR (10) [5]", "i9"),
    _task("I9002", "standup notes (10) [5]", "i9"),
]


# ---------------------------------------------------------------------------
# End-to-end functional test — would have caught the "${10}" no-op bug.
# ---------------------------------------------------------------------------

def test_domain_filter_actually_filters():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = {"updated": "t", "today": [], "关键路径": list(TASKS)}

        unfiltered = _ids_in_order(_run_listgen(tmp, cache))
        assert {"M5X001", "I9001", "I9002"} <= set(unfiltered)

        m5x2_only = _ids_in_order(_run_listgen(tmp, cache, domain_filter="m5x2"))
        assert m5x2_only == ["M5X001"], (
            f"expected only the m5x2 task, got {m5x2_only!r} — the domain "
            f"filter arg isn't reaching the list generator's filtering logic"
        )

        i9_only = _ids_in_order(_run_listgen(tmp, cache, domain_filter="i9"))
        assert set(i9_only) == {"I9001", "I9002"}


def test_no_domain_filter_arg_is_unfiltered_default():
    """The whole feature must be additive: omitting the 10th arg entirely
    (every EXISTING caller of $DTD_LIST before this feature) must behave
    exactly as before."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = {"updated": "t", "today": [], "关键路径": list(TASKS)}
        ids = set(_ids_in_order(_run_listgen(tmp, cache, domain_filter=None)))
        assert {"M5X001", "I9001", "I9002"} <= ids


def test_unknown_domain_filter_yields_empty_not_unfiltered():
    """A filter value that matches no task's domain must show nothing, not
    silently fall back to the unfiltered list -- fail loud, not confusing."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = {"updated": "t", "today": [], "关键路径": list(TASKS)}
        ids = [i for i in _ids_in_order(_run_listgen(tmp, cache, domain_filter="hcbp"))
               if i in {"M5X001", "I9001", "I9002"}]
        assert ids == []


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------

def test_invocation_line_forwards_tenth_argument():
    line = _invocation_line()
    assert '"${10}"' in line, (
        f"list generator's python invocation doesn't forward a 10th arg "
        f"(braced ${{10}} required in zsh -- bare $10 means ${{1}}0): {line!r}"
    )


def test_domain_filter_parsed_and_applied_before_view_sort():
    body = _listgen_payload()
    assert "domain_filter = sys.argv[10]" in body
    filter_idx = body.index("domain_filter = sys.argv[10]")
    apply_idx = body.index("unique = [t for t in unique if domain_of(t) == domain_filter]")
    view_idx = body.index("if view == 'project':")
    assert filter_idx < apply_idx < view_idx, (
        "domain filter must be applied to `unique` before the view-mode "
        "sort runs, so project/time views still work within the scoped set"
    )


def test_domainsearch_bound_to_change_event():
    assert "change:first+execute-silent($DTD_DOMAINSEARCH {q})" in DTD, (
        "the domain-search script must be chained onto the change: event "
        "(not a separate --bind change:... — verify multi-bind-per-event "
        "chaining before relying on that instead)"
    )


def test_domainsearch_domain_set_matches_dtd_colors():
    """Sync check, mirroring test_dtd_colors.py's cross-file version: the
    domain codes DTD_DOMAINSEARCH's case statement recognizes must be
    exactly dtd's own COLORS keys, or a real domain silently won't trigger
    the filter (or a stale/removed one will)."""
    import re
    colors_block = DTD[DTD.index("COLORS = {"):]
    colors_block = colors_block[:colors_block.index("}")]
    color_keys = set(re.findall(r"'([^']+)':\s*'\\033\[38;2;", colors_block))

    ds_block = _domainsearch_block()
    case_body = ds_block[ds_block.index("case \"\\$q\" in"):ds_block.index("esac")]
    case_line = next(l.strip().rstrip(")") for l in case_body.splitlines()
                     if "|" in l and l.strip().endswith(")"))
    case_keys = set(case_line.split("|"))

    assert case_keys == color_keys, (
        f"domain-search whitelist out of sync with COLORS: "
        f"missing={color_keys - case_keys} extra={case_keys - color_keys}"
    )


def test_domainsearch_resolves_date_directly_not_deferred():
    """Unlike DTD_RELOAD (baked into a long-lived --bind string at fzf
    startup, so it defers $(date...) to avoid a stale midnight-rollover
    date), this script is spawned fresh on every keystroke -- it should
    resolve `today` directly, not carry the extra deferred-eval escaping
    layer (which would need to survive an additional curl-POST hop with no
    proven precedent for that combination)."""
    block = _domainsearch_block()
    assert 'today="\\$(date +%Y-%m-%d)"' in block
    assert "'\\$today'" in block


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
