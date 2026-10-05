"""Unified schedule screen (2026-10-04): ctrl-d opens one fzf-native screen with
day-defer rows plus the minute/block delays; a typed number + enter defers N
days; esc / ctrl-c go back to the list from that screen, and exit dtd from the
bare list (so pressing either twice from the schedule screen exits).

Unit tests run the generated router scripts with stubbed children, so nothing
reaches Todoist. The pty test drives the real dtd + fzf with only ctrl-d, esc
and ctrl-c -- keys that never complete, defer or delete anything.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DTD = HERE / "dtd.sh"
SRC = DTD.read_text()
LINES = SRC.splitlines()
sys.path.insert(0, str(HERE))


def _heredoc(var, eof):
    """The `cat > "$VAR" << EOF ... EOF` block that writes one action script."""
    s = next(i for i, l in enumerate(LINES) if l.startswith(f'cat > "${var}"'))
    e = next(i for i in range(s, len(LINES)) if LINES[i].strip() == eof)
    return "\n".join(LINES[s:e + 1])


def _gen(tmp, var, eof, extra=""):
    log = tmp / "calls.log"
    log.write_text("")
    stub = tmp / "stub.sh"
    stub.write_text(f'#!/bin/zsh\nprint -r -- "$0|${{DTD_DEFER_DAYS:-}}|$*" >> "{log}"\necho STUB-STDOUT\n')
    stub.chmod(0o755)
    out = tmp / f"{var.lower()}.sh"
    # The script's own output path is assigned LAST so it can't be clobbered
    # by a stub default of the same name (DTD_BLOCKAPPLY is both).
    setup = (f'#!/bin/zsh\nDTD_HDR="{tmp}/hdr"; DTD_BLOCKPICK="{tmp}/blockpick"; '
             f'DTD_BLOCKAPPLY="{tmp}/apply-stub"; DTD_ENTER="{tmp}/enter-stub"; DTD_DEFER="{tmp}/defer-stub"; '
             f'STATE_DIR="{tmp}"\n{extra}\n{var}="{out}"\n')
    for name in ("apply-stub", "enter-stub", "defer-stub"):
        p = tmp / name
        p.write_text(stub.read_text())
        p.chmod(0o755)
    (tmp / "gen.zsh").write_text(setup + _heredoc(var, eof) + "\n")
    subprocess.run(["zsh", str(tmp / "gen.zsh")], check=True, capture_output=True)
    (tmp / "hdr").write_text("")
    return out, log


def _run(script, *args):
    r = subprocess.run(["zsh", str(script), *args], capture_output=True, text=True, timeout=10)
    return r.stdout


def _calls(log):
    return [l.split("|") for l in log.read_text().splitlines() if l.strip()]


# --- bindings ----------------------------------------------------------------

def _bind(key):
    m = re.search(rf'--bind "{re.escape(key)}:[^"]*"', SRC)
    assert m, f"no {key} binding"
    return m.group(0)


def test_enter_esc_ctrlc_ctrld_bindings():
    assert "enter:transform($DTD_PICKENTER {2} {q})+deselect-all+reload($DTD_RELOAD)+clear-query" in _bind("enter")
    for k in ("esc", "ctrl-c"):
        assert f"{k}:transform($DTD_BACK {{q}})+reload($DTD_RELOAD)+clear-query" in _bind(k)
    assert "ctrl-d:execute-silent(DTD_PICK_MODE=days $DTD_BLOCKARM {+2})" in _bind("ctrl-d")
    assert "ctrl-d: 📅schedule" in SRC and "esc: back" in SRC


# --- enter router ------------------------------------------------------------

def test_pickenter_number_on_schedule_screen_defers_n_days():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen(tmp, "DTD_PICKENTER", "PICKENTEREOF")
        (tmp / "blockpick").write_text("A1\n")
        assert _run(s, "", "12") == "", "router must print nothing (transform reads stdout as actions)"
        assert _calls(log) == [[str(tmp / "apply-stub"), "", "BLOCK:d12"]]
        log.write_text("")
        _run(s, "BLOCK:+10m", " 3 ")                       # spaces tolerated, number beats the row
        assert _calls(log)[0][2] == "BLOCK:d3"
        log.write_text("")
        _run(s, "", "2026-10-20")                          # absolute date also accepted
        assert _calls(log)[0][2] == "BLOCK:d2026-10-20"


def test_pickenter_rows_and_main_list_use_enter():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen(tmp, "DTD_PICKENTER", "PICKENTEREOF")
        (tmp / "blockpick").write_text("A1\n")
        _run(s, "BLOCK:申", "shen")                          # picking a row on the schedule screen
        assert _calls(log) == [[str(tmp / "enter-stub"), "", "BLOCK:申"]]   # enter.sh routes BLOCK:* to apply
        (tmp / "blockpick").unlink()
        log.write_text("")
        _run(s, "A1", "12")                                  # main list: a number is just a search
        assert _calls(log) == [[str(tmp / "enter-stub"), "", "A1"]]
        log.write_text("")
        assert _run(s, "", "zzz") == "" and _calls(log) == [], "no row, no number: no-op"


# --- esc / ctrl-c router -----------------------------------------------------

def test_back_from_schedule_screen_returns_to_list():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, _ = _gen(tmp, "DTD_BACK", "BACKEOF")
        (tmp / "blockpick").write_text("A1\n")
        (tmp / "blockpick.mode").write_text("days")
        assert _run(s, "") == "", "must not abort while on the schedule screen"
        assert not (tmp / "blockpick").exists() and not (tmp / "blockpick.mode").exists()
        assert "back to list" in (tmp / "hdr").read_text()


def test_back_on_main_list_exits_or_clears_query():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, _ = _gen(tmp, "DTD_BACK", "BACKEOF")
        assert _run(s, "") == "abort", "bare list: second press exits dtd"
        assert _run(s, "foo") == "", "list with a query: just clear it (bind tail)"


# --- apply: day rows go to defer with a preset, never the block writer -------

def test_apply_day_rows_route_to_defer_with_preset():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen(tmp, "DTD_BLOCKAPPLY", "APPLYEOF",
                      extra=f'HDR="{tmp}/hdr"')
        for glyph, want in (("dauto", "auto"), ("d1", "1"), ("d12", "12")):
            (tmp / "blockpick").write_text("A1\nB2\n")
            (tmp / "blockpick.mode").write_text("days")
            log.write_text("")
            _run(s, f"BLOCK:{glyph}")
            assert _calls(log) == [[str(tmp / "defer-stub"), want, "A1 B2"]], glyph
            assert not (tmp / "blockpick").exists() and not (tmp / "blockpick.mode").exists()
        assert not (tmp / "dtd-block-snooze.json").exists(), "day rows must not touch the block-snooze file"


# --- real dtd in a pty: ctrl-d, esc, esc -------------------------------------

@pytest.mark.skipif(not __import__("shutil").which("fzf"), reason="needs fzf")
def test_pty_esc_backs_out_of_schedule_screen_then_exits():
    import glob
    import pty
    import test_dtd_launch_smoke as smoke

    before = set(glob.glob("/tmp/dtd-*.start.sh"))
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child
        os.execvp("zsh", ["zsh", "-c", f"source {DTD}"])
    stem = None
    try:
        new = None
        for _ in range(100):
            smoke._drain(fd, 0.1)
            new = set(glob.glob("/tmp/dtd-*.start.sh")) - before
            if new:
                break
        assert new, "dtd never started"
        stem = sorted(new)[0][: -len(".start.sh")]
        for _ in range(250):
            smoke._drain(fd, 0.1)
            if os.path.exists(stem + ".port") and os.path.getsize(stem + ".port") > 0:
                break
        else:
            pytest.fail("fzf never came up")
        smoke._drain(fd, 1.0)
        pick = stem + ".blockpick"

        def armed():
            return os.path.exists(pick) and os.path.getsize(pick) > 0

        for back_key in (b"\x1b", b"\x03"):                 # esc, then ctrl-c
            os.write(fd, b"\x04")                            # ctrl-d -> schedule screen
            for _ in range(60):
                smoke._drain(fd, 0.1)
                if armed():
                    break
            if not armed():
                pytest.skip("no task rows in the live cache to open the schedule screen on")
            assert open(stem + ".blockpick.mode").read() == "days"
            os.write(fd, back_key)
            for _ in range(40):
                smoke._drain(fd, 0.1)
                if not os.path.exists(pick):
                    break
            assert not os.path.exists(pick), f"{back_key!r} must close the schedule screen"
            smoke._drain(fd, 0.5)
            done, _ = os.waitpid(pid, os.WNOHANG)
            assert done == 0, f"{back_key!r} on the schedule screen must NOT exit dtd"
        os.write(fd, b"\x1b")                                # bare list: exits
        assert smoke._reap(pid, fd, 6.0), "esc on the bare list must exit dtd"
        pid = None
    finally:
        if pid:
            import signal
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(os.getpgid(pid), sig)
                except (OSError, ProcessLookupError):
                    pass
                if smoke._reap(pid, fd, 2.0):
                    break
        os.close(fd)
        if stem:
            for leftover in glob.glob(stem + ".*"):
                try:
                    os.remove(leftover)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# --- real defer script: a preset day count skips the prompt and the tty drain

def test_defer_honors_preset_days_without_prompting():
    import test_dtd_defer_blockarm_single_jq as dj
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        d, a = dj._generate(tmp, "tschedpre")
        argv_log = tmp / "argv.log"
        (tmp / "stub.py").write_text(
            "import json,sys\n"
            f"open({str(argv_log)!r},'a').write(' '.join(sys.argv[1:])+'\\n')\n"
            'print(json.dumps({"target_date":"2026-10-16","claimed_points":0,"remaining_points":0}))\n')
        try:
            env = {**dj._env(), "DTD_DEFER_PROMPT": "1", "DTD_DEFER_DAYS": "12"}
            t0 = time.time()
            r = subprocess.run(["zsh", str(d), "A1"], env=env, timeout=10, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            assert time.time() - t0 < 5.0, "must not block on a tty prompt"
            assert "Defer '" not in r.stdout + r.stderr, "no prompt text when a preset is given"
            assert dj._wait_for(lambda: argv_log.exists() and argv_log.read_text().strip())
            assert argv_log.read_text().split() == ["--id", "A1", "12"]
            assert "+12" in (tmp / "hdr").read_text() or "⏭" in (tmp / "hdr").read_text()
        finally:
            d.unlink(); a.unlink()


# --- custom days entry + "delay N days" header (2026-10-04) --------------------

def test_custom_row_switches_prompt_then_number_applies_and_resets():
    """User request: a 'custom option' on the delay screen that takes you to a
    text input on the same screen for an integer number of days."""
    assert "✎ delay N days: type a number" in SRC
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen(tmp, "DTD_PICKENTER", "PICKENTEREOF")
        (tmp / "blockpick").write_text("A1\n")
        assert _run(s, "BLOCK:custom", "") == "change-prompt(📅 delay days > )"
        assert _calls(log) == [], "choosing the custom row applies nothing yet"
        assert (tmp / "blockpick.custom").exists() and "number of days" in (tmp / "hdr").read_text()
        assert _run(s, "", "5") == "change-prompt(> )", "applying the number restores the prompt"
        assert _calls(log) == [[str(tmp / "apply-stub"), "", "BLOCK:d5"]]
        assert not (tmp / "blockpick.custom").exists()


def test_picking_another_row_after_custom_still_restores_prompt():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen(tmp, "DTD_PICKENTER", "PICKENTEREOF")
        (tmp / "blockpick").write_text("A1\n")
        (tmp / "blockpick.custom").write_text("")
        assert _run(s, "BLOCK:d2", "") == "change-prompt(> )"
        assert _calls(log)[0][2] == "BLOCK:d2"


def test_esc_from_custom_entry_restores_prompt():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, _ = _gen(tmp, "DTD_BACK", "BACKEOF")
        (tmp / "blockpick").write_text("A1\n")
        (tmp / "blockpick.custom").write_text("")
        assert _run(s, "") == "change-prompt(> )"
        assert not (tmp / "blockpick.custom").exists()


def test_apply_never_sends_custom_to_the_block_writer():
    assert '[[ "\\$glyph" == custom ]]' in SRC


def test_typing_a_number_on_delay_screen_says_delay_n_days():
    """User follow-up: 'add the text delay XX days so I know that's what's
    happening'. The per-keystroke hook writes it to the header."""
    s = next(i for i, l in enumerate(LINES) if l.startswith("DTD_DOMAINSEARCH="))
    e = next(i for i in range(s, len(LINES)) if LINES[i] == "EOF")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "g.zsh").write_text(f'#!/bin/zsh\nDTD_ID="tdsh"; DTD_BLOCKPICK="{tmp}/bp"; DTD_HDR="{tmp}/hdr"; '
                                   f'DTD_PORT="{tmp}/port"\n' + "\n".join(LINES[s:e + 1]) + "\n")
        subprocess.run(["zsh", str(tmp / "g.zsh")], check=True)
        ds = "/tmp/dtd-tdsh.domainsearch.sh"
        try:
            (tmp / "bp").write_text("A1\n")
            for q, want in (("3", "↵ delay 3 days → "), ("1", "↵ delay 1 day → "), ("0", "↵ delay 0 days → next occurrence")):
                (tmp / "hdr").write_text("-")
                subprocess.run(["zsh", ds, q], check=True)
                assert (tmp / "hdr").read_text().startswith(want), (q, (tmp / "hdr").read_text())
            (tmp / "bp").unlink()
            (tmp / "hdr").write_text("-")
            subprocess.run(["zsh", ds, "5"], check=True)
            assert (tmp / "hdr").read_text() == "-", "main list: a number is just a search"
        finally:
            Path(ds).unlink(missing_ok=True)
