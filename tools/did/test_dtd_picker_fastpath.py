#!/usr/bin/env python3
"""Schedule screen rendered by zsh builtins (2026-10-21, "ctrl+d is still
feeling a little bit delayed"). When the picker is armed, the list script
prints the block/day rows itself and exits before the python list generator
(~62 ms of startup + cache load). Python's BLOCK-PICKER branch stays as the
reference; this test diffs the two byte-for-byte across mode x digit query x
snoozed, under a 24-timezone sweep so every hour-of-day gate is exercised.

Also pins the plumbing: the fast path precedes `python3 -c`, the arm script
writes $BLOCKPICK.snoozed from a third jq field with a forward-only date
gate, fzf has no result: transform, ctrl-d/v/k end at clear-query, and
domainsearch pushes the header hint itself (above its .days early exit).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()
LINES = SRC.split("\n")


def _list_body() -> str:
    i0 = next(i for i, l in enumerate(LINES) if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    i1 = next(i for i in range(i0 + 1, len(LINES)) if LINES[i] == "LISTEOF")
    return "\n".join(LINES[i0 + 1:i1]) + "\n"   # quoted heredoc: no expansion


def _py_payload() -> str:
    i0 = next(i for i, l in enumerate(LINES) if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(LINES)) if LINES[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(LINES)) if LINES[i].startswith('" "$1"'))
    return "\n".join(LINES[ps + 1:pe])


def test_fast_path_precedes_python_and_keeps_the_warm_guard():
    body = _list_body().splitlines()
    assert body[1].startswith('[[ -n "${DTD_WARM:-}" ]] && exit 0')
    fast = next(i for i, l in enumerate(body) if l.startswith('if [[ -n "$9" && -s "$9" ]]; then'))
    py = next(i for i, l in enumerate(body) if l.startswith('python3 -c "'))
    assert fast < py
    assert "LC_TIME=C" in "\n".join(body[fast:py])
    assert "strftime -r -s _noon" in "\n".join(body[fast:py]), "day label must use calendar arithmetic"


class _Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        home = tmp / "home"
        (home / ".local/state/jm").mkdir(parents=True)
        (home / "vault/z_ibx").mkdir(parents=True)
        (home / "i446-monorepo").symlink_to(Path.home() / "i446-monorepo")
        self.home = home
        self.cache = self._w("cache.json", json.dumps({"updated": "t", "today": [], "关键路径": [
            {"id": "T1", "content": "a task (10) [5]", "labels": ["i9"], "priority": 3,
             "due": "2026-10-21", "recurring": False}]}))
        (tmp / "removed.ids").write_text("")
        self.bp = self._w("blockpick", "T1\n")
        (tmp / "lg.py").write_text(_py_payload())
        (tmp / "list.zsh").write_text(_list_body())
        self.args = [self.cache, self._w("done.json", '{"date": "2026-10-21", "names": [], "ids": {}}'),
                     self._w("removed", ""), "2026-10-21", "120", self._w("skipped", ""), self._w("timer", ""),
                     self._w("view", ""), self.bp, self._w("domain", "")]

    def _w(self, n, t):
        (self.tmp / n).write_text(t)
        return str(self.tmp / n)

    def set(self, mode, days, snoozed):
        (self.tmp / "blockpick.mode").write_text(mode)
        (self.tmp / "blockpick.days").unlink(missing_ok=True)
        if days is not None:
            (self.tmp / "blockpick.days").write_text(days)
        (self.tmp / "blockpick.snoozed").unlink(missing_ok=True)
        sn = self.home / ".local/state/jm/dtd-block-snooze.json"
        sn.unlink(missing_ok=True)
        if snoozed:
            import datetime
            sn.write_text(json.dumps({"date": "2099-01-01", "snoozes": {"T1": 23}}))  # forward-only gate: still valid
            (self.tmp / "blockpick.snoozed").write_text("")

    def run(self, tz):
        env = dict(os.environ, HOME=str(self.home), TZ=tz)
        env.pop("LC_ALL", None)
        py = subprocess.run([sys.executable, str(self.tmp / "lg.py"), *self.args], capture_output=True, text=True, env=env)
        zs = subprocess.run(["zsh", str(self.tmp / "list.zsh"), *self.args], capture_output=True, text=True, env=env)
        assert py.returncode == 0, py.stderr
        assert zs.returncode == 0, zs.stderr
        return py.stdout, zs.stdout


def test_zsh_rows_match_python_rows_byte_for_byte_across_all_hours():
    with tempfile.TemporaryDirectory() as d:
        e = _Env(Path(d))
        combos = [(m, q, s) for m in ("days", "delay") for q in (None, "0", "4", "12") for s in (False, True)]
        checked = 0
        for off in range(-12, 13):            # Etc/GMT+12 .. Etc/GMT-12 sweeps every local hour
            tz = f"Etc/GMT{off:+d}"
            for mode, days, snoozed in combos:
                e.set(mode, days, snoozed)
                a, b = e.run(tz)
                if a != b:                     # the minute may have rolled between the two runs
                    a, b = e.run(tz)
                assert a == b, f"tz={tz} mode={mode} days={days} snoozed={snoozed}\npy={a!r}\nzsh={b!r}"
                checked += 1
        assert checked == 25 * len(combos)
        e.set("days", None, True)
        a, _ = e.run("America/Los_Angeles")        # sanity: a full picker was among the compared outputs
        assert "BLOCK:cancel" in a and "BLOCK:now" in a and "BLOCK:dauto" in a


def test_zsh_fast_path_is_fast_and_python_is_not_run():
    with tempfile.TemporaryDirectory() as d:
        e = _Env(Path(d))
        e.set("days", None, False)
        env = dict(os.environ, HOME=str(e.home), PATH="/nonexistent")   # no python3 reachable
        t0 = time.time()
        r = subprocess.run(["/bin/zsh", str(e.tmp / "list.zsh"), *e.args], capture_output=True, text=True, env=env)
        dt = time.time() - t0
        assert r.returncode == 0 and "BLOCK:d1" in r.stdout, r.stderr
        assert dt < 0.2, f"fast path took {dt*1000:.0f} ms"


def _heredoc(var: str, eof: str) -> str:
    i0 = next(i for i, l in enumerate(LINES) if l.startswith(f'cat > "${var}" <<') and eof in l)
    i1 = next(i for i in range(i0 + 1, len(LINES)) if LINES[i] == eof)
    return "\n".join(LINES[i0 + 1:i1])


def test_arm_script_writes_snoozed_flag_from_a_third_jq_field():
    arm = _heredoc("DTD_BLOCKARM", "ARMEOF")
    assert '.date >= (\\$lt | strftime("%Y-%m-%d"))' in arm, "forward-only date gate, like _sn_all"
    assert "| [\\$n, ., \\$snz] | @tsv'" in arm
    assert ': > "\\$BLOCKPICK.snoozed"; else rm -f "\\$BLOCKPICK.snoozed"' in arm
    assert arm.index("n=3; clean=") > arm.index("| @tsv'")


def test_bindings_have_no_result_transform_and_pickers_end_at_clear_query():
    assert '"result:' not in SRC
    for key in ("ctrl-d", "ctrl-v", "ctrl-k"):
        line = next(l for l in LINES if f'--bind "{key}:' in l)
        assert line.rstrip(" \\").endswith('+reload-sync($DTD_RELOAD)+clear-query"'), line
    load = next(l for l in LINES if '--bind "load:' in l)
    assert "transform-header($DTD_HDRGEN)+transform-footer($DTD_HDRGEN footer)" in load


def test_domainsearch_pushes_the_hint_header_itself_above_the_days_gate():
    ds = _heredoc("DTD_DOMAINSEARCH", "EOF")
    push = ds.index("_push_hdr")
    gate = ds.index('[[ "\\$_want" == "\\$_have" ]] && exit 0')
    assert push < gate, "the header POST must run before the .days early exit"
    assert 'change-header:\\$1   \\$DTD_KEYS' in ds
    assert "strftime -r -s _noon" in ds and "EPOCHSECONDS + _n * 86400" not in ds
    assert 'cp "$DTD_HDR" "$DTD_BLOCKPICK.hint"' in ds and 'cat "$DTD_BLOCKPICK.hint" > "$DTD_HDR"' in ds


def test_domainsearch_hint_saves_and_restores_the_schedule_line():
    s = next(i for i, l in enumerate(LINES) if l.startswith("DTD_DOMAINSEARCH="))
    e = next(i for i in range(s, len(LINES)) if LINES[i] == "EOF")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "g.zsh").write_text(f'#!/bin/zsh\nDTD_ID="tdpf"; DTD_BLOCKPICK="{tmp}/bp"; DTD_HDR="{tmp}/hdr"; '
                                   f'DTD_PORT="{tmp}/noport"\n' + "\n".join(LINES[s:e + 1]) + "\n")
        subprocess.run(["zsh", str(tmp / "g.zsh")], check=True)
        ds = "/tmp/dtd-tdpf.domainsearch.sh"
        try:
            (tmp / "bp").write_text("A1\n")
            (tmp / "hdr").write_text("📅 schedule a task (enter picks)\n")
            subprocess.run(["zsh", ds, "2"], check=True)
            assert (tmp / "hdr").read_text().startswith("↵ delay 2 days → ")
            assert (tmp / "bp.hint").read_text().startswith("📅 schedule")
            subprocess.run(["zsh", ds, "21"], check=True)      # still digits: hint kept, not re-saved
            assert (tmp / "bp.hint").read_text().startswith("📅 schedule")
            subprocess.run(["zsh", ds, "2:"], check=True)      # not digits any more: restored once
            assert (tmp / "hdr").read_text().startswith("📅 schedule a task")
            assert not (tmp / "bp.hint").exists()
        finally:
            Path(ds).unlink(missing_ok=True)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
