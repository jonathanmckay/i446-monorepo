#!/usr/bin/env python3
"""-1g prefill on the dtd card + alt-enter → form (2026-11-09, 1-1n idea 2).

The list generator shows the top open 0₲ goal on the 😈 -1g ritual card after
a second 😈 ("so I know it's a prefill") and writes it to the .prefill sidecar
next to the cache file. The done router (alt-enter) opens the -1g form when
that sidecar is non-empty ("the prefill already exists, hit ctrl+s then it
saves") and completes the card instantly, as before, when it is empty.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
TODAY = "2026-11-09"


def _heredoc(var: str, eof: str) -> str:
    lines = DTD.split("\n")
    i0 = next(i for i, l in enumerate(lines) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(lines)) if lines[i] == eof)
    return "\n".join(lines[i0 + 1:i1])


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines) if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines)) if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


BO = """## 0₲
- [x] done already {20}
- [ ] Good interviews, then rest {40}
### 以后的目标
- [ ] later thing {10}

## -1₲
- 卯
    - [ ]
- 辰
    - [ ]
"""


def _run_listgen(tmp: Path, cache_obj: dict, build_order: str | None) -> tuple[str, Path]:
    """Run the generator with HOME pointed at tmp: ~/i446-monorepo is a symlink
    to the real repo (lib import), ~/vault/g245/5e-1/build-order.md is ours."""
    home = tmp / "home"
    home.mkdir()
    (home / "i446-monorepo").symlink_to(HERE.parent.parent)
    if build_order is not None:
        bo = home / "vault/g245/5e-1/build-order.md"
        bo.parent.mkdir(parents=True)
        bo.write_text(build_order)
    def _w(name, text):
        p = tmp / name
        p.write_text(text)
        return str(p)
    cache = _w("x.cache.json", json.dumps(cache_obj, ensure_ascii=False))
    done = _w("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}}))
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    env = dict(os.environ, HOME=str(home))
    r = subprocess.run([sys.executable, str(payload), cache, done, removed, TODAY, "120",
                        skipped, timer, view], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout, tmp / "x.prefill"


def _cache():
    return {"updated": f"{TODAY}T10:00:00", "today": [
        {"id": "RIT_1G", "content": "😈 -1g", "labels": ["-1neon"], "due": TODAY, "recurring": True},
        {"id": "RIT_IBX", "content": "😈 -1ibx", "labels": ["-1neon"], "due": TODAY, "recurring": True},
    ]}


def test_card_shows_prefill_after_second_demon_and_writes_sidecar(tmp_path):
    out, sidecar = _run_listgen(tmp_path, _cache(), BO)
    row = next(l for l in out.splitlines() if "RIT_1G" in l)
    assert "😈 -1g 😈 Good interviews, then rest" in row, row
    ibx = next(l for l in out.splitlines() if "RIT_IBX" in l)
    assert "Good interviews" not in ibx
    assert sidecar.read_text() == "Good interviews, then rest"


def test_no_open_0g_goal_means_plain_card_and_empty_sidecar(tmp_path):
    out, sidecar = _run_listgen(tmp_path, _cache(), "## 0₲\n- [x] all done {10}\n\n## -1₲\n- 卯\n")
    row = next(l for l in out.splitlines() if "RIT_1G" in l)
    assert "😈 -1g 😈" not in row
    assert sidecar.exists() and sidecar.read_text() == ""


def test_missing_build_order_never_breaks_the_list(tmp_path):
    out, sidecar = _run_listgen(tmp_path, _cache(), None)
    assert any("RIT_1G" in l for l in out.splitlines())
    assert sidecar.read_text() == ""


def test_router_opens_form_only_with_prefill_on_show():
    body = _heredoc("DTD_DONE_ROUTER", "ROUTEREOF")
    guard = re.search(r'if \[\[ "\\\$_t_base" == "😈 -1g" && -s "(/tmp/dtd-\$DTD_ID\.prefill)" \]\]; then\n'
                      r"  printf 'execute\(%s %s %s --detach\)' \"(/tmp/dtd-\$DTD_ID\.form\.sh)\"", body)
    assert guard, body[:800]
    assert "-1g/-1g-form.py" in body
    # the plain-completion branches are untouched (one execute, one execute-silent)
    assert body.count("printf '%sexecute(") == 1 and body.count("printf '%sexecute-silent(") == 1
    # the sidecar path the router reads is the one the generator writes
    assert 'DTD_PREFILL="/tmp/dtd-$DTD_ID.prefill"' in DTD
    assert "rsplit('.', 2)[0] + '.prefill'" in _listgen_payload()
