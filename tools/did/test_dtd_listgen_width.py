#!/usr/bin/env python3
"""Regression: dtd list rows must fit the LIVE terminal width, so an estimate
row never wraps and renders twice.

Bug (2026-07-12): "Reach out to Andie Perez" showed up twice. It's a single task
(one Todoist id), but dtd was launched with COLUMNS=192 (baked into the reload
binding), while the terminal was 80 cols. `rjust_est` right-justifies the (N)/[N]
estimate to `cols-8`, so the padded row was ~184 chars wide and WRAPPED in the
80-col terminal — one row rendered as two. Only tasks WITH an estimate get
padded, which is why just this one looked doubled.

Fix: the DTD_LIST wrapper resolves the LIVE width (fzf's $FZF_COLUMNS, else the
real tty via `tput cols`, else the baked $5) and passes THAT to the python, so a
resized / mis-reported-at-launch terminal can't pad rows past the real width.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()
_ANSI = re.compile(r"\033\[[0-9;]*m")


# ── structural: wrapper uses the live width, not the baked arg ────────────────

def test_wrapper_resolves_live_width():
    assert '_cols="${FZF_COLUMNS:-}"' in DTD, "must prefer fzf's live window width"
    assert "tput cols" in DTD, "must fall back to the real tty width"


def test_wrapper_passes_live_width_to_python_not_baked_arg():
    # The python must be invoked with the resolved $_cols, not the baked $5.
    assert '"$4" "$_cols" "$6" "$7" "$8"' in DTD
    assert '"$4" "$5" "$6" "$7" "$8"' not in DTD, "the stale baked-$5 call must be gone"


# ── functional: rows fit the width the wrapper hands the python ───────────────

def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines)) if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run(tmp, cols, content):
    import json
    def w(name, obj):
        p = tmp / name
        p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
        return str(p)
    cache = w("cache.json", {"updated": "x", "today": [
        {"id": "T1", "content": content, "labels": ["s897"],
         "priority": 1, "due": "2026-07-12", "recurring": False},
    ]})
    done = w("done.json", {"date": "2026-07-12", "names": [], "ids": {}})
    removed = w("removed", ""); skipped = w("skipped", "")
    timer = w("timer", ""); view = w("view", "")
    (tmp / "removed.ids").write_text("")
    payload = tmp / "lg.py"; payload.write_text(_listgen_payload())
    r = subprocess.run([sys.executable, str(payload), cache, done, removed,
                        "2026-07-12", str(cols), skipped, timer, view],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_estimate_row_fits_given_width(tmp_path):
    # The real-world row that wrapped. At the correct width (80) the visible
    # field-1 text must NOT exceed the width — no wrap, one row.
    content = "Reach out to Andie Perez (overdue weekly: last contact 2026-06-14) (30) [30]"
    out = _run(tmp_path, 80, content)
    rows = [l for l in out.splitlines() if l.strip()]
    assert rows, "expected a rendered row"
    for row in rows:
        field1 = row.split("\t", 1)[0]
        visible = _ANSI.sub("", field1)
        assert len(visible) <= 80, (
            f"row is {len(visible)} cols wide at cols=80 — it will wrap:\n{visible!r}")


def test_stale_wide_width_is_what_caused_the_wrap(tmp_path):
    # Documents the bug: with the stale baked width (192) the SAME row is padded
    # far past an 80-col terminal — which is exactly what wrapped.
    content = "Reach out to Andie Perez (overdue weekly: last contact 2026-06-14) (30) [30]"
    out = _run(tmp_path, 192, content)
    field1 = _ANSI.sub("", out.splitlines()[0].split("\t", 1)[0])
    assert len(field1) > 80, "the stale-wide pad should overflow an 80-col terminal"


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
