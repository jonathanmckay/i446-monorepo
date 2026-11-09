#!/usr/bin/env python3
"""Regression 2026-11-09 ("I didn't see no 英文 media task today"): the daily
hcmc card 'validate no yt,英文,aggregator or banned' was skipped at 08:44 on
Straylight; a dtd relaunched at 09:54 on a cache that still predated the defer
showed it again (the periodic did-refresh-cache job never refetches the general
today bucket, and the per-day deferred marker was written for 0neon/夜neon
habits only); a second skip advanced the series to Nov 10.

defer-fast now marks every deferred recurring parent, and the list generator
must honour the marker for the general today bucket, not only 0neon/夜neon.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
TODAY = dt.date.today().isoformat()   # the generator keys the marker by the CURRENT date


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines) if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines)) if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run(tmp: Path, cache_obj: dict, marker_ids: list[str], mirror: bool) -> str:
    home = tmp / "home"
    home.mkdir()
    (home / "i446-monorepo").symlink_to(HERE.parent.parent)
    if marker_ids:
        if mirror:
            p = home / "vault/z_ibx" / f"habits-deferred-straylight-refit-local-{TODAY}.ids"
        else:
            p = home / ".cache/jm" / f"habits-deferred-{TODAY}.ids"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(i + "\n" for i in marker_ids))
    def _w(name, text):
        q = tmp / name
        q.write_text(text)
        return str(q)
    cache = _w("x.cache.json", json.dumps(cache_obj, ensure_ascii=False))
    done = _w("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}}))
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run([sys.executable, str(payload), cache, done, removed, TODAY, "120",
                        _w("skipped", ""), _w("timer", ""), _w("view", "")],
                       capture_output=True, text=True, env=dict(os.environ, HOME=str(home)))
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def _cache():
    return {"updated": f"{TODAY}T10:00:00", "today": [
        {"id": "V1", "content": "validate no yt,英文,aggregator or banned (1) [25]",
         "labels": ["hcmc"], "due": TODAY, "recurring": True},
        {"id": "K1", "content": "keep me (10) [5]", "labels": ["i9"], "due": TODAY, "recurring": False},
    ]}


def test_unmarked_recurring_card_in_today_bucket_shows(tmp_path):
    out = _run(tmp_path, _cache(), [], mirror=False)
    assert any("V1" in l for l in out.splitlines())


def test_marked_recurring_card_in_today_bucket_is_hidden(tmp_path):
    out = _run(tmp_path, _cache(), ["V1"], mirror=False)
    assert not any("V1" in l for l in out.splitlines())
    assert any("K1" in l for l in out.splitlines()), "unrelated cards stay"


def test_marker_mirrored_from_another_host_hides_too(tmp_path):
    out = _run(tmp_path, _cache(), ["V1"], mirror=True)
    assert not any("V1" in l for l in out.splitlines())
